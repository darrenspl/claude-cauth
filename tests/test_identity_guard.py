"""The guard that stops one account's tokens being written into another's profile.

Every one of these reproduces a defect that a pre-release audit found and confirmed by
sandboxed execution. They exist so the guard cannot be removed as "defensive".

sync_back is the chokepoint that switch_to, register_live_as and
prepare_for_external_login all cross. Before the guard it wrote whatever was live into
whatever profile it was handed, so any disagreement between the `active` pointer and the
credentials actually on disk destroyed an account silently.
"""

from __future__ import annotations

import json

import pytest

import cauth
from conftest import make_credentials, make_logged_out_credentials


def stored(alias: str) -> dict:
    creds, _ = cauth.profile_paths(alias)
    return json.loads(creds.read_text())


# --------------------------------------------------------------------------------------
# the confirmed clobbers
# --------------------------------------------------------------------------------------


def test_registering_over_an_alias_holding_a_different_account_is_refused(live_login):
    """Audit repro: cauth add <existing-alias> while another account is live.

    Reachable from the CLI with no confirm step. It used to overwrite work's stored
    tokens with personal's, destroying work.
    """
    live_login("alice")
    cauth.register_live_as("work")
    live_login("bob")
    cauth.register_live_as("personal")

    before = stored("work")

    with pytest.raises(cauth.CauthError, match="is not the account stored under"):
        cauth.register_live_as("work")

    assert stored("work") == before == make_credentials("alice")


def test_a_stale_active_pointer_cannot_clobber_the_profile_it_names(live_login):
    """Audit repro: alias prompt aborted after a login.

    prepare_for_external_login syncs and returns, the browser login replaces the live
    credentials, then the user presses Escape instead of naming the account. `active`
    still says 'work' while bob's tokens are live. The next switch used to write bob's
    tokens into work's profile.
    """
    live_login("alice")
    cauth.register_live_as("work")
    live_login("carol")
    cauth.register_live_as("other")
    cauth.switch_to("work")

    cauth.prepare_for_external_login()
    live_login("bob")  # the browser login lands, user then aborts the alias prompt

    before = stored("work")
    with pytest.raises(cauth.CauthError, match="not saved"):
        cauth.switch_to("other")

    assert stored("work") == before == make_credentials("alice")


def test_the_error_names_both_accounts_and_the_way_out(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    live_login("bob")

    with pytest.raises(cauth.CauthError) as exc:
        cauth.register_live_as("work")

    message = str(exc.value)
    assert "bob@example.test" in message
    assert "alice@example.test" in message
    assert "cauth add" in message


# --------------------------------------------------------------------------------------
# what must still be allowed
# --------------------------------------------------------------------------------------


def test_a_first_registration_has_nothing_to_compare_and_passes(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    assert stored("work") == make_credentials("alice")


def test_re_registering_the_same_account_updates_it(live_login):
    """The normal sync-back case: same account, newer tokens."""
    live_login("alice")
    cauth.register_live_as("work")
    live_login("alice", access="ROTATED", refresh="ROTATED")

    cauth.register_live_as("work")

    assert "ROTATED" in stored("work")["claudeAiOauth"]["accessToken"]


def test_a_normal_switch_still_syncs_the_outgoing_account(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    live_login("bob")
    cauth.register_live_as("personal")
    cauth.switch_to("work")
    live_login("alice", access="ROTATED", refresh="ROTATED")

    cauth.switch_to("personal")

    assert "ROTATED" in stored("work")["claudeAiOauth"]["accessToken"]


def test_uuid_wins_over_email_when_both_are_present(live_login, isolated_store):
    """Email can change on an account. The uuid is the identity."""
    live_login("alice")
    cauth.register_live_as("work")

    # Same uuid, renamed email. This is the same account and must be allowed.
    full = json.loads(cauth.LIVE_CLAUDE_JSON.read_text())
    full["oauthAccount"]["emailAddress"] = "renamed@example.test"
    cauth.atomic_write_json(cauth.LIVE_CLAUDE_JSON, full, mode=0o600)

    cauth.register_live_as("work")
    assert cauth.load_accounts()["accounts"]["work"]["email"] == "renamed@example.test"


def test_different_uuid_with_the_same_email_is_still_refused(live_login):
    """Two org seats can share an email. The uuid still separates them."""
    live_login("alice")
    cauth.register_live_as("work")

    full = json.loads(cauth.LIVE_CLAUDE_JSON.read_text())
    full["oauthAccount"]["accountUuid"] = "uuid-somebody-else"
    cauth.atomic_write_json(cauth.LIVE_CLAUDE_JSON, full, mode=0o600)

    with pytest.raises(cauth.CauthError, match="is not the account stored under"):
        cauth.register_live_as("work")


# --------------------------------------------------------------------------------------
# a broken live file must not strand you
# --------------------------------------------------------------------------------------


def test_a_live_file_with_no_token_block_is_skipped_not_fatal(live_login):
    """You must still be able to switch to a good account and repair the live file."""
    live_login("alice")
    cauth.register_live_as("work")
    live_login("bob")
    cauth.register_live_as("personal")
    cauth.switch_to("work")

    before = stored("work")
    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, {}, mode=0o600)

    cauth.switch_to("personal")

    assert stored("work") == before, "an empty live file overwrote a good profile"
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("bob")


def test_a_logged_out_oauth_block_cannot_replace_a_profile_or_its_backup(live_login):
    """Regression: a truthy logout skeleton destroyed both saved accounts in production."""
    live_login("alice")
    cauth.register_live_as("work")
    live_login("bob")
    cauth.register_live_as("personal")
    cauth.switch_to("work")

    work_creds, _ = cauth.profile_paths("work")
    work_backup = work_creds.with_name(work_creds.name + ".bak")
    cauth.atomic_write_json(
        work_backup, make_credentials("alice", access="OLDER", refresh="OLDER")
    )
    current_before = work_creds.read_bytes()
    backup_before = work_backup.read_bytes()

    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, make_logged_out_credentials())
    cauth.switch_to("personal")

    assert work_creds.read_bytes() == current_before
    assert work_backup.read_bytes() == backup_before
    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("bob")


def test_a_missing_live_credentials_file_does_not_block_a_switch(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    live_login("bob")
    cauth.register_live_as("personal")
    cauth.switch_to("work")

    cauth.LIVE_CREDENTIALS.unlink()
    cauth.switch_to("personal")

    assert json.loads(cauth.LIVE_CREDENTIALS.read_text()) == make_credentials("bob")


def test_a_profile_backup_is_kept_when_it_is_overwritten(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    live_login("alice", access="ROTATED", refresh="ROTATED")
    cauth.register_live_as("work")

    creds, _ = cauth.profile_paths("work")
    backup = creds.with_name(creds.name + ".bak")
    assert backup.exists()
    assert "ROTATED" not in json.loads(backup.read_text())["claudeAiOauth"]["accessToken"]


# --------------------------------------------------------------------------------------
# atomicity
# --------------------------------------------------------------------------------------


def test_a_failed_identity_write_leaves_both_live_files_untouched(live_login, monkeypatch):
    """activate() stages both files before committing either."""
    live_login("alice")
    cauth.register_live_as("work")
    live_login("bob")
    cauth.register_live_as("personal")
    cauth.switch_to("work")

    creds_before = cauth.LIVE_CREDENTIALS.read_bytes()
    state_before = cauth.LIVE_CLAUDE_JSON.read_bytes()

    real_stage = cauth.stage_json
    calls = {"n": 0}

    def flaky(path, data, mode=cauth.FILE_MODE):
        calls["n"] += 1
        if calls["n"] == 2:  # the identity file, staged second
            raise OSError("disk full")
        return real_stage(path, data, mode)

    monkeypatch.setattr(cauth, "stage_json", flaky)

    with pytest.raises(OSError):
        cauth.switch_to("personal")

    assert cauth.LIVE_CREDENTIALS.read_bytes() == creds_before
    assert cauth.LIVE_CLAUDE_JSON.read_bytes() == state_before


def test_temp_files_are_unique_per_write(isolated_store, tmp_path, monkeypatch):
    """A fixed .tmp sibling let two processes share one inode and corrupt the target."""
    seen = []
    real = cauth.tempfile.mkstemp

    def spy(*a, **kw):
        fd, name = real(*a, **kw)
        seen.append(name)
        return fd, name

    monkeypatch.setattr(cauth.tempfile, "mkstemp", spy)

    target = tmp_path / "x.json"
    cauth.atomic_write_json(target, {"a": 1})
    cauth.atomic_write_json(target, {"a": 2})

    assert len(seen) == 2 and seen[0] != seen[1]
    assert not list(tmp_path.glob("*.tmp")), "a temp file was left behind"
