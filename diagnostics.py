"""Bounded structured diagnostics. Never accept free-form input or exception messages."""

import contextvars
from contextlib import contextmanager
from datetime import datetime, timezone
import functools
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import uuid

MAX_BYTES = 1024 * 1024
BACKUPS = 3
SESSION = uuid.uuid4().hex[:12]
_operation = contextvars.ContextVar("diagnostic_operation", default=None)
_mutex = threading.RLock()
_warned = False
_NUMBERS = {"duration_ms", "returncode", "errno", "characters", "lines", "argument_count"}
_BOOLS = {"has_prefix", "whitespace_removed", "generate", "replacing", "interactive",
          "oauth_override", "api_key_override", "bearer_override", "provider_override", "endpoint_override"}
_ENUMS = {
    "kind": {"verified", "sign_in_required", "unavailable", "cancelled", "rate_limited"},
    "reason": {"invalid_format", "missing_selection", "bare_mode", "hidden_input_unavailable",
               "startup_file_unavailable"},
}


def path():
    import cauth
    return cauth.CONFIG_DIR / "logs" / "diagnostics.jsonl"


@contextmanager
def _locked():
    target = path()
    target.parent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target.parent.parent, 0o700)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target.parent, 0o700)
    fd = os.open(target.parent / ".lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b") as stream:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield target
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)


def _write(record):
    global _warned
    try:
        with _mutex, _locked() as target:
            if target.is_symlink():
                raise OSError("Refusing a linked diagnostic file")
            if target.exists() and target.stat().st_size >= MAX_BYTES:
                for number in range(BACKUPS, 0, -1):
                    previous = target if number == 1 else Path(str(target) + f".{number - 1}")
                    if previous.exists():
                        os.replace(previous, Path(str(target) + f".{number}"))
            fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                os.chmod(target, 0o600)
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        if not _warned:
            print("Warning: diagnostic logging is unavailable; the account operation will continue.", file=sys.stderr)
            _warned = True


def event(name, **fields):
    if not re.fullmatch(r"[a-z_]+(?:\.[a-z_]+)*", name):
        return
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": name, "pid": os.getpid(), "session": SESSION,
        "operation": _operation.get(), "python": sys.version.split()[0], "platform": sys.platform,
    }
    for key, value in fields.items():
        if key in _NUMBERS and type(value) in (int, float):
            record[key] = value
        elif key in _BOOLS and type(value) is bool:
            record[key] = value
        elif key in _ENUMS and isinstance(value, str) and value in _ENUMS[key]:
            record[key] = value
    _write(record)


def failure(exc):
    # Stack locations, not formatted tracebacks: no source lines, locals, arguments,
    # subprocess output or exception text (any of those could contain pasted secrets).
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": "operation.error", "pid": os.getpid(), "session": SESSION,
        "operation": _operation.get(), "error_type": type(exc).__name__,
        "frames": [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                   for frame in traceback.extract_tb(exc.__traceback__)[-8:]],
    }
    if isinstance(getattr(exc, "errno", None), int):
        record["errno"] = exc.errno
    _write(record)


def traced(name):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            parent = _operation.get()
            marker = _operation.set(parent or uuid.uuid4().hex[:12])
            start = time.monotonic()
            event(name + ".start")
            try:
                result = function(*args, **kwargs)
            except BaseException as exc:
                failure(exc)
                event(name + ".failed", duration_ms=round((time.monotonic() - start) * 1000))
                raise
            else:
                event(name + ".complete", duration_ms=round((time.monotonic() - start) * 1000))
                return result
            finally:
                _operation.reset(marker)
        return wrapped
    return decorate


def read_recent(limit=200):
    from collections import deque
    records = deque(maxlen=limit)
    target = path()
    # Reading logs does not initialize or modify the store.
    for source in [*(Path(str(target) + f".{i}") for i in range(BACKUPS, 0, -1)), target]:
        if source.is_file() and not source.is_symlink():
            with source.open(encoding="utf-8") as stream:
                records.extend(stream)
    return "".join(records)
