"""The real Switch screen must show the Claude probe result before success."""

from __future__ import annotations

import asyncio

import pytest

textual = pytest.importorskip("textual", reason="TUI tests need Textual installed")

import cauth  # noqa: E402
import tui  # noqa: E402


async def wait_for_result(screen: tui.SwitchScreen, marker: str) -> str:
    for _ in range(200):
        status = screen.query_one("#probe-status", tui.Static)
        rendered = str(status.content)
        if marker in rendered:
            return rendered
        await asyncio.sleep(0.01)
    raise AssertionError(f"did not see {marker!r} in probe status")


def seed_two_accounts(live_login) -> None:
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")


def test_switch_screen_shows_pass_only_after_probe(live_login, monkeypatch):
    seed_two_accounts(live_login)

    def successful_probe():
        assert cauth.read_live_state().email == "bob@example.test"
        return cauth.ClaudeProbeResult(returncode=0, output="CAUTH_OK")

    monkeypatch.setattr(cauth, "run_claude_probe", successful_probe)
    app = tui.CauthApp()

    async def drive() -> None:
        async with app.run_test() as pilot:
            screen = tui.SwitchScreen()
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("down", "enter")
            rendered = await wait_for_result(screen, "PASS")
            assert "bob" in rendered
            assert "CAUTH_OK" in rendered
            assert cauth.load_accounts()["active"] == "bob"

    asyncio.run(drive())


def test_switch_screen_shows_failure_and_rollback(live_login, monkeypatch):
    seed_two_accounts(live_login)

    def failed_probe():
        assert cauth.read_live_state().email == "bob@example.test"
        return cauth.ClaudeProbeResult(
            returncode=1,
            output="OAuth session expired and could not be refreshed",
        )

    monkeypatch.setattr(cauth, "run_claude_probe", failed_probe)
    app = tui.CauthApp()

    async def drive() -> None:
        async with app.run_test() as pilot:
            screen = tui.SwitchScreen()
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("down", "enter")
            rendered = await wait_for_result(screen, "FAIL")
            assert "OAuth session expired" in rendered
            assert "restored 'alice'" in rendered
            assert "press l to renew bob" in rendered
            assert cauth.load_accounts()["active"] == "alice"

    asyncio.run(drive())
