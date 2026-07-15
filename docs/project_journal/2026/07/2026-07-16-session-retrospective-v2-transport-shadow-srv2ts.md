---
id: 20260716-srv2ts
title: Session Retrospective v2 Transport and Shadow
status: completed
created: 2026-07-16
updated: 2026-07-16
branch: wip/session-retrospective-v2-transport-clean
pr:
supersedes: []
superseded_by:
---

# Session Retrospective v2 Transport and Shadow

## Summary

- Added the closed `session-shards-v1` descriptor and record transport for local and SSH-backed Codex rollout sources.
- Added authenticated controlled-holdout and real-backfill replacement handling with a persistent atomic ledger.
- Added a fail-closed macOS shadow runner and a reference-only v2 acceptance-campaign automation.

## Current State

- Transport pagination is bound to source tokens, resume cursors, exact byte and record coordinates, and terminal conservation proofs.
- Raw record handling has fixed spool, scan, JSON-depth, fragment, frame, timeout, and range limits.
- The shadow runner constrains coordinator commands, paths, writes, network access, and per-host concurrency before execution.
- Existing remote-host helper commands retain their prior interfaces and defaults.

## Next Steps

- Run the non-publishing acceptance campaign after the matching v2 coordinator is installed.

## Evidence

- `personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py`
- `personal_codex/skills/remote-host-context/scripts/session_retrospective_v2_shadow_runner.py`
- `personal_codex/skills/remote-host-context/references/session-shards-v1.md`
- `personal_codex/automations/session-retrospective-v2-shadow/automation.toml`
- `tests/test_remote_session_shards.py`
- `tests/test_session_retrospective_v2_shadow_automation.py`
- Full test suite: 549 tests passed with 3 platform-dependent skips.
