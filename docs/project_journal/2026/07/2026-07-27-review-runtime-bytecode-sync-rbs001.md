---
id: 20260727-rbs001
title: Review Runtime Bytecode Sync
status: completed
created: 2026-07-27
updated: 2026-07-30
branch: wip/review-runtime-bytecode-sync
pr:
supersedes: []
superseded_by:
---

# Review Runtime Bytecode Sync

## Summary

- Propagate the review runtime's no-bytecode contract through private CI, release, scheduled sync, and documented local validation.
- Align private CI exactly with the canonical reviewed fixture, including broker reproducibility and independent-supervisor jobs.
- Sync the hardened review control plane without introducing repository-root duplicates of private policy files.

## Current State

- All three private Python workflows set `PYTHONDONTWRITEBYTECODE=1` at workflow scope, so child Python processes inherit the contract.
- Documented local tests combine `PYTHONDONTWRITEBYTECODE=1` with `-B`; documented syntax validation compiles source bytes without writing cache files.
- Private CI matches the canonical private-profile fixture byte for byte.
- Private policy-scope validation resolves `agents/reviewer.toml` beneath `personal_codex/`, while canonical validation remains repository-rooted.
- Private sync rewrites the trusted-Mac operator command to the `personal_codex/` layout and fail-closes on any missing or unreviewed file in the independent-supervisor subtree.

## Next Steps

- None after the private overlay release is published and installed-release entrypoints confirm the immutable tree remains bytecode-free.

## Evidence

- Scheduled sync run `30238744153` failed before this change because a normal test interpreter imported the new fail-closed `review_runtime` package without disabling bytecode.
- Python 3.13 reproduces that failure without `-B`; the same focused contract passes with the no-bytecode control enabled.
- The complete private Python 3.13 suite passed 1,330/1,330 in 427.345 seconds with the environment propagated to child processes.
- The post-suite repository inventory contains no `__pycache__`, `.pyc`, or `.pyo` entry.
- Focused private workflow, synthetic-token, private policy-scope, and installed-bundle no-bytecode regressions passed.
- Canonical follow-up https://github.com/Joey-Tools/codex-review-workflows/pull/82 merged as `739ee04bb6813b00f590f2ce70d2ac8087c66562` and corrects the private policy-scope path used by the shared contract.
- Canonical https://github.com/Joey-Tools/codex-review-workflows/pull/84 merged as `0f77fb7b1dd59f5eed522fa9699497aa013695fc`; its tree exactly matches the reviewed signed head `4aec88368dcf5c101174fa3838ac870933e8bfa8`.
- Fresh Codex review of `b4caaf5dcc5e266a4022f5c9fa2999427c56145d..e296f4b00f5bfc1525bf57be3c6df87827c319cd` found the private trusted-Mac path and sync completeness gaps; both are corrected in the follow-up head with focused regressions.
- The final private root suite passed 1,333/1,333 in 167.831 seconds.
- The complete synced review suite ran 2,820 tests with 13 skips; its only parent-sandbox failure was the nested `sandbox-exec` broker case, which passed 1/1 outside the parent sandbox.
- The independent-supervisor deterministic runner passed 604/604 in 215.849 seconds.
- The installed-release immutability selector, canonical exact-inventory checks, source-only syntax compilation, and independent supervisor CLI smoke test passed under Python 3.13.
- `actionlint`, canonical CI fixture equality, Ruff lint, project-journal validation, and `git diff --check` passed.
- Whole-file Ruff format remains non-clean on pre-existing private test formatting outside this change; the new hunk matches Ruff's expected output and no unrelated formatting rewrite was applied.
- No local Python 3.10 run was performed.
- `Claude lane temporarily waived by Joey before 2026-08-01 00:00 Asia/Shanghai`; the unrun lane is not counted as a completed named double or triple.
