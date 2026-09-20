# ADR 0002: Exit Textual before Claude login

- Status: Accepted; login I/O clauses superseded by ADR 0005
- Date: 2026-08-07

ADR 0005 keeps the full Textual teardown from this decision, but replaces direct child
terminal inheritance with a narrow code-input mediator so Cauth can confirm a paste without
revealing the complete authorization code.

## Context

The original add-account flow called `app.suspend()` inside a synchronous
`@work(thread=True)` worker, then ran `claude auth login` in that suspended block. The
intent was correct: Claude needs inherited terminal input and output for a browser URL and
authorization-code prompt. The thread boundary was not.

Textual 8.x configures the terminal on its application thread. On Linux, suspension starts
by resetting a signal handler before disabling mouse reporting and joining Textual's input
thread. Python refuses signal changes outside the main thread. Textual swallowed that
exception, so the terminal was only partly restored:

- any-event mouse reporting remained enabled, preventing normal selection and producing
  visible SGR mouse escape reports;
- Textual's input thread remained alive and raced Claude for pasted text and Enter;
- resuming application mode failed on another main-thread-only operation.

Running `app.suspend()` on Textual's main thread is the documented API and fixes that exact
failure. Textual 8.2.8 also has exception paths where an error escaping the suspended body
can skip resume, and suspension remains an unnecessary shared lifecycle for this product.

## Decision

Treat the login as a top-level controller transition:

1. Cauth syncs the outgoing active profile before login can replace it.
2. The Textual app exits with a small `LoginRequest` result.
3. Only after `CauthApp.run()` returns does the controller invoke
   `claude auth login` with inherited terminal input and output.
4. On success, the controller clears `accounts.json`'s active pointer because no stored
   alias describes the newly authenticated live account yet.
5. The controller creates a fresh `CauthApp`, which opens directly at the alias modal and
   completes the existing registration and return flow.
6. On failure or cancellation, Cauth reports the child status and relaunches normally. A
   failed login can repair the previously active alias by selecting it again.

No terminal output is captured or parsed. Claude remains responsible for its browser URL,
prompt, OAuth exchange, and credential writes.

## Alternatives rejected

- **Suspend Textual on its main thread**: the smallest code change and valid according to
  Textual's API. Rejected because a complete teardown gives a stronger single-owner
  boundary and avoids suspension-specific exception and platform behavior.
- **Disable mouse reporting manually**: fixes selection but leaves Textual's input reader
  competing with Claude, so paste and Enter remain unreliable.
- **Proxy Claude through a new PTY and parse its output**: duplicates terminal behavior,
  couples Cauth to Claude's output format, and adds avoidable failure modes around wrapped
  URLs and interactive prompts.
- **Open another terminal window**: cross-platform terminal discovery, shell quoting, WSL
  host boundaries, and completion tracking are less reliable than the inherited terminal.

## Consequences

- Claude is the only process reading the terminal during authentication.
- URL clicking and text selection use the terminal's normal behavior, and pasted codes go
  directly to Claude.
- The full-screen TUI briefly exits and restarts around login. This is intentional and
  visible.
- A successful login resumes at alias capture without asking the user to navigate back to
  Add.
- Cancelling alias capture leaves the fresh login live but explicitly unregistered. It does
  not leave the old alias falsely marked active or overwrite the new login.
- Selecting an alias already named active verifies the live token pair and identity before
  treating the switch as a no-op. A stale pointer restores the stored profile directly.
- PTY regression coverage must prove terminal cooked/echo mode, mouse-mode teardown, exact
  paste delivery, successful relaunch, and clean final teardown using fake credentials only.
