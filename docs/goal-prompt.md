# Restart prompt: public source

Read `README.md`, `CLAUDE.md`, `docs/handoff.md`, and
`docs/adr/0008-long-lived-oauth-tokens.md` in this checkout first.

This checkout contains Cauth source prepared for future public sharing after fixing
hidden setup-token paste handling. Keep documentation portable and impersonal. Keep
private infrastructure, personal account details, transcript archives, and unrelated
internal process notes out of the repository. Preserve useful product behavior, security
limits, and architectural decisions. Private material was preserved externally before removal;
do not put archive locations or identifying metadata back into tracked files.

Inspect current status and diffs before continuing. Preserve concurrent work. Coordinate
source, installer, test, and documentation changes; do not claim completion from stale
verification. Run `python -m pytest tests/ -q` using an isolated environment containing
`requirements.txt` and pytest. Inspect retained document links and audit the final tracked
tree for personal identifiers, private endpoints, secrets, and credential artifacts.

The paste fix previously passed 284 full-suite tests, 53 targeted tests, 10 independent
adversarial checks, and the installed-launcher help check. After sanitation the full suite
passed 287 tests in 99.21 seconds, with exit status 0.
The separate clean public snapshot has 53 tracked files and passed the privacy helper
and Gitleaks directory scans with zero findings. Its neutral single-root-commit history
passed the all-reachable-history privacy helper and Gitleaks history scan, both with
exit status 0. Final documentation readback checked portability and scope boundaries.
Repeat the history scans and source-tree comparison after any candidate update.
Do not claim live account health or native Windows/macOS acceptance from fixtures.
The installed launcher runs the source checkout; restarting loads changes.

The official setup-token command must retain terminal ownership. Cauth accepts manual
hidden input afterward; no output scraping or automatic token capture. Real credentials
and account data stay in private user storage outside the repository. Never request or
print a real token. Do not operate live accounts to test sanitation.

The public-source branch contract is one clean root commit with neutral identity. The
selected cleanup includes the `tokens.json` ignore rule and replacement of local and
configured private main histories with that clean root; original history stays in private
external recovery copies. Inspect current local and remote refs rather than assuming
that final synchronization succeeded. Verify the ignore rule with a representative
`tokens.json` path and check that no credential file is tracked. Review all reachable
blobs, commit messages, identities, tags, and refs before distribution. Tree sanitation
alone does not sanitize history. GitHub publication has not been performed.

For local recovery and snapshot locations, read the repository-local Git configuration
keys `cauth.publicPrepBackup` and `cauth.publicPrepCandidate`. Those paths belong only
in local configuration, never in tracked documentation. If the keys are absent in a
different checkout, do not invent paths or assume the private archives are available.

This prompt grants no new permission to publish, rewrite history, force-push,
change authentication, delete account state, or
edit protected configuration. Stop on conflicts or an unmet approval boundary. Do not
assume any public GitHub owner/repository or configure a public remote without direction.

Refresh the handoff with actual verification when substantive work resumes. Do not create
a new autonomous goal or expand into unrelated product work.
