"""Home-screen facts: tier rendering and the env vars that defeat the swap."""

from __future__ import annotations

import pytest

import cauth
from conftest import make_logged_out_credentials


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("default_claude_max_20x", "Max 20x"),
        ("default_claude_max_5x", "Max 5x"),
        ("default_claude_pro", "Pro"),
        ("some_pro_variant", "Pro"),
        ("default_claude_free", "Free"),
        ("", "unknown"),
        (None, "unknown"),
        ("something_unmapped", "something_unmapped"),
    ],
)
def test_tier_labels(raw, expected):
    assert cauth.LiveState(email=None, tier_raw=raw, subscription=None).tier == expected


def test_read_live_state_prefers_the_identity_block(live_login):
    live_login("alice")
    state = cauth.read_live_state()
    assert state.email == "alice@example.test"
    assert state.tier == "Max 20x"
    assert state.subscription == "max"


def test_read_live_state_survives_a_missing_login(isolated_store):
    state = cauth.read_live_state()
    assert state.email is None
    assert state.tier == "unknown"


def test_logged_out_oauth_skeleton_warns_even_when_identity_remains(live_login):
    live_login("alice")
    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, make_logged_out_credentials())

    warnings = cauth.auth_state_warnings()

    assert len(warnings) == 1
    assert "logged out" in warnings[0]
    assert cauth.read_live_state().email == "alice@example.test"


def test_missing_linux_credentials_warn_when_identity_remains(live_login, monkeypatch):
    live_login("alice")
    cauth.LIVE_CREDENTIALS.unlink()
    monkeypatch.setattr(cauth.sys, "platform", "linux")

    warnings = cauth.auth_state_warnings()

    assert len(warnings) == 1
    assert "missing" in warnings[0]
    assert "logged out" in warnings[0]


def test_missing_macos_credentials_defers_to_keychain_notice(live_login, monkeypatch):
    live_login("alice")
    cauth.LIVE_CREDENTIALS.unlink()
    monkeypatch.setattr(cauth.sys, "platform", "darwin")

    assert cauth.auth_state_warnings() == []


def test_valid_oauth_credentials_have_no_auth_state_warning(live_login):
    live_login("alice")
    assert cauth.auth_state_warnings() == []


def test_expired_access_token_directs_user_to_tui_probe(live_login):
    live_login("alice")
    payload = cauth.read_json(cauth.LIVE_CREDENTIALS)
    payload["claudeAiOauth"]["expiresAt"] = 1_000
    payload["claudeAiOauth"]["refreshTokenExpiresAt"] = 10_000
    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, payload)

    warnings = cauth.auth_state_warnings(active_alias="alice", now_ms=5_000)

    assert len(warnings) == 1
    assert "access token has expired" in warnings[0]
    assert "Press v" in warnings[0]
    assert "Claude request" in warnings[0]
    assert "cauth login alice" in warnings[0]


def test_expired_refresh_deadline_requires_browser_renewal(live_login):
    live_login("alice")
    payload = cauth.read_json(cauth.LIVE_CREDENTIALS)
    payload["claudeAiOauth"]["expiresAt"] = 1_000
    payload["claudeAiOauth"]["refreshTokenExpiresAt"] = 2_000
    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, payload)

    warnings = cauth.auth_state_warnings(active_alias="alice", now_ms=5_000)

    assert len(warnings) == 1
    assert "login renewal has expired" in warnings[0]
    assert "cauth login alice" in warnings[0]


def test_refresh_deadline_warns_three_days_before_renewal(live_login):
    live_login("alice")
    payload = cauth.read_json(cauth.LIVE_CREDENTIALS)
    payload["claudeAiOauth"]["expiresAt"] = 10_000
    payload["claudeAiOauth"]["refreshTokenExpiresAt"] = 5_000 + 2 * 24 * 60 * 60 * 1000
    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, payload)

    warnings = cauth.auth_state_warnings(active_alias="alice", now_ms=5_000)

    assert len(warnings) == 1
    assert "expires in 2 days" in warnings[0]
    assert "cauth login alice" in warnings[0]


def test_oauth_token_env_var_warns(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FIXTURE")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    warnings = cauth.env_conflicts()
    assert len(warnings) == 1
    assert "NO effect" in warnings[0]


def test_api_key_env_var_warns(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-FIXTURE")
    warnings = cauth.env_conflicts()
    assert len(warnings) == 1
    assert "per token" in warnings[0]


def test_no_env_conflicts_is_quiet(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cauth.env_conflicts() == []


def test_both_env_vars_warn_together(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FIXTURE")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-FIXTURE")
    assert len(cauth.env_conflicts()) == 2


def test_file_auth_refuses_an_overriding_setup_token(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FIXTURE")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(cauth.CauthError, match="Exit Cauth"):
        cauth.require_file_auth()


def test_file_auth_allows_credential_file_auth(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cauth.require_file_auth()
