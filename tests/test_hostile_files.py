"""What happens on a machine that is not ours.

Every other test builds its files with cauth's own writer, so they are well formed by
construction. These cover the states a stranger's machine can be in: hand-edited JSON, a
truncated file, a missing file, a file with unusual permissions. The rule under test is
"halt rather than guess", and specifically that halting raises CauthError so the UI can
show a message instead of dying with a traceback.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

import cauth
from conftest import make_credentials, posix_modes_only


# --------------------------------------------------------------------------------------
# malformed JSON becomes CauthError, never a raw JSONDecodeError
# --------------------------------------------------------------------------------------


def test_truncated_claude_json_halts_with_a_readable_error(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    cauth.LIVE_CLAUDE_JSON.write_text('{"oauthAccount": {"emailAddress":')

    with pytest.raises(cauth.CauthError, match="not valid JSON"):
        cauth.switch_to("bob")


def test_corrupt_accounts_json_halts(isolated_store):
    cauth.ensure_store()
    cauth.ACCOUNTS_JSON.write_text("this is not json at all")

    with pytest.raises(cauth.CauthError, match="not valid JSON"):
        cauth.load_accounts()


def test_accounts_json_holding_a_list_halts(isolated_store):
    cauth.ensure_store()
    cauth.ACCOUNTS_JSON.write_text("[1, 2, 3]")

    with pytest.raises(cauth.CauthError, match="(?:must|should).*object"):
        cauth.load_accounts()


def test_accounts_json_with_a_non_object_accounts_key_halts(isolated_store):
    cauth.ensure_store()
    cauth.ACCOUNTS_JSON.write_text('{"active": null, "accounts": ["a"]}')

    with pytest.raises(cauth.CauthError, match="invalid accounts section"):
        cauth.load_accounts()


def test_claude_json_holding_a_list_is_refused(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    cauth.LIVE_CLAUDE_JSON.write_text("[]")

    with pytest.raises(cauth.CauthError, match="(?:must|should).*object"):
        cauth.switch_to("bob")


def test_corrupt_stored_profile_halts_without_touching_the_live_login(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    bob_creds, _ = cauth.profile_paths("bob")
    bob_creds.write_text("{ truncated")

    with pytest.raises(cauth.CauthError, match="not valid JSON"):
        cauth.switch_to("bob")

    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")


@posix_modes_only
def test_unreadable_file_becomes_cauth_error(isolated_store, tmp_path):
    target = tmp_path / "locked.json"
    target.write_text("{}")
    os.chmod(target, 0o000)
    try:
        with pytest.raises(cauth.CauthError, match="Cannot read"):
            cauth.read_json(target)
    finally:
        os.chmod(target, 0o600)


# --------------------------------------------------------------------------------------
# permissions are preserved, never widened
# --------------------------------------------------------------------------------------


@posix_modes_only
def test_a_switch_does_not_loosen_tightened_permissions(live_login):
    """Someone may have chmod 600'd ~/.claude.json. We must not widen it to 644."""
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    os.chmod(cauth.LIVE_CLAUDE_JSON, 0o600)
    os.chmod(cauth.LIVE_CREDENTIALS, 0o600)

    cauth.switch_to("bob")

    assert stat.S_IMODE(os.stat(cauth.LIVE_CLAUDE_JSON).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(cauth.LIVE_CREDENTIALS).st_mode) == 0o600


@posix_modes_only
def test_a_switch_preserves_a_wider_mode_it_found(live_login):
    """The reverse: we do not tighten either. We leave what we found."""
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    os.chmod(cauth.LIVE_CLAUDE_JSON, 0o644)

    cauth.switch_to("bob")

    assert stat.S_IMODE(os.stat(cauth.LIVE_CLAUDE_JSON).st_mode) == 0o644


def test_file_mode_of_falls_back_when_the_file_is_absent(tmp_path):
    assert cauth.file_mode_of(tmp_path / "nope.json", 0o600) == 0o600


# --------------------------------------------------------------------------------------
# missing files
# --------------------------------------------------------------------------------------


def test_status_survives_a_machine_with_no_claude_install(isolated_store):
    """Fresh machine, no Claude Code login at all. Read-only paths must not explode."""
    state = cauth.read_live_state()
    assert state.email is None
    assert cauth.load_accounts()["accounts"] == {}
    assert cauth.cmd_status() == 0


def test_switch_with_no_live_claude_json_still_works(live_login):
    """~/.claude.json can legitimately not exist yet on a fresh install."""
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")

    cauth.LIVE_CLAUDE_JSON.unlink()
    with pytest.raises(cauth.CauthError, match="not saved"):
        cauth.switch_to("bob")
    cauth.switch_to("bob", discard_unsaved=True)

    full = json.loads(cauth.LIVE_CLAUDE_JSON.read_text())
    assert full["oauthAccount"]["emailAddress"] == "bob@example.test"
