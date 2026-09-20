# ADR 0008: long-lived OAuth tokens are the primary workflow

Accepted 2026-09-12.

## Decision

The default app manages `claude setup-token` tokens. The prior exclusion of this workflow
is superseded by this decision. Browser-profile swapping remains
available only as a compatibility/migration path.

Store tokens, metadata and the active alias together in `tokens.json`, committed with an
atomic replace and private permissions. Renewal overwrites only its named account after
the official command succeeds and hidden input is validated. No browser-file snapshot,
refresh-token synthesis, or pending-login record participates in this flow.

`cauth run` launches the official Claude executable with the selected token in its child
environment. Bash/Zsh/Fish/PowerShell functions delegate ordinary `claude` calls to this
launcher. Functions contain no secrets; selection is read on every launch. A subprocess
cannot change its parent environment, so already-running sessions do not switch accounts.
Inherited alternative authentication and custom endpoint environment overrides are removed
from the child. Claude settings/managed policy remain Claude's responsibility.

The official setup command owns the terminal, opens authorization and displays its token.
Cauth accepts the copied token with echo disabled. There is no unsupported TTY scraping
or inference of token identity/expiry. Verification uses the selected token through the
same bounded Claude probe, with token-redacted errors.

## Compatibility and testing

Legacy profiles remain untouched unless the user invokes cleanup. Their aliases appear
in the token UI with an explicit renewal path. Old pending markers never block tokens.
Default CLI login/add/switch/status/remove now refer to long-lived tokens; old commands
are available under `cauth legacy`.

Tests cover atomic replacement failure, cancellation, secret-free metadata, permission
modes, real child environment/argument passing and Bash function A/B/A selection, UI
migration, verification failures and full reset. Real authorization requires a user's
browser interaction and is not simulated as a successful live Anthropic login.
