---
id: 20260717-rhc004
title: Remote Rollout Snapshot Guards
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260716-codex-private-workflows-remote-rollout-snapshot-safety
pr:
supersedes: []
superseded_by:
---

# Remote Rollout Snapshot Guards

## Summary

- Made local and embedded `session-meta` fail closed when the bounded prefix ends without valid metadata while the opened descriptor still has unread bytes.
- Bound full rollout fetches to the frozen snapshot size plus one growth-detection byte, then required exact length plus post-read descriptor and current-path identity checks.
- Derived `rollout-summary` source size from its scan descriptor and required descriptor and path identity to remain stable before any success output.
- Matched keywords against the full normalized signal before display truncation while retaining only a transient boolean and bounded signal-only text.
- Bounded remote full-fetch parent capture to the maximum base64 payload plus fixed framing overhead.

## Current State

- An oversized first JSONL record can no longer make a rollout disappear silently from `session-meta`; the helper reports the exact rollout as an explicit coverage error.
- A valid metadata record contained within the prefix still succeeds even when the remainder of the rollout is larger than the prefix budget.
- Append, truncation, or pathname replacement during full fetch or prefix summary invalidates the operation before fetched bytes or summary rows are emitted.
- A keyword beyond `--max-text-chars` can select the bounded signal row without storing or emitting the original long text.
- The private retrospective materialization now matches public canonical merge `31f252e2356a1401559dc2a1c65a4569073a19d8`; integration coverage requires both retrospective and remote-host probes to retain only the transient keyword boolean and treats a truncated session-meta prefix as an explicit coverage error.

## Next Steps

- Monitor subsequent scheduled source syncs for contract drift between public retrospective helpers and private cross-skill integration coverage.

## Evidence

- Focused remote probe suite: 34 tests passed, including seven new local and embedded truncation, snapshot, bounded-capture, and keyword cases.
- Private integration coverage: 2 focused compatibility tests and the full 588-test suite passed (`skipped=2`).
- Python byte compilation, Ruff lint, isolated skill quick validation, project journal validation, and `git diff --check` passed.
- `personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py`
- `personal_codex/skills/remote-host-context/SKILL.md`
- `tests/test_remote_codex_probe.py`
- Public source: `Joey-Tools/codex-workflow-hygiene#46`, merge `31f252e2356a1401559dc2a1c65a4569073a19d8`.
