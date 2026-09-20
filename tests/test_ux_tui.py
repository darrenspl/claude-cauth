"""Full user journeys, including cancellation and narrow terminal layouts."""

import asyncio

import pytest
import cauth
import tui


@pytest.fixture(autouse=True)
def fixture_probe(monkeypatch):
    monkeypatch.setattr(
        cauth, "run_claude_probe", lambda **kw: cauth.ClaudeProbeResult(0, "CAUTH_OK")
    )
    monkeypatch.setenv("DISPLAY", ":fixture")
    for key in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDECODE",
        "CLAUDE_CONFIG_DIR",
    ):
        monkeypatch.delenv(key, raising=False)


def test_invalid_name_stays_editable_and_return_destination_survives(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    pending = cauth.begin_login()
    live_login("bob")
    cauth.mark_live_unregistered()

    async def drive():
        app = tui.CauthApp(tui.LoginRequest(pending["return_to"]))
        async with app.run_test(size=(60, 20)) as pilot:
            for _ in range(30):
                await pilot.pause()
                if isinstance(app.screen, tui.AliasInputModal):
                    break
            app.screen.query_one("#alias", tui.Input).value = "client/acme"
            await pilot.press("enter")
            assert isinstance(app.screen, tui.AliasInputModal)
            assert "filename" in str(
                app.screen.query_one("#alias-error", tui.Static).content
            )
            app.screen.query_one("#alias", tui.Input).value = "bob"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, tui.HomeScreen)
            assert cauth.account_status()["installed"] == "alice"
            assert "bob" in cauth.load_accounts()["accounts"]
            assert cauth.pending_login() is None

    asyncio.run(drive())


@pytest.mark.parametrize("previous", ["alice", "bob"])
def test_signing_in_again_replaces_saved_credentials_without_naming(live_login, previous):
    for name in ("alice", "bob"):
        live_login(name, access="old", refresh="old")
        cauth.register_live_as(name)
    cauth.switch_to(previous)
    pending = cauth.begin_login()
    stored, _ = cauth.profile_paths("alice")
    # The saved credential pair can be unusable; matching the identity is enough.
    cauth.atomic_write_json(stored, {})
    live_login("alice", access="renewed", refresh="renewed")
    cauth.mark_live_unregistered()

    async def drive():
        app = tui.CauthApp(tui.LoginRequest(pending["return_to"]))
        async with app.run_test() as pilot:
            for _ in range(30):
                await pilot.pause()
                assert not isinstance(app.screen, tui.AliasInputModal)
                if cauth.pending_login() is None:
                    break
            assert isinstance(app.screen, tui.HomeScreen)
            assert cauth.pending_login() is None
            assert cauth.account_status()["installed"] == previous
            assert set(cauth.load_accounts()["accounts"]) == {"alice", "bob"}
            tokens = cauth.read_json(stored)["claudeAiOauth"]
            assert tokens["accessToken"].endswith("-renewed")
            assert tokens["refreshToken"].endswith("-renewed")
            assert "Updated alice" in app.last_action

    asyncio.run(drive())


def test_store_current_updates_existing_account_without_confirmation(live_login):
    live_login("alice", access="old", refresh="old")
    cauth.register_live_as("alice")
    live_login("alice", access="renewed", refresh="renewed")

    async def drive():
        app = tui.CauthApp()
        async with app.run_test() as pilot:
            await pilot.press("a")
            app.screen._store()
            await pilot.pause()
            assert isinstance(app.screen, tui.HomeScreen)
            stored, _ = cauth.profile_paths("alice")
            assert cauth.read_json(stored)["claudeAiOauth"]["accessToken"].endswith("-renewed")
            assert "Updated alice" in app.last_action

    asyncio.run(drive())


def test_cancel_new_login_restores_previous_identity(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    pending = cauth.begin_login()
    live_login("bob")
    cauth.mark_live_unregistered()

    async def drive():
        app = tui.CauthApp(tui.LoginRequest(pending["return_to"]))
        async with app.run_test() as pilot:
            for _ in range(30):
                await pilot.pause()
                if isinstance(app.screen, tui.AliasInputModal):
                    break
            await pilot.press("escape")
            assert isinstance(app.screen, tui.ConfirmModal)
            await pilot.press("y")
            await pilot.pause()
            assert cauth.account_status()["installed"] == "alice"
            assert cauth.pending_login() is None

    asyncio.run(drive())


def test_corrupt_index_keeps_recovery_and_quit_available():
    cauth.ensure_store()
    cauth.ACCOUNTS_JSON.write_text("{")

    async def drive():
        app = tui.CauthApp()
        async with app.run_test() as pilot:
            await pilot.press("d")
            assert isinstance(app.screen, tui.RecoveryScreen)
            await pilot.press("escape", "q")

    asyncio.run(drive())


@pytest.mark.parametrize("size", [(80, 24), (60, 20), (40, 15)])
def test_add_buttons_fit_width_and_can_be_focused(size):
    async def drive():
        app = tui.CauthApp()
        async with app.run_test(size=size) as pilot:
            await pilot.press("a")
            for ident in ("store", "login", "back"):
                button = app.screen.query_one("#" + ident, tui.Button)
                button.focus()
                await pilot.pause()
                assert button.region.x >= 0
                assert button.region.right <= size[0]
                assert button.region.y >= 1
                assert button.region.bottom <= size[1] - 1

    asyncio.run(drive())


def test_warning_stack_does_not_hide_home_accounts(live_login, monkeypatch):
    live_login("alice")
    cauth.register_live_as("alice")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "FIXTURE")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "FIXTURE")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("DISPLAY")

    async def drive():
        app = tui.CauthApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            region = app.screen.query_one("#accounts").region
            assert region.y < 18 and region.height >= 3
            await pilot.press("question_mark")
            assert isinstance(app.screen, tui.HelpScreen)

    asyncio.run(drive())


def test_alias_is_displayed_literally(live_login):
    live_login("alice")
    cauth.register_live_as("[red]client")

    async def drive():
        app = tui.CauthApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            label = app.screen.query_one("#accounts").query_one(tui.Label)
            assert "[red]client" in label.render().plain

    asyncio.run(drive())


def test_forget_on_tui_does_not_open_rename_dialog(live_login):
    live_login("alice")
    cauth.register_live_as("alice")

    async def drive():
        app = tui.CauthApp()
        async with app.run_test() as pilot:
            await pilot.press("r", "enter")
            assert isinstance(app.screen, tui.ConfirmModal)
            await pilot.press("y")
            await pilot.pause()
            assert cauth.load_accounts()["accounts"] == {}
            assert cauth.live_credentials_have_complete_oauth_tokens()

    asyncio.run(drive())


def test_probe_cancel_requests_cancellation_and_allows_return(live_login, monkeypatch):
    live_login("alice")
    cauth.register_live_as("alice")

    def cancelled(**kw):
        assert cauth._local_operation.cancel_event.wait(5)
        return cauth.ClaudeProbeResult(130, "Check cancelled.")

    monkeypatch.setattr(cauth, "run_claude_probe", cancelled)

    async def drive():
        app = tui.CauthApp()
        async with app.run_test() as pilot:
            await pilot.press("v", "escape")
            for _ in range(30):
                await pilot.pause()
                if not app.screen._probe_running:
                    break
            assert (
                "cancelled"
                in str(
                    app.screen.query_one("#verify-status", tui.Static).content
                ).lower()
            )
            await pilot.press("escape")
            assert isinstance(app.screen, tui.HomeScreen)
            assert cauth.account_status()["last_check"]["kind"] == "cancelled"

    asyncio.run(drive())


def test_failed_switch_keeps_target_selected_for_renewal(live_login, monkeypatch):
    for name in ("alice", "bob"):
        live_login(name)
        cauth.register_live_as(name)
    cauth.switch_to("alice")
    monkeypatch.setattr(
        cauth,
        "run_claude_probe",
        lambda: cauth.ClaudeProbeResult(1, "OAuth session expired"),
    )

    async def drive():
        app = tui.CauthApp()
        async with app.run_test() as pilot:
            await pilot.press("s", "down", "enter")
            for _ in range(30):
                await pilot.pause()
                if not app.screen._probe_running:
                    break
            await pilot.press("escape", "l")
            assert isinstance(app.screen, tui.AddScreen)
            assert app.screen.renew_alias == "bob"
            assert cauth.account_status()["installed"] == "alice"

    asyncio.run(drive())


def test_recovery_resumes_naming_after_restart(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    pending = cauth.begin_login()
    live_login("bob")
    cauth.mark_live_unregistered()
    pending["phase"] = "naming"
    pending["owner"] = -1  # prior fixture process
    cauth.atomic_write_json(cauth.CONFIG_DIR / ".pending-login.json", pending)

    async def drive():
        app = tui.CauthApp()
        async with app.run_test() as pilot:
            await pilot.press("d")
            app.screen._run_resume()
            for _ in range(30):
                await pilot.pause()
                if isinstance(app.screen, tui.AliasInputModal):
                    break
            assert isinstance(app.screen, tui.AliasInputModal)
            app.screen.query_one("#alias", tui.Input).value = "bob"
            await pilot.press("enter")
            await pilot.pause()
            assert cauth.account_status()["installed"] == "alice"
            assert "bob" in cauth.load_accounts()["accounts"]

    asyncio.run(drive())


def test_home_initial_selection_and_marker_are_visible(live_login):
    for name in ("alice", "bob", "work"):
        live_login(name)
        cauth.register_live_as(name)

    async def drive():
        app = tui.CauthApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            listing = app.screen.query_one("#accounts", tui.AccountList)
            assert listing.selected_alias() == "work"
            assert (
                listing.highlighted_child.query_one(tui.Label)
                .render()
                .plain.startswith("> work")
            )
            await pilot.press("up")
            assert listing.selected_alias() == "bob"
            labels = [label.render().plain for label in listing.query(tui.Label)]
            assert sum(label.startswith("> ") for label in labels) == 1
            assert any(label.startswith("> bob") for label in labels)

    asyncio.run(drive())


@pytest.mark.parametrize("size", [(100, 30), (80, 24), (40, 15)])
def test_multiline_accounts_are_separated_and_navigable(live_login, size):
    for name in ("personal", "client-long-name", "work"):
        live_login(name)
        cauth.register_live_as(name)
    data = cauth.load_accounts()
    for info in data["accounts"].values():
        info["last_check"] = {"kind": "verified", "at": 1789200000}
    cauth.atomic_write_json(cauth.ACCOUNTS_JSON, data)

    async def drive():
        app = tui.CauthApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            listing = app.screen.query_one("#accounts", tui.AccountList)
            items = list(listing.children)
            for item in items:
                title = item.query_one(".account-title", tui.Label)
                details = list(item.query(".account-detail"))
                assert len(details) == 2
                assert all(detail.region.x == title.region.x + 2 for detail in details)
                assert item.region.right <= size[0]
                assert item.region.bottom > details[-1].region.bottom
            for previous, following in zip(items, items[1:]):
                assert following.region.y >= previous.region.bottom
            await pilot.press("up")
            assert listing.selected_alias() == "personal"
            assert "> personal · Saved" in listing.highlighted_child.query_one(
                ".account-title", tui.Label
            ).render().plain
            assert "work · Installed" in items[-1].query_one(
                ".account-title", tui.Label
            ).render().plain
            await pilot.press("up", "down", "down")
            await pilot.pause()
            selected_title = listing.highlighted_child.query_one(".account-title")
            assert selected_title.region.y >= listing.content_region.y
            assert selected_title.region.bottom <= listing.content_region.bottom
            await pilot.press("/")
            app.screen.query_one("#filter", tui.Input).value = "client-long"
            await pilot.pause()
            assert listing.selected_alias() == "client-long-name"
            assert len(listing.children) == 1

    asyncio.run(drive())
