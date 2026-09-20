"""End-to-end state invariants identified by the UX audit; fixture accounts only."""

import pytest

import cauth


def seed(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    live_login("bob")
    cauth.register_live_as("bob")
    cauth.switch_to("alice")


def test_external_login_is_identified_without_trusting_index(live_login):
    seed(live_login)
    live_login("bob")
    state = cauth.account_status()
    assert state["installed"] == "bob"
    assert state["indexed"] == "alice"
    assert state["mismatch"]


def test_unsaved_login_cannot_be_silently_replaced(live_login):
    seed(live_login)
    live_login("charlie")
    cauth.mark_live_unregistered()
    with pytest.raises(cauth.CauthError, match="not saved"):
        cauth.switch_to("alice")
    assert cauth.read_live_state().email == "charlie@example.test"


def test_inconclusive_probe_keeps_installed_alias(live_login, monkeypatch):
    seed(live_login)
    monkeypatch.setattr(
        cauth, "run_claude_probe", lambda: cauth.ClaudeProbeResult(124, "Timed out")
    )
    result = cauth.switch_with_probe("alice")
    assert result.probe.kind == "unavailable"
    assert cauth.load_accounts()["active"] == "alice"
    assert cauth.account_status()["last_check"]["kind"] == "unavailable"


def test_verification_reports_refreshed_metadata_and_overrides(live_login, monkeypatch):
    seed(live_login)
    payload = cauth.read_json(cauth.LIVE_CREDENTIALS)
    payload["claudeAiOauth"]["expiresAt"] = 1
    cauth.atomic_write_json(cauth.LIVE_CREDENTIALS, payload)

    def refreshed(**kwargs):
        live_login("alice")
        return cauth.ClaudeProbeResult(0, "CAUTH_OK")

    monkeypatch.setattr(cauth, "run_claude_probe", refreshed)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "FIXTURE")
    report = cauth.validate_active()
    assert report.ok
    assert "EXPIRED" not in report.deadlines[0]
    assert any("ANTHROPIC_API_KEY" in message for message in report.warnings)


def test_registration_requires_complete_identity(live_login):
    live_login("alice")
    cauth.LIVE_CLAUDE_JSON.unlink()
    with pytest.raises(cauth.CauthError, match="identity"):
        cauth.register_live_as("alice")
    assert cauth.load_accounts()["accounts"] == {}


def test_partial_rename_rolls_back(live_login, monkeypatch):
    seed(live_login)
    old = cauth.profile_paths("bob")
    real = cauth.os.replace

    def fail(src, dst):
        if src == old[1]:
            raise OSError("fixture rename failure")
        return real(src, dst)

    monkeypatch.setattr(cauth.os, "replace", fail)
    with pytest.raises(OSError):
        cauth.rename_account("bob", "work")
    assert all(path.exists() for path in old)
    assert not any(path.exists() for path in cauth.profile_paths("work"))
    cauth.activate("bob")


def test_forget_last_profile_keeps_live_login_and_removes_backups(live_login):
    live_login("alice")
    cauth.register_live_as("alice")
    cauth.sync_back("alice")
    before = cauth.LIVE_CREDENTIALS.read_bytes()
    cauth.remove_account("alice")
    assert cauth.load_accounts()["accounts"] == {}
    assert cauth.load_accounts()["active"] is None
    assert cauth.LIVE_CREDENTIALS.read_bytes() == before
    assert not list(cauth.PROFILES_DIR.glob("alice.*"))


def test_cli_rejects_extra_words_before_mutation(live_login):
    live_login("alice")
    assert cauth.main(["add", "client", "acme"]) == 2
    assert not cauth.load_accounts()["accounts"]


def test_rebuild_recovers_complete_profiles(live_login):
    seed(live_login)
    cauth.ACCOUNTS_JSON.write_text("{")
    cauth.rebuild_index()
    assert set(cauth.load_accounts()["accounts"]) == {"alice", "bob"}
    assert cauth.account_status()["installed"] == "alice"


@pytest.mark.parametrize("name", ["CON", "client?", "a:b", "a\nnew", "trailing."])
def test_new_names_are_portable(name):
    with pytest.raises(cauth.CauthError):
        cauth.validate_alias(name)


def test_external_refresh_during_staging_is_preserved(live_login, monkeypatch):
    seed(live_login)
    original_stage = cauth.stage_json
    before = cauth.profile_paths("alice")[0].read_bytes()

    def stage_then_refresh(path, data, mode=cauth.FILE_MODE):
        staged = original_stage(path, data, mode)
        if path == cauth.LIVE_CLAUDE_JSON:
            live_login("alice", access="REFRESHED", refresh="REFRESHED")
        return staged

    monkeypatch.setattr(cauth, "stage_json", stage_then_refresh)
    with pytest.raises(cauth.ExternalChangeError):
        cauth.switch_to("bob")
    assert (
        "REFRESHED"
        in cauth.read_json(cauth.LIVE_CREDENTIALS)["claudeAiOauth"]["accessToken"]
    )
    assert cauth.profile_paths("alice")[0].read_bytes() == before
    assert cauth.load_accounts()["active"] == "alice"


def test_parallel_thread_cannot_mutate_during_operation(live_login):
    import threading

    seed(live_login)
    failures = []

    def competing():
        try:
            cauth.remove_account("bob")
        except cauth.CauthError as exc:
            failures.append(str(exc))

    with cauth.operation_lock():
        thread = threading.Thread(target=competing)
        thread.start()
        thread.join(timeout=2)
    assert failures and "running" in failures[0]
    assert "bob" in cauth.load_accounts()["accounts"]


def test_interrupted_transaction_can_be_restored(live_login):
    seed(live_login)
    snapshot = cauth.snapshot_files(cauth.managed_files())
    cauth.atomic_write_json(cauth.CONFIG_DIR / ".transaction.json", {"files": snapshot})
    cauth.profile_paths("bob")[0].unlink()
    with pytest.raises(cauth.CauthError, match="interrupted"):
        cauth.switch_to("alice")
    cauth.recover_transaction()
    assert cauth.profile_paths("bob")[0].exists()
    assert not (cauth.CONFIG_DIR / ".transaction.json").exists()


def test_probe_cancellation_terminates_the_real_child(monkeypatch):
    import subprocess
    import sys
    import threading

    original = subprocess.Popen
    children = []

    def launch(*args, **kwargs):
        child = original(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        children.append(child)
        return child

    monkeypatch.setattr(cauth.subprocess, "Popen", launch)
    event = threading.Event()
    event.set()
    cauth._local_operation.cancel_event = event
    try:
        result = cauth.run_claude_probe()
    finally:
        cauth._local_operation.cancel_event = None
    assert result.kind == "cancelled"
    assert children[0].poll() is not None


def test_changed_login_prompt_times_out_without_hanging(monkeypatch):
    import subprocess
    import sys

    original = subprocess.Popen
    children = []

    def launch(*args, **kwargs):
        child = original(
            [
                sys.executable,
                "-c",
                'import time; print("Unknown prompt", flush=True); time.sleep(30)',
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        children.append(child)
        return child

    monkeypatch.setattr(cauth.subprocess, "Popen", launch)
    assert cauth.run_claude_login(idle_seconds=0.05) == 124
    assert children[0].poll() is not None


def test_failed_rollback_commit_restores_whole_transaction(live_login, monkeypatch):
    seed(live_login)
    before = (cauth.LIVE_CREDENTIALS.read_bytes(), cauth.LIVE_CLAUDE_JSON.read_bytes())
    real_commit = cauth.commit_staged
    state_commits = 0

    def fail_during_rollback(temp, path):
        nonlocal state_commits
        if path == cauth.LIVE_CLAUDE_JSON:
            state_commits += 1
            if state_commits == 2:
                raise OSError("fixture rollback second-file failure")
        real_commit(temp, path)

    monkeypatch.setattr(cauth, "commit_staged", fail_during_rollback)
    monkeypatch.setattr(
        cauth,
        "run_claude_probe",
        lambda: cauth.ClaudeProbeResult(1, "OAuth session expired"),
    )
    with pytest.raises(cauth.CauthError, match="restore"):
        cauth.switch_with_probe("bob")
    assert (
        cauth.LIVE_CREDENTIALS.read_bytes(),
        cauth.LIVE_CLAUDE_JSON.read_bytes(),
    ) == before
    assert cauth.load_accounts()["active"] == "alice"
    assert not (cauth.CONFIG_DIR / ".transaction.json").exists()
