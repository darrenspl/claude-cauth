# ADR 0006: Remote cross-platform installers

- Status: Accepted
- Date: 2026-08-18

## Context

The original installers work only after a user clones the repository. A common command-line
installation pattern streams a small script from a remote repository and runs it directly.
Cauth needs that convenience on Linux and macOS, and a native equivalent on Windows.

A streamed script has no repository beside it. The existing launcher deliberately executes
the source checkout directly, so pointing it at a temporary download would leave a broken
command as soon as the temporary directory disappeared. The source must land at a durable
location before the existing venv and launcher installation runs.

Bash and PowerShell need separate entry points. Repository access depends on the
source URL and user authentication; installers must not assume a hosting account.

## Decision

Use two remote-capable entry points with one managed source layout:

1. `install.sh` serves Linux and macOS. When run from a complete checkout it keeps the
   existing local behavior. When streamed or downloaded alone, it enters remote mode.
2. `install.ps1` serves Windows PowerShell. Local execution delegates to `install.cmd`.
   Remotely evaluated execution enters remote mode and then delegates to the cloned
   checkout's `install.cmd`.
3. Remote mode clones `main` into `~/.local/share/claude-cauth/app`. The existing private
   venv remains beside it at `~/.local/share/claude-cauth/venv`, and launchers remain under
   `~/.local/bin`.
4. A repeat run fetches and fast-forwards the managed checkout only when it is clean, on
   `main`, and has the expected origin. A dirty checkout, detached HEAD, different branch,
   different origin, or non-repository destination stops with an error.
5. `CAUTH_APP_DIR`, `CAUTH_VENV`, `CAUTH_BIN`, and `CAUTH_REPO_URL` override the source,
   virtualenv, command, and remote locations respectively.
6. Remote mode requires an explicit `CAUTH_REPO_URL`. No personal or private fallback
   repository URL is embedded. Local-checkout installation does not need that setting.

## Alternatives rejected

- **One polyglot installer**: Bash is not native to Windows, and PowerShell is not present on
  a normal Linux or macOS installation.
- **Point the launcher at a temporary checkout**: the command breaks when temporary files are
  cleaned up.
- **Download only the Python files**: this invents a second update mechanism, loses Git's
  origin and dirty-tree safety checks, and can mix files from different revisions after a
  partial download.
- **Reset the managed checkout on every run**: this can silently destroy changes. The
  installer stops and asks the user to resolve the checkout instead.
- **Assume anonymous repository access**: access depends on the supplied source and its
  visibility. Git authentication should use a credential manager, not a token in a URL.

## Consequences

- Linux and macOS share one Bash entry point; Windows has a native PowerShell entry point.
- Local clones retain their existing behavior and do not move into the managed app directory.
- Remote installs have a stable source checkout, so rerunning the installer can update both
  code and dependencies without rebuilding a download strategy.
- Publication does not configure a default source automatically. Remote installations
  continue to use an explicitly supplied repository URL.
- Shell behavior is exercised through isolated clone, update, launch, and dirty-tree tests.
  PowerShell behavior is exercised by streaming the real script, cloning a fixture remote,
  invoking a platform-native fixture installer, and refusing a dirty managed checkout.
- This makes installation portable to macOS, but does not resolve the separate open question
  of whether Claude Code stores credentials in Keychain rather than the files Cauth manages.
