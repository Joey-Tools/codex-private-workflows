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
- The runner derives and exposes the exact installed coordinator path from the effective home, safely initializes the fixed ignored shadow parent on first use, rechecks its inert history directory after coordinator actions, and persists Daily pair transitions plus the original holdout identity key ID atomically.
- Daily qualification bootstraps its direct-child holdout identity exactly once through the runner before pair state exists; later status-issued transport must reuse that identity and its source lease rather than create or replace it.
- Supervisor cleanup failures are propagated, and controlled-holdout commands are matched field-for-field to their authenticated source lease before the transport snapshot executes.
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
- Python 3.10.19 with `tomli==2.2.1` on the Linux code path: 39 shadow tests passed in 2.714 seconds with 1 platform-dependent skip.
- Focused transport/shadow suite: 105 tests passed in 7.709 seconds with 1 platform-dependent skip.
- Full test suite: 577 tests passed in 94.250 seconds with 3 platform-dependent skips.
- Changed-file Ruff format/check, Python compileall, remote-host skill validation, actionlint, and project journal validation passed.
