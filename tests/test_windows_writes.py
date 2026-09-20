"""Windows cannot fsync a directory, and the write path must survive that.

On win32 `os.open(some_directory, os.O_RDONLY)` raises PermissionError. That call used to
sit outside the try that guarded the fsync, so it escaped after `os.replace` had already
committed the new bytes: the credential write had landed and cauth reported it as failed.
These tests pin the fix by simulating the Windows behaviour on any host.
"""

from __future__ import annotations

import os

import pytest

import cauth


@pytest.fixture
def windows(monkeypatch):
    """Pretend to be Windows, including os.open refusing to open a directory."""
    monkeypatch.setattr(cauth.sys, "platform", "win32")
    real_open = os.open

    def refuse_directories(path, flags, *args, **kwargs):
        if os.path.isdir(path):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(cauth.os, "open", refuse_directories)


def test_fsync_dir_does_nothing_on_windows(tmp_path, windows):
    cauth.fsync_dir(tmp_path)


def test_fsync_dir_still_syncs_elsewhere(tmp_path, monkeypatch):
    """The POSIX branch must still fsync, asserted on any host including Windows.

    os.open and os.close are stubbed as well as os.fsync, because on a real Windows box
    the open is exactly what cannot happen, and this test has to keep meaning something
    there rather than skipping on the one platform the change was made for.
    """
    synced = []
    monkeypatch.setattr(cauth.sys, "platform", "linux")
    monkeypatch.setattr(cauth.os, "open", lambda path, flags: 4242)
    monkeypatch.setattr(cauth.os, "fsync", synced.append)
    monkeypatch.setattr(cauth.os, "close", lambda fd: None)
    cauth.fsync_dir(tmp_path)
    assert synced == [4242], "the directory fsync is the durability guarantee, do not lose it"


def test_atomic_write_json_completes_on_windows(tmp_path, windows):
    target = tmp_path / "creds.json"
    cauth.atomic_write_json(target, {"claudeAiOauth": {"accessToken": "fixture"}})
    assert cauth.read_json(target) == {"claudeAiOauth": {"accessToken": "fixture"}}
    assert not list(tmp_path.glob("*.tmp")), "a staged temp file was left behind"


def test_commit_staged_completes_on_windows(tmp_path, windows):
    target = tmp_path / "state.json"
    tmp = cauth.stage_json(target, {"oauthAccount": {"emailAddress": "a@example.com"}})
    cauth.commit_staged(tmp, target)
    assert cauth.read_json(target) == {"oauthAccount": {"emailAddress": "a@example.com"}}
    assert not tmp.exists()
