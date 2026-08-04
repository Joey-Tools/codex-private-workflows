---
id: 20260804-dsf008
title: Project Journal Private Sync Contract Repair
status: active
created: 2026-08-04
updated: 2026-08-04
branch: codex/fix-project-journal-sync-contract
pr: null
supersedes: []
superseded_by:
---

# Project Journal Private Sync Contract Repair

## Summary

- Align the private project-journal personalization rule with the current public source wording without weakening required replacement checks.

## Current State

- The public project-journal description now starts with `Maintain repository project journals` instead of `Manage repository project journals`.
- The public workflow removed the old `For repositories` policy sentence, so the private rule no longer requires or recreates it.
- The remaining project-journal personalizations stay required and continue to fail closed on unreviewed source drift.
- Focused project-journal sync coverage includes both the current source contract and a frontmatter-drift rejection.

## Next Steps

- Complete the local delivery gate and independent review.
- Merge the repair PR.
- Rerun the scheduled private overlay sync and release workflow.

## Evidence

- https://github.com/Joey-Tools/codex-private-workflows/actions/runs/30897370215
- https://github.com/Joey-Tools/codex-project-journal/commit/4f53fd1bf9ba0a7c85db8d183016210d3d0089e5
- https://github.com/Joey-Tools/codex-review-workflows/pull/88
- `scripts/sync_private_overlay_sources.py`
- `tests/test_private_overlay_sync.py`
