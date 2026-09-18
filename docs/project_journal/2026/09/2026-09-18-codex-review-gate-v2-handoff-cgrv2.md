---
id: 20260918-cgrv2
title: Install Codex Review Gate v2
status: completed
created: 2026-09-18
updated: 2026-09-18
branch: codex/organization-v2-handoff
pr:
supersedes: []
superseded_by:
---

# Install Codex Review Gate v2

## Summary

- Install the canonical v2 verifier, controller, CODEOWNERS policy, and temporary legacy bridge.
- Remove the redundant default `GITHUB_TOKEN` `pull-requests: write` scope from the scheduled private-overlay release workflow.

## Current State

- After this change lands, the repository emits the v2 native check while continuing to emit the legacy v1 context during the organization-wide dual-protection handoff.
- The scheduled sync workflow still uses `PRIVATE_OVERLAY_SYNC_PR_TOKEN` for its explicitly separate sync-PR mutation contract; this installation does not claim that the token is runtime-discoverable or absent.
- The legacy bridge remains until the organization handoff receipt proves that no effective rule requires `codex/review-gate`.

## Evidence

- Canonical installer: `Joey-Tools/codex-review-gate` v2 bootstrap.
- Validation: canonical installer byte-idempotence and repository workflow diff checks.
