---
id: 20260717-rsc002
title: Review Skill Sync Layout Drift
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-review-sync-layout-drift
pr:
supersedes: []
superseded_by:
---

# Review Skill Sync Layout Drift

## Summary

- Removed three obsolete private-layout source rewrites from the review-orchestration sync rule after the canonical skill gained native canonical/private layout detection.
- Kept common Joey text substitutions and the private synthetic-token catalog overlay unchanged.
- Added a sync-rule regression that rejects reintroducing the obsolete source-shape dependency.

## Current State

- The private overlay sync no longer requires canonical source text that was removed by the layout-aware review skill.
- Required replacements elsewhere remain fail-closed; only the superseded review-layout rewrites were removed.
- The private synthetic-token catalog continues to replace the public catalog byte-for-byte after canonical sources are staged.

## Next Steps

- Re-run the forced public-to-private sync workflow and confirm its generated sync PR and default-branch release after merge.

## Evidence

- GitHub Actions run `29593766698` failed only because the obsolete `REPO_ROOT = SKILL_ROOT.parents[1]` replacement no longer matched the canonical review skill.
- The focused review sync-rule regression passed.
- The complete private overlay sync module passed 126 tests.
- The complete repository suite passed 627 tests.
- Python byte compilation, Ruff lint, project journal validation, and `git diff --check` passed.
- `scripts/sync_private_overlay_sources.py`
- `tests/test_private_overlay_sync.py`
