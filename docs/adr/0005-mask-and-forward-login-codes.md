# ADR 0005: Mask and forward browser login codes

- Status: Accepted
- Date: 2026-08-18

## Context

ADR 0002 fixed a broken browser login by fully exiting Textual before running
`claude auth login`. It then gave the Claude child direct ownership of terminal input and
output. That made paste reliable, but Cauth could not tell whether a paste arrived. A user
could press Enter, see no visible input, and have no confirmation until Claude either
accepted or rejected the code.

The confirmation must be useful without exposing the complete authorization code. The login
must still preserve the terminal teardown, auth-override removal, child exit status, renewal
email prefill, cancellation behavior, and post-login TUI relaunch established by the prior
work.

Claude Code 2.1.234 was tested with an isolated temporary config and a fixture code. It
continued to print its browser URL and paste prompt when stdout was piped, accepted its code
on piped stdin, and rejected the fixture normally. This permits a standard-library solution
on Linux, macOS, and Windows without emulating a terminal.

## Decision

Keep the top-level Textual teardown from ADR 0002, then mediate only the login streams:

1. Start `claude auth login` with piped stdin and combined piped output.
2. Mirror every Claude output byte to the user's terminal, preserving its URL and messages.
3. When Claude emits `Paste code here if prompted >`, read one line from the real terminal
   with echo disabled.
4. Refuse `getpass`'s visible-input fallback if the terminal cannot hide the paste.
5. Print `Code received: abcd...wxyz`, using only the first and last four characters. Codes
   too short to retain a hidden middle receive no character preview. Escape control
   characters before printing any preview.
6. Forward the complete line to Claude through the pipe, keep it only in process memory, and
   discard Cauth's reference immediately. Repeat if Claude asks for another code.
7. Preserve the child's real exit status and the existing 127, 126, and 130 launch and
   cancellation statuses.

Both the TUI add path and `cauth login <alias>` call the same mediator so their behavior
cannot drift.

## Alternatives rejected

- **Keep direct terminal inheritance**: reliable paste, but Cauth cannot acknowledge receipt
  without seeing the code.
- **Echo the pasted line normally**: confirms receipt by exposing the entire authorization
  code in the terminal and any transcript or screen recording.
- **Pass the code as a command argument**: `claude auth login` has no authorization-code
  option, and command arguments can be visible in process listings.
- **Proxy through a pseudo-terminal**: POSIX has `pty`, but Python's standard library has no
  equivalent Windows ConPTY API. A PTY also recreates terminal mode and input-forwarding
  risks that ADR 0002 removed.

## Consequences

- Users get an immediate, recognizable confirmation that the paste worked without seeing
  the full value.
- Claude receives the exact complete code, including any punctuation, followed by one
  newline.
- Cauth is the only process reading the real terminal during the hidden paste. Claude reads
  from its private pipe, so Textual cannot race either reader.
- The mediator depends on Claude's paste-prompt text. The terminal-level regression pins the
  current phrase and must be updated deliberately if Claude changes it.
- POSIX terminal coverage proves mouse-mode teardown, hidden input, masked display, exact
  code delivery through the pipe, TUI relaunch, and clean final teardown. Platform-neutral
  unit coverage pins the same process wiring for Windows.
