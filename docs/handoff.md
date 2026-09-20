# Public-preparation handoff

Updated 2026-09-20. This repository contains source prepared for possible public distribution;
no public repository owner, publication URL, or completed publication is assumed.

## Product state

The default workflow manages long-lived setup-tokens with private local storage, hidden
manual paste, alias selection, and child-only Claude launches. Legacy browser profiles
remain available separately. [ADR 0008](adr/0008-long-lived-oauth-tokens.md) defines the
terminal and authentication boundary.

The paste repair accepts bare/wrapped tokens, quotes, and the supported export assignment.
Bracketed paste also accepts the standard labelled output and completes with one Enter;
other input paths use blank-line completion. Unknown prose and ambiguous tokens are
rejected. Local parsing does not prove token completeness or account validity.

## Verification and remaining work

Before public-preparation changes, the paste repair passed 284 full-suite tests, 53
targeted tests, 10 independent adversarial checks, and an installed-launcher help check.
These are fixture results, not live authorization or native Windows/macOS acceptance.
After sanitation, the full suite passed **287 tests in 99.21 seconds**, with exit status 0.
The separate clean public snapshot contains 53 tracked files and passed the privacy helper
and Gitleaks directory scans with
zero findings. Its neutral single-root-commit history passed the all-reachable-history
privacy helper and Gitleaks history scan, both with exit status 0.

Private session records, old planning notes, and review captures were removed after
verified preservation outside the repository. The retained tree passed checks for
personal/internal identifiers and broken local documentation links.

The public-source branch contract is one clean root commit with neutral commit identity.
The selected cleanup includes a `tokens.json` ignore rule and replacement of the local
and configured private main histories with that clean root. Original history and recovery
copies belong outside this repository. These notes do not certify the final remote state:
inspect local and remote refs before further work or distribution. Check all reachable
history, including tags and other refs; a clean main branch alone does not sanitize them.

The verification above is prior evidence for the prepared snapshot. After any candidate
update, repeat tree and history privacy scans, inspect documentation links, verify token
ignore behavior, and compare its tree with the intended source. GitHub publication is
separate and has not been performed.

Account data, diagnostics, credentials, and private recovery copies belong outside the
repository. Ignore rules do not prevent an explicit forced add or sanitize Git history.
Local recovery locations may be recorded in the repository-local Git configuration keys
`cauth.publicPrepBackup` and `cauth.publicPrepCandidate`; do not copy their values into
tracked files or assume those private copies exist in another checkout.

See the [restart prompt](goal-prompt.md) for the next checks and scope boundaries.
