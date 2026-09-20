"""UI lifecycle failures must never dump locals or trigger a pending login."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys

import cauth
import tokenauth
import tokenui
import tui


def test_resume_before_mount_is_safe():
    screen = tokenui.TokenHome()
    screen.on_screen_resume()
    assert not screen._refresh_scheduled


def test_resume_while_list_detached_is_safe():
    async def drive():
        app = tokenui.TokenApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await screen.query_one("#tokens", tokenui.ListView).remove()
            screen.on_screen_resume()
            await pilot.pause()
            assert app._exception is None
    asyncio.run(drive())


def test_alias_modal_handoff_with_existing_accounts_does_not_refresh_closing_screen(live_login):
    for alias in ("legacy-a", "legacy-b", "legacy-c"):
        live_login(alias)
        cauth.register_live_as(alias)
    tokenauth.save("token-account", "sk-ant-oat01-FIXTURE-hidden-token")

    async def drive():
        for _ in range(3):
            app = tokenui.TokenApp()
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("a")
                app.screen.query_one("#alias", tui.Input).value = "new-account"
                await pilot.press("enter")
                await pilot.pause()
                assert app.return_value == tokenui.Request("create", "new-account")
                assert app.closing
                assert app._exception is None
    asyncio.run(drive())


def test_failed_app_never_dispatches_login(monkeypatch):
    class FailedApp:
        return_code = 1

        def run(self):
            return tokenui.Request("create", "fixture")

    def forbidden(*args, **kwargs):
        raise AssertionError("login must not start after a UI error")
    monkeypatch.setattr(tokenui, "TokenApp", FailedApp)
    monkeypatch.setattr(tokenauth, "login", forbidden)
    assert tokenui.run_tui() == 1


def test_fatal_ui_error_logs_locations_without_traceback_or_locals(tmp_path):
    env = os.environ.copy()
    env["HOME"] = str(cauth.CONFIG_DIR.parent.parent)
    script = '''
import tokenui
class BrokenApp(tokenui.TokenApp):
    def on_mount(self):
        secret = "sk-ant-oat01-FIXTURE-private-error-value"
        self._return_value = tokenui.Request("create", "fixture")
        raise RuntimeError(secret)
    def run(self):
        return super().run(headless=True)
tokenui.TokenApp = BrokenApp
raise SystemExit(tokenui.run_tui())
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(cauth.__file__).parent,
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "internal error" in output
    assert "Traceback" not in output
    assert "private-error-value" not in output
    assert "Claude will create" not in output
    logs = cauth.diagnostics.read_recent()
    assert "RuntimeError" in logs
    assert "on_mount" in logs
    assert "private-error-value" not in logs
