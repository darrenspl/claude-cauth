"""Legacy aliases must never reinstall a stale copy of the same account's tokens."""

import asyncio

import pytest

import cauth
import tui
from conftest import make_credentials


def legacy_duplicate(source, alias):
    # Seed the pre-fix on-disk format without using the registration guard.
    for src, dst in zip(cauth.profile_paths(source), cauth.profile_paths(alias)):
        cauth.atomic_write_json(dst, cauth.read_json(src))
    data = cauth.load_accounts()
    data["accounts"][alias] = dict(data["accounts"][source])
    cauth.save_accounts(data)


def test_cli_cannot_create_another_label_for_same_account(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    assert cauth.main(["legacy", "add", "duplicate"]) == 1
    assert set(cauth.load_accounts()["accounts"]) == {"work"}


def test_switching_legacy_alias_preserves_rotated_live_tokens(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    legacy_duplicate("work", "duplicate")
    live_login("alice", access="ROTATED", refresh="ROTATED")
    cauth.switch_to("duplicate")
    assert cauth.read_json(cauth.LIVE_CREDENTIALS) == make_credentials(
        "alice", "ROTATED", "ROTATED"
    )


def test_switch_away_then_to_duplicate_preserves_rotation(live_login):
    live_login("bob")
    cauth.register_live_as("personal")
    live_login("alice")
    cauth.register_live_as("work")
    legacy_duplicate("work", "duplicate")
    live_login("alice", access="ROTATED", refresh="ROTATED")
    cauth.switch_to("personal")
    cauth.switch_to("duplicate")
    assert cauth.read_json(cauth.LIVE_CREDENTIALS) == make_credentials(
        "alice", "ROTATED", "ROTATED"
    )


def test_renewal_updates_legacy_aliases(live_login, monkeypatch):
    live_login("alice")
    cauth.register_live_as("work")
    legacy_duplicate("work", "duplicate")
    live_login("alice", access="RENEWED", refresh="RENEWED")
    monkeypatch.setattr(
        cauth, "run_claude_probe", lambda: cauth.ClaudeProbeResult(0, "CAUTH_OK")
    )
    cauth.finish_renewal("duplicate", "work")
    assert cauth.read_json(cauth.LIVE_CREDENTIALS) == make_credentials(
        "alice", "RENEWED", "RENEWED"
    )


@pytest.mark.parametrize("size", [(80, 24), (60, 20)])
def test_expired_duplicates_and_delete_are_visible(live_login, size):
    live_login("alice")
    cauth.register_live_as("work")
    payload = cauth.read_json(cauth.profile_paths("work")[0])
    payload["claudeAiOauth"]["refreshTokenExpiresAt"] = 1
    cauth.atomic_write_json(cauth.profile_paths("work")[0], payload)
    legacy_duplicate("work", "duplicate")

    async def drive():
        app = tui.CauthApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            labels = "\n".join(str(label.content) for label in app.screen.query(tui.Label))
            assert "Renew required" in labels
            assert "Same account as" in labels
            binding = next(b for b in app.screen.BINDINGS if b.key == "r")
            assert binding.show and binding.description == "Delete"
            assert "Delete" in app.export_screenshot()
            before = cauth.LIVE_CREDENTIALS.read_bytes()
            await pilot.press("r", "enter", "y")
            await pilot.pause()
            assert len(cauth.load_accounts()["accounts"]) == 1
            assert cauth.LIVE_CREDENTIALS.read_bytes() == before

    asyncio.run(drive())


def test_sync_does_not_merge_different_organization_seats(live_login):
    live_login("alice")
    cauth.register_live_as("work")
    full = cauth.read_json(cauth.LIVE_CLAUDE_JSON)
    full["oauthAccount"]["organizationUuid"] = "other-org"
    cauth.atomic_write_json(cauth.LIVE_CLAUDE_JSON, full)
    cauth.register_live_as("other-seat")
    other_before = cauth.profile_paths("other-seat")[0].read_bytes()
    cauth.switch_to("work")
    live_login("alice", access="ROTATED", refresh="ROTATED")
    full = cauth.read_json(cauth.LIVE_CLAUDE_JSON)
    full["oauthAccount"].pop("organizationUuid", None)
    cauth.atomic_write_json(cauth.LIVE_CLAUDE_JSON, full)
    cauth.sync_back("work")
    assert cauth.profile_paths("other-seat")[0].read_bytes() == other_before


def test_duplicate_sync_failure_restores_every_profile(live_login, monkeypatch):
    live_login("alice")
    cauth.register_live_as("work")
    legacy_duplicate("work", "duplicate")
    live_login("alice", access="ROTATED", refresh="ROTATED")
    before = {p: p.read_bytes() for p in cauth.managed_files()}
    real_write = cauth.atomic_write_json

    def fail_duplicate(path, *args, **kwargs):
        if path == cauth.profile_paths("duplicate")[0]:
            raise OSError("fixture disk failure")
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr(cauth, "atomic_write_json", fail_duplicate)
    with pytest.raises(OSError, match="fixture disk failure"):
        cauth.sync_back("work")
    assert {p: p.read_bytes() for p in cauth.managed_files()} == before


def test_unreadable_unrelated_identity_does_not_block_sync(live_login):
    live_login("bob")
    cauth.register_live_as("personal")
    live_login("alice")
    cauth.register_live_as("work")
    cauth.profile_paths("personal")[1].write_text("{broken")
    live_login("alice", access="ROTATED", refresh="ROTATED")
    cauth.sync_back("work")
    assert cauth.read_json(cauth.profile_paths("work")[0]) == make_credentials(
        "alice", "ROTATED", "ROTATED"
    )
