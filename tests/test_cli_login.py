"""Browser renewal from the CLI must preserve the parked account until success."""

from __future__ import annotations

import io
import subprocess

import pytest

import cauth
from conftest import make_credentials, make_logged_out_credentials, make_oauth_account


@pytest.fixture(autouse=True)
def fixture_renewal_probe(monkeypatch):
    monkeypatch.setattr(
        cauth, "run_claude_probe", lambda: cauth.ClaudeProbeResult(0, "CAUTH_OK")
    )


def test_masked_authorization_code_keeps_a_hidden_middle_and_escapes_controls():
    assert cauth.masked_authorization_code("abcd-fixture-wxyz") == "abcd...wxyz"
    assert cauth.masked_authorization_code("12345678") == "(too short to preview)"
    assert cauth.masked_authorization_code("\x1b[31fixture-tail") == "\\x1b[31...tail"


def test_authorization_code_reader_refuses_getpass_visible_fallback(monkeypatch):
    def visible_fallback(_prompt):
        cauth.warnings.warn("fixture fallback", cauth.getpass.GetPassWarning)
        return "fixture-code-that-must-not-echo"

    monkeypatch.setattr(cauth.getpass, "getpass", visible_fallback)
    with pytest.raises(EOFError, match="cannot hide"):
        cauth._read_authorization_code()


def test_run_claude_login_forwards_code_and_prints_only_masked_preview(
    monkeypatch, capfd
):
    seen = {}

    class RecordingStdin:
        def __init__(self):
            self.payload = bytearray()

        def write(self, data):
            self.payload.extend(data)

        def flush(self):
            pass

        def close(self):
            pass

    class FakeProcess:
        def __init__(self):
            self.stdin = RecordingStdin()
            self.stdout = io.BytesIO(
                b"Opening browser\nPaste code here if prompted > Login complete\n"
            )

        def wait(self):
            return 23

    process = FakeProcess()

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        seen["stdin"] = kwargs["stdin"]
        seen["stdout"] = kwargs["stdout"]
        seen["stderr"] = kwargs["stderr"]
        return process

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FIXTURE")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-FIXTURE")
    monkeypatch.setattr(cauth.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        cauth, "_read_authorization_code", lambda: "abcd-fixture-secret-wxyz"
    )

    assert cauth.run_claude_login("alice@example.test") == 23
    assert seen["argv"] == [
        "claude",
        "auth",
        "login",
        "--email",
        "alice@example.test",
    ]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen["env"]
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert seen["stdin"] is subprocess.PIPE
    assert seen["stdout"] is subprocess.PIPE
    assert seen["stderr"] is subprocess.STDOUT
    assert bytes(process.stdin.payload) == b"abcd-fixture-secret-wxyz\n"

    output = capfd.readouterr().out
    assert "Code received: abcd...wxyz" in output
    assert "abcd-fixture-secret-wxyz" not in output


@pytest.mark.parametrize(
    "error,expected",
    [
        (FileNotFoundError(), 127),
        (OSError("cannot execute"), 126),
        (KeyboardInterrupt(), 130),
    ],
)
def test_run_claude_login_turns_launch_failures_into_status_codes(
    monkeypatch, error, expected
):
    def fail(_argv, **_kwargs):
        raise error

    monkeypatch.setattr(cauth.subprocess, "Popen", fail)
    assert cauth.run_claude_login() == expected


def test_reauthenticate_replaces_a_dead_profile_only_after_success(
    live_login, monkeypatch
):
    live_login("alice", access="old", refresh="old")
    cauth.register_live_as("alice")
    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, make_logged_out_credentials())

    def successful_login(email):
        assert email == "alice@example.test"
        cauth.atomic_write_json(
            cauth.LIVE_CREDENTIALS,
            make_credentials("alice", access="new", refresh="new"),
        )
        cauth.atomic_write_json(
            cauth.LIVE_CLAUDE_JSON,
            {"oauthAccount": make_oauth_account("alice")},
        )
        return 0

    monkeypatch.setattr(cauth, "run_claude_login", successful_login)

    cauth.reauthenticate("alice")

    stored, _ = cauth.profile_paths("alice")
    payload = cauth.read_json(stored)["claudeAiOauth"]
    assert payload["accessToken"].endswith("-new")
    assert payload["refreshToken"].endswith("-new")
    assert cauth.load_accounts()["active"] == "alice"


def test_failed_reauthentication_restores_the_previous_live_account(
    live_login, monkeypatch
):
    live_login("alice", access="safe", refresh="safe")
    cauth.register_live_as("alice")

    def failed_login(_email):
        cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, make_logged_out_credentials())
        return 9

    monkeypatch.setattr(cauth, "run_claude_login", failed_login)

    with pytest.raises(cauth.CauthError, match="status 9"):
        cauth.reauthenticate("alice")

    payload = cauth.read_json(cauth.LIVE_CREDENTIALS)["claudeAiOauth"]
    assert payload["accessToken"].endswith("-safe")
    assert payload["refreshToken"].endswith("-safe")
    assert cauth.load_accounts()["active"] == "alice"


def test_wrong_account_during_renewal_does_not_overwrite_the_alias(
    live_login, monkeypatch
):
    live_login("alice", access="safe", refresh="safe")
    cauth.register_live_as("alice")
    stored, _ = cauth.profile_paths("alice")
    before = stored.read_bytes()

    def wrong_login(_email):
        live_login("bob", access="wrong", refresh="wrong")
        return 0

    monkeypatch.setattr(cauth, "run_claude_login", wrong_login)

    with pytest.raises(cauth.CauthError, match="not the account stored under 'alice'"):
        cauth.reauthenticate("alice")

    assert stored.read_bytes() == before
    assert cauth.load_accounts()["active"] == "alice"
    assert cauth.read_live_state().email == "alice@example.test"


def test_renewal_checks_target_then_returns_to_previous_account(
    live_login, monkeypatch
):
    for name in ("alice", "bob"):
        live_login(name)
        cauth.register_live_as(name)
    cauth.switch_to("alice")

    def login(email):
        assert email == "bob@example.test"
        live_login("bob", access="renewed", refresh="renewed")
        return 0

    def probe():
        assert cauth.account_status()["installed"] == "bob"
        return cauth.ClaudeProbeResult(0, "CAUTH_OK")

    monkeypatch.setattr(cauth, "run_claude_login", login)
    monkeypatch.setattr(cauth, "run_claude_probe", probe)
    assert cauth.reauthenticate("bob").ok
    assert cauth.account_status()["installed"] == "alice"
    assert cauth.load_accounts()["accounts"]["bob"]["last_check"]["kind"] == "verified"
    assert cauth.pending_login() is None
