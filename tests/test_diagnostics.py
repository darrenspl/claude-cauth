"""Diagnostics must explain failures without ever recording user-supplied secrets."""

import json
import os
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

import cauth
import diagnostics
import tokenauth


TOKEN = "sk-ant-oat01-FIXTURE-do-not-log-this-token"
FRAGMENT = "fixture-unprefixed-private-paste"


def records():
    return [json.loads(line) for line in diagnostics.read_recent().splitlines()]


def test_login_retry_and_save_have_correlated_secret_free_diagnostics(monkeypatch):
    pastes = iter([FRAGMENT, TOKEN[:20] + "\n" + TOKEN[20:]])
    monkeypatch.setattr(tokenauth, "_read_token_paste", lambda: next(pastes))
    monkeypatch.setattr(tokenauth.subprocess, "call", lambda *a, **kw: 0)
    tokenauth.login("private-account-alias")
    events = records()
    names = [item["event"] for item in events]
    assert "token.generator_exit" in names
    assert "token.paste_rejected" in names
    assert "token.save.complete" in names
    assert names[-1] == "token.login.complete"
    assert len({item["operation"] for item in events}) == 1
    assert all(item["operation"] and item["timestamp"] and item["session"] for item in events)
    assert next(item for item in events if item["event"] == "token.paste")["has_prefix"] is False
    raw = diagnostics.read_recent()
    for secret in (TOKEN, TOKEN[20:], FRAGMENT, "private-account-alias"):
        assert secret not in raw


def test_exception_text_locals_and_command_prompt_are_not_logged(monkeypatch):
    tokenauth.save("alias", TOKEN)
    def fail(*args, **kw):
        raise PermissionError(13, FRAGMENT + TOKEN)
    monkeypatch.setattr(tokenauth.subprocess, "call", fail)
    with pytest.raises(PermissionError):
        tokenauth.run(["-p", FRAGMENT])
    raw = diagnostics.read_recent()
    assert FRAGMENT not in raw and TOKEN not in raw
    error = next(item for item in records() if item["event"] == "operation.error")
    assert error["error_type"] == "PermissionError" and error["errno"] == 13
    assert error["frames"][-1]["function"] == "fail"


def test_event_fields_are_allowlisted():
    diagnostics.event("token.paste", token=TOKEN, output=FRAGMENT, reason=FRAGMENT,
                      characters=17, has_prefix=False, duration_ms=TOKEN)
    entry = records()[0]
    assert entry["characters"] == 17
    assert "token" not in entry and "output" not in entry
    assert "reason" not in entry and "duration_ms" not in entry
    assert TOKEN not in diagnostics.read_recent() and FRAGMENT not in diagnostics.read_recent()


def test_environment_logs_presence_without_values(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", FRAGMENT)
    tokenauth.environment()
    entry = records()[-1]
    assert entry["oauth_override"] and entry["endpoint_override"]
    assert TOKEN not in diagnostics.read_recent() and FRAGMENT not in diagnostics.read_recent()


def test_rotation_permissions_and_retention(monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 1)
    for number in range(8):
        diagnostics.event("test.event", characters=number)
    target = diagnostics.path()
    logs = sorted(target.parent.glob("diagnostics.jsonl*"))
    assert len(logs) == 4
    assert [item["characters"] for item in records()] == [4, 5, 6, 7]
    if os.name == "posix":
        assert target.parent.stat().st_mode & 0o777 == 0o700
        assert all(log.stat().st_mode & 0o777 == 0o600 for log in logs)


def test_logging_failure_does_not_block_token_operations(monkeypatch, capsys):
    monkeypatch.setattr(diagnostics, "_warned", False)
    def fail():
        raise PermissionError("fixture log failure")
    monkeypatch.setattr(diagnostics, "_locked", fail)
    tokenauth.save("alice", TOKEN)
    assert tokenauth.selected() == ("alice", TOKEN)
    assert "diagnostic logging is unavailable" in capsys.readouterr().err


def test_logs_cli_is_read_only_and_shows_recent_events(capsys):
    assert cauth.main(["logs", "--path"]) == 0
    assert str(diagnostics.path()) in capsys.readouterr().out
    assert not cauth.CONFIG_DIR.exists()
    diagnostics.event("test.event")
    assert cauth.main(["logs"]) == 0
    assert '"event":"test.event"' in capsys.readouterr().out


def test_concurrent_threads_write_valid_complete_events():
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda number: diagnostics.event("test.event", characters=number), range(100)))
    assert sorted(item["characters"] for item in records()) == list(range(100))


@pytest.mark.skipif(os.name != "posix", reason="POSIX subprocess lock fixture")
def test_concurrent_processes_keep_all_events(tmp_path):
    env = os.environ.copy()
    env["HOME"] = str(cauth.CONFIG_DIR.parent.parent)
    script = "import diagnostics; [diagnostics.event('test.event') for _ in range(30)]"
    processes = [subprocess.Popen([sys.executable, "-c", script], cwd=Path(cauth.__file__).parent, env=env) for _ in range(3)]
    assert all(process.wait(timeout=10) == 0 for process in processes)
    assert len(records()) == 90
