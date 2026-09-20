# Contributor guidance

Cauth manages long-lived Claude setup-tokens by default. Browser-profile management is
available under `cauth legacy`. Read the README, product requirements, and ADR 0008
before changing authentication behavior.

## Safety boundaries

- Keep every real token, account export, diagnostic capture, and private backup outside
  the repository. Use fictional fixture credentials in tests and documentation.
- Never print stored tokens, place them in command arguments, or expose them in errors.
- Preserve existing account state when input is rejected, cancelled, or fails to save.
- Use private atomic writes. Legacy switches must preserve outgoing refreshed credentials
  and unrelated Claude settings. Halt rather than overwrite uncertain state.
- The official setup command owns the terminal. Exit the TUI before invoking it; accept
  a manual hidden paste afterward. Do not scrape its token output.
- Do not operate live accounts or reset real state as part of automated testing.

## Development and verification

Python and Textual are used directly, with no compilation step. Install dependencies
from `requirements.txt` into an isolated environment, add pytest, and run
`python -m pytest tests/ -q`. Tests redirect credential storage to temporary fixtures.
Keep that isolation intact. Test terminal behavior with fixture PTYs and Textual tests;
the project has no browser UI or production deployment.

Use scoped changes and preserve unrelated work. Native Windows/macOS behavior and real
provider authorization require separate verification; Linux fixture results do not prove
those flows. A syntactically accepted token is not proof of authentication.
