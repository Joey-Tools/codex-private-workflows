---
id: 20260919-cgrv2o
title: Organization Codex Review Gate v2 Handoff
status: active
created: 2026-09-19
updated: 2026-09-20
branch: wip/codex-review-gate-v2-handoff-journal-replacement
pr:
supersedes: []
superseded_by:
---

# Organization Codex Review Gate v2 Handoff

## Summary

- 已签名发布并验证 `JoeyTeng/codex-review-gate-action` `v2.0.1`；该版本仅修正发布包中的 controller 权限模板与安装文档，运行时 gate 逻辑未变。
- 九个常规目标仓库与 `codex-session-retrospective-history` 已完成 v2 安装或刷新，并以未合并 canary 证明同一 PR head 上的 native v2 check 与 legacy v1 bridge status 都能成功。
- `codex-waited-delivery` 已归档，不再属于活动 consumer cohort，也不会安装 v2。
- 组织 ruleset 切换和 v1 清理尚未开始；旧 ruleset 继续保留 deletion/non-fast-forward 等保护，最终只移除 v1 context。

## Current State

- 已完成常规双保护实证的仓库：`codex-apple-notes-toolkit`、`codex-debug-triage`、`codex-personal-sync`、`codex-private-workflows`、`codex-project-journal`、`codex-review-workflows`、`codex-rollout-backup`、`codex-toolbox`、`codex-workflow-hygiene`。
- `codex-review-workflows#116` 与 `codex-private-workflows#195` 是最后两条无害 canary；均已关闭且未合并，保留成功的 gate 证据。#195 的未完成通用 CI 已在 gate 证据完成后取消，以避免额外 Actions minutes。
- GitHub 当前把 `JoeyTeng/codex-review-gate-action@v2` 的 Node 20 action runtime 强制到 Node 24 并给出弃用 warning；未观察到功能失败。这是后续 source release 的兼容性债务，不阻塞本次交接。
- `codex-session-retrospective-history#7` 是首次安装例外：默认分支此前没有 legacy producer 或 controller。仅在该 PR 的固定 base/head 窗口内，临时从旧 organization ruleset 的目标列表移除该 repository ID，完成 squash merge 后立即恢复原有 ruleset；没有增加 bypass actor 或改变 required-status、strictness、deletion/non-fast-forward 语义。
- `codex-session-retrospective-history#8` 已关闭且未合并。它在同一 exact head 上获得 v1 `codex/review-gate` status success、v2 `codex/github-review-gate` success、CI success 与 current-head Codex terminal clean；两次完整读取相隔 5 秒且未漂移。

## Active Boundary

- 活动 cohort 现在是 10 个仓库：九个常规目标与 `codex-session-retrospective-history`。归档仓库不作为 v2 安装或 canary admission 的成员。
- organization ruleset 切换仍未开始。它只能在受控 handoff executor 的实现、验证与完整 cohort reread 完成后推进；在此之前保留所有 legacy contexts 和 bridges。

## Next Steps

- 重新读取完整 10 仓库活动 cohort，并将归档仓库从最终 handoff manifest / organization target 计划中显式排除。
- cohort 全部满足后，按不可跳过的 phase gate 执行：
  1. 记录精确 v2 organization ruleset ID、target repository IDs、payload digest，以及旧 organization/repository protection 的 before snapshot；此处是 rollback/reconcile 的身份边界。
  2. 激活精确 v2 organization ruleset，但保留所有旧 v1 contexts 与 deletion/non-fast-forward 保护。
  3. 对全部 10 个活动仓库做稳定完整 reread，证明 v2 是实际 required 的严格 context、target 覆盖精确，且旧 protection 仍有效。
  4. 仅在第 3 步通过后，按受控 repository cleanup 从所有有效 organization/repository rules 中移除 `codex/review-gate`；保留旧 ruleset 的其余保护。
  5. 再次完整 reread，要求全部有效规则都不再要求 `codex/review-gate`，同时 v2 coverage 与 deletion/non-fast-forward 仍存在。
  6. 只有第 5 步通过后，才在各仓库删除 legacy bridge，并验证 bridge removal 后的新 PR 不会等待旧 context。
- 任一阶段读回不完整、identity/payload 漂移或覆盖不符时停止后续写入；根据已记录的 before snapshot 重新 reconcile，绝不先删 v2 或 bridge 再尝试恢复旧 v1 保护。

## Evidence

- Source release PR: `Joey-Tools/codex-review-gate#55` (`149769eac4b51df023a0edb79ad4a611d7a3edc3`).
- Release workflow: `Joey-Tools/codex-review-gate` run `35403192145`; target release `JoeyTeng/codex-review-gate-action@v2.0.1`.
- Controller refresh merges: `codex-review-workflows#117` (`0c9c6343963d4915ce4fea10193dd7a06622a6db`) and `codex-private-workflows#194` (`af1e7a0fb5aa66048b2964d5d85bb6b5f1c44382`), plus the seven earlier regular-repository refresh merges.
- Bootstrap installation: `codex-session-retrospective-history#7`, squash commit `53c9a16545147be15c8a5330c5397304a3edb332`. Its authorized exception temporarily excluded only repository ID `1246526548` from organization ruleset `#16590367`, then restored the exact required-status payload and target list immediately after merge.
- Bootstrap canary: `codex-session-retrospective-history#8`, closed unmerged. Canonical request `#5752956871`, terminal clean `#5752960740`, v1 status `54546138115`, v2 verifier run `35540038644`, and 5-second stable double-read evidence are recorded on the PR.
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
| `codex-session-retrospective-history` | #8 (closed) | `0b6556c7b7dc7c934b43151223ad78eeb4e3e9db` | `35540038644` | `54546138115` |
