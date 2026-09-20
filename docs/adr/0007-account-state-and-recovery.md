# ADR 0007: Separate installed identity, verification, and recoverable operations

- Status: Accepted
- Date: 2026-09-10
- Scope: Legacy browser profiles; supersedes conflicting outcome and cancellation clauses in ADRs 0002 and 0004.

## Decision

Installed identity is reconciled against complete saved profiles. An index pointer is a
selection hint, not proof of identity or authentication. Verification is a separate,
timestamped result: verified, sign-in required, rate limited, cancelled, or unavailable.
Timeout and process failure do not erase installed identity or automatically imply renewal.
Verification reads metadata after the request and reports authentication environment overrides.

Home is an account selection surface. Account rows retain email, plan and organization
context. Forms validate before dismissal, show inline errors and render aliases literally.
Renewal is reachable through the TUI; existing identities update their saved label instead
of accidentally creating duplicate labels through the normal Add flow.

A login operation saves the prior live files and index before the official client runs.
The TUI still fully exits before browser login. On login failure, wrong renewal identity,
or explicit cancellation, the prior files are restored. Naming retains its return destination
across invalid input and cancellation. Successful Add/Renew returns to the previously
installed saved account when available. A pending login snapshot survives process interruption
and is recoverable from Recovery. An unsaved outgoing login requires saving or explicit discard
before a switch; starting another browser login requires saving it first.

Cauth operations use a cross-process OS lock and a reentrant thread lock. Multi-file
mutations write a mode-600 journal containing the previous file contents before mutation.
Nested operations share the transaction. Errors roll back; abrupt process interruption
leaves the journal for explicit recovery. Staging detects concurrent live-file changes and
preserves the external writer's files while rolling back Cauth's saved-file changes.
Cauth cannot serialize the official Claude client, so shared-file race risk remains.

Checks run in worker threads with elapsed time and a 90-second bound. Esc cancels the
child and allows switch recovery to complete. Quit waits for that cancellation/recovery
path. A failed target is not synced over its parked pair during rollback.

Forget removes the saved pair and rotation backups, even for the installed/last account.
It does not change the live Claude login. Interrupted-operation snapshots are recovery
material, not a permanent account archive. Index rebuild retains a protected copy of the
old metadata and leaves incomplete profiles untouched.

CLI switching remains file-only and says UNVERIFIED. CLI verify provides explicit exit
semantics and optional JSON. Doctor is nonmutating and does not read credentials. Installers
verify the TUI import as well as version/launcher execution.

## Consequences and limits

Recovery snapshots contain credentials and require the same permissions as profiles. They
are removed after successful completion or restore. Restoring a snapshot can replace later
external changes; the UI asks explicitly and advises closing other sessions. A damaged
journal may need manual diagnosis; Cauth retains it when automatic restoration fails.

The old tests asserting active-profile removal refusal, a cleared pointer after wrong-account
renewal, and unchanged check metadata are updated to these deliberate new contracts. Tests
continue to assert preservation of live credentials and protected stored profiles.

Native Windows/macOS credential storage, screen readers and real browser/provider behavior
are not proven by fixture tests. Platform support statements must remain qualified.
