---
id: 20260719-rrf001
title: Review Runtime Required Files
status: completed
created: 2026-07-19
updated: 2026-07-19
branch: wip/required-review-runtime-files-current
pr:
supersedes: []
superseded_by:
---

# Review Runtime Required Files

## Summary

- Require the Claude refresh-lock runtime and its dedicated regression module in every synchronized review-orchestration release tree.
- Cover each omission with a functional contract regression whose expected paths are independent of the production required-file list.

## Current State

- `CANONICAL_REVIEW_REQUIRED_FILES` includes `scripts/review_runtime/claude_refresh_lock.py` and `tests/test_claude_refresh_lock.py`.
- Installed-target validation and secure staged-manifest validation now reject a release tree that omits either file before live replacement.

## Next Steps

- None.

## Evidence

- The new regression failed twice before the required-file update and passed afterward.
- Six focused canonical-review sync contract tests passed.
- The complete `tests.test_private_overlay_sync` module passed (`167` tests).
- Ruff 0.13.2, Python 3.13.0 byte compilation, sync-manifest change validation, journal validation, and diff checks passed.
- `scripts/sync_private_overlay_sources.py`
- `tests/test_private_overlay_sync.py`
