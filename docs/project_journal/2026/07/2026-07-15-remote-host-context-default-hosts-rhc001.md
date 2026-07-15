---
id: 20260715-rhc001
title: Remote Host Context Default Hosts
status: completed
created: 2026-07-15
updated: 2026-07-15
branch: wip/remote-host-context-default-hosts
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/83
supersedes: []
superseded_by:
---

# Remote Host Context Default Hosts

## Summary

- Added `BL-mac-mini-m4-hoteng` and `codex-hoteng-srv-01` to the default read-only evidence scope.
- Registered the BL Mac alias in the bounded remote probe and documented both verified Codex roots.
- Propagated the expanded scope through private automations, session mining guidance, and session retrospective materialization and retention.

## Current State

- The default preflight covers the local machine plus four SSH aliases.
- `hoteng-srv-01` and `codex-hoteng-srv-01` remain separate evidence roots despite resolving to the same server hostname.
- Focused tests lock the default command shape and each newly added account-specific Codex root.
- Private sync transforms preserve the Joey-specific four-remote default when refreshing session workflow sources.

## Next Steps

- Monitor the expanded host preflight after the released private overlay is installed.

## Evidence

- `personal_codex/skills/remote-host-context/`
- `personal_codex/skills/codex-session-retrospective/`
- `personal_codex/automations/`
- `scripts/sync_private_overlay_sources.py`
- `tests/test_remote_codex_probe.py`
- `tests/test_session_retrospective.py`
- `tests/test_private_overlay_sync.py`
- Read-only SSH preflights completed on 2026-07-15.
- `https://github.com/Joey-Tools/codex-private-workflows/pull/83`
