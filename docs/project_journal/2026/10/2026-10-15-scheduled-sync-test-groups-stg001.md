---
id: 20261015-stg001
title: Parallel Scheduled Sync Test Groups
status: completed
created: 2026-10-15
updated: 2026-10-15
branch: codex/private-scheduled-sync-test-shards-20261015
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/226
supersedes: []
superseded_by:
---

# Parallel Scheduled Sync Test Groups

## Summary
- Separate candidate generation, complete-module test groups, and the final sync/release writer.
- Bind each independent test checkout and the final writer to the original workflow commit, one patch SHA-256, and the exact generated Git tree.
- Preserve all discovered tests, Python 3.13, source-lock verification, manifest validation, package checks, and source-only guards.

## Rationale
- [Run 38564046926](https://github.com/Joey-Tools/codex-private-workflows/actions/runs/38564046926) completed generation and source verification, then its serial overlay suite was cancelled at the enclosing 30-minute job limit. It never produced a successful full-test summary.
- Reuse the existing four-group complete-module runner instead of sharing fixtures or a mutable checkout between parallel test processes.
- Changed candidates run overlay groups and defer canonical review only to required sync-PR CI. Unchanged incomplete-release repair additionally requires the review groups before publication.
- Intentional no-op skips are distinct from missing or unexpectedly skipped required groups. Preparation or test failure prevents the writer from progressing.
- Refresh overlay scheduling hints using PR #225 run 38712130407's completed Ubuntu module steps, including the previously unweighted 736-second uninstall regression module. These are cross-version hints, not a measured Python 3.13 speedup.

## Boundaries
- Finite per-job limits remain; parallelism makes no claim about account-level runner capacity or measured elapsed-time speedup.
- The transport protects exact candidate content and tree identity within one trusted workflow run. It does not claim to defeat a malicious same-UID actor racing the runner filesystem.
- The immutable public toolbox base pin is unchanged. Native installation and GitHub policy changes are outside this workstream.

## Evidence
- After a normal signed merge of PR #225's actual squash result `eaddfc70d7926455acaf6411f24759666b0bacb0`, Python 3.13.0 passed all 471 tests in the four affected private test modules in 143.133 seconds, including attribution follow-ups, candidate transport, heavy-module separation, and shell-plan/gate negative cases. The merge preserved the previously tested implementation tree without rewriting history.
- Actionlint (including ShellCheck), `bash -n` on all 27 literal workflow shell blocks, Ruff, whitespace, and project-journal validation passed.
- Fresh real discovery checks confirmed complete, non-overlapping module ownership for 2,777 overlay cases across 24 modules and 3,305 review cases across 23 modules; discovery alone does not execute their test cases.
- `.github/workflows/scheduled-sync-release.yml`
- `scripts/scheduled_sync_test_candidate.py`
- `scripts/run_release_test_group.py`
- `tests/test_unittest_shard.py`
- `tests/test_private_overlay_sync.py`
