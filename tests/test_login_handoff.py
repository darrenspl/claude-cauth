"""The browser login must run only after Textual fully releases the terminal.

The original implementation called ``app.suspend()`` from a thread worker. Textual's
Linux driver tried to install a signal handler from that worker, swallowed the resulting
exception, and left its mouse mode and input reader alive beside ``claude auth login``.
That made selection impossible, stole pasted authorization codes, and printed mouse escape
reports as junk. These tests pin a single-owner terminal boundary without real credentials.
"""

from __future__ import annotations

import asyncio
import errno
import os
import select
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
import pytest

import tui

if os.name == "posix":
    import fcntl
    import pty
    import termios


def test_tui_login_handoff_uses_the_shared_login_runner(monkeypatch):
    monkeypatch.setattr(tui.cauth, "run_claude_login", lambda: 23)
    assert tui.run_claude_login() == 23


def test_controller_starts_claude_only_after_textual_run_returns(monkeypatch):
    events = []
    request = tui.LoginRequest("work")
    app_results = iter((request, None))

    class FakeApp:
        def __init__(self, pending_login=None):
            events.append(("app-created", pending_login))

        def run(self):
            events.append("app-running")
            result = next(app_results)
            events.append("app-returned")
            return result

    def fake_login():
        assert events[-1] == "app-returned"
        events.append("claude-running")
        return 0

    def fake_mark_unregistered():
        assert events[-1] == "claude-running"
        events.append("live-unregistered")

    monkeypatch.setattr(tui, "CauthApp", FakeApp)
    monkeypatch.setattr(tui, "run_claude_login", fake_login)
    monkeypatch.setattr(tui.cauth, "mark_live_unregistered", fake_mark_unregistered)

    assert tui.run_tui() == 0
    assert events == [
        ("app-created", None),
        "app-running",
        "app-returned",
        "claude-running",
        "live-unregistered",
        ("app-created", request),
        "app-running",
        "app-returned",
    ]


def test_failed_login_relaunches_without_alias_capture(monkeypatch):
    created_with = []
    app_results = iter((tui.LoginRequest("work"), None))

    class FakeApp:
        def __init__(self, pending_login=None):
            created_with.append(pending_login)

        def run(self):
            return next(app_results)

    monkeypatch.setattr(tui, "CauthApp", FakeApp)
    monkeypatch.setattr(tui, "run_claude_login", lambda: 1)
    monkeypatch.setattr(
        tui.cauth,
        "mark_live_unregistered",
        lambda: pytest.fail("failed login must not change the active pointer"),
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert tui.run_tui() == 0
    assert created_with == [None, None]


def test_controller_stops_safely_if_post_login_state_cannot_be_reconciled(
    monkeypatch, capsys
):
    results = iter((tui.LoginRequest("work"), None))
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    class FakeApp:
        def __init__(self, pending_login=None):
            assert pending_login is None

        def run(self):
            return next(results)

    monkeypatch.setattr(tui, "CauthApp", FakeApp)
    monkeypatch.setattr(tui, "run_claude_login", lambda: 0)

    def fail_mark():
        raise tui.cauth.CauthError("fixture write failure")

    monkeypatch.setattr(tui.cauth, "mark_live_unregistered", fail_mark)

    assert tui.run_tui() == 0
    output = capsys.readouterr().out
    assert "Login could not be saved" in output
    assert "Previous login restored" in output


def test_successful_login_reopens_at_the_alias_modal():
    app = tui.CauthApp(tui.LoginRequest(None))

    async def drive() -> None:
        async with app.run_test() as pilot:
            # Poll rather than pausing a fixed number of times. Getting to the modal takes
            # a mount of AddScreen and then a push on top of it, and how many event loop
            # turns that costs is not ours to predict: two pauses were enough on Linux and
            # left the app on AddScreen under Windows scheduling.
            for _ in range(50):
                if isinstance(app.screen, tui.AliasInputModal):
                    break
                await pilot.pause()
            assert isinstance(app.screen, tui.AliasInputModal)

    asyncio.run(drive())


def _read_until(fd: int, output: bytearray, target: bytes, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while target not in output and time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError as exc:
            if exc.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)
    assert target in output, (
        f"did not see {target!r}; terminal tail was {bytes(output[-2000:])!r}"
    )


def _drain(fd: int, output: bytearray, duration: float) -> None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            continue
        try:
            output.extend(os.read(fd, 65536))
        except OSError as exc:
            if exc.errno == errno.EIO:
                return
            raise


def _wait_for_child(pid: int, fd: int, output: bytearray, timeout: float = 8) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found, status = os.waitpid(pid, os.WNOHANG)
        if found:
            return status
        _drain(fd, output, 0.05)
    raise TimeoutError("Cauth did not exit after the fake login flow")


@pytest.mark.skipif(os.name != "posix", reason="PTY terminal-mode check is POSIX-only")
def test_real_pty_handoff_disables_mouse_and_delivers_paste(tmp_path):
    """Drive the TUI and prove the full pasted code stays off the terminal."""
    repo = Path(__file__).resolve().parent.parent
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    received_code = tmp_path / "received-code.txt"
    fake_claude.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "print(f'FAKE_STDIN_TTY={int(os.isatty(sys.stdin.fileno()))}', flush=True)\n"
        "print('https://example.test/' + ('selectable-' * 20), flush=True)\n"
        "print('Paste code here if prompted > ', end='', flush=True)\n"
        "code = sys.stdin.readline()\n"
        "pathlib.Path(os.environ['FAKE_CODE_PATH']).write_text(code, encoding='utf-8')\n"
        "print('FAKE_LOGIN_DONE', flush=True)\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o700)

    fake_home = tmp_path / "pty-home"
    fake_home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(fake_home),
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
            "TERM": "xterm-256color",
            "FAKE_CODE_PATH": str(received_code),
        }
    )
    for name in (
        "ANTHROPIC_API_KEY",
        "CLAUDECODE",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CONFIG_DIR",
    ):
        env.pop(name, None)

    pid, master = pty.fork()
    if pid == 0:
        try:
            os.chdir(repo)
            os.execve(
                sys.executable,
                [sys.executable, str(repo / "cauth.py"), "legacy"],
                env,
            )
        finally:
            os._exit(127)

    output = bytearray()
    child_reaped = False
    try:
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
        _read_until(master, output, b"Claude Cauth")
        os.write(master, b"a")
        _read_until(master, output, b"Add account")
        os.write(master, b"\t\r")
        _read_until(master, output, b"Hand the terminal to")
        os.write(master, b"y")
        _read_until(master, output, b"Paste code here if prompted >")
        _read_until(master, output, b"Paste code now (input hidden) >")
        os.write(master, b"fixture-code-123\n")
        _read_until(master, output, b"Code received: fixt...-123")
        _read_until(master, output, b"FAKE_LOGIN_DONE")
        _read_until(master, output, b"work, personal, client-acme")

        # Cancel alias capture, explicitly restore the previous login, then quit Home.
        os.write(master, b"\x1b")
        _drain(master, output, 0.5)
        os.write(master, b"y")
        _drain(master, output, 0.5)
        os.write(master, b"q")
        status = _wait_for_child(pid, master, output)
        child_reaped = True
    finally:
        os.close(master)
        if not child_reaped:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

    raw = bytes(output)
    assert os.waitstatus_to_exitcode(status) == 0
    assert received_code.read_text(encoding="utf-8") == "fixture-code-123\n"
    assert b"fixture-code-123" not in raw
    assert b"FAKE_STDIN_TTY=0" in raw

    marker = raw.index(b"FAKE_STDIN_TTY")
    for mode in (b"1000", b"1003", b"1015", b"1006", b"2004"):
        enabled = b"\x1b[?" + mode + b"h"
        disabled = b"\x1b[?" + mode + b"l"
        assert raw.rfind(disabled, 0, marker) > raw.rfind(enabled, 0, marker), mode
        assert raw.rfind(disabled) > raw.rfind(enabled), mode
