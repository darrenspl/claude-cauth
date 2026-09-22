# Compatibility handoff

## Terminal activation follow-up — 2026-09-22

A terminal opened before Cauth's shell integration was installed still used the old
Claude launch path and asked for login. Loading `cauth shell-init zsh` with `eval`
in that terminal resolved it; the user confirmed a successful interactive response.
Fresh-shell `claude` and `cc` requests were also verified on macOS. No replacement
token, credential-file rewrite, or parent-shell environment export was required.

Every token save (create/import/renew) and selection already calls the shared
startup-integration installer. Keep that path idempotent. The follow-up makes its
activation guidance explicit and adds a prominent post-TUI exit notice, with the
command outside the box for copying. Normal `claude` and user shortcuts are the
primary workflow; launching inside Cauth is optional.

An existing terminal needs activation once, or the user can open a new terminal
after successful Bash/Zsh setup. Subsequent switches are read on each new Claude
launch without refreshing the terminal. Existing Claude processes keep their token.
Never claim that Cauth can change its parent shell. Tests cover installer repair on
save/renew/select, same-shell token switching, and instructions surviving TUI exit.

The macOS Python 3.12 full suite passed 286 tests with two PowerShell tests skipped.
After expanding the same-shell regression to cover Bash and Zsh, all 55 token-workflow
tests passed. The installed Cauth launcher executes this checkout directly; no reinstall
is needed for these Python/UI changes. Windows runtime acceptance remains separate.

## Previous compatibility handoff

Updated 2026-09-21. The public repository is available; the earlier public-preparation
handoff is superseded. The current branch is `main`, based on public root commit
`dd49714be50b4b7095210b3aaad28cde6d08fff8`.

## Completed work

Python 3.9 remains supported. The default token UI now postpones annotation evaluation
so `str | None` annotations do not fail during import on Python 3.9. The pre-mount
resume test constructs its Textual screen inside an async runner, matching the runtime
requirements of supported Textual versions. No authentication behavior changed.

The full fixture suite passed **287 tests on each of Python 3.9, 3.12, and 3.13**
after the compatibility changes. TUI imports and the CLI engine's import without
Textual were also checked. These are Linux fixture results; they do not establish
live account validity or native Windows/macOS acceptance.

## Synchronization and scope

The compatibility fix and these documents are intended for an ordinary commit and
fast-forward push to every configured remote. Read current local and remote refs and
the CI run for the exact pushed commit before treating synchronization or hosted checks
as complete. The historical failed CI run remains historical evidence, not current status.

The sanitized public root is retained. Normal subsequent commits are expected; do not
rewrite history to preserve a one-commit repository. Keep neutral contributor identity,
portable documentation, and private account data outside the repository. Recovery locations
may exist in repository-local Git configuration under `cauth.publicPrepBackup` and
`cauth.publicPrepCandidate`; never copy their values into tracked documents.

No production deployment or live-account operation is part of this closeout. There is
no additional product work queued by this handoff. See the [restart prompt](goal-prompt.md)
for read-only orientation and verification boundaries.
