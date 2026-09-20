# ADR 0004: Probe before reporting a TUI switch successful

- Status: Accepted
- Date: 2026-08-18

## Context

Cauth previously treated an atomic credential-file swap as a successful account switch.
That proved only that local JSON could be copied. A stored OAuth pair can still be expired,
revoked, or impossible to refresh. In that state the TUI named the target active, but the
next Claude Code session immediately reported `Login expired`.

Local shape checks and `claude auth status` cannot prove server acceptance. The official
Claude client must make a real authenticated request. The proof must happen before the TUI
claims success, and it must not leave the user stranded on a rejected account.

## Decision

Every account selected in the TUI Switch screen follows this sequence:

1. Sync the outgoing live credentials back to their stored profile.
2. Install the selected profile atomically.
3. Run a bounded request through the official `claude -p` client on a worker thread.
4. Show TESTING while the request runs and disable navigation that could start another switch.
5. Show PASS only when Claude exits 0. Keep the selected alias active.
6. On nonzero exit, timeout, or launch failure, show FAIL with bounded, token-redacted child
   output. Preserve the selected stored profile for later renewal.
7. Restore the previously active alias when possible. If there is no distinct previous alias,
   clear the active pointer rather than retain a known false claim.

The probe requests Haiku, disables tools, slash commands, MCP servers, and session
persistence, and has a 90-second timeout. Authentication override environment variables are
removed so the probe can exercise only the credential file Cauth installed.

The noninteractive `cauth switch <alias>` command remains a file-operation primitive and does
not run the network probe. Scripts that use it retain their existing behavior.

## Alternatives rejected

- **Trust complete local token fields**: refresh tokens can be expired or revoked while still
  looking complete.
- **Trust `claude auth status`**: it can report local login metadata without proving an
  authenticated model request.
- **Probe after returning to the home screen**: this recreates the false-success window and
  makes rollback ambiguous.
- **Delete a profile that fails**: failure may be recoverable through `cauth login <alias>`;
  deleting it removes recovery evidence and surprises the user.

## Consequences

- A TUI switch now incurs one small Claude request and can take several seconds.
- PASS means the selected account completed a real request, not merely that files were copied.
- FAIL contains the actionable Claude error and the renewal command.
- The last working alias remains active after a rejected target whenever rollback is possible.
- Probe output must remain bounded and token-redacted, and the worker must never own Textual's
  terminal.
