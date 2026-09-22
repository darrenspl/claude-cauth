"""Long-lived account lifecycle, safe child environments, and the default UI."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import cauth
import tokenauth
import tokenui


ALICE = "sk-ant-oat01-FIXTURE-alice-long-lived"
BOB = "sk-ant-oat01-FIXTURE-bob-long-lived"


@pytest.mark.parametrize("pasted", [
    '"' + ALICE + '"',
    "export CLAUDE_CODE_OAUTH_TOKEN='" + ALICE + "'",
    "Your OAuth token (valid for 1 year):\n\n " + ALICE[:23] + "\n " + ALICE[23:] +
    "\n\nStore this token securely. You won't be able to see it again.\n\n"
    "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>",
])
def test_read_token_accepts_known_copied_output(monkeypatch, capsys, pasted):
    attempts = iter([pasted])
    monkeypatch.setattr(tokenauth, "_read_token_paste", lambda: next(attempts))
    assert tokenauth.read_token() == ALICE
    assert ALICE not in capsys.readouterr().out


@pytest.mark.parametrize("pasted", [
    ALICE + "\n" + BOB,
    ALICE + "\n" + ALICE,
    ALICE[:23] + "...AA",
    ALICE[:23] + "\u2026AA",
    "export CLAUDE_CODE_OAUTH_TOKEN='" + ALICE,
    ALICE + " trailing words",
    "unknown-prefix " + ALICE,
    ALICE + "\n\nignored text",
    ALICE[:23] + "\n\n" + ALICE[23:],
    "Your OAuth token (valid for 1 year):\n\n" + ALICE + "\n\nunknown footer",
])
def test_copied_output_rejects_ambiguous_or_incomplete_candidates(pasted):
    with pytest.raises(cauth.CauthError):
        tokenauth._token_from_paste(pasted)


def test_bracketed_paste_finishes_with_one_enter_without_leaking_text():
    chars = iter("\x1b[200~" + ALICE[:23] + "\n " + ALICE[23:] + "\x1b[201~\r")
    notices = []
    assert tokenauth._collect_hidden_input(lambda: next(chars, ""), notices.append) == ALICE[:23] + "\n " + ALICE[23:]
    assert all(ALICE not in notice for notice in notices)


def test_atomic_renewal_selection_rename_remove_and_redacted_status(capsys):
    tokenauth.save("alice", ALICE)
    tokenauth.save("bob", BOB)
    tokenauth.select("bob")
    tokenauth.save("alice", ALICE + "-renewed", "setup-token")
    assert tokenauth.selected() == ("bob", BOB)
    tokenauth.rename("alice", "personal")
    tokenauth.select("personal")
    assert tokenauth.selected()[1] == ALICE + "-renewed"
    assert cauth.main(["status", "--json"]) == 0
    output = capsys.readouterr().out
    assert "personal" in output
    assert "sk-ant-" not in output
    assert "refreshToken" not in tokenauth.path().read_text()
    if os.name == "posix":
        assert tokenauth.path().stat().st_mode & 0o777 == 0o600
    tokenauth.remove("personal")
    assert tokenauth.load()["active"] is None
    assert "personal" not in tokenauth.load()["accounts"]


def test_login_runs_setup_token_and_saves_hidden_input_without_legacy_pending(monkeypatch, capsys):
    tokenauth.save("alice", ALICE)
    (cauth.CONFIG_DIR / ".pending-login.json").write_text("{")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "old-environment-token")
    seen = {}
    def create(argv, **kw):
        seen.update(argv=argv, **kw)
        return 0
    monkeypatch.setattr(tokenauth.subprocess, "call", create)
    monkeypatch.setattr(tokenauth, "_read_token_paste", lambda: BOB)
    assert cauth.main(["login", "alice"]) == 0
    assert seen["argv"] == ["claude", "setup-token"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen["env"]
    assert tokenauth.selected() == ("alice", BOB)
    assert "sk-ant-" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["exit", "cancel", "invalid", "write"])
def test_failed_renewal_keeps_old_token(monkeypatch, failure):
    tokenauth.save("alice", ALICE)
    monkeypatch.setattr(tokenauth.subprocess, "call", lambda *a, **k: 9 if failure == "exit" else 0)
    def read():
        if failure == "cancel":
            raise KeyboardInterrupt
        return "wrong" if failure == "invalid" else BOB
    monkeypatch.setattr(tokenauth, "read_token", read)
    if failure == "write":
        def fail(*a, **kw):
            raise OSError("fixture write failure")
        monkeypatch.setattr(cauth.os, "replace", fail)
    assert cauth.main(["login", "alice"]) in (1, 130)
    assert tokenauth.selected() == ("alice", ALICE)


def test_child_receives_selected_token_without_mutating_parent_or_live_files(live_login, monkeypatch):
    live_login("legacy")
    tokenauth.save("alice", ALICE)
    tokenauth.save("bob", BOB)
    tokenauth.select("bob")
    before = cauth.snapshot_files([cauth.LIVE_CREDENTIALS, cauth.LIVE_CLAUDE_JSON])
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_VERTEX"):
        monkeypatch.setenv(name, "fixture-override")
    def child(argv, env):
        assert argv == ["claude", "-p", "hello world"]
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == BOB
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert "CLAUDE_CODE_USE_VERTEX" not in env
        return 17
    monkeypatch.setattr(tokenauth.subprocess, "call", child)
    assert cauth.main(["run", "--", "-p", "hello world"]) == 17
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "fixture-override"
    assert cauth.snapshot_files([cauth.LIVE_CREDENTIALS, cauth.LIVE_CLAUDE_JSON]) == before


def test_probe_uses_long_lived_token_and_redacts_error(monkeypatch):
    tokenauth.save("alice", ALICE)
    def probe(argv, **kw):
        assert kw["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == ALICE
        assert ALICE not in " ".join(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="unauthorized " + ALICE)
    monkeypatch.setattr(cauth.subprocess, "run", probe)
    result = tokenauth.verify()
    assert result.kind == "sign_in_required"
    assert ALICE not in result.output
    assert tokenauth.public_status()["accounts"]["alice"]["last_check"]["kind"] == "sign_in_required"
    assert tokenauth.selected() == ("alice", ALICE)


@pytest.mark.parametrize("value", ["", "code", "sk-ant-api03-fixture", ALICE + "\nexport BAD=yes"])
def test_invalid_token_never_stored(value):
    with pytest.raises(cauth.CauthError):
        tokenauth.save("alice", value)
    assert not tokenauth.path().exists()


def test_reset_also_erases_long_lived_tokens():
    tokenauth.save("alice", ALICE)
    cauth.reset_logins()
    assert not tokenauth.path().exists()


@pytest.mark.parametrize("shell,filename", [("bash", ".bashrc"), ("zsh", ".zshrc")])
def test_shell_installation_is_idempotent_and_preserves_config(tmp_path, monkeypatch, shell, filename):
    monkeypatch.setattr(tokenauth.Path, "home", lambda: tmp_path)
    target = tmp_path / filename
    target.write_text("# Existing settings\nexport EDITOR=vim\n")
    tokenauth.install_shell(shell)
    first = target.read_bytes()
    tokenauth.install_shell(shell)
    assert target.read_bytes() == first
    assert first.startswith(b"# Existing settings\nexport EDITOR=vim\n")
    assert b"sk-ant-" not in first
    assert first.count(b"command cauth run") == 1


def test_hidden_input_refuses_visible_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "msvcrt", None)
    with pytest.raises(cauth.CauthError, match="cannot hide"):
        tokenauth._read_windows_paste()


@pytest.mark.skipif(os.name != "posix", reason="POSIX dotfile symlink")
def test_shell_installation_preserves_dotfile_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(tokenauth.Path, "home", lambda: tmp_path)
    target = tmp_path / "managed-bashrc"
    target.write_text("# managed dotfile\n")
    link = tmp_path / ".bashrc"
    link.symlink_to(target)
    tokenauth.install_shell("bash")
    assert link.is_symlink()
    assert target.read_text().startswith("# managed dotfile\n")
    assert "command cauth run" in target.read_text()


@pytest.mark.parametrize("args", [["--bare"], ["--bare=true"]])
def test_bare_mode_is_refused_before_launch(args, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("must not launch")
    monkeypatch.setattr(tokenauth.subprocess, "call", forbidden)
    assert cauth.main(["run", *args]) == 1


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_real_launch_and_shell_function_use_selected_token_without_refresh(tmp_path, monkeypatch, shell):
    if os.name != "posix":
        pytest.skip("POSIX executable fixture")
    import shutil
    if not shutil.which(shell):
        pytest.skip(f"{shell} is not installed")
    tokenauth.save("alice", ALICE)
    tokenauth.save("bob", BOB)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "assert os.environ['CLAUDE_CODE_OAUTH_TOKEN'] == os.environ['EXPECTED_FIXTURE_TOKEN']\n"
        "assert sys.argv[1:] == ['-p', 'hello world']\n"
        "print('fixture authenticated')\n"
    )
    fake_claude.chmod(0o700)
    launcher = fake_bin / "cauth"
    launcher.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(Path(cauth.__file__).parent)!r})\nimport cauth\nraise SystemExit(cauth.main())\n")
    launcher.chmod(0o700)
    env = os.environ.copy()
    env.update(HOME=str(cauth.CONFIG_DIR.parent.parent), PATH=str(fake_bin) + os.pathsep + env["PATH"])
    # Activate once, then switch A/B/A in the SAME shell. No restart or second eval.
    script = f'eval "$(cauth shell-init {shell})"\n'
    for alias, token in (("alice", ALICE), ("bob", BOB), ("alice", ALICE)):
        script += (f'cauth switch {alias} >/dev/null\n'
                   f'export EXPECTED_FIXTURE_TOKEN={token}\n'
                   'claude -p "hello world" || exit $?\n')
    flags = ["--noprofile", "--norc"] if shell == "bash" else ["-f"]
    completed = subprocess.run(
        [shell, *flags, "-c", script], env=env, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ["fixture authenticated"] * 3
    for token in (ALICE, BOB):
        assert token not in completed.stdout + completed.stderr


@pytest.mark.parametrize("size", [(100, 30), (60, 20)])
def test_default_ui_selects_tokens_and_renews_legacy_alias(live_login, size):
    live_login("legacy")
    cauth.register_live_as("legacy")
    tokenauth.save("alice", ALICE)
    tokenauth.save("bob", BOB)
    async def drive():
        app = tokenui.TokenApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()
            assert tokenauth.selected() == ("bob", BOB)
            feedback = str(app.screen.query_one("#token-result").render())
            assert 'eval "$(cauth shell-init bash)"' in feedback
            assert "open a new terminal" in feedback
            await pilot.press("down", "l")
            await pilot.pause()
            assert app.return_value == tokenui.Request("create", "legacy")
    asyncio.run(drive())


@pytest.mark.skipif(os.name != "posix", reason="POSIX terminal fixture")
@pytest.mark.parametrize("paste_style", ["wrapped", "leading_blanks", "bracketed", "bracketed_one_enter", "official_output", "crlf", "carriage_return"])
def test_default_ui_setup_token_handoff_and_hidden_paste(tmp_path, paste_style):
    import fcntl
    import json
    import pty
    import signal
    import struct
    import termios
    from test_login_handoff import _read_until, _drain, _wait_for_child

    repo = Path(cauth.__file__).parent
    fake_home = tmp_path / "terminal-home"
    fake_home.mkdir()
    fake_bin = tmp_path / "terminal-bin"
    fake_bin.mkdir()
    fake = fake_bin / "claude"
    fake.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "assert sys.argv[1:] == ['setup-token']\n"
        "assert os.isatty(0) and os.isatty(1)\n"
        "assert 'CLAUDE_CODE_OAUTH_TOKEN' not in os.environ\n"
        "print('FIXTURE_SETUP_TOKEN_COMPLETE', flush=True)\n"
    )
    fake.chmod(0o700)
    env = os.environ.copy()
    env.update(HOME=str(fake_home), PATH=str(fake_bin) + os.pathsep + env["PATH"], TERM="xterm-256color")
    env.pop("CLAUDE_CONFIG_DIR", None)
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execve(sys.executable, [sys.executable, str(repo / "cauth.py")], env)
        finally:
            os._exit(127)
    output = bytearray()
    reaped = False
    try:
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
        _read_until(master, output, b"Selected:")
        os.write(master, b"a")
        _read_until(master, output, b"Name this long-lived token")
        os.write(master, b"personal\r")
        _read_until(master, output, b"FIXTURE_SETUP_TOKEN_COMPLETE")
        _read_until(master, output, b"then again if needed):")
        pasted = ALICE[:23] + "\n " + ALICE[23:] + "\n"
        if paste_style == "leading_blanks":
            pasted = "\n\n  \n" + pasted
        elif paste_style == "bracketed":
            pasted = "\x1b[200~\n\n" + pasted + "\x1b[201~\n"
        elif paste_style == "bracketed_one_enter":
            pasted = "\x1b[200~" + ALICE + "\x1b[201~"
        elif paste_style == "official_output":
            pasted = ("\x1b[200~This will guide you through long-lived (1-year) auth token setup for your Claude account. Claude subscription required.\n\n"
                      "Long-lived authentication token created successfully!\n\n"
                      "Your OAuth token (valid for 1 year):\n\n " + ALICE[:23] + "\n " + ALICE[23:] +
                      "\n\nStore this token securely. You won't be able to see it again.\n\n"
                      "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>\x1b[201~")
        elif paste_style == "crlf":
            pasted = ALICE[:23] + "\r\n  " + ALICE[23:] + "\r\n"
        elif paste_style == "carriage_return":
            pasted = ALICE[:23] + "\r  " + ALICE[23:] + "\r\r"
        os.write(master, (pasted + "\n").encode())
        _read_until(master, output, b"Saved long-lived token for personal")
        os.write(master, b"\n")
        _drain(master, output, 0.5)
        os.write(master, b"q")
        status = _wait_for_child(pid, master, output)
        reaped = True
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        os.close(master)
        if not reaped:
            try:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
    assert ALICE.encode() not in output
    assert ALICE[23:].encode() not in output
    assert b"Received hidden text" in output
    # Activation guidance must survive Textual clearing its alternate screen.
    after_ui = output.rsplit(b"\x1b[?1049l", 1)[-1]
    assert b'eval "$(cauth shell-init bash)"' in after_ui
    assert b"REFRESH THIS TERMINAL ONCE" in after_ui
    assert b"Use claude or your usual shortcut" in after_ui
    assert b"paste was incomplete" not in output
    store = fake_home / ".config" / "claude-oauth" / "tokens.json"
    assert json.loads(store.read_text())["accounts"]["personal"]["token"] == ALICE
    assert not (store.parent / ".pending-login.json").exists()
    assert not (fake_home / ".claude" / ".credentials.json").exists()


def test_wrapped_paste_is_joined_and_invalid_paste_retries_without_reauthorization(monkeypatch, capsys):
    attempts = iter(["only-a-tail", ALICE[:25] + "\n  " + ALICE[25:]])
    monkeypatch.setattr(tokenauth, "_read_token_paste", lambda: next(attempts))
    calls = []
    monkeypatch.setattr(tokenauth.subprocess, "call", lambda argv, **kw: calls.append(argv) or 0)
    tokenauth.login("alice")
    assert calls == [["claude", "setup-token"]]
    assert tokenauth.selected() == ("alice", ALICE)
    output = capsys.readouterr().out
    assert "do not need to authorize again" in output
    assert ALICE not in output
    assert "only-a-tail" not in output


def test_windows_reader_ignores_leading_blank_lines(monkeypatch, capsys):
    chars = iter("\r\n  \r\n" + ALICE[:23] + "\r\n  " + ALICE[23:] + "\r\n\r\n")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(chars)))
    assert tokenauth.re.sub(r"\s+", "", tokenauth._read_windows_paste()) == ALICE
    assert ALICE not in capsys.readouterr().out


@pytest.mark.parametrize("separator", ["\r\n  ", "\r  ", "\n  "])
def test_hidden_reader_keeps_tail_after_wrapped_line_break(separator):
    chars = iter(ALICE[:23] + separator + ALICE[23:] + "\r\r")
    pasted = tokenauth._collect_hidden_input(lambda: next(chars, ""), lambda message: None)
    assert tokenauth.re.sub(r"\s+", "", pasted) == ALICE


@pytest.mark.parametrize("shell,filename", [("bash", ".bashrc"), ("zsh", ".zshrc")])
def test_token_operations_install_and_repair_shell_integration(monkeypatch, shell, filename):
    monkeypatch.setenv("SHELL", f"/bin/{shell}")
    target = Path.home() / filename
    original = "export EDITOR=vim\n"
    target.write_text(original)
    notice = tokenauth.save("alice", ALICE)
    assert "open a new terminal" in notice
    assert f'eval "$(cauth shell-init {shell})"' in notice
    assert "cannot refresh its parent terminal" in notice
    installed = target.read_bytes()
    backups = list(target.parent.glob(filename + ".cauth-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == original
    if os.name == "posix":
        assert backups[0].stat().st_mode & 0o777 == 0o600
    notice = tokenauth.save("bob", BOB)
    assert "open a new terminal" in notice
    assert tokenauth.selected() == ("alice", ALICE)
    assert target.read_bytes() == installed
    assert list(target.parent.glob(filename + ".cauth-backup-*")) == backups
    target.write_text(original)
    notice = tokenauth.save("alice", ALICE + "-renewed", "setup-token")
    assert target.read_bytes() == installed
    assert f'eval "$(cauth shell-init {shell})"' in notice
    target.write_text(original)
    notice = tokenauth.select("bob")
    assert f'eval "$(cauth shell-init {shell})"' in notice
    assert target.read_bytes() == installed
    assert tokenauth.selected() == ("bob", BOB)
    assert b"sk-ant-" not in installed


def test_quitting_ui_keeps_activation_instructions_in_terminal(monkeypatch, capsys):
    monkeypatch.setattr(tokenui.TokenApp, "run", lambda self: None)
    assert tokenui.run_tui() == 0
    output = capsys.readouterr().out
    assert "REFRESH THIS TERMINAL ONCE" in output
    assert "Use claude or your usual shortcut" in output
    assert 'eval "$(cauth shell-init bash)"' in output
    assert "open a new terminal" in output
    assert "no terminal refresh is needed" in output


def test_shell_setup_failure_retains_token_and_reports_cli_retry(capsys):
    target = Path.home() / ".bashrc"
    target.write_text("# >>> Claude Cauth OAuth >>>\n# incomplete block\n")
    notice = tokenauth.save("alice", ALICE)
    assert "shell setup failed" in notice
    assert tokenauth.selected() == ("alice", ALICE)
    assert cauth.main(["switch", "alice"]) == 0
    output = capsys.readouterr().out
    assert "cauth install-shell bash" in output
    assert ALICE not in output
    assert target.read_text().endswith("# incomplete block\n")


def test_unsupported_shell_keeps_token_and_provides_manual_setup(monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    notice = tokenauth.save("alice", ALICE)
    assert "cauth shell-init" in notice
    assert tokenauth.selected() == ("alice", ALICE)
    assert not (Path.home() / ".bashrc").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX shell executable fixture")
def test_installed_function_routes_arbitrary_shortcuts_without_rewriting_them(tmp_path):
    launcher = tmp_path / "cauth"
    launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    launcher.chmod(0o700)
    target = Path.home() / ".bashrc"
    original = ("shopt -s expand_aliases\nalias my_ai='claude --verbose'\n"
                'work_ai() { claude --model sonnet "$@"; }\n'
                "alias claude='false'\n")
    target.write_text(original)
    tokenauth.save("alice", ALICE)
    assert target.read_text().startswith(original)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c",
         f'source "{target}"\nmy_ai hello\nwork_ai world'],
        env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "run", "--", "--verbose", "hello", "run", "--", "--model", "sonnet", "world"]
