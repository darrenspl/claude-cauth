# Claude Cauth

Claude OAuth account manager. Create long-lived tokens with `claude setup-token`, store
them privately under aliases, and choose which token Claude uses. The documented lifetime
is one year. Cauth does not treat short-lived browser credentials as one-year tokens.

## Install

Run `./install.sh` on Linux/WSL/macOS or `install.cmd` on Windows. Python and Textual are
used directly; there is no build step. The POSIX installer configures the `claude` shell
function for Bash/Zsh. Open a new terminal after installation. Windows/macOS still need
native platform validation; Linux is covered by executable and terminal tests.

Run installers from a source checkout you trust. If you instead run a streamed or
standalone installer, set `CAUTH_REPO_URL` to the Git repository you intend to install;
there is no default hosting account or repository address. Use Git's credential manager
for private repositories instead of embedding credentials in the URL. Local-checkout
installation does not require `CAUTH_REPO_URL`.

## Create and use tokens

```bash
cauth login personal
cauth login work
cauth switch work
cauth run
```

`login` hands the terminal to the official `claude setup-token` command. Complete its
browser authorization, copy the complete token it displays, then paste it into Cauth's
hidden prompt. Include every wrapped line; Cauth joins them. A bare token, a token in
single or double quotes, or the printed environment-variable assignment with its real
token are accepted. Do not copy an abbreviated token or combine several tokens.

In terminals supporting bracketed paste, press Enter once after pasting. These terminals
also accept the standard labelled setup-token output, including its normal instructions.
Other terminals, including the Windows input path, require Enter on a blank line to
finish; copy only the token or its assignment there. Follow the prompt's completion
instructions. Unknown surrounding text and ambiguous input are rejected without
repeating authorization, and pasted text stays hidden.

`cauth add <alias>` imports an already-generated token through the same hidden prompt.
If generation already succeeded, use this to retry with the token you still have; another
browser authorization is unnecessary. No token is accepted as a command-line argument.
Cauth saves syntactically accepted input privately; use Verify to establish whether the
account accepts it. Parsing alone cannot prove that a copied token is complete or valid.

Run `cauth` for the terminal UI. **a** creates a token, **i** imports one, **Enter** selects
an alias, **c** launches Claude, **v** verifies the selected token, **l** renews the highlighted
alias, **e** renames it, **r** forgets it, and **d** opens cleanup. Renewal replaces the
stored token only after successful generation and input. Cancellation keeps the old token.
**?** opens help and **g** opens diagnostics. The home screen keeps instructions separate
from the account list; narrow terminals use shorter button labels for the same actions.

The first token is selected automatically. Saving another token keeps the existing
selection. Labels are user-supplied; a setup-token is opaque, so Cauth does not invent
account email, subscription tier, refresh tokens, or exact expiration timestamps.
Imported token age is unknown. Verification makes a bounded real request through Claude;
selection alone does not claim the token is valid.

## Use your ordinary claude command

**You do not need to launch Claude from inside Cauth.** Use Cauth to manage tokens,
then launch `claude` or a shortcut such as `cc` from your terminal.

### If Claude still asks you to log in after saving a token

An already-open terminal may not have loaded the shell integration yet. Cauth can
install the startup configuration, but a child process cannot refresh its parent shell.
Exit Claude, then either **open a new terminal session** or run the command for your
shell once in the existing terminal:

```bash
# Zsh
eval "$(cauth shell-init zsh)"

# Bash
eval "$(cauth shell-init bash)"
```

Then run `claude` or `cc`. This activation is needed once per terminal that predates
the integration. After it is loaded, adding, renewing, or selecting tokens does not
require another terminal refresh: each new Claude launch reads the current selection.
Already-running Claude sessions keep their original token. Saving a second token
does not select it automatically; select it with Enter in Cauth to make it the main one.

Every create/import/renew/select operation checks or repairs Bash/Zsh startup integration
and displays activation guidance. Quitting the UI displays a prominent **REFRESH THIS
TERMINAL ONCE** notice after the full-screen UI closes, with the activation command
on its own line outside the box for easy copying. If shell setup reports failure, resolve it first;
opening another terminal alone cannot repair a failed installation.

### Integration details

`cauth run -- <Claude arguments>` works immediately, including interactive sessions and
`-p`. Creating, importing, renewing, or selecting a token automatically installs or
repairs the Bash/Zsh shell function, including actions in the terminal UI. Existing
startup files are backed up privately before changes. Open a new terminal once to load
the integration; subsequent token switches take effect on the next Claude launch.
For an already-open Bash terminal, run `eval "$(cauth shell-init bash)"` once before
your usual shortcut. Saving a token cannot update the parent terminal's functions.
Cauth prints these activation instructions after the UI closes so they remain visible.
User-defined aliases and functions that call `claude` follow the same selection;
Cauth does not create, rename, or edit those shortcuts. Shortcuts that call Claude's
executable by absolute path or use `command claude` bypass this shell integration.
Setup-tokens do not support `--remote-control`.
Other shells receive manual setup instructions. If setup fails, the token is retained
and Cauth displays a retry instruction; `cauth run` remains available.

You can also install the function explicitly:

```bash
# Bash (persistent; then open a new terminal)
cauth install-shell bash
# Enable immediately in the current Bash terminal too:
eval "$(cauth shell-init bash)"

# Zsh: use install-shell zsh and shell-init zsh instead.
```

PowerShell: `cauth shell-init powershell | Out-String | Invoke-Expression`.
Add that line to `$PROFILE` to retain it in new sessions.
Fish: `cauth shell-init fish | source`; add that line to `config.fish` for persistence.
The function contains no token. It loads the selected token afresh on each launch.
Remove the marked Cauth block from your shell startup file to uninstall the integration.

Cauth supplies `CLAUDE_CODE_OAUTH_TOKEN` only to the Claude child process. It removes
inherited API-key, bearer-token, provider, profile, and custom-endpoint overrides from
that child environment. It does not export secrets to the parent shell or rewrite live
browser credential files. Already-running Claude processes retain their original token;
start a new session after changing accounts. Direct launches outside the shell function
or `cauth run` do not use Cauth's selection. `--bare` is rejected because Claude ignores
the OAuth environment variable in that mode. Setup-tokens do not support Remote Control.

## Existing browser profiles

Old aliases appear in the new UI as browser profiles. Highlight one and press **l** to
create a long-lived token under that alias. Old profiles remain intact; they cannot be
converted into year-long tokens without authorization. **b** or `cauth legacy` opens the
previous UI. Legacy CLI commands use `cauth legacy <command>`.

The legacy UI flags duplicate profiles and expired renewals, and exposes **r Delete**.
Duplicate aliases share refreshed credentials; new duplicate aliases are refused.

See [legacy browser-profile instructions](docs/legacy-browser-profiles.md) for the old
file-swapping workflow. Those recovery markers do not gate long-lived token operations.

## Cleanup

- `cauth remove <alias>` deletes that stored long-lived token; it does not revoke a token
  already held by another process. Use `cauth legacy remove <alias>` for old profiles.
- `cauth clear-login --yes` removes an unfinished browser-login marker without restoring
  old credentials.
- `cauth reset --yes` deletes all Cauth tokens, saved browser profiles, rotation/index
  backups, recovery records, and the live file-based login. No recovery copy is kept.
  Claude settings and conversations remain. Close other Claude/Cauth sessions first.
  Shell/environment secrets, OS keychains, and server-side sessions are not revoked.

## Diagnostic logs

Diagnostics are enabled automatically. `cauth logs` prints the last 200 events;
`cauth logs --path` prints their location. Press **g** in the token UI for recent events.
To save a troubleshooting report outside the checkout in a POSIX shell, run
`cauth logs > "$HOME/cauth-diagnostics.log"`. Review it before sharing.

Logs live at `~/.config/claude-oauth/logs/diagnostics.jsonl`, rotate at 1 MiB, and keep
three previous files. On POSIX the log directory is mode 700 and files are mode 600.
Each event has a UTC timestamp, process/session/operation IDs, durations, subprocess
exit codes, and failure stack locations. Paste diagnostics record only character/line
counts and whether the expected prefix was present. Tokens, pasted text, aliases,
Claude prompts/arguments, environment values, subprocess output and exception messages
are excluded. Reset retains these secret-free diagnostic events for troubleshooting.
Unexpected UI errors show a short message and stop the current handoff; their safe stack
locations go to these logs instead of displaying a traceback with local variables.

## Storage and validation

`~/.config/claude-oauth/tokens.json` contains the tokens and selection in one atomically
replaced file. On POSIX the directory is mode 700 and the file is mode 600. Windows uses
the user's filesystem ACLs. Cauth never prints stored tokens, including in status JSON
and verification errors. The official `setup-token` command itself displays the newly
issued token for copying. `cauth status --json` contains only allowlisted metadata.

Keep account stores, exports, diagnostic captures, and private backups outside the
repository. File permissions restrict local access; the token store is not encrypted.
Treat copies and backups as credentials, and never add real tokens to issues or commits.

The documented year applies to newly generated setup-tokens, not a guarantee against
revocation or account changes. Cauth never fabricates a year of validity for an imported
token. [Official authentication documentation](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token).

Run `python -m pytest tests/ -q` after installing `requirements.txt` and pytest. Tests use
fixture credentials only. `cauth doctor` checks the installation without reading tokens.
