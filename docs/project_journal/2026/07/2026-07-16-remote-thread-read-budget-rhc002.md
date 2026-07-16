---
id: 20260716-rhc002
title: Remote Thread Read Budget
status: completed
created: 2026-07-16
updated: 2026-07-17
branch: codex/daily-skill-friction-20260716-codex-private-workflows-remote-thread-read-budget-v2
pr:
supersedes: []
superseded_by:
---

# Remote Thread Read Budget

## Summary

- Added a bounded locator contract for `codex://threads/<id>` evidence reads.
- Resolved pinned review P1 by requiring server-side item-type, item-count, and whole-response byte controls in addition to the one-thread, one-turn, no-output, and per-item limits.
- Forbade `read_thread` when any server-side whole-response control is unavailable and routed known-date and unknown-date recovery through bounded metadata lookup, exact session-id filtering, and rollout summaries.
- Preserved later substantive human follow-ups after automation or instruction wrappers and routed deeper evidence recovery back to canonical rollout summaries and chunked reads.

## Current State

- `remote-host-context` no longer treats post-fetch projection or per-item limits as a whole-response budget.
- Focused documentation tests lock the server-side controls, complete `read_thread` bypass, bounded date discovery, human-follow-up retention, and rollout fallback.

## Next Steps

- Monitor whether a future thread service adds all three server-side controls; until then, keep `read_thread` out of this recovery flow.

## Evidence

- Daily Skill Friction session `019f662f-2c12-7483-bb7f-9e2be4a71259` returned `original_token_count` values of 109,375 and 34,342 for thread reads on 2026-07-15.
- Pinned review P1 identified that `turnLimit: 1`, a per-item cap, and caller-side projection still allowed an unbounded item count and whole response.
- `personal_codex/skills/remote-host-context/SKILL.md`
- `personal_codex/skills/remote-host-context/references/hosts.md`
- `tests/test_remote_codex_probe.py`
