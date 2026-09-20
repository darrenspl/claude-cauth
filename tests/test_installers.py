"""Remote installers must land on a stable checkout and preserve local changes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


def run(
    *argv: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def commit_all(repo: Path, message: str) -> None:
    run("git", "add", ".", cwd=repo)
    run("git", "commit", "--quiet", "-m", message, cwd=repo)


def make_source_repo(tmp_path: Path, files: tuple[str, ...]) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    for name in files:
        shutil.copy2(REPO / name, source / name)
    run("git", "init", "--quiet", cwd=source)
    run("git", "checkout", "--quiet", "-b", "main", cwd=source)
    run("git", "config", "user.name", "Cauth Test", cwd=source)
    run("git", "config", "user.email", "cauth@example.test", cwd=source)
    commit_all(source, "fixture source")
    return source


def make_fake_uv(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "if [ \"$1\" = venv ]; then\n"
        "  mkdir -p \"$2/bin\"\n"
        "  cat > \"$2/bin/python\" <<'FIXTURE'\n"
        "#!/usr/bin/env bash\n"
        "exec \"$FIXTURE_PYTHON\" \"$@\"\n"
        "FIXTURE\n"
        "  chmod +x \"$2/bin/python\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = pip ]; then exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o700)
    return fake_bin


def shell_install_env(tmp_path: Path, source: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "home"
    app = tmp_path / "managed-app"
    venv = tmp_path / "venv"
    bin_dir = tmp_path / "bin"
    home.mkdir(exist_ok=True)
    fake_bin = make_fake_uv(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": os.pathsep.join((str(bin_dir), str(fake_bin), env.get("PATH", ""))),
            "FIXTURE_PYTHON": sys.executable,
            "CAUTH_REPO_URL": source.as_uri(),
            "CAUTH_APP_DIR": str(app),
            "CAUTH_VENV": str(venv),
            "CAUTH_BIN": str(bin_dir),
        }
    )
    return env, app


def run_streamed_shell_installer(
    tmp_path: Path, env: dict[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash"],
        cwd=tmp_path,
        env=env,
        input=(REPO / "install.sh").read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
    )


def run_streamed_powershell_installer(
    tmp_path: Path, env: dict[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", "-"],
        cwd=tmp_path,
        env=env,
        input=(REPO / "install.ps1").read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
    )


@pytest.mark.skipif(os.name != "posix", reason="Bash launcher is POSIX-only")
def test_streamed_shell_installer_requires_explicit_repository(tmp_path):
    env = os.environ.copy()
    env.pop("CAUTH_REPO_URL", None)
    app = tmp_path / "must-not-create"
    env["CAUTH_APP_DIR"] = str(app)
    result = run_streamed_shell_installer(tmp_path, env)
    assert result.returncode != 0
    assert "Set CAUTH_REPO_URL" in result.stderr
    assert not app.exists()


@pytest.mark.skipif(os.name != "posix", reason="Bash launcher is POSIX-only")
def test_local_shell_installer_does_not_require_repository_url(tmp_path):
    source = make_source_repo(tmp_path, ("install.sh", "cauth.py", "tui.py", "tokenui.py", "tokenauth.py", "diagnostics.py", "requirements.txt", ".gitignore"))
    env, app = shell_install_env(tmp_path, source)
    env.pop("CAUTH_REPO_URL")
    result = run("bash", str(source / "install.sh"), cwd=tmp_path, env=env)
    assert result.returncode == 0
    assert not app.exists()
    launcher = Path(env["CAUTH_BIN"]) / "cauth"
    assert str(source / "cauth.py") in launcher.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is not installed")
def test_streamed_powershell_installer_requires_explicit_repository(tmp_path):
    env = os.environ.copy()
    env.pop("CAUTH_REPO_URL", None)
    app = tmp_path / "must-not-create"
    env["CAUTH_APP_DIR"] = str(app)
    result = run_streamed_powershell_installer(tmp_path, env)
    assert result.returncode != 0
    assert "Set CAUTH_REPO_URL" in result.stdout + result.stderr
    assert not app.exists()


@pytest.mark.skipif(os.name != "posix", reason="Bash launcher is POSIX-only")
def test_streamed_shell_installer_clones_updates_and_launches(tmp_path):
    source = make_source_repo(tmp_path, ("install.sh", "cauth.py", "tui.py", "tokenui.py", "tokenauth.py", "diagnostics.py", "requirements.txt", ".gitignore"))
    env, app = shell_install_env(tmp_path, source)

    first = run_streamed_shell_installer(tmp_path, env)
    assert first.returncode == 0, first.stdout + first.stderr
    launcher = Path(env["CAUTH_BIN"]) / "cauth"
    assert app.joinpath(".git").is_dir()
    installed_version = run(str(launcher), "--version", cwd=tmp_path, env=env)
    assert installed_version.stdout.strip() == "cauth 0.2.0"
    assert str(app / "cauth.py") in launcher.read_text(encoding="utf-8")

    source.joinpath("remote-update.txt").write_text("updated\n", encoding="utf-8")
    commit_all(source, "fixture update")
    second = run_streamed_shell_installer(tmp_path, env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert app.joinpath("remote-update.txt").read_text(encoding="utf-8") == "updated\n"


@pytest.mark.skipif(os.name != "posix", reason="Bash launcher is POSIX-only")
def test_streamed_shell_installer_refuses_dirty_or_wrong_managed_checkout(tmp_path):
    source = make_source_repo(tmp_path, ("install.sh", "cauth.py", "tui.py", "tokenui.py", "tokenauth.py", "diagnostics.py", "requirements.txt", ".gitignore"))
    env, app = shell_install_env(tmp_path, source)
    first = run_streamed_shell_installer(tmp_path, env)
    assert first.returncode == 0, first.stdout + first.stderr

    local_file = app / "keep-my-work.txt"
    local_file.write_text("do not overwrite\n", encoding="utf-8")
    second = run_streamed_shell_installer(tmp_path, env)

    assert second.returncode != 0
    assert "has local changes; refusing to overwrite them" in second.stderr
    assert local_file.read_text(encoding="utf-8") == "do not overwrite\n"

    local_file.unlink()
    run(
        "git",
        "remote",
        "set-url",
        "origin",
        "https://example.test/not-cauth",
        cwd=app,
    )
    third = run_streamed_shell_installer(tmp_path, env)
    assert third.returncode != 0
    assert "belongs to a different origin" in third.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is not installed")
def test_streamed_powershell_installer_clones_and_runs_native_installer(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    native_installer = source / "install.cmd"
    if os.name == "nt":
        native_installer.write_text(
            '@echo off\r\n> "%CAUTH_TEST_MARKER%" echo installed\r\n',
            encoding="utf-8",
        )
    else:
        native_installer.write_text(
            '#!/usr/bin/env sh\nprintf "installed\\n" > "$CAUTH_TEST_MARKER"\n',
            encoding="utf-8",
        )
        native_installer.chmod(0o700)
    run("git", "init", "--quiet", cwd=source)
    run("git", "checkout", "--quiet", "-b", "main", cwd=source)
    run("git", "config", "user.name", "Cauth Test", cwd=source)
    run("git", "config", "user.email", "cauth@example.test", cwd=source)
    commit_all(source, "fixture source")

    app = tmp_path / "managed-app"
    marker = tmp_path / "native-installer-ran.txt"
    env = os.environ.copy()
    env.update(
        {
            "CAUTH_REPO_URL": source.as_uri(),
            "CAUTH_APP_DIR": str(app),
            "CAUTH_TEST_MARKER": str(marker),
        }
    )
    result = run_streamed_powershell_installer(tmp_path, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert app.joinpath(".git").is_dir()
    assert marker.read_text(encoding="utf-8").strip() == "installed"

    marker.unlink()
    local_file = app / "keep-my-work.txt"
    local_file.write_text("do not overwrite\n", encoding="utf-8")
    second = run_streamed_powershell_installer(tmp_path, env)
    assert second.returncode != 0
    assert "has local changes; refusing to overwrite them" in second.stdout + second.stderr
    assert local_file.read_text(encoding="utf-8") == "do not overwrite\n"
    assert not marker.exists()
