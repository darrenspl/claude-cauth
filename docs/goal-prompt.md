# Restart prompt: compatibility closeout

Recorded 2026-09-21. Resolve the checkout's absolute path with
`git rev-parse --show-toplevel`; this public document intentionally contains no host-local
path. Branch at preparation: `main`. Checked base commit:
`dd49714be50b4b7095210b3aaad28cde6d08fff8`. The closeout commit is the subsequent commit
containing this prompt and the Python 3.9 compatibility fix; verify current history instead
of assuming HEAD still points to it. Private host orientation is stored only under the
local Git directory when available.

Read `README.md`, `CLAUDE.md`, `docs/handoff.md`, and
`docs/adr/0008-long-lived-oauth-tokens.md`, in that order. Inspect status and diffs first;
preserve any concurrent work. This prompt grants no new execution authority.

## Completed objective

Restore the documented Python 3.9 support without raising the minimum Python version.
`tokenui.py` postpones annotation evaluation. The pre-mount resume fixture runs inside
an async runner. The complete fixture suite passed 287 tests on each of Python 3.9,
3.12, and 3.13. TUI and independent CLI import smoke checks passed. Do not redo these
changes or treat fixture tests as live-account or native Windows/macOS acceptance.

## Orientation checks

1. Confirm the current branch and inspect the compatibility commit and any later work.
2. Compare its `main` ref with every configured remote and inspect the CI run for that
   exact commit. Do not infer current green CI from older successful runs.
3. If further changes are requested, run `python -m pytest tests/ -q` in isolated
   environments containing `requirements.txt` and pytest for Python 3.9, 3.12, and 3.13.
   Verify TUI import, plus CLI import without Textual in a separate environment.
4. Inspect changed documentation links and scan the tracked tree and reachable history
   for secrets or private identifying material before any authorized public push.

No further product objective is authorized here. If synchronization and CI are green,
report the completed state and wait for a new task. If checks fail, report the specific
failure and preserve current work; this prompt alone does not authorize a new fix or push.

## Boundaries

Keep public documentation portable and impersonal. Credentials, diagnostics, transcript
archives, and private recovery paths stay outside tracked files. Preserve neutral commit
identity and the sanitized public root; ordinary subsequent commits are expected.
Never rewrite history, force-push, create a new publication destination, change protected
configuration, deploy, or alter account state without explicit authorization. Stop on
conflicts, unexpected divergence, secret exposure, or uncertain ownership of dirty files.

The official setup-token command retains terminal ownership; accept only a manual hidden
paste afterward. Do not scrape token output or operate real accounts for verification.
Recovery keys `cauth.publicPrepBackup` and `cauth.publicPrepCandidate`, if present in local
Git configuration, are host-local references and must remain untracked. Do not create a
new autonomous goal or reintroduce removed private session archives.
