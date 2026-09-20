"""Adding several accounts in one sitting without being dragged through each one.

The subtle risk here is the `active` pointer. sync_back copies the LIVE credentials into
whatever `active` names, so if `active` ever disagrees with the credentials actually on
disk, the next switch writes one account's tokens into another account's profile. These
tests assert the walk-back does the right thing AND that no profile gets cross-contaminated
along the way.
"""

from __future__ import annotations

import json

import cauth
from conftest import make_credentials, make_logged_out_credentials


def stored(alias: str) -> dict:
    creds, _ = cauth.profile_paths(alias)
    return json.loads(creds.read_text())


def test_add_then_return_leaves_you_where_you_started(live_login):
    live_login("alice")
    cauth.register_live_as("work")

    # A browser login makes bob live, which is what claude auth login really does.
    live_login("bob")
    landed = cauth.add_then_return("personal", return_to="work")

    assert landed == "work"
    assert cauth.load_accounts()["active"] == "work"
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")


def test_adding_ten_accounts_never_moves_you_and_stores_all_of_them(live_login):
    live_login("home")
    cauth.register_live_as("home")

    for i in range(10):
        live_login(f"acct{i}")
        landed = cauth.add_then_return(f"alias{i}", return_to="home")
        assert landed == "home", f"got dragged onto alias{i}"

    data = cauth.load_accounts()
    assert data["active"] == "home"
    assert len(data["accounts"]) == 11

    # Every stored profile holds its OWN credentials, not a neighbour's.
    assert stored("home") == make_credentials("home")
    for i in range(10):
        assert stored(f"alias{i}") == make_credentials(f"acct{i}"), f"alias{i} corrupted"

    # And the live login is still the one we started on.
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("home")


def test_each_added_account_is_reachable_afterwards(live_login):
    live_login("home")
    cauth.register_live_as("home")
    for name in ("one", "two"):
        live_login(name)
        cauth.add_then_return(name, return_to="home")

    cauth.switch_to("two")
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("two")
    cauth.switch_to("one")
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("one")
    cauth.switch_to("home")
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("home")


def test_first_ever_account_has_nowhere_to_return_to(live_login):
    live_login("alice")
    landed = cauth.add_then_return("first", return_to=None)
    assert landed == "first"
    assert cauth.load_accounts()["active"] == "first"


def test_returning_to_a_deleted_alias_stays_put_rather_than_failing(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    live_login("bob")

    landed = cauth.add_then_return("personal", return_to="ghost")

    assert landed == "personal"
    assert cauth.load_accounts()["active"] == "personal"


def test_returning_to_yourself_is_a_no_op(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    landed = cauth.add_then_return("work", return_to="work")
    assert landed == "work"
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")


def test_a_token_refreshed_before_an_external_login_is_not_destroyed(live_login):
    """The whole point of the product, at the one moment cauth is not in control.

    `claude auth login` overwrites the live credentials itself. If the outgoing account
    refreshed its token during the session, that refresh exists ONLY in the live file, so
    it has to be synced to the profile before the login runs or it is lost permanently.
    """
    live_login("home")
    cauth.register_live_as("home")

    # Claude Code rotates home's token while home is live.
    live_login("home", access="ROTATED", refresh="ROTATED")

    # cauth syncs before handing the terminal over. This is the step under test.
    returning_to = cauth.prepare_for_external_login()
    assert returning_to == "home"

    # Only now does the browser login clobber the live file.
    live_login("newguy")
    cauth.add_then_return("newguy", return_to=returning_to)

    # Coming back must give us the rotated token, not the one captured at registration.
    live = json.loads(cauth.LIVE_CREDENTIALS.read_text())["claudeAiOauth"]
    assert "ROTATED" in live["accessToken"]
    assert "ROTATED" in stored("home")["claudeAiOauth"]["accessToken"]


def test_without_the_pre_login_sync_the_refresh_would_be_lost(live_login):
    """Pins why prepare_for_external_login exists, so nobody deletes it as redundant."""
    live_login("home")
    cauth.register_live_as("home")
    live_login("home", access="ROTATED", refresh="ROTATED")

    # Skip the sync, exactly as a naive implementation would.
    live_login("newguy")
    cauth.add_then_return("newguy", return_to="home")

    # The rotated token is gone. This is the bug the pre-login sync prevents.
    assert "ROTATED" not in stored("home")["claudeAiOauth"]["accessToken"]


def test_prepare_for_external_login_is_safe_with_no_accounts(isolated_store, live_login):
    live_login("alice")
    assert cauth.prepare_for_external_login() is None


def test_logged_out_state_does_not_return_to_a_revoked_profile(live_login):
    """A fresh login must remain active when there was no usable outgoing login."""
    live_login("home")
    cauth.register_live_as("home")
    stored_before = stored("home")

    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, make_logged_out_credentials())

    assert cauth.prepare_for_external_login() is None
    assert stored("home") == stored_before
