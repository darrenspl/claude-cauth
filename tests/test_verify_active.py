"""Verify checks the live login with a real Claude request and writes nothing."""

from __future__ import annotations

import asyncio

import pytest

import cauth

textual = pytest.importorskip("textual", reason="TUI tests need Textual installed")

import tui  # noqa: E402


DAY_MS = 24 * 60 * 60 * 1000


def test_token_deadlines_report_expiry_state(live_login):
    live_login("alice")
    now = 1_000 * DAY_MS
    payload = cauth.read_json(cauth.LIVE_CREDENTIALS)
    payload["claudeAiOauth"]["expiresAt"] = now - DAY_MS
    payload["claudeAiOauth"]["refreshTokenExpiresAt"] = now + 10 * DAY_MS
    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, payload)

    access, renewal = cauth.token_deadlines(now_ms=now)

    assert access.startswith("access token: EXPIRED")
    assert renewal.startswith("login renewal: valid for 10 days")


def test_validate_active_reports_identity_and_probe_verdict(live_login, monkeypatch):
    live_login("alice")
    cauth.register_live_as("alice")
    monkeypatch.setattr(
        cauth,
        "run_claude_probe",
        lambda timeout_seconds=90: cauth.ClaudeProbeResult(0, "CAUTH_OK"),
    )

    report = cauth.validate_active()

    assert report.ok
    assert report.alias == "alice"
    assert report.email == "alice@example.test"
    assert len(report.deadlines) == 2


def test_validate_active_does_not_touch_the_live_files(live_login, monkeypatch):
    live_login("alice")
    cauth.register_live_as("alice")
    before = (
        cauth.LIVE_CREDENTIALS.read_text(),
        cauth.LIVE_CLAUDE_JSON.read_text(),
    )
    monkeypatch.setattr(
        cauth,
        "run_claude_probe",
        lambda timeout_seconds=90: cauth.ClaudeProbeResult(1, "OAuth token has expired"),
    )

    report = cauth.validate_active()

    assert not report.ok
    assert (
        cauth.LIVE_CREDENTIALS.read_text(),
        cauth.LIVE_CLAUDE_JSON.read_text(),
    ) == before

    assert cauth.account_status()["last_check"]["kind"] == "sign_in_required"


async def wait_for(screen, marker: str) -> str:
    for _ in range(200):
        rendered = str(screen.query_one("#verify-status", tui.Static).content)
        if marker in rendered:
            return rendered
        await asyncio.sleep(0.01)
    raise AssertionError(f"did not see {marker!r} in the verify status")


def test_verify_screen_shows_the_failed_probe_output(live_login, monkeypatch):
    live_login("alice")
    cauth.register_live_as("alice")
    monkeypatch.setattr(
        cauth,
        "run_claude_probe",
        lambda timeout_seconds=90: cauth.ClaudeProbeResult(1, "OAuth token has expired"),
    )
    app = tui.CauthApp()

    async def drive() -> None:
        async with app.run_test() as pilot:
            screen = tui.VerifyScreen()
            app.push_screen(screen)
            await pilot.pause()
            rendered = await wait_for(screen, "FAIL")
            assert "OAuth token has expired" in rendered
            assert "alice" in rendered

    asyncio.run(drive())
