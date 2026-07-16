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
- Limited thread skims to one thread and one turn, retained creation/update timestamps for bounded cross-day lookup, excluded outputs and reasoning, and capped emitted message snippets.
- Preserved later substantive human follow-ups after automation or instruction wrappers and routed deeper evidence recovery back to canonical rollout summaries and chunked reads.

## Current State

- `remote-host-context` no longer leaves desktop thread reads outside its output-budget guidance.
- Focused documentation tests lock the thread-reader arguments, projection limits, human-follow-up retention, and rollout fallback.

## Next Steps

- Monitor new remote-thread investigations for raw serialization or multi-thread batching after the updated private overlay is released.

## Evidence

- Daily Skill Friction session `019f662f-2c12-7483-bb7f-9e2be4a71259` returned `original_token_count` values of 109,375 and 34,342 for thread reads on 2026-07-15.
- `personal_codex/skills/remote-host-context/SKILL.md`
- `personal_codex/skills/remote-host-context/references/hosts.md`
- `tests/test_remote_codex_probe.py`
