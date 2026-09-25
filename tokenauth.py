"""Long-lived setup-token accounts. Secrets stay in one private, atomic store."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cauth
import diagnostics


HELP = """Claude OAuth token manager
  cauth                     open long-lived OAuth account manager
  cauth login <alias>        run claude setup-token, then securely save its token
  cauth add <alias>          paste an existing setup-token (hidden input)
  cauth switch <alias>       select the token for future Claude launches
  cauth run [--] [args...]   launch Claude with the selected token
  cauth verify              test the selected token through Claude
  cauth status [--json]      show aliases/check results, never tokens
  cauth list                list token aliases
  cauth rename <old> <new>   rename a token account
  cauth remove <alias>       delete a stored token
  cauth shell-init <shell>   print a secret-free claude function (bash/zsh/fish/powershell)
  cauth install-shell <shell> install the function in bash/zsh startup files
  cauth clear-login --yes    clear an old browser-login marker without rollback
  cauth reset --yes          remove all tokens, old profiles and local file logins
  cauth legacy [command]    access the previous browser-profile workflow
  cauth doctor              check installation
  cauth logs [--path]        show recent diagnostics or their location (no tokens/prompts)

Use 'cauth run' or the shell function to apply your selection. Already-running Claude
processes retain their original environment. Setup-tokens have a documented one-year
lifetime; imported token age and server-side revocation cannot be inferred locally.
"""


def path():
    return cauth.CONFIG_DIR / "tokens.json"


def launcher_dir():
    """Holds Cauth's secret-free `claude` launcher, placed first on PATH."""
    return cauth.CONFIG_DIR / "bin"


# Caller-chosen authentication. A launch through the PATH launcher that already has one
# of these (for example a child of a Cauth-launched session) keeps it untouched.
AUTH_OVERRIDES = (
    "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


def load():
    if not path().exists():
        return {"active": None, "accounts": {}}
    data = cauth.read_json(path())
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        raise cauth.CauthError("Invalid token store. Reset all logins to start fresh.")
    data.setdefault("active", None)
    for alias, entry in data["accounts"].items():
        cauth.validate_alias(alias)
        if not isinstance(entry, dict):
            raise cauth.CauthError("Invalid token account.")
        validate_token(entry.get("token"))
        if entry.get("last_check") is not None and not isinstance(entry["last_check"], dict):
            raise cauth.CauthError("Invalid token check metadata. Reset the damaged token store.")
    if data.get("active") is not None and (
        not isinstance(data["active"], str) or data["active"] not in data["accounts"]
    ):
        raise cauth.CauthError("Selected token is missing. Reset the damaged token store.")
    return data


def validate_token(token):
    if not isinstance(token, str) or not re.fullmatch(r"sk-ant-oat01-[A-Za-z0-9_-]+", token):
        raise cauth.CauthError("Paste only the OAuth token from claude setup-token, not an API key or authorization code.")
    return token


@diagnostics.traced("token.save")
def save(alias, token, source="imported"):
    alias = cauth.validate_alias(alias)
    token = validate_token(token.strip())
    with cauth.operation_lock():
        data = load()
        diagnostics.event("token.save_details", replacing=alias in data["accounts"])
        # A single replace commits the secret and its metadata together. No stale backups.
        data["accounts"][alias] = {
            "token": token, "saved_at": int(time.time()), "source": source,
            "last_check": None,
        }
        if data["active"] is None:
            data["active"] = alias
        cauth.atomic_write_json(path(), data, mode=cauth.FILE_MODE)
    return ensure_shell_integration()


@diagnostics.traced("token.select")
def select(alias):
    with cauth.operation_lock():
        data = load()
        if alias not in data["accounts"]:
            raise cauth.CauthError(f"No long-lived token for '{alias}'. Run cauth login '{alias}' first.")
        data["active"] = alias
        cauth.atomic_write_json(path(), data)
    return ensure_shell_integration()


@diagnostics.traced("token.remove")
def remove(alias):
    with cauth.operation_lock():
        data = load()
        if alias not in data["accounts"]:
            raise cauth.CauthError(f"Unknown token alias '{alias}'.")
        del data["accounts"][alias]
        if data["active"] == alias:
            data["active"] = None
        cauth.atomic_write_json(path(), data)


@diagnostics.traced("token.rename")
def rename(old, new):
    new = cauth.validate_alias(new)
    with cauth.operation_lock():
        data = load()
        if old not in data["accounts"] or new in data["accounts"]:
            raise cauth.CauthError("Source alias must exist and destination must be unused.")
        data["accounts"][new] = data["accounts"].pop(old)
        if data["active"] == old:
            data["active"] = new
        cauth.atomic_write_json(path(), data)


def public_status():
    data = load()
    # Explicit allowlist: secrets must never reach status JSON, UI, or reports.
    return {"active": data["active"], "accounts": {
        alias: {key: entry.get(key) for key in ("saved_at", "source", "last_check")}
        for alias, entry in data["accounts"].items()
    }}


def environment(token=None):
    env = os.environ.copy()
    diagnostics.event(
        "claude.environment", oauth_override=bool(env.get("CLAUDE_CODE_OAUTH_TOKEN")),
        api_key_override=bool(env.get("ANTHROPIC_API_KEY")),
        bearer_override=bool(env.get("ANTHROPIC_AUTH_TOKEN")),
        provider_override=any(bool(env.get(key)) for key in (
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")),
        endpoint_override=bool(env.get("ANTHROPIC_BASE_URL")),
    )
    for key in (
        "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE", "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    ):
        env.pop(key, None)
    if token is not None:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = validate_token(token)
    return env


def selected():
    data = load()
    alias = data["active"]
    if not alias:
        diagnostics.event("token.selection_rejected", reason="missing_selection")
        raise cauth.CauthError("No token selected. Run cauth login <alias>, then cauth switch <alias>.")
    return alias, data["accounts"][alias]["token"]


def _read_windows_paste():
    try:
        import msvcrt
    except ImportError as exc:
        diagnostics.event("token.input_rejected", reason="hidden_input_unavailable")
        raise cauth.CauthError("This terminal cannot hide input. Use a real terminal; token was not read.") from exc
    def read_character():
        while True:
            char = msvcrt.getwch()  # Console input without echo; never a visible fallback.
            if char in ("\x00", "\xe0"):
                msvcrt.getwch()  # Ignore the rest of a function/arrow-key sequence.
                continue
            return char
    print("Paste setup-token (hidden; press Enter after pasting, then again if needed): ", end="", flush=True)
    try:
        return _collect_hidden_input(read_character, lambda message: print("\n" + message, flush=True))
    finally:
        print()


def _collect_hidden_input(read_character, notice):
    """Keep pasted paragraphs intact; Enter submits after a bracketed paste."""
    lines, current = [], ""
    escape = ""
    bracketed = False
    paste_complete = False
    previous_cr = False
    waiting_notice = False
    while True:
        char = read_character()
        if not char or char == "\x04":
            raise EOFError
        if char == "\x03":
            raise KeyboardInterrupt
        if escape:
            escape += char
            if escape == "\x1b[200~":
                bracketed, escape = True, ""
                paste_complete = False
            elif escape == "\x1b[201~":
                bracketed, escape = False, ""
                paste_complete = True
                notice("Received hidden text. Press Enter to finish.")
            elif not any(marker.startswith(escape) for marker in ("\x1b[200~", "\x1b[201~")):
                escape = ""
            continue
        if char == "\x1b":
            escape = char
            previous_cr = False
            continue
        if char == "\n" and previous_cr:
            previous_cr = False
            continue
        previous_cr = char == "\r"
        if char in ("\r", "\n"):
            if paste_complete and (lines or current.strip()):
                return "\n".join(lines + ([current] if current.strip() else []))
            if not current.strip():
                if lines and not bracketed:
                    return "\n".join(lines)
                if lines and bracketed:
                    lines.append("")
                if not lines and not waiting_notice:
                    notice("No text received yet. Paste the token; input stays hidden.")
                    waiting_notice = True
            else:
                lines.append(current)
                if not bracketed:
                    notice("Received hidden text. Paste any remaining lines, or press Enter to finish.")
            current = ""
        elif char in ("\x7f", "\b"):
            current = current[:-1]
        elif char == "\x15":
            current = ""
        else:
            current += char
            paste_complete = False


def _read_token_paste():
    if os.name == "nt":
        return _read_windows_paste()
    import termios
    # Keep echo disabled for the ENTIRE paste, including embedded newlines. Repeated
    # standalone getpass calls restore echo between lines and can expose queued text.
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError as exc:
        diagnostics.event("token.input_rejected", reason="hidden_input_unavailable")
        raise cauth.CauthError("A real terminal is required for hidden token input.") from exc
    with os.fdopen(fd, "r") as terminal:
        original = termios.tcgetattr(fd)
        hidden = original[:]
        hidden[6] = original[6][:]
        hidden[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON)
        hidden[3] |= termios.ISIG
        # Preserve CR/LF bytes: mapping CR to LF would turn a Windows CRLF into
        # two newlines, prematurely submitting a wrapped token before its tail.
        hidden[0] &= ~(termios.ICRNL | termios.IGNCR | termios.INLCR)
        hidden[6][termios.VMIN] = 1
        hidden[6][termios.VTIME] = 0
        try:
            termios.tcsetattr(fd, termios.TCSANOW, hidden)
            os.write(fd, b"\x1b[?2004h")
            os.write(fd, b"Paste setup-token (hidden; press Enter after pasting, then again if needed): ")
            return _collect_hidden_input(
                lambda: os.read(fd, 1).decode("latin1"),
                lambda message: os.write(fd, ("\n" + message + "\n").encode()),
            )
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, original)
            os.write(fd, b"\x1b[?2004l\n")


def _token_from_paste(pasted):
    """Accept one token, a quoted/export value, or Claude's labelled output."""
    invalid = "Paste one complete OAuth token or the labelled setup-token output."
    if pasted.count("sk-ant-oat01-") != 1:
        raise cauth.CauthError(invalid)
    lines = pasted.strip().splitlines()
    index = next(i for i, line in enumerate(lines) if "sk-ant-oat01-" in line)
    # Only the official heading may precede a copied output block. Do not extract
    # a credential embedded in arbitrary prose, commands, or diagnostic output.
    before = "\n".join(lines[:index]).strip()
    if before and not before.endswith("Your OAuth token (valid for 1 year):"):
        raise cauth.CauthError(invalid)
    first = lines[index].strip()
    if first.startswith("export CLAUDE_CODE_OAUTH_TOKEN="):
        first = first[len("export CLAUDE_CODE_OAUTH_TOKEN="):]
    quote = first[:1] if first[:1] in ("'", '"') else ""
    if quote:
        first = first[1:]
    parts = [first]
    remaining = lines[index + 1:]
    for offset, line in enumerate(remaining):
        if not line.strip():
            suffix = "\n".join(remaining[offset:]).strip()
            if suffix and (not before or suffix != (
                "Store this token securely. You won't be able to see it again.\n\n"
                "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>"
            )):
                raise cauth.CauthError(invalid)
            break
        parts.append(line.strip())
    token = "".join(parts)
    if quote:
        if not token.endswith(quote):
            raise cauth.CauthError(invalid)
        token = token[:-1]
    return validate_token(token)


def read_token():
    while True:
        pasted = _read_token_paste()
        # Diagnostics describe shape only; parsing never logs the pasted value.
        token = re.sub(r"\s+", "", pasted)
        diagnostics.event("token.paste", characters=len(token), lines=len(pasted.splitlines()),
                          has_prefix=token.startswith("sk-ant-oat01-"), whitespace_removed=token != pasted)
        try:
            return _token_from_paste(pasted)
        except cauth.CauthError:
            diagnostics.event("token.paste_rejected", reason="invalid_format")
            print("No single complete token found. Copy the full token or its export assignment, including all wrapped lines. A terminal with bracketed paste also accepts Claude's labelled token output. Abbreviated tokens and multiple tokens cannot be saved. Ctrl+C cancels; you do not need to authorize again.")


@diagnostics.traced("token.login")
def login(alias, generate=True):
    alias = cauth.validate_alias(alias)
    diagnostics.event("token.login_details", generate=generate)
    if generate:
        print("Claude will create a long-lived OAuth token. Copy its FULL token, including wrapped lines, then paste it into Cauth's hidden prompt. Press Enter after pasting; terminals without bracketed paste need a blank line to finish.")
        # The official command needs the real terminal; it prints the new token itself.
        code = subprocess.call(["claude", "setup-token"], env=environment())
        diagnostics.event("token.generator_exit", returncode=code)
        if code:
            raise cauth.CauthError(f"Token creation exited with status {code}. Stored token unchanged.")
    token = read_token()
    notice = save(alias, token, "setup-token" if generate else "imported")
    print(f"Saved long-lived token for {alias}.")
    print(notice)


@diagnostics.traced("claude.launch")
def run(args):
    if args[:1] == ["--"]:
        args = args[1:]
    if any(arg == "--bare" or arg.startswith("--bare=") for arg in args):
        diagnostics.event("claude.launch_rejected", reason="bare_mode")
        raise cauth.CauthError("Claude bare mode ignores OAuth tokens. Remove --bare.")
    via_launcher = os.environ.pop("CAUTH_SHIM", None) == "1"
    claude = cauth.claude_executable()
    if not os.path.isabs(claude):
        raise cauth.CauthError("Claude executable not found on PATH. Reinstall Claude Code.")
    if args[:1] == ["setup-token"]:
        return subprocess.call([claude, *args], env=environment())
    if via_launcher:
        # A plain `claude` launch that skipped the shell function. Supply the token when
        # the caller brought no authentication of its own; never block the launch.
        token = None
        if not any(os.environ.get(key) for key in AUTH_OVERRIDES):
            try:
                token = selected()[1]
            except cauth.CauthError as exc:
                print(f"cauth: {exc} Starting Claude without a saved token.", file=sys.stderr)
        diagnostics.event("claude.launcher", token_supplied=token is not None)
        if token is None:
            if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
                args = strip_remote_control(args)
            return subprocess.call([claude, *args])
    else:
        _, token = selected()
    args = strip_remote_control(args)
    diagnostics.event("claude.launch_details", argument_count=len(args), interactive=not bool(args))
    code = subprocess.call([claude, *args], env=environment(token))
    diagnostics.event("claude.exit", returncode=code)
    return code


def strip_remote_control(args):
    """Setup-tokens cannot run Remote Control; Claude answers the flag with a /login
    prompt. Drop it (and its optional name, parsed the way Claude parses it)."""
    kept, skip_name, dropped = [], False, False
    for arg in args:
        if skip_name:
            skip_name = False
            if not arg.startswith("-"):
                continue
        if arg == "--remote-control":
            skip_name = dropped = True
            continue
        if arg.startswith("--remote-control="):
            dropped = True
            continue
        kept.append(arg)
    if dropped:
        print("cauth: removed --remote-control. Long-lived tokens cannot use Remote Control, "
              "and Claude would ask you to log in.", file=sys.stderr)
    return kept


@diagnostics.traced("token.verify")
def verify():
    alias, token = selected()
    result = cauth.run_claude_probe(oauth_token=token)
    diagnostics.event("token.verify_result", returncode=result.returncode, kind=result.kind)
    with cauth.operation_lock():
        data = load()
        entry = data["accounts"].get(alias)
        if entry and entry["token"] == token:
            entry["last_check"] = {"kind": result.kind, "at": int(time.time())}
            cauth.atomic_write_json(path(), data)
    return result


def shell_init(shell):
    # Put the launcher first on PATH (moving it if already present), so scripts, tmux
    # commands and `command claude` also get the token, not only typed commands.
    front = (f"_cauth_bin={shlex.quote(str(launcher_dir()))}\n"
             'PATH=":$PATH:"; PATH="${PATH//":$_cauth_bin:"/:}"; PATH="${PATH#:}"; PATH="${PATH%:}"\n'
             'export PATH="$_cauth_bin${PATH:+:$PATH}"; unset _cauth_bin\n')
    posix = front + 'unalias claude 2>/dev/null || true\nclaude() { command cauth run -- "$@"; }'
    scripts = {
        "bash": posix,
        "zsh": posix,
        "fish": 'function claude; command cauth run -- $argv; end',
        "powershell": 'function global:claude { & cauth run -- @args }',
    }
    if shell not in scripts:
        raise cauth.CauthError("Supported shells: bash, zsh, fish, powershell.")
    return scripts[shell]


def launch_instructions():
    shell = Path(os.environ.get("SHELL", "")).name
    commands = {
        "bash": 'eval "$(cauth shell-init bash)"',
        "zsh": 'eval "$(cauth shell-init zsh)"',
        "fish": "cauth shell-init fish | source",
        "pwsh": "cauth shell-init powershell | Out-String | Invoke-Expression",
        "powershell": "cauth shell-init powershell | Out-String | Invoke-Expression",
    }
    command = commands.get(shell)
    notice = "Use claude or your usual shortcut (such as cc) outside Cauth."
    if command:
        notice += ("\nExisting terminal still asks for login? Exit Claude, then run once:\n  "
                   + command)
    else:
        notice += "\nLoad the function from cauth shell-init <shell> in your terminal."
    if shell in ("bash", "zsh"):
        notice += "\nOr open a new terminal session after successful shell setup."
    notice += ("\nCauth cannot refresh its parent terminal. Once integration is loaded, "
               "token switches apply on the next Claude launch; no terminal refresh is needed."
               "\nAlready-running Claude sessions keep their original token.")
    return notice + "\nLong-lived tokens do not support Remote Control (--remote-control)."


def install_launcher():
    """Write the secret-free PATH launcher. Rewritten only when it differs."""
    found = shutil.which("cauth", path=os.pathsep.join(
        entry for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and os.path.realpath(entry) != os.path.realpath(launcher_dir())))
    command = (shlex.quote(found) if found else
               shlex.quote(sys.executable) + " " + shlex.quote(str(Path(cauth.__file__).resolve())))
    body = ("#!/bin/sh\n"
            "# Generated by Cauth. Contains no token. Sends plain `claude` launches through\n"
            "# Cauth so the selected long-lived token is supplied. Cauth repairs this file.\n"
            f'CAUTH_SHIM=1 exec {command} run -- "$@"\n')
    target = launcher_dir() / "claude"
    if target.exists() and not target.is_symlink():
        try:
            if target.read_text() == body and os.access(target, os.X_OK):
                return target
        except (OSError, UnicodeError):
            pass
    launcher_dir().mkdir(parents=True, exist_ok=True, mode=cauth.DIR_MODE)
    fd, temporary = cauth.tempfile.mkstemp(prefix=".claude-", dir=launcher_dir())
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(body)
        os.chmod(temp, 0o700)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


def heal():
    """Quietly repair the launcher and shell block on every Cauth use. Only once a token
    is selected, so browser-login-only users keep a plain `claude`. Never raises."""
    try:
        if not load()["active"]:
            return
        shell = Path(os.environ.get("SHELL", "")).name
        if shell in ("bash", "zsh"):
            install_shell(shell)
    except (OSError, UnicodeError, cauth.CauthError):
        diagnostics.event("shell.heal_failed")


def ensure_shell_integration():
    """Repair persistent routing after a committed token operation, without losing it."""
    shell = Path(os.environ.get("SHELL", "")).name
    if shell not in ("bash", "zsh"):
        return ("Token saved/selected. Use cauth run, or cauth shell-init <shell> "
                "to enable ordinary claude commands in your shell.")
    try:
        install_shell(shell)
    except (OSError, UnicodeError, cauth.CauthError):
        diagnostics.event("shell.install_failed", reason="startup_file_unavailable")
        return (f"Token saved/selected, but shell setup failed. Use cauth run now; "
                f"run cauth install-shell {shell} to diagnose and retry.")
    return "Claude startup integration installed/checked.\n" + launch_instructions()


def install_shell(shell):
    if shell not in ("bash", "zsh"):
        raise cauth.CauthError("Automatic installation supports bash/zsh. Use shell-init for fish/PowerShell.")
    install_launcher()
    destination = Path.home() / (".bashrc" if shell == "bash" else ".zshrc")
    if destination.is_symlink():
        destination = destination.resolve(strict=True)
    original = destination.read_text() if destination.exists() else ""
    start, end = "# >>> Claude Cauth OAuth >>>", "# <<< Claude Cauth OAuth <<<"
    block = start + "\n" + shell_init(shell) + "\n" + end
    if start in original or end in original:
        if original.count(start) != 1 or original.count(end) != 1 or original.index(end) < original.index(start):
            raise cauth.CauthError("Cauth shell block is damaged; startup file was not changed.")
        before, rest = original.split(start, 1)
        _, after = rest.split(end, 1)
        updated = before + block + after
    else:
        updated = original + ("\n" if original and not original.endswith("\n") else "") + "\n" + block + "\n"
    if updated != original:
        if destination.exists():
            backup_fd, backup = cauth.tempfile.mkstemp(
                prefix=destination.name + ".cauth-backup-", dir=destination.parent)
            os.close(backup_fd)
            shutil.copyfile(destination, backup)
        fd, temporary = cauth.tempfile.mkstemp(prefix=".cauth-shell-", dir=destination.parent)
        temp = Path(temporary)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, cauth.file_mode_of(destination, cauth.FILE_MODE))
            os.replace(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
    return destination


def main(argv):
    if not argv:
        heal()
        from tokenui import run_tui
        return run_tui()
    cmd, *args = argv
    if cmd != "shell-init":
        heal()
    if cmd == "logs" and args in ([], ["--path"]):
        print(str(diagnostics.path()) if args else diagnostics.read_recent() or "No diagnostic events recorded yet.")
        return 0
    cmd = {"ls": "list", "st": "status", "rm": "remove"}.get(cmd, cmd)
    arity = {"login": 1, "add": 1, "switch": 1, "remove": 1, "rename": 2,
             "verify": 0, "list": 0, "status": 0, "shell-init": 1, "install-shell": 1}
    if cmd == "run":
        return run(args)
    if cmd not in arity or (len(args) != arity[cmd] and not (cmd == "status" and args == ["--json"])):
        print(HELP)
        return 2
    if cmd in ("login", "add"):
        login(args[0], generate=cmd == "login")
    elif cmd == "switch":
        notice = select(args[0])
        print(f"Selected {args[0]} for future launches through cauth run / the claude shell function. Not yet verified.")
        print(notice)
    elif cmd == "remove":
        remove(args[0])
        print("Stored token removed. Already-running Claude sessions keep their token.")
    elif cmd == "rename":
        rename(*args)
    elif cmd == "verify":
        result = verify()
        print(result.summary)
        if not result.ok:
            print(result.output)
        return 0 if result.ok else (1 if result.kind == "sign_in_required" else 3)
    elif cmd == "shell-init":
        print(shell_init(args[0]))
    elif cmd == "install-shell":
        destination = install_shell(args[0])
        print(f"Installed secret-free Claude function in {destination}. Open a new terminal, or evaluate cauth shell-init in this one.")
    else:
        status = public_status()
        if cmd == "list":
            print("\n".join(sorted(status["accounts"])))
        elif args:
            print(cauth.json.dumps(status))
        else:
            print(f"Selected token: {status['active'] or 'none'}")
            for alias, info in sorted(status["accounts"].items()):
                print(f"  {alias}: {cauth.check_description(info['last_check'])}")
    return 0
