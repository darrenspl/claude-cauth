"""The switch algorithm: the part that can lose an account if it is wrong."""

from __future__ import annotations

import hashlib
import json
import os
import stat

import pytest

import cauth
from conftest import (
    make_credentials,
    make_logged_out_credentials,
    make_oauth_account,
    posix_modes_only,
)


def sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------------------


def test_register_live_snapshots_the_current_login(live_login):
    live_login("alice")
    cauth.register_live_as("alice")

    data = cauth.load_accounts()
    assert data["active"] == "alice"
    assert data["accounts"]["alice"]["email"] == "alice@example.test"

    creds, oauth = cauth.profile_paths("alice")
    assert creds.exists() and oauth.exists()
    assert json.loads(oauth.read_text())["emailAddress"] == "alice@example.test"


def test_register_without_a_live_login_refuses(isolated_store):
    with pytest.raises(cauth.CauthError, match="does not exist"):
        cauth.register_live_as("alice")


def test_register_refuses_a_truthy_but_logged_out_oauth_block(live_login):
    live_login("alice")
    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, make_logged_out_credentials())

    with pytest.raises(cauth.CauthError, match="non-empty accessToken"):
        cauth.register_live_as("alice")

    assert cauth.load_accounts()["accounts"] == {}
    assert not cauth.profile_paths("alice")[0].exists()


# --------------------------------------------------------------------------------------
# the round trip (PRD definition of done, item 2)
# --------------------------------------------------------------------------------------


def test_a_to_b_to_a_leaves_both_profiles_byte_identical(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")

    alice_creds, alice_oauth = cauth.profile_paths("alice")
    bob_creds, bob_oauth = cauth.profile_paths("bob")
    before = {p: sha256(p) for p in (alice_creds, alice_oauth, bob_creds, bob_oauth)}

    cauth.switch_to("alice")
    assert cauth.load_accounts()["active"] == "alice"
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")
    assert (
        json.loads(cauth.LIVE_CLAUDE_JSON.read_text())["oauthAccount"]
        == make_oauth_account("alice")
    )

    cauth.switch_to("bob")
    assert cauth.load_accounts()["active"] == "bob"
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("bob")

    cauth.switch_to("alice")
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")

    for path, digest in before.items():
        assert sha256(path) == digest, f"{path.name} changed across the round trip"


def test_switch_to_the_active_account_is_a_no_op(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    creds, _ = cauth.profile_paths("alice")
    before = sha256(creds)

    cauth.switch_to("alice")

    assert sha256(creds) == before


@pytest.mark.parametrize("live_state", ["missing", "logged-out", "different-account"])
def test_selecting_the_named_active_alias_repairs_a_stale_live_login(
    live_login, live_state
):
    live_login("alice")
    cauth.register_live_as("alice")
    stored_before = sha256(cauth.profile_paths("alice")[0])

    if live_state == "missing":
        cauth.LIVE_CREDENTIALS.unlink()
    elif live_state == "logged-out":
        cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, make_logged_out_credentials())
    else:
        live_login("bob")

    if live_state == "different-account":
        with pytest.raises(cauth.CauthError, match="not saved"):
            cauth.switch_to("alice")
        cauth.switch_to("alice", discard_unsaved=True)
    else:
        cauth.switch_to("alice")

    assert cauth.load_accounts()["active"] == "alice"
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")
    assert (
        json.loads(cauth.LIVE_CLAUDE_JSON.read_text())["oauthAccount"]
        == make_oauth_account("alice")
    )
    assert sha256(cauth.profile_paths("alice")[0]) == stored_before


def test_mark_live_unregistered_only_clears_the_active_pointer(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    profile_hashes = [sha256(path) for path in cauth.profile_paths("work")]
    live_hashes = [sha256(cauth.LIVE_CREDENTIALS), sha256(cauth.LIVE_CLAUDE_JSON)]

    cauth.mark_live_unregistered()

    data = cauth.load_accounts()
    assert data["active"] is None
    assert data["accounts"]["work"]["email"] == "alice@example.test"
    assert [sha256(path) for path in cauth.profile_paths("work")] == profile_hashes
    assert [sha256(cauth.LIVE_CREDENTIALS), sha256(cauth.LIVE_CLAUDE_JSON)] == live_hashes


def test_switch_to_unknown_alias_refuses(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    with pytest.raises(cauth.CauthError, match="Unknown alias"):
        cauth.switch_to("nobody")


# --------------------------------------------------------------------------------------
# sync-back: the step whose absence silently loses an account
# --------------------------------------------------------------------------------------


def test_switch_syncs_a_refreshed_token_back_before_swapping(live_login):
    """A token refreshed mid-session must reach the profile, not be discarded."""
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    # Claude Code refreshes alice's token while she is the live account.
    live_login("alice", access="REFRESHED", refresh="REFRESHED")

    cauth.switch_to("bob")

    alice_creds, _ = cauth.profile_paths("alice")
    stored = json.loads(alice_creds.read_text())["claudeAiOauth"]
    assert "REFRESHED" in stored["accessToken"]
    assert "REFRESHED" in stored["refreshToken"]

    # And coming back gets the refreshed pair, not the stale one.
    cauth.switch_to("alice")
    live = json.loads(cauth.LIVE_CREDENTIALS.read_text())["claudeAiOauth"]
    assert "REFRESHED" in live["accessToken"]


# --------------------------------------------------------------------------------------
# halt rather than guess
# --------------------------------------------------------------------------------------


def test_activate_refuses_when_the_oauth_block_is_missing(live_login):
    """Tokens from one account plus an identity block from another is a lying state."""
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")

    cauth.switch_to("alice")
    # Bob's identity file goes missing on disk while he is parked, so sync-back cannot
    # quietly recreate it on the way out.
    _, bob_oauth = cauth.profile_paths("bob")
    bob_oauth.unlink()

    with pytest.raises(cauth.CauthError, match="no matching"):
        cauth.switch_to("bob")

    # The live account is untouched: still alice, still coherent.
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")
    assert (
        json.loads(cauth.LIVE_CLAUDE_JSON.read_text())["oauthAccount"]["emailAddress"]
        == "alice@example.test"
    )


def test_activate_refuses_credentials_with_no_oauth_payload(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    bob_creds, _ = cauth.profile_paths("bob")
    cauth.atomic_write_json(bob_creds, {"somethingElse": True})

    with pytest.raises(cauth.CauthError, match="no claudeAiOauth"):
        cauth.switch_to("bob")

    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")


def test_activate_refuses_a_truthy_but_logged_out_oauth_block(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    bob_creds, _ = cauth.profile_paths("bob")
    cauth.atomic_write_json(bob_creds, make_logged_out_credentials())

    with pytest.raises(cauth.CauthError, match="non-empty accessToken"):
        cauth.switch_to("bob")

    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")
    assert cauth.load_accounts()["active"] == "alice"


def test_missing_profile_refuses(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    data = cauth.load_accounts()
    data["accounts"]["ghost"] = {"email": None, "tier": None}
    cauth.save_accounts(data)

    with pytest.raises(cauth.CauthError, match="No stored credentials"):
        cauth.switch_to("ghost")


# --------------------------------------------------------------------------------------
# unrelated state in ~/.claude.json
# --------------------------------------------------------------------------------------


def test_switch_preserves_every_other_key_in_claude_json(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login(
        "bob",
        mcpServers={"weather": {"command": "x"}},
        numStartups=417,
        tipsHistory={"a": 1},
    )
    cauth.register_live_as("bob")

    cauth.switch_to("alice")

    full = json.loads(cauth.LIVE_CLAUDE_JSON.read_text())
    assert full["mcpServers"] == {"weather": {"command": "x"}}
    assert full["numStartups"] == 417
    assert full["tipsHistory"] == {"a": 1}
    assert full["oauthAccount"]["emailAddress"] == "alice@example.test"


# --------------------------------------------------------------------------------------
# removal
# --------------------------------------------------------------------------------------


def test_forget_active_account_preserves_live_login(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    before = cauth.LIVE_CREDENTIALS.read_bytes()
    cauth.remove_account("alice")
    assert cauth.LIVE_CREDENTIALS.read_bytes() == before
    assert cauth.load_accounts()["active"] is None


def test_remove_deletes_both_profile_files(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")

    creds, oauth = cauth.profile_paths("alice")
    cauth.remove_account("alice")

    assert not creds.exists() and not oauth.exists()
    assert "alice" not in cauth.load_accounts()["accounts"]


# --------------------------------------------------------------------------------------
# permissions and atomicity (PRD NFR1, NFR2)
# --------------------------------------------------------------------------------------


@posix_modes_only
def test_store_is_700_and_files_are_600(live_login):
    live_login("alice")
    cauth.register_live_as("alice")

    assert stat.S_IMODE(os.stat(cauth.CONFIG_DIR).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(cauth.PROFILES_DIR).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(cauth.ACCOUNTS_JSON).st_mode) == 0o600
    for path in cauth.profile_paths("alice"):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_atomic_write_leaves_no_temp_file_behind(isolated_store, tmp_path):
    target = tmp_path / "out.json"
    cauth.atomic_write_json(target, {"ok": True})

    assert json.loads(target.read_text()) == {"ok": True}
    assert not (tmp_path / "out.json.tmp").exists()


def test_a_failed_write_does_not_clobber_the_existing_file(isolated_store, tmp_path):
    target = tmp_path / "out.json"
    cauth.atomic_write_json(target, {"good": True})

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        cauth.atomic_write_json(target, {"bad": Unserializable()})

    assert json.loads(target.read_text()) == {"good": True}
    assert not (tmp_path / "out.json.tmp").exists()
