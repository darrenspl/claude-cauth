"""Renaming an alias, and the validation that keeps an alias from escaping the store."""

from __future__ import annotations

import json

import pytest

import cauth
from conftest import make_credentials


# --------------------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("alias", ["work", "client-acme", "a", "personal_2", "  pad  "])
def test_valid_aliases_are_accepted_and_trimmed(alias):
    assert cauth.validate_alias(alias) == alias.strip()


@pytest.mark.parametrize("alias", ["", "   ", ".", "..", "a/b", "a\\b", "x" * 65])
def test_hostile_or_empty_aliases_are_refused(alias):
    with pytest.raises(cauth.CauthError):
        cauth.validate_alias(alias)


def test_registering_with_a_path_separator_is_refused(live_login):
    live_login("alice")
    with pytest.raises(cauth.CauthError, match="filename"):
        cauth.register_live_as("../escape")
    assert cauth.load_accounts()["accounts"] == {}


# --------------------------------------------------------------------------------------
# rename
# --------------------------------------------------------------------------------------


def test_rename_moves_both_profile_files_and_the_index(live_login):
    live_login("alice")
    cauth.register_live_as("work")

    cauth.rename_account("work", "day-job")

    data = cauth.load_accounts()
    assert "work" not in data["accounts"]
    assert data["accounts"]["day-job"]["email"] == "alice@example.test"

    old_cred, old_oauth = cauth.profile_paths("work")
    new_cred, new_oauth = cauth.profile_paths("day-job")
    assert not old_cred.exists() and not old_oauth.exists()
    assert new_cred.exists() and new_oauth.exists()
    assert json.loads(new_cred.read_text()) == make_credentials("alice")


def test_renaming_the_active_account_keeps_it_active_and_signed_in(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    before = cauth.LIVE_CREDENTIALS.read_bytes()

    cauth.rename_account("work", "day-job")

    assert cauth.load_accounts()["active"] == "day-job"
    # A rename is a relabel. It must not touch the live login at all.
    assert cauth.LIVE_CREDENTIALS.read_bytes() == before


def test_rename_then_switch_still_resolves(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")

    cauth.rename_account("alice", "personal")
    cauth.switch_to("personal")

    assert cauth.load_accounts()["active"] == "personal"
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("alice")


def test_rename_to_an_existing_alias_is_refused(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")

    with pytest.raises(cauth.CauthError, match="already taken"):
        cauth.rename_account("alice", "bob")

    data = cauth.load_accounts()
    assert set(data["accounts"]) == {"alice", "bob"}
    assert cauth.profile_paths("alice")[0].exists()


def test_rename_unknown_alias_is_refused(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    with pytest.raises(cauth.CauthError, match="Unknown alias"):
        cauth.rename_account("ghost", "whatever")


def test_rename_to_the_same_name_is_a_no_op(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    cauth.rename_account("alice", "alice")
    assert set(cauth.load_accounts()["accounts"]) == {"alice"}


def test_rename_rejects_a_hostile_new_alias(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    with pytest.raises(cauth.CauthError, match="filename"):
        cauth.rename_account("alice", "../../etc/passwd")
    assert cauth.profile_paths("alice")[0].exists()


# --------------------------------------------------------------------------------------
# runtime notices
# --------------------------------------------------------------------------------------


# has_display short-circuits to True on darwin and win32, where DISPLAY means nothing.
# Every test below pins the platform, so each one asserts the POSIX branch everywhere
# instead of asserting the real branch on Linux and nothing at all on a Mac or a Windows
# box, which is how the no-DISPLAY case first surfaced as a Windows-only failure.


def test_claude_code_session_is_flagged(monkeypatch):
    monkeypatch.setattr(cauth.sys, "platform", "linux")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("DISPLAY", ":0")
    notes = cauth.runtime_notices()
    assert len(notes) == 1
    assert "changes identity underneath itself" in notes[0]


def test_missing_display_is_flagged(monkeypatch):
    monkeypatch.setattr(cauth.sys, "platform", "linux")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    notes = cauth.runtime_notices()
    assert len(notes) == 1
    assert "prints a URL instead" in notes[0]


def test_a_plain_graphical_terminal_is_quiet(monkeypatch):
    monkeypatch.setattr(cauth.sys, "platform", "linux")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert cauth.runtime_notices() == []


def test_wayland_counts_as_a_browser_capable_session(monkeypatch):
    monkeypatch.setattr(cauth.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert cauth.browser_available() is True
