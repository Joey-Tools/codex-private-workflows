---
id: 20260718-rhc010
title: Remote Summary Resource Bounds
status: completed
created: 2026-07-18
updated: 2026-07-18
branch: codex/daily-skill-friction-20260718-codex-private-workflows-remote-summary-resource-bounds
pr:
supersedes: []
superseded_by:
---

# Remote Summary Resource Bounds

## Summary

- Bounded every local and embedded rollout chunk by both bytes and physical-record count.
- Made malformed JSONL records produce explicit partial coverage and raw-fetch guidance even when adjacent structured evidence parses successfully.
- Enforced the advertised fetch-range plan limit across the complete chunked summary, not only within one chunk.

## Current State

- Each in-memory chunk retains at most 4,096 physical records while preserving exact byte and record coordinates.
- `chunk_meta.json_error_count` and `json_parse_error` expose malformed input without discarding valid evidence from the same chunk.
- Local and embedded summaries fail closed before emitting a plan whose cumulative explicit ranges and implicit whole-chunk entries exceed 4,096.

## Next Steps

- None.

## Evidence

- Focused local and embedded record-cap, malformed-JSON coverage, and global fetch-plan regressions passed 6/6.
- The remote-host module passed 105/105, the retrospective integration module passed 409/409, and the private repository root suite passed 1,252/1,252.
- Isolated skill validation, journal validation, `py_compile`, Ruff, and `git diff --check` passed.
