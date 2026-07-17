---
id: 20260717-wat001
title: Wait Agent Timeout Guidance
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-integrate-toolbox-wait-agent-guidance
pr:
supersedes: []
superseded_by:
---

# Wait Agent Timeout Guidance

## Summary

- Synchronized the public personal `wait_agent` timeout contract into the private overlay guidance.
- Added private content regression coverage for the supported bounds and recommended polling intervals.

## Current State

- Agents omit `timeout_ms` for the `30000` millisecond default or stay within the supported `10000`–`3600000` millisecond range.
- Guidance distinguishes imminent-response polling at `10000` milliseconds from ordinary or reviewer polling at `30000`–`60000` milliseconds.
- The private overlay content contract keeps this canonical guidance present across releases.

## Next Steps

- Revisit the numeric bounds if the collaboration API contract changes.

## Evidence

- Focused private overlay content contract: 1 test passed.
- Full private overlay test suite on base `4bdb72adfc43466fe643e000944bb58229e97e67`: 589 tests passed, 2 skipped.
- Project journal validation and `git diff --check` passed.
- `personal_codex/AGENTS.md`
- `tests/test_private_overlay_sync.py`
- Public source: `Joey-Tools/codex-toolbox#16`, merge `fdd4baaab300cd362d79a742bf75070b3b83f2d0`.
