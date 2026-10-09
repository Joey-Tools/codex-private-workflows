---
id: 20261009-ssc001
title: Scheduled Sync CI Matrix Handoff
status: completed
created: 2026-10-09
updated: 2026-10-09
branch: codex/sync-test-parallelism
pr:
supersedes: []
superseded_by:
---

# Scheduled Sync CI Matrix Handoff

## Summary
- Changed scheduled sync candidates retain source-lock, syntax, private overlay, and manifest checks before PR creation, while the duplicate serial canonical review suite runs in the existing required PR CI matrix.
- Unchanged incomplete-release repair continues to run the full canonical suite before publication.

## Decision and Rationale
- [Scheduled sync run 37912484617](https://github.com/Joey-Tools/codex-private-workflows/actions/runs/37912484617) passed the 2,510-test core suite in 539.98 seconds; the following canonical suite reached the approximately 30-minute job limit and the recorded conclusion was `cancelled`, not a confirmed `timed_out`.
- Remove only the duplicate serial canonical run on changed-sync paths and retain complete coverage in required PR CI. Do not raise the timeout or remove tests; preserve pre-publication canonical validation for unchanged incomplete-release repair.

## Current State
- The generated sync PR makes no pre-PR canonical-suite pass claim. Its existing required `test` aggregate remains fail-closed across the review-module matrix, macOS shards, and private overlay checks.
- The scheduler's deferral notice is visible in both the runner log and job summary.
- Release publication remains on the unchanged default-branch release workflow after full validation.

## Next Steps
- Preserve the required aggregate gate and update the scheduler regression if the canonical CI matrix or repair-path contract changes.

## Evidence
- `.github/workflows/scheduled-sync-release.yml`
- `.github/workflows/ci.yml`
- `tests/test_private_overlay_sync.py`
- `README.md`
