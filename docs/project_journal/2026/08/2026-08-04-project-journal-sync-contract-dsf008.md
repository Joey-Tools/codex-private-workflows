---
id: 20260804-dsf008
title: Project Journal Private Sync Contract Repair
status: completed
created: 2026-08-04
updated: 2026-08-04
branch: codex/fix-project-journal-sync-contract
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/141
supersedes: []
superseded_by:
---

# Project Journal Private Sync Contract Repair

## Summary

- Align the private project-journal personalization rule with the current public source wording without weakening required replacement checks.

## Current State

- The public project-journal description now starts with `Maintain repository project journals` instead of `Manage repository project journals`.
- The public workflow removed the old `For repositories` policy sentence, so the private rule no longer requires or recreates it.
- The description personalization is bound to `SKILL.md` and requires exactly one source anchor, so matching historical wording in another file cannot mask frontmatter drift.
- The remaining project-journal personalizations stay required and continue to fail closed on unreviewed source drift.
- Focused project-journal sync coverage includes the current source contract, a cross-file bait regression, and a duplicate-anchor rejection.
- The repository's complete 1,332-test suite passes with the final implementation.

## Next Steps

- None.

## Evidence

- https://github.com/Joey-Tools/codex-private-workflows/actions/runs/30897370215
- https://github.com/Joey-Tools/codex-project-journal/commit/4f53fd1bf9ba0a7c85db8d183016210d3d0089e5
- https://github.com/Joey-Tools/codex-review-workflows/pull/88
- https://github.com/Joey-Tools/codex-private-workflows/pull/141
- `scripts/sync_private_overlay_sources.py`
- `tests/test_private_overlay_sync.py`
