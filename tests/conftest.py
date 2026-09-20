"""Test isolation for the whole suite.

`cauth` resolves five module-level paths at import time, two of which are the live files
that authenticate every Claude Code session on this machine. A test that forgets to patch
one of them would overwrite the developer's real login. The fixture below is `autouse`, so
isolation is not something a test opts into, and it asserts the redirect actually landed
before any test body runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cauth  # noqa: E402

# The five module attributes that must never point at the real home during a test.
PATCHED_PATHS = (
    "CONFIG_DIR",
    "PROFILES_DIR",
    "ACCOUNTS_JSON",
    "LIVE_CREDENTIALS",
    "LIVE_CLAUDE_JSON",
)

REAL_HOME = Path.home()


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Redirect every cauth path into tmp_path and prove none escaped."""
    home = tmp_path / "home"
    config = home / ".config" / "claude-oauth"
    layout = {
        "CONFIG_DIR": config,
        "PROFILES_DIR": config / "profiles",
        "ACCOUNTS_JSON": config / "accounts.json",
        "LIVE_CREDENTIALS": home / ".claude" / ".credentials.json",
        "LIVE_CLAUDE_JSON": home / ".claude.json",
    }
    assert set(layout) == set(PATCHED_PATHS)

    for name, path in layout.items():
        monkeypatch.setattr(cauth, name, path)

    # Structural guard. If a constant is ever renamed or added and this fixture is not
    # updated, the suite fails here rather than writing into the developer's real account.
    for name in PATCHED_PATHS:
        live = getattr(cauth, name)
        assert str(live).startswith(str(tmp_path)), f"{name} escaped the sandbox: {live}"
        assert REAL_HOME / ".claude" not in live.parents, f"{name} points at the real home"

    (home / ".claude").mkdir(parents=True)
    # Token creation/selection also installs shell integration. Never touch real dotfiles.
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/bash")
    return layout


# --------------------------------------------------------------------------------------
# platform gates
# --------------------------------------------------------------------------------------

# Windows has no POSIX permission bits. os.chmod there only toggles a read-only flag, so
# every mode assertion reads back 0o666 or 0o777 no matter what was asked for, and chmod
# 0o000 does not make a file unreadable. The README already scopes the 700-on-the-store,
# 600-on-its-contents guarantee to Linux and macOS. These tests are marked rather than
# deleted because on the platforms that can keep that guarantee, they are the guarantee.
posix_modes_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits do not exist on Windows",
)


# --------------------------------------------------------------------------------------
# fixture credentials, never real tokens
# --------------------------------------------------------------------------------------


def make_credentials(account: str, access: str = "a", refresh: str = "r") -> dict:
    """A structurally valid credentials blob with obviously fake tokens."""
    return {
        "claudeAiOauth": {
            "accessToken": f"sk-ant-oat01-FIXTURE-{account}-{access}",
            "refreshToken": f"sk-ant-ort01-FIXTURE-{account}-{refresh}",
            "expiresAt": 4102444800000,
            "refreshTokenExpiresAt": 4104864000000,
            "scopes": ["user:inference"],
            "subscriptionType": "max",
            "rateLimitTier": "default_claude_max_20x",
        }
    }


def make_logged_out_credentials() -> dict:
    """The truthy but tokenless payload Claude Code leaves behind when logged out."""
    payload = make_credentials("logged-out")
    payload["claudeAiOauth"].update(
        {
            "accessToken": "",
            "refreshToken": "",
            "expiresAt": 0,
            "refreshTokenExpiresAt": 1786716244929,
        }
    )
    return payload


def make_oauth_account(account: str, tier: str = "default_claude_max_20x") -> dict:
    return {
        "emailAddress": f"{account}@example.test",
        "organizationName": f"{account} org",
        "userRateLimitTier": tier,
        "seatTier": "seat",
        "accountUuid": f"uuid-{account}",
    }


@pytest.fixture
def live_login(isolated_store):
    """Install a given account as the live login, as `claude auth login` would."""

    def _install(account: str, access: str = "a", refresh: str = "r", **extra_state):
        cauth.atomic_write_json(
            cauth.LIVE_CREDENTIALS, make_credentials(account, access, refresh)
        )
        full = {}
        if cauth.LIVE_CLAUDE_JSON.exists():
            full = json.loads(cauth.LIVE_CLAUDE_JSON.read_text())
        full["oauthAccount"] = make_oauth_account(account)
        full.update(extra_state)
        cauth.atomic_write_json(cauth.LIVE_CLAUDE_JSON, full, mode=0o644)

    return _install
