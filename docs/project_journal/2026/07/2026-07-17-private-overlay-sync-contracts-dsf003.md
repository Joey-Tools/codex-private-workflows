---
id: 20260717-dsf003
title: Private Overlay Sync Contract Alignment
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-private-session-retrospective-contract-sync
pr:
supersedes: []
superseded_by:
---

# Private Overlay Sync Contract Alignment

## Summary

- Synchronized pending canonical retrospective snapshot-safety and review-wait continuity changes into the private overlay.
- Aligned two private-only retrospective tests with the canonical fail-closed and hidden-signal contracts.

## Current State

- Retrospective summary records no longer retain `_match_text`; the distinct private remote-host probe keeps its existing pre-signal behavior.
- Truncated session metadata scans raise `SessionMetaRolloutError` with rollout evidence instead of returning an empty result.
- Oversized binary JSONL records drain through the newline delimiter, so a bare carriage return cannot expose the remaining bytes as a new record.
- The next private overlay release will also consume the merged public `wait_agent` timeout guidance from `codex-toolbox`.

## Next Steps

- None.

## Evidence

- https://github.com/Joey-Tools/codex-private-workflows/actions/runs/29550022797
- https://github.com/Joey-Tools/codex-workflow-hygiene/pull/46
- https://github.com/Joey-Tools/codex-workflow-hygiene/pull/47
- https://github.com/Joey-Tools/codex-toolbox/pull/16
- https://github.com/Joey-Tools/codex-review-workflows/pull/57
- `tests/test_session_retrospective.py`
