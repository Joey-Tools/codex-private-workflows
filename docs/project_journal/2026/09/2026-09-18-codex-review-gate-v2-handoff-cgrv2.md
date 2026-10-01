---
id: 20260918-cgrv2
title: Install Codex Review Gate v2
status: completed
created: 2026-09-18
updated: 2026-10-01
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

- 仓库继续产生 v2 native check；在组织收尾回执核验通过后，临时 v1 bridge 已移除。
- The scheduled sync workflow still uses `PRIVATE_OVERLAY_SYNC_PR_TOKEN` for its explicitly separate sync-PR mutation contract; this installation does not claim that the token is runtime-discoverable or absent.
- v2 verifier 和 controller 已对齐 canonical 模板：请求者权限策略为 `any`，`issue_comment` 入口仅响应新建评论。
- The controller matches the canonical fix from `Joey-Tools/codex-review-gate#93`: when `CODEX_REVIEW_GATE_AUTO_REQUEST` is `true`, a first-attempt failed verifier can request a review for its associated PR without a static `workflow_run.name` payload guard. The variable defaults off until a selected-repository canary is enabled.

## Evidence

- Canonical installer: `Joey-Tools/codex-review-gate` v2 bootstrap.
- Validation: canonical installer byte-idempotence and repository workflow diff checks.
- Post-cutover audit receipt SHA-256: `9a8b38f2188a14168423a07639d6662c87e198fe2dd12041f67fc224f363817e`.
