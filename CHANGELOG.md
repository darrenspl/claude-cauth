# Changelog

All notable changes to Claude Cauth are recorded here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Fixed

- Restore Python 3.9 compatibility when importing the default token terminal UI.

- Hidden setup-token input accepts quoted tokens and the supported export assignment.
  Bracketed paste also accepts standard labelled command output and finishes with one
  Enter. Ambiguous input and unknown surrounding text remain rejected without exposing
  the paste or repeating authorization.

- Prevent duplicate aliases in CLI registration as well as the Add form. Sync legacy
  aliases together so switching or renewing cannot reinstall an older token copy.
- Restore the visible `r Delete` action and label saved duplicates and expired renewals.
- Creating, importing, renewing, and selecting tokens now automatically install or
  repair Bash/Zsh shell integration, through both the CLI and terminal UI. Startup
  files are backed up privately before changes. Setup failures retain the token and
  explain how to retry. User-defined launch shortcuts remain independent of Cauth.

- Defer token-list refreshes until mounted and stop them during shutdown. UI crashes
  now show a short error, record safe stack locations, and abort pending login handoffs
  instead of printing tracebacks with locals and continuing into authorization.

- Preserve the complete token when pasted with Windows CRLF or carriage-return line
  breaks and indentation. CRLF is handled as one newline, not an early submission.

- Leading blank lines no longer submit and discard a token paste. Hidden input now
  confirms receipt, ignores terminal paste markers, and restores predictable line input
  after Claude's terminal UI.

- Hidden token input accepts wrapped multi-line tokens and retries invalid pastes without
  repeating browser authorization. Echo remains disabled throughout the entire paste.

### Added

- Automatic private rotating JSON diagnostics for token lifecycle, paste validation,
  Claude launches, verification and cleanup. `cauth logs` and the UI's g key show recent
  events without tokens, pasted text, prompts, aliases or raw exception messages.

- Long-lived OAuth token creation/import, secure atomic storage, renewal, selection,
  rename/removal, token verification and Claude launching are now the default workflow.
  Add secret-free shell integration; preserve browser profiles under `cauth legacy`.

- Recovery actions and CLI commands to clear an unfinished login without rollback
  (`clear-login --yes`) or reset all saved/live file logins, backups, and pending state
  (`reset --yes`). Reset works with damaged Cauth indexes and pending records, preserves
  unrelated Claude settings, and retains no credential recovery copy.

### Changed

- Remote installers require an explicit `CAUTH_REPO_URL`; local-checkout installation
  is unchanged. Private project notes and infrastructure references are removed from
  the source tree in preparation for public distribution.

- Restore a compact account-focused token UI: bordered summary, bold names and muted
  details, a full-height account list, equal-width controls, and separate Help/Logs screens.

- Signing in to an already saved account automatically replaces its stored credentials,
  keeping its alias and skipping the naming dialog. Store current also updates directly.
- Separate saved accounts with a visible divider, bold account names, indented muted
  details, and a focused selection highlight so multiline entries remain easy to scan.

## 0.2.0 - 2026-09-10

### Changed

- Reconcile installed identity and retain classified, timestamped verification results.
- Recover interrupted account operations; preserve external changes detected during staging.
- Restore cancelled/failed browser login and keep naming errors editable.
- Add account-centered Home, search, Renew, Recovery, narrow-terminal forms and cancellable checks.
- Forget saved profiles and backups without logging Claude out, including the last profile.
- Add CLI verify/JSON/doctor/recovery, strict arguments and explicit unverified-switch output.
- Verify TUI imports during installation and document the updated lifecycle.

### Added

- Verify screen (`v` on the home screen). Checks the account Claude Code is signed in as
  right now: alias, email, plan, subscription type, access-token and login-renewal deadlines,
  plus a real bounded `claude -p` request that reports PASS only when Anthropic accepts the
  login. It switches nothing and writes nothing, so it is safe to run mid-session.
- Remote installers for all three desktop platforms. A streamed `install.sh` handles Linux
  and macOS, while `install.ps1` provides the Windows PowerShell entry point. Both clone or
  fast-forward a stable managed checkout, refuse dirty or unexpected destinations, and then
  reuse the existing platform-native venv and launcher installation.
- Browser login now hides the pasted authorization code and confirms receipt with only its
  first and last four characters, such as `abcd...wxyz`. The complete code is forwarded in
  memory to the official Claude process and is never printed or placed on its command line.
  This applies to both the TUI add flow and `cauth login <alias>` renewal.
- TUI account switches now run a bounded request through the official `claude -p` client.
  The Switch screen persistently shows TESTING, PASS, or the captured token-redacted FAIL.
  A failed target keeps its stored profile intact and restores the previously active alias
  when possible. See [ADR 0004](docs/adr/0004-probe-before-switch-success.md).
- `cauth login <alias>` renews a stored account through Claude's official browser flow,
  pre-fills its known email, strips auth overrides for the child process, restores the
  previous live account when login fails, and identity-checks success before replacing the
  parked profile.
- Windows support. `install.cmd` builds the same private venv and `~/.local/bin` launcher
  that `install.sh` does, preferring `uv` because the `python.exe` on PATH under
  `WindowsApps` is a Microsoft Store stub rather than an interpreter. See ADR 0003.

### Fixed

- Switching no longer reports success merely because credential files copied successfully.
  A selected account is successful in the TUI only after Claude accepts a real request.
- Status now distinguishes a tokenless logout, an expired access token that Claude may
  refresh during the TUI probe, and an expired or approaching OAuth login-renewal deadline.
- Every credential write raised on Windows. `os.open` on a directory fails there, and the
  directory fsync that follows `os.replace` called it outside the guarded block, so the
  error escaped after the write had already landed and a completed switch was reported as
  a failure. Both call sites now go through one helper that skips the sync on win32 only.
- The test suite assumed a POSIX host: four tests asserted permission bits that Windows
  does not have, four exercised a no-DISPLAY branch that never runs there, and one drove
  the Textual app with a fixed number of pauses. Gated, pinned, and polled respectively.

- Refuse Claude Code's truthy but tokenless logout credential skeleton during registration,
  sync-back, and activation. It can no longer overwrite a stored profile or its recovery
  backup, and both the TUI and CLI now warn when a stale identity block makes a logged-out
  account look active.
- Fully release the terminal before `claude auth login`. The previous worker-thread
  suspension left Textual's mouse reporting and input reader active, which prevented normal
  selection, stole pasted authorization codes, printed mouse escape reports as junk, and
  could not resume cleanly. Cauth now exits first, runs Claude with sole terminal ownership,
  then relaunches directly into alias capture.
- Reconcile the active alias after external login and verify it before treating a switch as
  a no-op. Cancelling alias capture now leaves the fresh account explicitly unregistered,
  while selecting an alias named active repairs a missing, tokenless, or mismatched live
  login from its stored profile.

## 0.1.0 - 2026-08-07

Initial version.

### Added

- **Account switching.** Register any number of Claude Code accounts under aliases and swap
  the active one in place. `s` in the TUI, or `cauth switch <alias>`.
- **Sync-back before every swap.** The outgoing account's live credentials are copied to its
  profile before the incoming account's are installed. Claude Code refreshes tokens during a
  session, so the live file is newer than what was stored at registration. Without this step
  an account breaks the first time its token rotates.
- **Sync-back before an external login.** `claude auth login` overwrites the live credentials
  itself, before cauth is involved. The outgoing account is synced ahead of the browser
  handoff, so a token refreshed during the session is not destroyed by the login.
- **Batch adding.** Adding an account no longer strands you on it. The login signs you in as
  the new account, then cauth returns you to the one you were using, so several accounts can
  be added in one sitting from a standing start.
- **Terminal UI** built on Textual: home, switch, add, rename, remove, a reusable y/n confirm
  dialog, and an alias input modal.
- **Alias renaming** with `e`, including on the active account. A rename moves the stored copy
  and never touches the live login.
- **Built-in help** on `?`, covering the two-file model, why a switch affects sessions that
  are already running, the browser handoff and its no-DISPLAY fallback, and the environment
  variables that defeat the tool.
- **Live hazard detection.** The home screen reports when `CLAUDE_CODE_OAUTH_TOKEN` or
  `ANTHROPIC_API_KEY` is set, either of which silently defeats the file swap, and when cauth
  is running inside a Claude Code session or without a `DISPLAY`.
- **Full CLI**, usable without Textual installed: `status`, `list`, `switch`, `add`, `rename`,
  `remove`.
- **`install.sh`**, which builds a private virtualenv and puts a `cauth` launcher on `PATH`
  without touching the system Python. Idempotent.
- 74 tests against fixture credentials, and CI on Python 3.9, 3.12 and 3.13.

### Security

- Every write to a live file goes through a temp file plus `os.replace`, so a crash
  mid-switch cannot leave a truncated credentials file.
- `~/.claude.json` is patched by loading the whole file, changing the one key, and writing it
  back, so unrelated Claude Code state is preserved. The file's existing permissions are
  preserved rather than replaced, so a user who tightened it does not get it widened.
- The profile store is `700` and every file in it is `600`, enforced and tested.
- Aliases are validated before use. They become filenames, so empty, oversized, `.`, `..`,
  and separator-bearing aliases are refused at both registration and rename.
- Malformed or unreadable JSON raises a handled error rather than a traceback, so a
  hand-edited `~/.claude.json` produces a message instead of a crash.
- No network calls and no telemetry. Tokens are never printed, logged, or placed on a command
  line. The test suite redirects every path into a temp directory via an autouse fixture and
  asserts the redirect landed, so it cannot reach a real login.

### Known limitations

- The first login for each account still requires a browser. Cauth removes the browser step
  from every switch after that, not from the initial sign-in.
- A switch affects Claude Code sessions that are already running, because they read the same
  credentials file. Run cauth in its own terminal.
- Developed and tested on Linux. macOS and Windows are untested rather than unsupported.
