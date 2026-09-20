# ADR 0003: A separate Windows installer, and no directory fsync there

- Status: Accepted
- Date: 2026-08-13

## Context

Cauth was built and installed on Linux. It has to run natively on Windows too: Claude Code
is installed there as `claude.exe`, it keeps a live `.credentials.json` and `.claude.json`
under the user's home directory, and it holds one account at a time on that machine exactly as it
does on any other. Reaching those files from WSL is not the same thing as running there,
because a WSL cauth swaps the WSL account, and the Windows Claude Code never sees it.

Two things stood between the code and that machine.

**1. `install.sh` cannot be made to work.** It writes `~/.local/bin/cauth` as a bash
wrapper with a shebang. Windows does not execute shebangs. Making one script serve both
would mean a POSIX shell on every Windows box, which is a heavier prerequisite than the
tool itself.

**2. The write path raised on every credential write.** Both `commit_staged` and
`atomic_write_json` fsynced the parent directory after `os.replace`, to persist the rename
and not only the bytes. On win32 `os.open(directory, os.O_RDONLY)` raises `PermissionError`,
and it is the open that fails, not the fsync, so the existing `except OSError` around the
fsync did not catch it. The exception escaped after `os.replace` had already committed. The
credential write had landed and cauth reported it as failed, which is the worst available
outcome for a tool whose promise is that it never loses an account.

## Decision

**`install.cmd`, alongside `install.sh`.** Same shape as the POSIX installer, so both
platforms keep one layout: a private venv at `%USERPROFILE%\.local\share\claude-cauth\venv`,
a launcher at `%USERPROFILE%\.local\bin\cauth.cmd` that execs that interpreter against the
repo checkout, `CAUTH_VENV` and `CAUTH_BIN` overriding both, and `--version` rather than
`status` as the verification, for the reason `install.sh` already records.

It prefers `uv` over the system interpreter. The `python.exe` that ships on PATH under
`WindowsApps` is a two byte Microsoft Store stub that opens the Store instead of running
anything, and it shadows a real install depending on PATH order, so trusting `python` here
fails in a way that reads as a broken installer rather than a missing Python.

**One `fsync_dir` helper that returns early on win32.** Not a wider `except` around the
existing code: that would also swallow a genuine durability loss on Linux and macOS, where
the sync is a real guarantee. Windows is a platform that cannot do this at all, which is a
different fact from a sync that failed, and the code says so.

## Alternatives rejected

- **Run cauth from WSL against `/mnt/c`**: it would swap the files, but `CLAUDE_DIR` and
  `Path.home()` resolve to the WSL home, so making it work means hand-set environment
  variables on every invocation, and one wrong invocation writes the wrong account's tokens
  into the wrong home. Rejected as a footgun aimed at the one file that must not be lost.
- **Make `install.sh` cross-platform under Git Bash**: Git Bash is not on a bare Windows
  box, and the wrapper it wrote still would not run from `cmd` or PowerShell.
- **PowerShell installer**: works, and lands on execution policy, which is a support
  conversation for a script that copies two paths into a launcher. `.cmd` has no such gate.
- **Drop the directory fsync everywhere**: rejected. It is the difference between a
  persisted rename and a directory entry pointing at nothing after a power loss, on the
  platforms that can offer it.

## Consequences

- Windows gets the same guarantees as POSIX except file modes. `os.chmod` there only
  toggles a read-only bit, so the 700 on the store and 600 on its contents are Linux and
  macOS only, as `README.md` already says. Four tests that assert modes carry a
  `posix_modes_only` marker and skip there.
- The suite runs on Windows and is expected to stay green. It found all of this.
- Two installers now have to be kept in step. They are short and the layout is shared, so
  the drift risk is a launcher line, not a design.
