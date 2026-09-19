---
id: 20260919-cgrv2o
title: Organization Codex Review Gate v2 Handoff
status: blocked
created: 2026-09-19
updated: 2026-09-19
branch: wip/codex-review-gate-v2-handoff-journal-replacement
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
- cohort 全部满足后，按不可跳过的 phase gate 执行：
  1. 记录精确 v2 organization ruleset ID、target repository IDs、payload digest，以及旧 organization/repository protection 的 before snapshot；此处是 rollback/reconcile 的身份边界。
  2. 激活精确 v2 organization ruleset，但保留所有旧 v1 contexts 与 deletion/non-fast-forward 保护。
  3. 对全部 11 个仓库做稳定完整 reread，证明 v2 是实际 required 的严格 context、target 覆盖精确，且旧 protection 仍有效。
  4. 仅在第 3 步通过后，按受控 repository cleanup 从所有有效 organization/repository rules 中移除 `codex/review-gate`；保留旧 ruleset 的其余保护。
  5. 再次完整 reread，要求全部有效规则都不再要求 `codex/review-gate`，同时 v2 coverage 与 deletion/non-fast-forward 仍存在。
  6. 只有第 5 步通过后，才在各仓库删除 legacy bridge，并验证 bridge removal 后的新 PR 不会等待旧 context。
- 任一阶段读回不完整、identity/payload 漂移或覆盖不符时停止后续写入；根据已记录的 before snapshot 重新 reconcile，绝不先删 v2 或 bridge 再尝试恢复旧 v1 保护。

## Evidence

- Source release PR: `Joey-Tools/codex-review-gate#55` (`149769eac4b51df023a0edb79ad4a611d7a3edc3`).
- Release workflow: `Joey-Tools/codex-review-gate` run `35403192145`; target release `JoeyTeng/codex-review-gate-action@v2.0.1`.
- Controller refresh merges: `codex-review-workflows#117` (`0c9c6343963d4915ce4fea10193dd7a06622a6db`) and `codex-private-workflows#194` (`af1e7a0fb5aa66048b2964d5d85bb6b5f1c44382`), plus the seven earlier regular-repository refresh merges.
- 每条 canary 的成功定位如下；`v2 verifier` 是 `codex/github-review-gate` success 的 workflow run，`legacy status` 是同一 head 上 `codex/review-gate` success 的 commit status ID：

| Repository | Canary PR | Head | v2 verifier | Legacy status |
| --- | --- | --- | --- | --- |
| `codex-apple-notes-toolkit` | #7 | `535a365489872fd6b294fc844fd605dd4c3c13b5` | `35441985737` | `54504397189` |
| `codex-personal-sync` | #23 | `8618eb3fd2a57287c12210d0379d15e97a985b2d` | `35441985612` | `54504392585` |
| `codex-rollout-backup` | #9 | `df6f577e26eb581bb4dc5a162ef7cb4ac6a6563b` | `35441985957` | `54504394096` |
| `codex-debug-triage` | #10 | `e42202fe04b857a38f00c3fc9a76c429516220de` | `35441985492` | `54504392554` |
| `codex-project-journal` | #8 | `ffc808d98fe440e1d646c55f953519babb6c24a4` | `35441985707` | `54504392167` |
| `codex-toolbox` | #34 | `33768d043450f20e95faf275f1ddeee72b6b9ddb` | `35441985549` | `54504392029` |
| `codex-workflow-hygiene` | #78 | `7af0a238bd474b94644f4bff76d09d01a53c8256` | `35441986029` | `54504403801` |
| `codex-review-workflows` | #116 | `58fd209c5760d83053f5ce7c0a1ddeaa28b4c82b` | `35442677862` (attempt 3) | `54504770723` |
| `codex-private-workflows` | #195 | `bc8ed7d4eeba39cb0c964902f8d7ba05a0010caf` | `35442918011` (attempt 3) | `54504839487` |
