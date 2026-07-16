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
- Updated the reference-only Daily and Weekly release templates for exact first registration or verified in-place update cutover.
- Added a shadow-only Daily partial/direct-successor backfill launcher that never suppresses a production source.

## Current State

- Transport pagination is bound to source tokens, resume cursors, exact byte and record coordinates, and terminal conservation proofs.
- Raw record handling has fixed spool, scan, JSON-depth, fragment, frame, idle-progress, total-timeout, and range limits.
- The shadow runner constrains coordinator commands, paths, writes, network access, and per-host concurrency before execution.
- The runner derives and exposes the exact installed coordinator path from the effective home, rechecks its inert history directory after coordinator actions, and persists Daily pair transitions plus the original holdout identity key ID atomically.
- Release templates do not assert that a live automation exists, and this change performs no live automation registration or update.
- Existing remote-host helper commands retain their prior interfaces and defaults.

## Next Steps

- Run the non-publishing acceptance campaign after the matching v2 coordinator is installed.

## Evidence

- `personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py`
- `personal_codex/skills/remote-host-context/scripts/session_retrospective_v2_shadow_runner.py`
- `personal_codex/skills/remote-host-context/references/session-shards-v1.md`
- `personal_codex/automations/session-retrospective-v2-shadow/automation.toml`
- `personal_codex/automations/daily-session-retrospective/automation.toml`
- `personal_codex/automations/weekly-session-retrospective/automation.toml`
- `tests/test_remote_session_shards.py`
- `tests/test_session_retrospective_v2_shadow_automation.py`
- Focused transport/shadow suite: 97 tests passed in 9.201 seconds with 1 platform-dependent skip.
- Full test suite: 569 tests passed in 159.860 seconds with 3 platform-dependent skips.
- Changed-file Ruff format/check, Python compileall, remote-host skill validation, actionlint, and project journal validation passed.
