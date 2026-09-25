#!/usr/bin/env python3
"""Claude Cauth: switch between Claude Code accounts without a browser round trip.

Claude Code keeps one authenticated account at a time, in two places:
  ~/.claude/.credentials.json   the OAuth token blob
  ~/.claude.json                an `oauthAccount` identity block, among unrelated state

This stores a copy of each account's pair under an alias and swaps them in place.
"""

from __future__ import annotations

__version__ = "0.2.0"

import base64
import functools
import hashlib
import queue
import shlex
import shutil
import threading
from contextlib import contextmanager
import getpass
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import warnings
import diagnostics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Storage layout is locked by .planning/BUILD-HANDOFF.md. The directory name stays
# claude-oauth even though the app is Claude Cauth: renaming orphans existing profiles.
CONFIG_DIR = Path.home() / ".config" / "claude-oauth"
PROFILES_DIR = CONFIG_DIR / "profiles"
ACCOUNTS_JSON = CONFIG_DIR / "accounts.json"

# Claude Code honours CLAUDE_CONFIG_DIR for the credentials file. Anyone who has set it
# would otherwise get a tool that reads and writes a file nothing consults.
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
LIVE_CREDENTIALS = CLAUDE_DIR / ".credentials.json"
# Not under the config dir. This one stays in the home directory.
LIVE_CLAUDE_JSON = Path.home() / ".claude.json"

DIR_MODE = 0o700
FILE_MODE = 0o600

TIER_LABELS = {
    "default_claude_max_20x": "Max 20x",
    "default_claude_max_5x": "Max 5x",
}


USAGE = """usage: cauth [command]
  (no command)          launch the terminal UI
  status [--json]       show installed identity and last check (not a new check)
  list                  list saved aliases
  switch <alias>        install locally; UNVERIFIED until checked
  verify [--json]       check installed login; exit 0 verified, 1 sign-in required, 3 unavailable
  login <alias>         renew a saved login; return to the previously installed account
  add <alias>           save the current login under an alias
  rename <old> <new>     change a saved label
  remove <alias>        forget profile and backups; keep live Claude login
  doctor                check installation without reading credentials
  recover               restore a pending operation/login snapshot
  rebuild               rebuild index from valid saved pairs; retain old index
  clear-login --yes      discard unfinished-login marker; keep current credentials
  reset --yes            delete all saved and live file logins, backups and pending state
  --version             print version
  --help                print this message
Quote aliases containing spaces. Example (POSIX shell): cauth switch 'client acme'
"""


class CauthError(Exception):
    """Something went wrong that must stop the operation rather than guess."""


class ExternalChangeError(CauthError):
    """A concurrent Claude write was detected before installing any target files."""


def live_fingerprints() -> dict:
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.exists()
        else None
        for path in (LIVE_CREDENTIALS, LIVE_CLAUDE_JSON)
    }


@dataclass(frozen=True)
class ClaudeProbeResult:
    """The bounded result of asking Claude to prove the selected OAuth login works."""

    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def kind(self) -> str:
        if self.ok:
            return "verified"
        text = self.output.lower()
        if self.returncode == 130:
            return "cancelled"
        if self.returncode in (124, 126, 127):
            return "unavailable"
        if any(
            word in text for word in ("rate limit", "rate_limit", "429", "usage limit")
        ):
            return "rate_limited"
        if any(
            word in text
            for word in (
                "oauth session expired",
                "oauth token has expired",
                "not logged in",
                "invalid authentication",
                "invalid oauth",
                "authentication_error",
                "login expired",
                "unauthorized",
            )
        ):
            return "sign_in_required"
        return "unavailable"

    @property
    def summary(self) -> str:
        return {
            "verified": "PASS: Claude accepted this login.",
            "sign_in_required": "FAIL: Sign-in required.",
            "rate_limited": "Could not check: usage limit reached. Try again later.",
            "cancelled": "Check cancelled.",
            "unavailable": "Could not check: authentication is unknown. Retry or check Claude/network setup.",
        }[self.kind]


@dataclass(frozen=True)
class SwitchProbeOutcome:
    """A TUI switch plus its real Claude verification and any safety rollback."""

    target_alias: str
    previous_alias: str | None
    probe: ClaudeProbeResult
    restored_alias: str | None = None
    rollback_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.probe.ok


@dataclass(frozen=True)
class ValidationReport:
    """What the active login is, plus Claude's verdict on whether it still works."""

    alias: str | None
    email: str | None
    tier: str
    subscription: str | None
    deadlines: list[str]
    warnings: list[str]
    probe: ClaudeProbeResult

    @property
    def ok(self) -> bool:
        return self.probe.ok


# State shared by all UI and CLI paths. Paths are derived at call time for test isolation.
_local_operation = threading.local()
_operation_mutex = threading.RLock()


@contextmanager
def operation_lock():
    """Serialize Cauth writers across threads and processes, without blocking the UI."""
    if not _operation_mutex.acquire(blocking=False):
        raise CauthError(
            "Another account operation is running. Wait for it to finish, then retry."
        )
    handle = None
    nested = getattr(_local_operation, "locked", False)
    try:
        if not nested:
            ensure_store()
            handle = open(CONFIG_DIR / ".operation.lock", "a+b")
            os.chmod(handle.name, FILE_MODE)
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    if not handle.read(1):
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CauthError(
                    "Another Cauth process is working. Wait, then retry."
                ) from exc
            _local_operation.locked = True
        yield
    finally:
        if not nested:
            _local_operation.locked = False
            if handle is not None:
                handle.close()
        _operation_mutex.release()


def managed_files() -> list[Path]:
    profiles = [
        p
        for p in PROFILES_DIR.iterdir()
        if p.is_file()
        and any(
            p.name.endswith(suffix)
            for suffix in (
                ".credentials.json",
                ".oauth.json",
                ".credentials.json.bak",
                ".oauth.json.bak",
            )
        )
    ]
    return [ACCOUNTS_JSON, LIVE_CREDENTIALS, LIVE_CLAUDE_JSON, *profiles]


def snapshot_files(paths: list[Path]) -> dict:
    return {
        str(p): {
            "data": base64.b64encode(p.read_bytes()).decode("ascii"),
            "mode": file_mode_of(p, FILE_MODE),
        }
        if p.exists()
        else None
        for p in paths
    }


def validate_snapshot(snapshot: dict) -> None:
    if not isinstance(snapshot, dict):
        raise CauthError("Invalid recovery snapshot. Retained for manual repair.")
    for name, entry in snapshot.items():
        if not isinstance(name, str):
            raise CauthError("Invalid recovery path.")
        path = Path(name)
        if (
            path not in (ACCOUNTS_JSON, LIVE_CREDENTIALS, LIVE_CLAUDE_JSON)
            and path.parent != PROFILES_DIR
        ):
            raise CauthError(
                "Recovery snapshot contains an unexpected path. Refusing to restore it."
            )
        if entry is not None:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("data"), str)
                or not isinstance(entry.get("mode"), int)
                or not 0 <= entry["mode"] <= 0o7777
            ):
                raise CauthError("Invalid recovery file metadata. Snapshot retained.")
            try:
                base64.b64decode(entry["data"], validate=True)
            except ValueError as exc:
                raise CauthError(
                    "Invalid recovery file contents. Snapshot retained."
                ) from exc


def restore_files(snapshot: dict) -> None:
    validate_snapshot(snapshot)
    for name, entry in snapshot.items():
        path = Path(name)
        if (
            path not in (ACCOUNTS_JSON, LIVE_CREDENTIALS, LIVE_CLAUDE_JSON)
            and path.parent != PROFILES_DIR
        ):
            raise CauthError(
                "Recovery snapshot contains an unexpected path. Refusing to restore it."
            )
        if entry is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=".cauth-restore-", dir=path.parent)
        temp = Path(filename)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(base64.b64decode(entry["data"], validate=True))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, entry["mode"])
            os.replace(temp, path)
            fsync_dir(path.parent)
        finally:
            temp.unlink(missing_ok=True)


def transactional(function):
    """A recoverable transaction around a user operation, including nested helpers."""

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with operation_lock():
            if getattr(_local_operation, "transaction", False):
                return function(*args, **kwargs)
            journal = CONFIG_DIR / ".transaction.json"
            if journal.exists():
                raise CauthError(
                    "An interrupted operation needs recovery. Open Recovery or run 'cauth recover'."
                )
            pending = pending_login()
            if pending and pending.get("owner") != os.getpid():
                raise CauthError(
                    "A browser login is pending in another session. Finish it or open Recovery."
                )
            snapshot = snapshot_files(managed_files())
            atomic_write_json(
                journal, {"files": snapshot, "operation": function.__name__}
            )
            _local_operation.transaction = True
            try:
                result = function(*args, **kwargs)
            except BaseException as failure:
                try:
                    rollback = snapshot
                    if isinstance(failure, ExternalChangeError):
                        rollback = {
                            name: entry
                            for name, entry in snapshot.items()
                            if Path(name) not in (LIVE_CREDENTIALS, LIVE_CLAUDE_JSON)
                        }
                    for path in managed_files():
                        if str(path) not in snapshot:
                            path.unlink(missing_ok=True)
                    restore_files(rollback)
                    journal.unlink()
                    fsync_dir(CONFIG_DIR)
                except Exception as recovery_error:
                    raise CauthError(
                        "Operation failed and automatic restore failed. Open Recovery; the recovery snapshot is retained."
                    ) from recovery_error
                raise
            else:
                journal.unlink()
                fsync_dir(CONFIG_DIR)
                return result
            finally:
                _local_operation.transaction = False

    return wrapped


def recover_transaction() -> None:
    with operation_lock():
        journal = CONFIG_DIR / ".transaction.json"
        if not journal.exists():
            return
        payload = read_json(journal)
        if not isinstance(payload, dict) or "files" not in payload:
            raise CauthError("Invalid operation journal. Retained for manual repair.")
        snapshot = payload["files"]
        # Recover original files; retain unindexed new files for index rebuild/manual review.
        restore_files(snapshot)
        journal.unlink()
        fsync_dir(CONFIG_DIR)


def account_status() -> dict:
    data = load_accounts()
    state = read_live_state()
    live_block = live_identity()
    identity = (*identity_of(live_block), (live_block or {}).get("organizationUuid"))
    complete = live_credentials_have_complete_oauth_tokens()
    matches = []
    if complete:
        for alias in data["accounts"]:
            try:
                if live_matches_profile(alias):
                    matches.append(alias)
            except CauthError:
                continue
    indexed = data.get("active")
    installed = indexed if indexed in matches else (matches[0] if matches else None)
    info = data["accounts"].get(installed, {})
    check = info.get("last_check")
    return {
        "installed": installed,
        "indexed": indexed,
        "email": state.email,
        "tier": state.tier,
        "identity": identity,
        "mismatch": bool(indexed and installed != indexed),
        "unsaved": bool(complete and not matches),
        "signed_in_locally": complete,
        "matches": matches,
        "last_check": check if isinstance(check, dict) else None,
        "organization": (live_identity() or {}).get("organizationName"),
    }


def remember_check(alias: str | None, probe: ClaudeProbeResult) -> None:
    if not alias:
        return
    data = load_accounts()
    if alias not in data["accounts"]:
        return
    data["accounts"][alias]["last_check"] = {
        "kind": probe.kind,
        "at": int(time.time()),
        "output": _safe_probe_output(probe.output, None),
        "returncode": probe.returncode,
    }
    save_accounts(data)


def check_description(check: dict | None) -> str:
    if not check:
        return "Not checked"
    labels = {
        "verified": "Verified",
        "unavailable": "Could not check",
        "cancelled": "Cancelled",
        "sign_in_required": "Sign-in required",
        "rate_limited": "Usage limited",
    }
    stamp = check.get("at")
    try:
        when = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))
            if isinstance(stamp, (int, float))
            else "unknown time"
        )
    except (OverflowError, ValueError, OSError):
        when = "unknown time"
    return f"{labels.get(check.get('kind'), 'Not checked')} · {when}"


def rebuild_index() -> list[str]:
    """Recover valid profile pairs without deleting broken profiles or the old index."""
    with operation_lock():
        recovered = {}
        skipped = []
        for path in sorted(PROFILES_DIR.glob("*.credentials.json")):
            alias = path.name[: -len(".credentials.json")]
            try:
                validate_alias(alias)
                identity = read_json(profile_paths(alias)[1])
                if not has_complete_oauth_tokens(read_json(path)) or not any(
                    identity_of(identity)
                ):
                    raise CauthError("Incomplete profile")
                recovered[alias] = {
                    "email": identity.get("emailAddress"),
                    "tier": identity.get("userRateLimitTier"),
                    "organization": identity.get("organizationName"),
                }
            except (CauthError, OSError, AttributeError):
                skipped.append(alias)
        if ACCOUNTS_JSON.exists():
            # Preserve damaged metadata for diagnosis, with secure permissions.
            backup = CONFIG_DIR / f"accounts.pre-rebuild-{time.time_ns()}.bak"
            backup.write_bytes(ACCOUNTS_JSON.read_bytes())
            os.chmod(backup, FILE_MODE)
        save_accounts({"active": None, "accounts": recovered})
        data = load_accounts()
        data["active"] = account_status()["installed"]
        save_accounts(data)
        return skipped


def begin_login(alias: str | None = None) -> dict:
    with operation_lock():
        if (CONFIG_DIR / ".pending-login.json").exists():
            raise CauthError(
                "A login is already pending. Finish it or restore it in Recovery."
            )
        if account_status()["unsaved"]:
            raise CauthError(
                "The current login is not saved. Save it before starting another browser login."
            )
        return_to = prepare_for_external_login()
        pending = {
            "return_to": return_to,
            "alias": alias,
            "owner": os.getpid(),
            "phase": "browser",
            "files": snapshot_files(
                [LIVE_CREDENTIALS, LIVE_CLAUDE_JSON, ACCOUNTS_JSON]
            ),
        }
        atomic_write_json(CONFIG_DIR / ".pending-login.json", pending)
        return pending


def pending_login() -> dict | None:
    path = CONFIG_DIR / ".pending-login.json"
    if not path.exists():
        return None
    pending = read_json(path)
    if (
        not isinstance(pending, dict)
        or not isinstance(pending.get("owner"), int)
        or pending.get("phase") not in ("browser", "naming")
    ):
        raise CauthError("Invalid pending-login record. Retained for manual repair.")
    validate_snapshot(pending.get("files"))
    return pending


def restore_login() -> None:
    with operation_lock():
        pending = pending_login()
        if pending:
            restore_files(pending["files"])
            (CONFIG_DIR / ".pending-login.json").unlink()
            fsync_dir(CONFIG_DIR)


def complete_login() -> None:
    (CONFIG_DIR / ".pending-login.json").unlink(missing_ok=True)
    fsync_dir(CONFIG_DIR)


@diagnostics.traced("login.clear_pending")
def clear_unfinished_login() -> None:
    """Discard even a damaged pending marker without restoring stale credentials."""
    with operation_lock():
        complete_login()


@diagnostics.traced("login.reset")
def reset_logins() -> None:
    """Explicit, retryable purge of file-backed logins, including damaged recovery state.

    Deliberately bypass transactional recovery: retaining its snapshot would retain the
    credentials being deleted. Only recognized Cauth files are removed. Claude settings
    survive, with only oauthAccount removed; unreadable settings stop the reset upfront.
    """
    with operation_lock():
        settings = None
        if LIVE_CLAUDE_JSON.exists():
            settings = read_json(LIVE_CLAUDE_JSON)
            if not isinstance(settings, dict):
                raise CauthError("Claude settings must be a JSON object; no logins were reset.")
            settings.pop("oauthAccount", None)
        targets = [
            CONFIG_DIR / "tokens.json",
            CONFIG_DIR / ".pending-login.json",
            CONFIG_DIR / ".transaction.json",
            *sorted(CONFIG_DIR.glob("accounts.pre-rebuild-*.bak")),
            *[p for p in managed_files() if p not in (ACCOUNTS_JSON, LIVE_CLAUDE_JSON)],
        ]
        # Preflight before removing anything; never recurse into unexpected directories.
        for path in targets:
            if path.exists() and not path.is_file() and not path.is_symlink():
                raise CauthError(f"Expected a file at {path}; no logins were reset.")
        if settings is not None:
            atomic_write_json(
                LIVE_CLAUDE_JSON, settings, mode=file_mode_of(LIVE_CLAUDE_JSON, FILE_MODE)
            )
        for path in targets:
            path.unlink(missing_ok=True)
        save_accounts({"active": None, "accounts": {}})
        for directory in (PROFILES_DIR, CONFIG_DIR, LIVE_CREDENTIALS.parent):
            if directory.exists():
                fsync_dir(directory)


def command_for(command: str, alias: str) -> str:
    # This display is explicitly labeled POSIX shell in help; UI actions need no shell.
    return f"cauth {command} {shlex.quote(alias)}"


# --------------------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------------------


def stage_json(path: Path, data: Any, mode: int = FILE_MODE) -> Path:
    """Write JSON to a temp file beside `path` and return it, without committing.

    Split out from atomic_write_json so a caller that must update two files together can
    do all the fallible work (serialise, write, fsync, chmod) up front, then commit both
    with nothing in between that can fail. See activate().
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        return tmp
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def fsync_dir(directory: Path) -> None:
    """Persist a rename, not just the bytes it moved.

    Without this a power loss can leave the directory entry pointing at nothing.

    Windows cannot do it at all: `os.open` on a directory there raises PermissionError,
    and it is the open that fails, not the fsync, so guarding only the fsync leaves the
    error to escape. It escapes AFTER os.replace has already landed, which turns a
    completed credential write into a reported failure, on the one tool whose promise is
    that it never loses an account. So skip the whole thing on win32 rather than widen
    the except, which would also swallow a real durability loss on POSIX.
    """
    if sys.platform == "win32":
        return
    dirfd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(dirfd)
    except OSError:
        pass
    finally:
        os.close(dirfd)


def commit_staged(tmp: Path, path: Path) -> None:
    """Rename a staged temp file into place, then persist the rename."""
    os.replace(tmp, path)
    fsync_dir(path.parent)


def atomic_write_json(path: Path, data: Any, mode: int = FILE_MODE) -> None:
    """Write JSON via a temp file plus os.replace.

    A crash mid-write must never leave a truncated credentials file, because that file
    authenticates every running Claude Code session.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp, not a fixed ".tmp" sibling. Two cauth processes writing the same target
    # would otherwise share one temp inode, interleave their writes, and the loser's file
    # descriptor would follow the rename straight into the live credentials file. mkstemp
    # also creates at 0600, closing the window where a fixed-name temp existed at the
    # umask default before chmod ran.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        fsync_dir(path.parent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    """Read JSON, turning every failure into a CauthError.

    Callers halt on CauthError and report it. A raw JSONDecodeError or OSError escaping
    from here would instead crash the TUI with a traceback, which is the worst outcome
    for a tool whose whole job is to not damage a credentials file. A hand-edited or
    truncated ~/.claude.json is the likeliest way a stranger's machine differs from ours.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise CauthError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}). "
            "Refusing to touch it. Fix or restore that file first."
        ) from exc
    except OSError as exc:
        raise CauthError(f"Cannot read {path}: {exc.strerror or exc}") from exc


def file_mode_of(path: Path, default: int) -> int:
    """The mode a file already has, so rewriting it never loosens its permissions."""
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return default


def ensure_store() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, DIR_MODE)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(PROFILES_DIR, DIR_MODE)
    if not ACCOUNTS_JSON.exists():
        atomic_write_json(ACCOUNTS_JSON, {"active": None, "accounts": {}})


def load_accounts() -> dict:
    ensure_store()
    data = read_json(ACCOUNTS_JSON)
    if not isinstance(data, dict):
        raise CauthError(
            "The account index must be an object. Use Recovery to rebuild it from saved profiles."
        )
    data.setdefault("active", None)
    data.setdefault("accounts", {})
    if not isinstance(data["accounts"], dict):
        raise CauthError(
            "The account index has an invalid accounts section. Use Recovery to rebuild it."
        )
    if data["active"] is not None and not isinstance(data["active"], str):
        raise CauthError(
            "The account index has an invalid active alias. Use Recovery to rebuild it."
        )
    for alias, info in data["accounts"].items():
        if (
            not isinstance(alias, str)
            or any(c in alias for c in ("/", "\\", "\0"))
            or alias in (".", "..")
        ):
            raise CauthError(
                "The account index contains an unsafe alias. Use Recovery."
            )
        if not isinstance(info, dict) or any(
            info.get(k) is not None and not isinstance(info[k], str)
            for k in ("email", "tier", "organization")
        ):
            raise CauthError(
                f"Invalid metadata for {alias!r}. Use Recovery to rebuild the index."
            )
    return data


def save_accounts(data: dict) -> None:
    atomic_write_json(ACCOUNTS_JSON, data)


@transactional
def mark_live_unregistered() -> None:
    """Clear the alias pointer after an external login replaces the live account.

    The caller keeps any previous alias separately while the new login is being named.
    Leaving that alias in ``accounts.json`` would claim the newly authenticated account
    was still the old one if alias capture were cancelled.
    """
    data = load_accounts()
    if data.get("active") is None:
        return
    data["active"] = None
    save_accounts(data)


def validate_alias(alias: str) -> str:
    """Aliases become filenames, so a stray slash or dot-dot would escape the store."""
    cleaned = alias.strip()
    if not cleaned:
        raise CauthError("An alias cannot be empty.")
    if len(cleaned) > 64:
        raise CauthError("An alias cannot be longer than 64 characters.")
    if cleaned in (".", ".."):
        raise CauthError(f"'{cleaned}' is not a usable alias.")
    for bad in ("/", "\\", "\0"):
        if bad in cleaned:
            raise CauthError(
                f"An alias cannot contain '{bad}'. It is used as a filename."
            )
    if any(ord(c) < 32 or c in '<>:"|?*' for c in cleaned) or cleaned.endswith(
        (".", " ")
    ):
        raise CauthError(
            'Use a portable filename: no control characters, < > : " | ? *, or trailing dots.'
        )
    if cleaned.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise CauthError("That name is reserved on Windows. Choose another alias.")
    return cleaned


def profile_paths(alias: str) -> tuple[Path, Path]:
    return (
        PROFILES_DIR / f"{alias}.credentials.json",
        PROFILES_DIR / f"{alias}.oauth.json",
    )


# --------------------------------------------------------------------------------------
# live state
# --------------------------------------------------------------------------------------


def has_complete_oauth_tokens(payload: Any) -> bool:
    """Whether a credentials payload contains the token pair Claude Code needs.

    Claude Code leaves a truthy ``claudeAiOauth`` object behind when it logs out or an
    external login fails, but its access and refresh tokens are empty strings. Checking
    only for the object would treat that logout skeleton as a login and let it overwrite
    a stored account. Expiry is deliberately not checked here: an expired access token is
    still recoverable when its refresh token is present.
    """
    if not isinstance(payload, dict):
        return False
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return False
    return all(
        isinstance(oauth.get(name), str) and bool(oauth[name].strip())
        for name in ("accessToken", "refreshToken")
    )


def live_credentials_have_complete_oauth_tokens() -> bool:
    """Whether the live credential file exists and contains a complete token pair."""
    return LIVE_CREDENTIALS.exists() and has_complete_oauth_tokens(
        read_json(LIVE_CREDENTIALS)
    )


def auth_state_warnings(
    active_alias: str | None = None, now_ms: int | None = None
) -> list[str]:
    """User-facing warnings when the local token state needs attention.

    A complete token pair is not proof that Anthropic will accept it. Access tokens
    expire routinely and may refresh during a real Claude request; refresh sessions have
    a separate renewal deadline and can also be revoked server-side before that date. The
    latter cannot be proven from local metadata, so this function directs the user to the
    TUI's bounded Claude probe.
    """
    renew = (
        command_for("login", active_alias)
        if active_alias
        else "cauth (press a to sign in)"
    )
    if not LIVE_CREDENTIALS.exists():
        # Claude Code may intentionally use the macOS Keychain instead of this file. On
        # other platforms a retained identity beside a missing credential file is a stale
        # display state, not a usable login.
        if sys.platform != "darwin" and live_identity() is not None:
            return [
                f"Claude Code's live credential file is missing at {LIVE_CREDENTIALS}. "
                "Claude Code is logged out, even if the identity below still names an "
                "account."
            ]
        return []
    payload = read_json(LIVE_CREDENTIALS)
    if not has_complete_oauth_tokens(payload):
        return [
            "Claude Code's live credentials contain no usable OAuth token pair. Claude "
            f"Code is logged out, even if the identity below still names an account. Run "
            f"'{renew}' to renew it."
        ]

    oauth = payload["claudeAiOauth"]
    now = int(time.time() * 1000) if now_ms is None else now_ms
    access_deadline = oauth.get("expiresAt")
    refresh_deadline = oauth.get("refreshTokenExpiresAt")
    day_ms = 24 * 60 * 60 * 1000

    if isinstance(refresh_deadline, (int, float)) and refresh_deadline <= now:
        return [
            "This OAuth login renewal has expired. The stored token pair can still look "
            f"complete locally, but Claude requires a browser login. Run '{renew}'."
        ]

    if isinstance(access_deadline, (int, float)) and access_deadline <= now:
        renewal_note = ""
        if isinstance(refresh_deadline, (int, float)):
            remaining_days = max(1, math.ceil((refresh_deadline - now) / day_ms))
            if refresh_deadline - now <= 3 * day_ms:
                renewal_note = (
                    f" Its login renewal also expires in {remaining_days} days."
                )
        return [
            "The OAuth access token has expired. Press v to check the installed login "
            "with a Claude request and let it refresh. If "
            f"the check reports Sign-in required, run '{renew}'.{renewal_note}"
        ]

    if (
        isinstance(refresh_deadline, (int, float))
        and refresh_deadline - now <= 3 * day_ms
    ):
        remaining_days = max(1, math.ceil((refresh_deadline - now) / day_ms))
        return [
            f"This OAuth login renewal expires in {remaining_days} days. Run '{renew}' "
            "before it becomes a hard browser-login failure."
        ]

    return []


@dataclass
class LiveState:
    email: str | None
    tier_raw: str | None
    subscription: str | None

    @property
    def tier(self) -> str:
        raw = self.tier_raw or ""
        if raw in TIER_LABELS:
            return TIER_LABELS[raw]
        low = raw.lower()
        if "pro" in low:
            return "Pro"
        if "free" in low:
            return "Free"
        return raw or "unknown"


def read_live_state() -> LiveState:
    email = tier = sub = None
    if LIVE_CLAUDE_JSON.exists():
        full = read_json(LIVE_CLAUDE_JSON)
        if not isinstance(full, dict):
            raise CauthError(
                "The live identity file must contain a JSON object. Open Recovery."
            )
        acct = full.get("oauthAccount") or {}
        if not isinstance(acct, dict):
            raise CauthError(
                "The live account identity must be an object. Open Recovery."
            )
        if any(
            acct.get(key) is not None and not isinstance(acct[key], str)
            for key in ("accountUuid", "organizationUuid", "organizationName")
        ):
            raise CauthError(
                "Live account identity has invalid field types. Open Recovery."
            )
        email = acct.get("emailAddress")
        tier = acct.get("userRateLimitTier")
    if LIVE_CREDENTIALS.exists():
        payload = read_json(LIVE_CREDENTIALS)
        if not isinstance(payload, dict):
            raise CauthError(
                "The live credential file must contain a JSON object. Open Recovery."
            )
        oauth = payload.get("claudeAiOauth") or {}
        if not isinstance(oauth, dict):
            raise CauthError("The live OAuth section must be an object. Open Recovery.")
        sub = oauth.get("subscriptionType")
        tier = tier or oauth.get("rateLimitTier")
    if any(
        value is not None and not isinstance(value, str) for value in (email, tier, sub)
    ):
        raise CauthError(
            "Live identity metadata has invalid field types. Open Recovery."
        )
    return LiveState(email=email, tier_raw=tier, subscription=sub)


def running_inside_claude_code() -> bool:
    """Claude Code sets CLAUDECODE=1 in every session it spawns.

    Matters because a switch rewrites the credentials file that the surrounding session is
    authenticated with, so the session gets an identity change underneath it.
    """
    return os.environ.get("CLAUDECODE") == "1"


def browser_available() -> bool:
    """Whether `claude auth login` can plausibly open a browser from this terminal.

    macOS and Windows have no DISPLAY concept and always have a way to open a browser,
    so checking the X11 and Wayland variables there would wrongly tell every Mac user
    their browser will not open.
    """
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def runtime_notices() -> list[str]:
    """Advisory notes about the terminal cauth is running in. Not errors."""
    notes = []
    if sys.platform == "darwin" and not LIVE_CREDENTIALS.exists():
        # Claude Code on macOS can keep credentials in the login Keychain rather than in
        # a file. If it does, every swap here writes something nothing reads, and the
        # failure is silent, which is the worst kind. Say so rather than pretend.
        notes.append(
            "On macOS, Claude Code may keep credentials in the login Keychain instead "
            f"of {LIVE_CREDENTIALS}, which does not exist here. If so, switching will "
            "have no effect. Please open an issue reporting what you see."
        )
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        notes.append(
            f"CLAUDE_CONFIG_DIR is set, so credentials are read from {LIVE_CREDENTIALS} "
            "rather than the default location."
        )
    if running_inside_claude_code():
        notes.append(
            "This is running inside a Claude Code session. Switching rewrites the "
            "credentials that session is signed in with, so it changes identity "
            "underneath itself. Prefer a plain terminal."
        )
    if not browser_available():
        notes.append(
            "No DISPLAY is set, so 'claude auth login' cannot open a browser here. It "
            "prints a URL instead. Open that on any machine and paste the code back."
        )
    return notes


def env_conflicts() -> list[str]:
    """Env vars that break or bypass the file swap. Shown loudly on the home screen."""
    out = []
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        out.append(
            "CLAUDE_CODE_OAUTH_TOKEN is set. Claude Code reads it instead of the "
            "credentials file, so switching accounts here will have NO effect."
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append(
            "ANTHROPIC_API_KEY is set. That bills per token instead of using your "
            "subscription."
        )
    return out


def require_file_auth() -> None:
    """Refuse actions that cannot affect Claude while env auth overrides the files."""
    blockers = []
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        blockers.append("CLAUDE_CODE_OAUTH_TOKEN")
    if os.environ.get("ANTHROPIC_API_KEY"):
        blockers.append("ANTHROPIC_API_KEY")
    if blockers:
        names = " and ".join(blockers)
        raise CauthError(
            f"Cannot switch accounts while {names} is set. Exit Cauth, remove the override "
            "from the current shell, then relaunch. See Help for shell-specific instructions."
        )


# --------------------------------------------------------------------------------------
# live Claude verification
# --------------------------------------------------------------------------------------


_SECRET_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_PROBE_PROMPT = "Reply with exactly CAUTH_OK and nothing else. Do not use tools."


def _safe_probe_output(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    """Combine bounded child output without ever surfacing an OAuth token."""

    def as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    parts = [
        part.strip() for part in (as_text(stdout), as_text(stderr)) if part.strip()
    ]
    output = _SECRET_PATTERN.sub("[REDACTED_TOKEN]", "\n".join(parts))
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    if len(output) > 2_000:
        output = output[:995] + "\n… truncated …\n" + output[-990:]
    return output


def claude_executable() -> str:
    """The official Claude executable. Skips Cauth's own PATH launcher so a launch
    through that launcher can never call itself again."""
    launcher_dir = os.path.normcase(os.path.realpath(CONFIG_DIR / "bin"))
    search = os.pathsep.join(
        entry for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and os.path.normcase(os.path.realpath(entry)) != launcher_dir
    )
    return shutil.which("claude", path=search) or "claude"


def run_claude_probe(timeout_seconds: int = 90, *, oauth_token: str | None = None) -> ClaudeProbeResult:
    """Make Claude prove the installed OAuth profile works with a real print request.

    This is intentionally different from ``claude auth status``, which can report
    ``loggedIn: true`` from local JSON while the server-side session is expired or revoked.
    The request uses the cheapest model, disables tools and persistence, captures all
    output, and never lets child output take ownership of Textual's terminal.
    """
    argv = [
        claude_executable(),
        "-p",
        _PROBE_PROMPT,
        "--model",
        "haiku",
        "--output-format",
        "text",
        "--tools",
        "",
        "--permission-mode",
        "plan",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
    ]
    env = os.environ.copy()
    # The TUI refuses these overrides before switching. Remove them here too so this
    # proof can only exercise the file Cauth just installed.
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    if oauth_token is not None:
        from tokenauth import environment
        env = environment(oauth_token)
    try:
        cancel = getattr(_local_operation, "cancel_event", None)
        if cancel is None:
            completed = subprocess.run(
                argv,
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout_seconds,
            )
        else:
            process = subprocess.Popen(
                argv,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
            deadline = time.monotonic() + timeout_seconds
            while True:
                if cancel.is_set() or time.monotonic() >= deadline:
                    process.terminate()
                    try:
                        stdout, stderr = process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        stdout, stderr = process.communicate()
                    code = 130 if cancel.is_set() else 124
                    return ClaudeProbeResult(
                        code,
                        (
                            "Check cancelled."
                            if code == 130
                            else f"Claude probe timed out after {timeout_seconds} seconds."
                        )
                        + "\n"
                        + _safe_probe_output(stdout, stderr),
                    )
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    completed = subprocess.CompletedProcess(
                        argv, process.returncode, stdout, stderr
                    )
                    break
                except subprocess.TimeoutExpired:
                    continue
    except FileNotFoundError:
        return ClaudeProbeResult(127, "'claude' was not found on PATH.")
    except subprocess.TimeoutExpired as exc:
        partial = _safe_probe_output(exc.stdout, exc.stderr)
        message = f"Claude probe timed out after {timeout_seconds} seconds."
        if partial:
            message += f"\n{partial}"
        return ClaudeProbeResult(124, message)
    except KeyboardInterrupt:
        return ClaudeProbeResult(130, "Claude probe cancelled.")
    except OSError as exc:
        return ClaudeProbeResult(
            126, f"Could not start Claude probe: {exc.strerror or exc}"
        )

    output = _safe_probe_output(completed.stdout, completed.stderr)
    if not output:
        output = "Claude returned no output."
    return ClaudeProbeResult(completed.returncode, output)


def _deadline_line(label: str, value: Any, now_ms: int) -> str:
    """One human-readable expiry line for an epoch-milliseconds deadline."""
    if not isinstance(value, (int, float)):
        return f"{label}: no deadline recorded"
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(value / 1000))
    except (OverflowError, ValueError, OSError):
        return f"{label}: invalid deadline recorded"
    remaining = value - now_ms
    if remaining <= 0:
        return f"{label}: EXPIRED {stamp}"
    minutes = remaining / 60_000
    if minutes < 60:
        span = f"{max(1, int(minutes))} min"
    elif minutes < 60 * 24:
        span = f"{int(minutes // 60)} hours"
    else:
        span = f"{int(minutes // (60 * 24))} days"
    return f"{label}: valid for {span} (until {stamp})"


def token_deadlines(now_ms: int | None = None) -> list[str]:
    """Local expiry facts for the live OAuth token pair. Read-only, no network."""
    if not LIVE_CREDENTIALS.exists():
        return []
    payload = read_json(LIVE_CREDENTIALS)
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    if not isinstance(oauth, dict):
        return []
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return [
        _deadline_line("access token", oauth.get("expiresAt"), now),
        _deadline_line("login renewal", oauth.get("refreshTokenExpiresAt"), now),
    ]


def validate_active(timeout_seconds: int = 90) -> ValidationReport:
    """Check the login Claude Code is using right now, without switching anything.

    Local metadata alone cannot prove a session is still accepted, so this pairs the
    stored identity, tier and expiry deadlines with a real bounded 'claude -p' request.
    Cauth records the check in the index without changing live credentials. Claude may
    refresh its access token during the request; metadata is read again afterward.
    """
    with operation_lock():
        before = account_status()
        probe = run_claude_probe(timeout_seconds=timeout_seconds)
        status = account_status()
        if before["identity"] != status["identity"]:
            probe = ClaudeProbeResult(
                125,
                "The live identity changed during verification. Reconcile and retry.",
            )
        alias = status["installed"]
        state = read_live_state()
        remember_check(alias, probe)
        notices = env_conflicts() + auth_state_warnings(alias)
        if status["mismatch"]:
            notices.append(
                "Saved selection differs from the installed identity. The result describes the installed identity."
            )
        return ValidationReport(
            alias=alias,
            email=state.email,
            tier=state.tier,
            subscription=state.subscription,
            deadlines=token_deadlines(),
            warnings=notices,
            probe=probe,
        )


@transactional
def switch_with_probe(
    target: str, *, discard_unsaved: bool = False
) -> SwitchProbeOutcome:
    """Switch, prove the target with Claude, and roll back a failed selection.

    Verification and installed identity are separate facts. Failed checks preserve the
    parked target and restore the previous alias without syncing the rejected live pair.
    """
    data = load_accounts()
    previous = account_status()["installed"] or data.get("active")
    switch_to(target, discard_unsaved=discard_unsaved)
    probe = run_claude_probe()
    live = live_identity()
    if any(identity_of(live)) and not identities_match(
        read_json(profile_paths(target)[1]), live
    ):
        raise ExternalChangeError(
            "The live identity changed during the check. That external login was preserved. Reload before retrying."
        )
    remember_check(target, probe)
    if probe.ok:
        return SwitchProbeOutcome(target, previous, probe)
    if previous and previous != target and previous in data["accounts"]:
        try:
            # Do not sync a rejected target over its preserved profile.
            activate(previous)
            current = load_accounts()
            current["active"] = previous
            save_accounts(current)
            return SwitchProbeOutcome(target, previous, probe, restored_alias=previous)
        except (CauthError, OSError) as exc:
            raise CauthError(
                "Could not restore the previous profile. The full operation will be restored from its snapshot; if that fails, open Recovery."
            ) from exc
    # Installed identity and authentication outcome are independent facts.
    return SwitchProbeOutcome(target, previous, probe)


# --------------------------------------------------------------------------------------
# the switch
# --------------------------------------------------------------------------------------


def identity_of(block: Any) -> tuple[str | None, str | None]:
    """The two fields that tell two Claude accounts apart, uuid first."""
    if not isinstance(block, dict):
        return (None, None)
    values = (block.get("accountUuid"), block.get("emailAddress"))
    return tuple(
        value if isinstance(value, str) and value.strip() else None for value in values
    )


def identities_match(stored: Any, live: Any) -> bool:
    stored_uuid, stored_email = identity_of(stored)
    live_uuid, live_email = identity_of(live)
    same = (
        stored_uuid == live_uuid
        if stored_uuid and live_uuid
        else bool(stored_email and stored_email == live_email)
    )
    if not same:
        return False
    old_org = stored.get("organizationUuid") if isinstance(stored, dict) else None
    live_org = live.get("organizationUuid") if isinstance(live, dict) else None
    return not (old_org and live_org and old_org != live_org)


def live_identity() -> Any:
    if not LIVE_CLAUDE_JSON.exists():
        return None
    full = read_json(LIVE_CLAUDE_JSON)
    if not isinstance(full, dict):
        raise CauthError(
            f"{LIVE_CLAUDE_JSON} should contain a JSON object. Refusing to sync the "
            "outgoing account against it."
        )
    return full.get("oauthAccount")


def aliases_for_identity(identity: Any) -> list[str]:
    """Find legacy duplicate labels, without merging different or unknown org seats."""
    if not isinstance(identity, dict) or not any(identity_of(identity)):
        return []
    matches = []
    for alias in load_accounts()["accounts"]:
        path = profile_paths(alias)[1]
        if not path.exists():
            continue
        try:
            stored = read_json(path)
        except (CauthError, OSError):
            # An unreadable, unrelated profile must not block saving a good login.
            continue
        if identities_match(stored, identity) and (
            stored.get("organizationUuid") == identity.get("organizationUuid")
        ):
            matches.append(alias)
    return matches


def saved_account_notes(alias: str) -> list[str]:
    """Show saved-file health even when the live login is missing or logged out."""
    credentials, identity = profile_paths(alias)
    notes = []
    try:
        payload = read_json(credentials)
        if not has_complete_oauth_tokens(payload):
            notes.append("Renew required: saved login has no usable tokens (l)")
        else:
            deadline = payload["claudeAiOauth"].get("refreshTokenExpiresAt")
            if isinstance(deadline, (int, float)) and deadline <= time.time() * 1000:
                notes.append("Renew required: saved renewal expired (l)")
        duplicates = [a for a in aliases_for_identity(read_json(identity)) if a != alias]
        if duplicates:
            notes.append("Same account as: " + ", ".join(duplicates))
    except (CauthError, OSError):
        notes.append("Saved profile needs recovery (d)")
    return notes


def live_matches_profile(alias: str) -> bool:
    """Whether the live login is healthy and identifies as the stored alias."""
    if not live_credentials_have_complete_oauth_tokens():
        return False
    _, oauth_path = profile_paths(alias)
    if not oauth_path.exists():
        return False

    return identities_match(read_json(oauth_path), live_identity())


@transactional
def sync_back(alias: str) -> None:
    """Copy the LIVE credentials into `alias`'s profile, if they really are that account.

    Runs before every swap. Skipping it discards any token refresh that happened during
    the session, which is how an account gets silently lost.

    The identity guard is the other half. This function is the chokepoint every mutating
    path crosses, and it used to write whatever was live into whatever profile it was
    handed. If `active` ever disagreed with the credentials actually on disk, and there
    are several ways that happened, it would overwrite one account's stored tokens with
    another account's and destroy it with no warning. So: refuse when the live identity
    is not the identity already stored under this alias.

    A missing or unparseable live credentials file is NOT fatal here. The caller still
    needs to be allowed to install the target, which repairs the live file. Refusing
    would strand someone whose credentials are already broken with no route back.
    """
    fingerprints = live_fingerprints()
    cred_dst, oauth_dst = profile_paths(alias)

    live_acct = live_identity()

    # Compare identities only when this alias already holds a profile. A first
    # registration has nothing to compare against and must be allowed through.
    if oauth_dst.exists():
        stored_uuid, stored_email = identity_of(read_json(oauth_dst))
        live_uuid, live_email = identity_of(live_acct)
        if stored_uuid is None and stored_email is None:
            raise CauthError(
                f"The stored identity for '{alias}' has neither accountUuid nor "
                "emailAddress. Refusing to sync against a profile we cannot identify."
            )
        if live_acct is not None:
            same = identities_match(read_json(oauth_dst), live_acct)
            if not same:
                raise CauthError(
                    f"The live account ({live_email or 'unknown'}) is not the account "
                    f"stored under '{alias}' ({stored_email or 'unknown'}). Refusing to "
                    f"overwrite that profile. Run 'cauth add <new-alias>' to file the "
                    "live account under its own name first."
                )

    destinations = list(dict.fromkeys([alias, *aliases_for_identity(live_acct)]))
    if LIVE_CREDENTIALS.exists():
        payload = read_json(LIVE_CREDENTIALS)
        # A logged-out skeleton is not worth storing. Skip it quietly so switching to a
        # good profile can repair the live file without sacrificing the parked account.
        if has_complete_oauth_tokens(payload):
            # Old releases allowed multiple aliases for one identity. Rotating only
            # one copy lets the next alias switch reinstall a revoked refresh token.
            for destination in destinations:
                cred_path = profile_paths(destination)[0]
                backup_profile(cred_path)
                atomic_write_json(cred_path, payload, mode=FILE_MODE)

    if live_acct is not None:
        for destination in destinations:
            oauth_path = profile_paths(destination)[1]
            backup_profile(oauth_path)
            atomic_write_json(oauth_path, live_acct)

    if live_fingerprints() != fingerprints:
        raise ExternalChangeError(
            "Claude refreshed the login while Cauth was saving it. Reload and retry; external changes were preserved."
        )


def backup_profile(path: Path) -> None:
    """Keep one previous generation of a profile file.

    Cheap, and it is the only recovery path if a guard above is ever wrong about which
    account is which.
    """
    if path.exists():
        try:
            os.replace(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass


@transactional
def activate(alias: str) -> None:
    """Install a validated pair; the surrounding journal recovers partial commits."""
    fingerprints = live_fingerprints()
    cred_src, oauth_src = profile_paths(alias)
    if not cred_src.exists():
        raise CauthError(f"No stored credentials for '{alias}' at {cred_src}")

    payload = read_json(cred_src)
    if not has_complete_oauth_tokens(payload):
        raise CauthError(
            f"Stored credentials for '{alias}' have no claudeAiOauth block with non-empty "
            "accessToken and refreshToken values. Refusing to install them, that would "
            "log you out of every session."
        )

    if not oauth_src.exists():
        # Halt rather than guess. Installing the target's tokens while leaving the previous
        # account's oauthAccount in ~/.claude.json produces a mismatched pair: the identity
        # block says one account, the tokens belong to another, and every status readout
        # lies about who is logged in.
        raise CauthError(
            f"Stored credentials for '{alias}' have no matching {oauth_src.name}. "
            "Refusing to switch, that would leave the identity block and the tokens "
            "pointing at different accounts."
        )

    acct = read_json(oauth_src)
    if (
        not isinstance(acct, dict)
        or not any(identity_of(acct))
        or any(
            acct.get(key) is not None and not isinstance(acct[key], str)
            for key in (
                "accountUuid",
                "emailAddress",
                "organizationUuid",
                "organizationName",
                "userRateLimitTier",
            )
        )
    ):
        raise CauthError(
            f"The saved identity for {alias!r} is incomplete or malformed. Renew it before switching."
        )
    # Load the whole file and change one key. ~/.claude.json holds unrelated Claude
    # Code state and dropping any of it is a real bug.
    full = read_json(LIVE_CLAUDE_JSON) if LIVE_CLAUDE_JSON.exists() else {}
    if not isinstance(full, dict):
        raise CauthError(
            f"{LIVE_CLAUDE_JSON} should contain a JSON object. Refusing to overwrite it."
        )
    full["oauthAccount"] = acct

    # Keep whatever permissions these files already had. Someone may have tightened
    # ~/.claude.json to 600, and silently widening it to 644 would be our bug.
    creds_mode = file_mode_of(LIVE_CREDENTIALS, FILE_MODE)
    state_mode = file_mode_of(LIVE_CLAUDE_JSON, 0o600)

    # These two files must agree about who is logged in: tokens from one account beside
    # another account's identity block makes every readout lie. Stage both first, so all
    # the work that can fail (serialise, write, fsync, chmod) happens before either is
    # visible, then commit them back to back.
    staged_creds = stage_json(LIVE_CREDENTIALS, payload, mode=creds_mode)
    try:
        staged_state = stage_json(LIVE_CLAUDE_JSON, full, mode=state_mode)
    except BaseException:
        staged_creds.unlink(missing_ok=True)
        raise

    try:
        if live_fingerprints() != fingerprints:
            raise ExternalChangeError(
                "Claude changed the live files during this operation. No target was installed. Close other sessions, reload, and retry."
            )
        commit_staged(staged_creds, LIVE_CREDENTIALS)
        commit_staged(staged_state, LIVE_CLAUDE_JSON)
    finally:
        staged_creds.unlink(missing_ok=True)
        staged_state.unlink(missing_ok=True)


@transactional
def switch_to(target: str, *, discard_unsaved: bool = False) -> None:
    data = load_accounts()
    if target not in data["accounts"]:
        raise CauthError(f"Unknown alias '{target}'")

    status = account_status()
    if status["unsaved"] and not discard_unsaved:
        raise CauthError(
            "The current login is not saved. Save it before switching, or explicitly discard it."
        )
    active = status["installed"] or (None if status["unsaved"] else data.get("active"))
    if active == target:
        # The index can outlive the actual login. Selecting its named alias must repair a
        # missing, tokenless, or identity-mismatched live file rather than short-circuiting
        # and stranding the user in a logged-out state. Do not sync the broken live state
        # back first; activate the known stored pair directly.
        if not live_matches_profile(target):
            activate(target)
        if data.get("active") != target:
            data["active"] = target
            save_accounts(data)
        return
    if active and active in data["accounts"] and status["installed"]:
        sync_back(active)

    activate(target)
    data["active"] = target
    save_accounts(data)


@transactional
def rename_account(old: str, new: str) -> None:
    """Change an account's alias, keeping its stored credentials in place.

    The alias is only a label, so this moves the two profile files and the index entry.
    Nothing touches the live credentials, which means renaming the active account is safe
    and does not sign anybody out.
    """
    new = validate_alias(new)
    data = load_accounts()
    if old not in data["accounts"]:
        raise CauthError(f"Unknown alias '{old}'")
    if new == old:
        return
    if new in data["accounts"]:
        raise CauthError(f"'{new}' is already taken. Pick another alias.")

    old_cred, old_oauth = profile_paths(old)
    new_cred, new_oauth = profile_paths(new)
    if new_cred.exists() or new_oauth.exists():
        raise CauthError(
            f"Files for '{new}' already exist in the store. Refusing to overwrite them."
        )

    # The operation journal restores the complete old pair if either move fails.
    if old_cred.exists():
        os.replace(old_cred, new_cred)
    if old_oauth.exists():
        os.replace(old_oauth, new_oauth)

    for old_path, new_path in ((old_cred, new_cred), (old_oauth, new_oauth)):
        backup = old_path.with_name(old_path.name + ".bak")
        if backup.exists():
            os.replace(backup, new_path.with_name(new_path.name + ".bak"))
    data["accounts"][new] = data["accounts"].pop(old)
    if data.get("active") == old:
        data["active"] = new
    save_accounts(data)


@transactional
def register_live_as(alias: str) -> None:
    """Snapshot whatever account is live right now and store it under `alias`.

    `active` becomes this alias, and that is not a preference, it is a correctness
    requirement. sync_back copies the LIVE credentials into whatever `active` names, so an
    `active` that disagrees with the credentials actually on disk would write one account's
    tokens into another account's profile on the next switch. If you want to end up
    somewhere else after registering, register first and then switch, which is what
    add_then_return does.
    """
    alias = validate_alias(alias)
    if not LIVE_CREDENTIALS.exists():
        raise CauthError(
            f"{LIVE_CREDENTIALS} does not exist. Log in first with 'claude auth login'."
        )
    payload = read_json(LIVE_CREDENTIALS)
    if not has_complete_oauth_tokens(payload):
        raise CauthError(
            f"{LIVE_CREDENTIALS} has no claudeAiOauth block with non-empty accessToken "
            "and refreshToken values. Claude Code is logged out; log in before storing "
            "this account."
        )
    identity = live_identity()
    if not isinstance(identity, dict) or not any(identity_of(identity)):
        raise CauthError(
            "The live login has no usable identity. Sign in again before saving this account."
        )
    data = load_accounts()
    duplicates = aliases_for_identity(identity)
    if alias not in data["accounts"] and duplicates:
        raise CauthError(
            f"This identity is already saved as {duplicates[0]!r}. "
            "Update that alias or use 'cauth rename <old> <new>' to change its label."
        )
    sync_back(alias)
    live = read_live_state()
    data["accounts"][alias] = {
        "email": live.email,
        "tier": live.tier_raw,
        "organization": identity.get("organizationName")
        if isinstance(identity.get("organizationName"), str)
        else None,
    }
    data["active"] = alias
    save_accounts(data)


@transactional
def prepare_for_external_login() -> str | None:
    """Sync the active account BEFORE something outside cauth replaces the live login.

    `claude auth login` overwrites ~/.claude/.credentials.json as its normal behaviour, and
    it does so before cauth is involved. Whatever the outgoing account refreshed during
    this session exists only in that file, so it has to be copied into the outgoing profile
    first or the login destroys it permanently. This is the same sync-back switch_to does,
    hoisted ahead of a swap we do not control.

    Returns the alias that was active only when the live file contains a complete outgoing
    login, so the caller can safely walk back to it afterwards. A logged-out identity can
    still name an active alias in the index; returning to that would discard the fresh login.
    """
    status = account_status()
    active = status["installed"]
    if active:
        sync_back(active)
    return active


@transactional
def add_then_return(alias: str, return_to: str | None) -> str | None:
    """Register the live account, then go back to the account you were using.

    Adding several accounts in a sitting should not drag you through each one. The login
    itself is what moves you, since `claude auth login` overwrites the live credentials
    before cauth is involved, so the fix is to walk back afterwards rather than to pretend
    it did not happen.

    Returns the alias actually left active, so the caller can report it honestly.
    """
    register_live_as(alias)
    if not return_to or return_to == alias:
        return alias
    data = load_accounts()
    if return_to not in data["accounts"]:
        return alias
    switch_to(return_to)
    return return_to


CLAUDE_LOGIN_CODE_PROMPT = b"Paste code here if prompted >"


def masked_authorization_code(code: str) -> str:
    """Return a useful paste confirmation without revealing the complete code."""
    if len(code) < 9:
        return "(too short to preview)"
    preview = code[:4] + "..." + code[-4:]
    # OAuth codes are printable ASCII today. Escaping here prevents a malformed paste from
    # turning its eight visible characters into terminal control sequences.
    return preview.encode("unicode_escape").decode("ascii")


def _read_authorization_code() -> str:
    """Read one code from the real terminal without echoing the pasted value."""
    try:
        with warnings.catch_warnings():
            # getpass otherwise falls back to visible stdin when it cannot control the
            # terminal. Refuse that fallback because the complete code must never echo.
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass("\nPaste code now (input hidden) > ")
    except getpass.GetPassWarning as exc:
        raise EOFError("terminal cannot hide pasted input") from exc


def _write_login_output(chunk: bytes) -> None:
    """Mirror Claude's bytes to this terminal, including its browser URL and prompt."""
    binary_stdout = getattr(sys.stdout, "buffer", None)
    if binary_stdout is not None:
        binary_stdout.write(chunk)
        binary_stdout.flush()
        return
    sys.stdout.write(chunk.decode("utf-8", errors="replace"))
    sys.stdout.flush()


def _stop_login_process(process: Any) -> None:
    """Stop a login child after local cancellation or terminal EOF."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_claude_login(email: str | None = None, idle_seconds: float = 180) -> int:
    """Run Claude login while confirming pasted input without exposing the full code.

    Claude Code 2.1.234 accepts its authorization code on piped stdin and still emits the
    browser URL and prompt on piped output. Cauth mirrors that output until the code prompt,
    reads the paste from the terminal with echo disabled, prints only a masked preview, and
    forwards the complete value to Claude in memory.
    """
    argv = [claude_executable(), "auth", "login"]
    if email:
        argv.extend(["--email", email])
    env = os.environ.copy()
    # These variables bypass file-backed subscription auth. They must not turn an OAuth
    # renewal into an API-key or setup-token session by accident.
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        process = subprocess.Popen(
            argv,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except FileNotFoundError:
        return 127
    except KeyboardInterrupt:
        return 130
    except OSError:
        return 126

    try:
        if process.stdin is None or process.stdout is None:
            _stop_login_process(process)
            return 126

        chunks: queue.Queue = queue.Queue(maxsize=8192)
        reader_stopped = threading.Event()

        def enqueue(chunk: bytes) -> None:
            while not reader_stopped.is_set():
                try:
                    chunks.put(chunk, timeout=0.2)
                    return
                except queue.Full:
                    pass

        def read_output() -> None:
            try:
                while not reader_stopped.is_set():
                    chunk = process.stdout.read(1)
                    enqueue(chunk)
                    if not chunk:
                        return
            except (OSError, ValueError):
                enqueue(b"")

        threading.Thread(target=read_output, daemon=True).start()
        prompt_tail = bytearray()
        idle_deadline = time.monotonic() + idle_seconds
        while True:
            try:
                chunk = chunks.get(timeout=max(0.01, idle_deadline - time.monotonic()))
            except queue.Empty:
                _stop_login_process(process)
                print(
                    "\nSign-in stopped waiting for Claude. Its prompt may have changed. Update Claude/Cauth and retry; Ctrl+C always cancels sign-in."
                )
                return 124
            if not chunk:
                break
            _write_login_output(chunk)
            prompt_tail.extend(chunk)
            if len(prompt_tail) > 1024:
                del prompt_tail[:-1024]
            if not re.search(
                rb"(?:paste[^\r\n]{0,100}code[^\r\n]{0,100}|(?:authorization|authentication) code[^\r\n]{0,100})[>:]\s*$",
                bytes(prompt_tail),
                re.IGNORECASE,
            ):
                continue
            code = _read_authorization_code()
            try:
                print(f"Code received: {masked_authorization_code(code)}", flush=True)
                process.stdin.write((code + "\n").encode("utf-8"))
                process.stdin.flush()
            except OSError:
                pass
            finally:
                code = ""
            prompt_tail.clear()
            idle_deadline = time.monotonic() + idle_seconds

        return process.wait()
    except EOFError:
        _stop_login_process(process)
        print("\nNo authorization code received.")
        return 1
    except KeyboardInterrupt:
        _stop_login_process(process)
        print("\nClaude login cancelled.")
        return 130
    finally:
        if "reader_stopped" in locals():
            reader_stopped.set()
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.stdout is not None:
            process.stdout.close()


def finish_renewal(alias: str, return_to: str | None) -> ClaudeProbeResult:
    register_live_as(alias)
    print(
        "Checking the renewed login with Claude (up to 90 seconds; Ctrl+C cancels the check)."
    )
    result = run_claude_probe()
    remember_check(alias, result)
    if return_to and return_to != alias:
        switch_to(return_to)
    return result


@diagnostics.traced("legacy.renew")
def reauthenticate(alias: str) -> ClaudeProbeResult:
    """Renew an existing alias through Claude without risking its parked profile.

    The old account is synced before the subprocess starts. A failed browser flow repairs
    the prior live login when possible. A successful login is identity-checked before the
    stored alias is replaced, so signing into the wrong browser account cannot destroy the
    profile the user meant to renew.
    """
    alias = validate_alias(alias)
    data = load_accounts()
    if alias not in data["accounts"]:
        raise CauthError(
            f"Unknown alias '{alias}'. Use 'cauth add {alias}' to store a new account."
        )

    with operation_lock():
        begin_login(alias)
        try:
            code = run_claude_login(data["accounts"][alias].get("email"))
            if code != 0:
                raise CauthError(
                    f"Claude login exited with status {code}. Previous login restored."
                )
            mark_live_unregistered()
            pending = pending_login()
            return_to = pending.get("return_to") if pending else None
            result = finish_renewal(alias, return_to)
            complete_login()
            return result
        except BaseException:
            restore_login()
            raise


@transactional
def remove_account(alias: str) -> None:
    data = load_accounts()
    if alias not in data["accounts"]:
        raise CauthError(f"Unknown alias '{alias}'")
    if data.get("active") == alias:
        data["active"] = None
    for p in profile_paths(alias):
        p.unlink(missing_ok=True)
        p.with_name(p.name + ".bak").unlink(missing_ok=True)
    del data["accounts"][alias]
    save_accounts(data)


# --------------------------------------------------------------------------------------
# CLI (works without Textual installed)
# --------------------------------------------------------------------------------------


def cmd_status(as_json: bool = False) -> int:
    data = load_accounts()
    status = account_status()
    warnings_list = env_conflicts() + auth_state_warnings(status["installed"])
    if status["mismatch"]:
        warnings_list.append(
            "The saved selection differs from the live identity. Reconcile in Cauth."
        )
    if as_json:
        print(
            json.dumps(
                {**status, "warnings": warnings_list, "accounts": data["accounts"]}
            )
        )
        return 0
    print(
        f"installed: {status['installed'] or ('unsaved login' if status['unsaved'] else 'not signed in')}"
    )
    print(f"email:     {status['email'] or 'unknown'}")
    print(f"plan:      {status['tier']}")
    print(f"checked:   {check_description(status['last_check'])}")
    print(f"saved:     {len(data['accounts'])}")
    for alias, info in sorted(data["accounts"].items()):
        mark = "Installed" if alias == status["installed"] else "Saved"
        print(f"  {alias} | {info.get('email') or 'unknown'} | {mark}")
    for message in warnings_list + runtime_notices():
        print(f"NOTE: {message}")
    return 0


def doctor() -> int:
    """Nonmutating installation checks; no credential reads or store creation."""
    checks = [
        ("Python 3.9+", sys.version_info >= (3, 9)),
        ("Claude executable on PATH", os.path.isabs(claude_executable())),
    ]
    try:
        import tui
        import tokenui

        checks.append(("Terminal UI imports", callable(tui.run_tui) and callable(tokenui.run_tui)))
    except ImportError:
        checks.append(("Terminal UI imports: rerun the checkout's installer", False))
    for label, ok in checks:
        print(f"{'OK' if ok else 'MISSING'}: {label}")
    print(f"Application: {Path(__file__).resolve()}")
    print(f"Interpreter: {sys.executable}")
    print(f"Credential location: {LIVE_CREDENTIALS}")
    if sys.platform == "darwin":
        print(
            "macOS Keychain credentials are not supported; file-backed authentication must be confirmed."
        )
    return 0 if all(ok for _, ok in checks) else 1


def cmd_list() -> int:
    for alias in sorted(load_accounts()["accounts"]):
        print(alias)
    return 0


def legacy_main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        try:
            from tui import run_tui
        except ImportError:
            print(
                "The terminal UI is unavailable. Rerun install.sh (POSIX) or install.cmd (Windows) from your checkout.",
                file=sys.stderr,
            )
            return 1
        return run_tui()
    cmd, *rest = argv
    if cmd in ("--version", "-V", "version") and not rest:
        print(f"cauth {__version__}")
        return 0
    if cmd in ("--help", "-h", "help") and not rest:
        print(USAGE)
        return 0
    aliases = {"st": "status", "ls": "list", "rm": "remove"}
    cmd = aliases.get(cmd, cmd)
    arity = {
        "status": 0,
        "list": 0,
        "switch": 1,
        "login": 1,
        "add": 1,
        "rename": 2,
        "remove": 1,
        "verify": 0,
        "doctor": 0,
        "recover": 0,
        "rebuild": 0,
        "clear-login": 1,
        "reset": 1,
    }
    structured = cmd in ("status", "verify") and rest == ["--json"]
    if cmd not in arity or (not structured and len(rest) != arity[cmd]):
        print(USAGE, file=sys.stderr)
        return 2
    if cmd in ("clear-login", "reset") and rest != ["--yes"]:
        print(f"Use 'cauth {cmd} --yes' to confirm. See 'cauth --help' for its scope.", file=sys.stderr)
        return 2
    try:
        if cmd == "clear-login":
            clear_unfinished_login()
            print("Unfinished login discarded. Current credentials kept; no old login restored.")
            return 0
        if cmd == "reset":
            reset_logins()
            print("All Cauth profiles, backups, pending state and live file login removed. No recovery copy retained. Sign in again to start fresh.")
            print("Environment tokens, OS keychains and server-side sessions are not cleared.")
            return 0
        if cmd == "doctor":
            return doctor()
        if cmd == "status":
            return cmd_status(structured)
        if cmd == "list":
            return cmd_list()
        if cmd == "verify":
            report = validate_active()
            if structured:
                from dataclasses import asdict

                print(json.dumps({**asdict(report), "kind": report.probe.kind}))
            else:
                print(report.probe.summary)
                print(report.probe.output)
                for message in report.warnings:
                    print(f"NOTE: {message}")
            return (
                0
                if report.ok
                else (1 if report.probe.kind == "sign_in_required" else 3)
            )
        if cmd == "recover":
            recover_transaction()
            restore_login()
            print(
                "Recovery completed. Previous login restored if a pending operation existed."
            )
        elif cmd == "rebuild":
            skipped = rebuild_index()
            print("Index rebuilt. Original index retained as a protected backup.")
            if skipped:
                print("Incomplete profiles retained for repair: " + ", ".join(skipped))
        elif cmd == "switch":
            require_file_auth()
            switch_to(rest[0])
            print(
                "Installed locally; this switch is UNVERIFIED. Run 'cauth verify' to check it."
            )
        elif cmd == "login":
            result = reauthenticate(rest[0])
            print(result.summary)
            if not result.ok:
                print(result.output)
                cmd_status()
                return 1 if result.kind == "sign_in_required" else 3
        elif cmd == "add":
            register_live_as(rest[0])
        elif cmd == "rename":
            rename_account(*rest)
        elif cmd == "remove":
            remove_account(rest[0])
            print(
                "Saved profile and its rotation backups removed. Claude's live login was not changed."
            )
        return cmd_status()
    except (CauthError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] == ["legacy"]:
            return legacy_main(argv[1:])
        if argv[:1] and argv[0] in ("reset", "clear-login", "doctor", "--version", "-V", "version"):
            return legacy_main(argv)
        import tokenauth
        if argv[:1] and argv[0] in ("--help", "-h", "help"):
            print(tokenauth.HELP)
            return 0
        return tokenauth.main(argv)
    except (CauthError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Cancelled. No new token saved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    # Imports by tokenauth/tui must share this module's state when used as a script.
    sys.modules["cauth"] = sys.modules[__name__]
    raise SystemExit(main())
