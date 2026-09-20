"""Explicit cleanup must work through damaged login state without restoring tokens."""

import asyncio

import pytest

import cauth
import tui


def test_reset_removes_damaged_profiles_and_recovery_but_keeps_settings(live_login):
    for alias in ("alice", "bob"):
        live_login(alias)
        cauth.register_live_as(alias)
        cauth.sync_back(alias)
    cauth.begin_login()
    settings = cauth.read_json(cauth.LIVE_CLAUDE_JSON)
    settings["projects"] = {"fixture-project": {"trusted": True}}
    cauth.atomic_write_json(cauth.LIVE_CLAUDE_JSON, settings)
    # Neither a corrupt index nor a corrupt journal may prevent an explicit reset.
    for path in (
        cauth.ACCOUNTS_JSON,
        cauth.CONFIG_DIR / ".transaction.json",
        cauth.CONFIG_DIR / ".pending-login.json",
        cauth.CONFIG_DIR / "accounts.pre-rebuild-123.bak",
        cauth.profile_paths("orphan")[0],
    ):
        path.write_text("{")
    unrelated = cauth.PROFILES_DIR / "notes.txt"
    unrelated.write_text("keep")

    assert cauth.main(["reset", "--yes"]) == 0
    assert cauth.load_accounts() == {"active": None, "accounts": {}}
    assert not cauth.LIVE_CREDENTIALS.exists()
    assert cauth.read_json(cauth.LIVE_CLAUDE_JSON) == {
        key: value for key, value in settings.items() if key != "oauthAccount"
    }
    assert set(cauth.PROFILES_DIR.iterdir()) == {unrelated}
    assert set(p.name for p in cauth.CONFIG_DIR.iterdir()) == {
        "profiles", "accounts.json", ".operation.lock", "logs"
    }
    cauth.reset_logins()  # Repeated cleanup is harmless.
    cauth.begin_login()  # A fresh browser login is no longer blocked.


def test_clear_unfinished_login_keeps_new_credentials_and_saved_accounts(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    cauth.begin_login()
    live_login("bob")
    before = cauth.snapshot_files(cauth.managed_files())
    (cauth.CONFIG_DIR / ".pending-login.json").write_text("{")
    assert cauth.main(["clear-login", "--yes"]) == 0
    assert cauth.pending_login() is None
    assert cauth.snapshot_files(cauth.managed_files()) == before
    assert cauth.read_live_state().email == "bob@example.test"


@pytest.mark.parametrize("command", ["reset", "clear-login"])
@pytest.mark.parametrize("args", [[], ["alice"], ["--yes", "extra"]])
def test_cleanup_requires_explicit_cli_confirmation(live_login, command, args):
    live_login("alice")
    cauth.register_live_as("alice")
    cauth.begin_login()
    before = cauth.snapshot_files(cauth.managed_files())
    assert cauth.main([command, *args]) == 2
    assert cauth.snapshot_files(cauth.managed_files()) == before
    assert cauth.pending_login() is not None


def test_reset_preserves_unreadable_claude_settings_and_all_logins(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    cauth.LIVE_CLAUDE_JSON.write_text("{")
    before = cauth.snapshot_files(cauth.managed_files())
    assert cauth.main(["reset", "--yes"]) == 1
    assert cauth.snapshot_files(cauth.managed_files()) == before


def test_reset_can_retry_after_partial_delete(live_login, monkeypatch):
    live_login("alice")
    cauth.register_live_as("alice")
    cauth.begin_login()
    target = cauth.profile_paths("alice")[0]
    original = cauth.Path.unlink

    def fail_once(path, *args, **kwargs):
        if path == target:
            raise PermissionError("fixture blocked file")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(cauth.Path, "unlink", fail_once)
        assert cauth.main(["reset", "--yes"]) == 1
    cauth.reset_logins()
    assert not target.exists()
    assert cauth.pending_login() is None
    assert cauth.load_accounts()["accounts"] == {}


@pytest.mark.parametrize("action", ["clear-login", "reset-logins"])
def test_recovery_cleanup_confirmation_and_cancel(live_login, action):
    live_login("alice")
    cauth.register_live_as("alice")
    cauth.begin_login()
    before = cauth.snapshot_files(cauth.managed_files())

    async def drive():
        app = tui.CauthApp()
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.press("d")
            screen = app.screen
            button = screen.query_one("#" + action, tui.Button)
            button.focus()
            await pilot.pause()
            await pilot.press("enter")
            assert isinstance(app.screen, tui.ConfirmModal)
            await pilot.press("escape")
            assert cauth.snapshot_files(cauth.managed_files()) == before
            assert cauth.pending_login() is not None
            button.focus()
            await pilot.press("enter", "y")
            await pilot.pause()
            assert cauth.pending_login() is None
            if action == "reset-logins":
                assert not cauth.LIVE_CREDENTIALS.exists()
                assert cauth.load_accounts()["accounts"] == {}
            else:
                assert cauth.snapshot_files(cauth.managed_files()) == before

    asyncio.run(drive())
