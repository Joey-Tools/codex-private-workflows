---
id: 20260919-cgrv2o
title: Organization Codex Review Gate v2 Handoff
status: completed
created: 2026-09-19
updated: 2026-09-27
branch: wip/remove-v1-bridge-private
pr:
supersedes: []
superseded_by:
---

# Organization Codex Review Gate v2 Handoff

## Summary

- 已签名发布并验证 `JoeyTeng/codex-review-gate-action` `v2.0.1`；该版本仅修正发布包中的 controller 权限模板与安装文档，运行时 gate 逻辑未变。
- 九个常规目标仓库与 `codex-session-retrospective-history` 已完成 v2 安装或刷新，并以未合并 canary 证明同一 PR head 上的 native v2 check 与 legacy v1 bridge status 都能成功。
- `codex-waited-delivery` 已归档，不再属于活动 consumer cohort，也不会安装 v2。
- 组织 ruleset 切换、active-10 post-cutover audit 与 v1 required-status 清理均已完成；旧 ruleset 仅继续保留 deletion/non-fast-forward 等保护，不再要求 v1 context。

## Completion (2026-09-27)

- The organization v2 ruleset cutover and the full active-10 post-cutover audit completed before this cleanup. The admitted closure receipt was exactly recovered from persistent GitHub PR, Actions, and organization-ruleset evidence; its canonical SHA-256 is `9a8b38f2188a14168423a07639d6662c87e198fe2dd12041f67fc224f363817e`.
- This repository no longer contains the temporary v1 legacy bridge or produces `codex/review-gate`; pull requests use the canonical v2 verifier and controller only.
- The regression guard scans both GitHub Actions workflow extensions (`.yml` and `.yaml`) before accepting that no legacy v1 caller or bridge remains.
- The retained legacy organization ruleset continues to protect deletion and non-fast-forward updates without reintroducing a v1 required status.

## Historical State (2026-09-20)

- 已完成常规双保护实证的仓库：`codex-apple-notes-toolkit`、`codex-debug-triage`、`codex-personal-sync`、`codex-private-workflows`、`codex-project-journal`、`codex-review-workflows`、`codex-rollout-backup`、`codex-toolbox`、`codex-workflow-hygiene`。
- `codex-review-workflows#116` 与 `codex-private-workflows#195` 是最后两条无害 canary；均已关闭且未合并，保留成功的 gate 证据。#195 的未完成通用 CI 已在 gate 证据完成后取消，以避免额外 Actions minutes。
- GitHub 当前把 `JoeyTeng/codex-review-gate-action@v2` 的 Node 20 action runtime 强制到 Node 24 并给出弃用 warning；未观察到功能失败。这是后续 source release 的兼容性债务，不阻塞本次交接。
- `codex-session-retrospective-history#7` 是首次安装例外：默认分支此前没有 legacy producer 或 controller。仅在该 PR 的固定 base/head 窗口内，临时从旧 organization ruleset 的目标列表移除该 repository ID，完成 squash merge 后立即恢复原有 ruleset；没有增加 bypass actor 或改变 required-status、strictness、deletion/non-fast-forward 语义。恢复 payload 的 sorted-JSON (`jq -S -c`) SHA-256 是 `ed59cb148cdeecaeb471427011151ed3fbc02d30574a232033f97d139ca85a5a`，其 target IDs 精确为 `1242512097, 1242512092, 1242511852, 1242512336, 1242511845, 1242511842, 1242512323, 1242511840, 1242512099, 1242512084, 1246526548`，`bypass_actors` 为空。随后对该仓库默认分支的 effective-rules readback 已确认 organization source `Joey-Tools`、ruleset `16590367` 仍提供 deletion、non-fast-forward 和 strict `codex/review-gate`。
- `codex-session-retrospective-history#8` 已关闭且未合并。它在同一 exact head 上获得 v1 `codex/review-gate` status success、v2 `codex/github-review-gate` success、CI success 与 current-head Codex terminal clean；两次完整读取相隔 5 秒且未漂移。

## Historical Pre-Cutover Boundary (2026-09-20)

- 活动 cohort 现在是 10 个仓库：九个常规目标与 `codex-session-retrospective-history`。归档仓库不作为 v2 安装或 canary admission 的成员。
- 当时 organization ruleset 尚未切换：受控 handoff executor 的实现、验证与完整 cohort reread 尚在进行，因而暂时保留了所有 legacy contexts 和 bridges。该限制已在后续 cutover 完成后解除；当前只保留旧 ruleset 的 deletion/non-fast-forward 保护。

## Completed Cutover

- The active-10 cohort was reread with stable, complete snapshots before the organization v2 ruleset became the required strict context.
- The old v1 required status was removed while the legacy ruleset retained deletion and non-fast-forward protections.
- A second complete audit admitted the immutable receipt above before any repository bridge removal.
- This repository bridge removal was then performed by the receipt-bound helper; any unreadable or drifting recheck would have preserved the bridge instead.

## Evidence

- Source release PR: `Joey-Tools/codex-review-gate#55` (`149769eac4b51df023a0edb79ad4a611d7a3edc3`).
- Release workflow: `Joey-Tools/codex-review-gate` run `35403192145`; target release `JoeyTeng/codex-review-gate-action@v2.0.1`.
- Controller refresh merges: `codex-review-workflows#117` (`0c9c6343963d4915ce4fea10193dd7a06622a6db`) and `codex-private-workflows#194` (`af1e7a0fb5aa66048b2964d5d85bb6b5f1c44382`), plus the seven earlier regular-repository refresh merges.
- Bootstrap installation: `codex-session-retrospective-history#7`, squash commit `53c9a16545147be15c8a5330c5397304a3edb332`. Its authorized exception temporarily excluded only repository ID `1246526548` from organization ruleset `#16590367`, then restored the exact required-status payload and target list immediately after merge. The restored sorted-JSON payload fingerprint is `ed59cb148cdeecaeb471427011151ed3fbc02d30574a232033f97d139ca85a5a`; the post-restore effective-rule readback binds the same organization source/ruleset ID and the three expected legacy rules.
- Bootstrap canary: `codex-session-retrospective-history#8`, closed unmerged. Canonical request `#5752956871`, terminal clean `#5752960740`, v1 status `54546138115`, v2 verifier [run `35540038644` attempt 4](https://github.com/Joey-Tools/codex-session-retrospective-history/actions/runs/35540038644/attempts/4), and 5-second stable double-read evidence are recorded on the PR.
- Post-cutover audit implementation: `Joey-Tools/codex-review-gate#81`.
- Final audit receipt: `9a8b38f2188a14168423a07639d6662c87e198fe2dd12041f67fc224f363817e`. Its exact recovery used only durable GitHub PR, Actions, and organization-ruleset records, reconstructing the original canonical receipt rather than minting a replacement receipt.
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
| `codex-session-retrospective-history` | #8 (closed) | `0b6556c7b7dc7c934b43151223ad78eeb4e3e9db` | `35540038644` (attempt 4) | `54546138115` |
