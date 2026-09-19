---
id: 20260919-cgrv2o
title: Organization Codex Review Gate v2 Handoff
status: blocked
created: 2026-09-19
updated: 2026-09-19
branch: wip/codex-review-gate-v2-handoff-journal
pr:
supersedes: []
superseded_by:
---

# Organization Codex Review Gate v2 Handoff

## Summary

- 已签名发布并验证 `JoeyTeng/codex-review-gate-action` `v2.0.1`；该版本仅修正发布包中的 controller 权限模板与安装文档，运行时 gate 逻辑未变。
- 九个常规目标仓库已完成默认分支 v2 controller 权限刷新，并以未合并 canary 证明同一 PR head 上的 native v2 check 与 legacy v1 bridge status 都能成功。
- 组织 ruleset 切换和 v1 清理尚未开始；旧 ruleset 继续保留 deletion/non-fast-forward 等保护，最终只移除 v1 context。

## Current State

- 已完成常规双保护实证的仓库：`codex-apple-notes-toolkit`、`codex-debug-triage`、`codex-personal-sync`、`codex-private-workflows`、`codex-project-journal`、`codex-review-workflows`、`codex-rollout-backup`、`codex-toolbox`、`codex-workflow-hygiene`。
- `codex-review-workflows#116` 与 `codex-private-workflows#195` 是最后两条无害 canary；均已关闭且未合并，保留成功的 gate 证据。#195 的未完成通用 CI 已在 gate 证据完成后取消，以避免额外 Actions minutes。
- GitHub 当前把 `JoeyTeng/codex-review-gate-action@v2` 的 Node 20 action runtime 强制到 Node 24 并给出弃用 warning；未观察到功能失败。这是后续 source release 的兼容性债务，不阻塞本次交接。

## Blockers

- `codex-waited-delivery` 不在 workspace manifest，无法按受控 workspace 流程创建 worktree 或安装；需先恢复其 workspace 配置。
- `codex-session-retrospective-history` 缺少可证明的 legacy v1 producer/证据路径；在选择并授权一次性 v1 proof 路径前，不能安全进入双保护或组织 ruleset 切换。

## Next Steps

- 解决两个特殊目标的安装与双保护证据，再重新读取完整 11 仓库 cohort。
- cohort 全部满足后，按已确认的顺序执行 organization v2 ruleset activation、repository legacy cleanup，以及最终 v1 bridge removal。
- 不因当前完成状态提前删除 legacy bridge、组织 v1 required context、repository v1 required context，或现有 deletion/non-fast-forward 保护。

## Evidence

- Source release PR: `Joey-Tools/codex-review-gate#55` (`149769eac4b51df023a0edb79ad4a611d7a3edc3`).
- Release workflow: `Joey-Tools/codex-review-gate` run `35403192145`; target release `JoeyTeng/codex-review-gate-action@v2.0.1`.
- Controller refresh merges: `codex-review-workflows#117` (`0c9c6343963d4915ce4fea10193dd7a06622a6db`) and `codex-private-workflows#194` (`af1e7a0fb5aa66048b2964d5d85bb6b5f1c44382`), plus the seven earlier regular-repository refresh merges.
- Final canaries: `Joey-Tools/codex-review-workflows#116` and `Joey-Tools/codex-private-workflows#195`.
