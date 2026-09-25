"""A file swap is not successful until Claude accepts the selected login."""

from __future__ import annotations

from types import SimpleNamespace

import cauth
from conftest import make_logged_out_credentials


def test_claude_probe_is_a_real_bounded_print_request(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="CAUTH_OK\n", stderr="")

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FIXTURE")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-FIXTURE")
    monkeypatch.setattr(cauth.subprocess, "run", fake_run)

    result = cauth.run_claude_probe(timeout_seconds=17)

    assert result.ok
    assert result.returncode == 0
    assert result.output == "CAUTH_OK"
    assert cauth.Path(seen["argv"][0]).stem.lower() == "claude"
    assert seen["argv"][1] == "-p"
    assert "--model" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--model") + 1] == "haiku"
    assert "--tools" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--tools") + 1] == ""
    assert "--no-session-persistence" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--mcp-config") + 1] == (
        '{"mcpServers":{}}'
    )
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["timeout"] == 17
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen["kwargs"]["env"]
    assert "ANTHROPIC_API_KEY" not in seen["kwargs"]["env"]


def test_claude_probe_preserves_the_error_but_redacts_tokens(monkeypatch):
    def fake_run(_argv, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "OAuth session expired and could not be refreshed "
                "sk-ant-oat01-FIXTURE-public-test-1\n"
            ),
        )

    monkeypatch.setattr(cauth.subprocess, "run", fake_run)

    result = cauth.run_claude_probe()

    assert not result.ok
    assert "OAuth session expired" in result.output
    assert "NEVER-SHOW-THIS" not in result.output
    assert "[REDACTED_TOKEN]" in result.output


def test_failed_probe_restores_previous_alias_and_preserves_target_profile(
    live_login, monkeypatch
):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")
    bob_credentials, _ = cauth.profile_paths("bob")
    bob_before = bob_credentials.read_bytes()

    def failed_probe():
        assert cauth.read_live_state().email == "bob@example.test"
        cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, make_logged_out_credentials())
        return cauth.ClaudeProbeResult(
            returncode=1,
            output="OAuth session expired and could not be refreshed",
        )

    monkeypatch.setattr(cauth, "run_claude_probe", failed_probe)

    outcome = cauth.switch_with_probe("bob")

    assert not outcome.ok
    assert outcome.previous_alias == "alice"
    assert outcome.restored_alias == "alice"
    assert outcome.rollback_error is None
    assert cauth.load_accounts()["active"] == "alice"
    assert cauth.read_live_state().email == "alice@example.test"
    assert bob_credentials.read_bytes() == bob_before


def test_successful_probe_keeps_selected_alias_active(live_login, monkeypatch):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    monkeypatch.setattr(
        cauth,
        "run_claude_probe",
        lambda: cauth.ClaudeProbeResult(returncode=0, output="CAUTH_OK"),
    )

    outcome = cauth.switch_with_probe("bob")

    assert outcome.ok
    assert outcome.restored_alias is None
    assert cauth.load_accounts()["active"] == "bob"
    assert cauth.read_live_state().email == "bob@example.test"
