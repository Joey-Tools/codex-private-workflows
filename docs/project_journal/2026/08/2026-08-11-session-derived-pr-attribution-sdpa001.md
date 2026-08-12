---
id: 20260811-sdpa001
title: Derive Codex PR Attribution From Session History
status: completed
created: 2026-08-11
updated: 2026-08-12
branch: wip/pr-attribution
pr:
supersedes: []
superseded_by:
---

# Derive Codex PR Attribution From Session History

## Summary

- Replace the static Codex PR attribution with a session-derived model and
  reasoning label.
- Count complete model/reasoning pairs by unique turn across the root task and
  its recursive subagents.
- Keep `GPT-5.6 Sol Ultra` as the deterministic full-sentence fallback.

## Current State

- `codex-session-mining` provides a small standard-library helper that selects
  the pair mode and resolves ties with the UUIDv7 turn timestamp rather than a
  replayable record timestamp, falling back on same-millisecond ambiguity.
- Root-task resolution follows explicit subagent provenance, legacy archived
  rollout names can be indexed from bounded metadata, and unsafe or oversized
  rollout inputs produce the fallback rather than a partial attribution.
- Root, parent, and task-role bindings are checked across original and replayed
  lifecycle metadata so contradictory provenance cannot contribute a vote.
- Inventoried rollout identity is revalidated at open, and validated lifecycle
  cursors prevent copied turns from receiving duplicate cross-rollout votes.
- The helper deliberately uses the whole task family because resumed or
  compacted rollouts can replay old turns with new record timestamps; a simple
  time cutoff is not a reliable change-set boundary.
- Personal PR guidance requires recomputation at PR creation and immediately
  before merge.

## Evidence

- Focused tests cover recursive family discovery, root-task resolution,
  per-turn deduplication, replay-safe tie resolution, every Desktop reasoning
  label, legacy archived layouts, and fail-closed fallback paths.
- The final focused suite passed 41 tests, and the repository suite passed
  2,064 tests with 3 skips.

## Next Steps

- None within the tracked change. Default-branch private overlay publication is
  the downstream delivery step.
