---
id: 20260908-asi001
title: Archify External Source Integration
status: completed
created: 2026-09-08
updated: 2026-09-09
branch: wip/archify-source-sync
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/186
supersedes: []
superseded_by:
---

# Archify External Source Integration

## Summary

- 将 `Joey-Tools/archify` 纳入 private overlay 的 source-lock inventory。
- 将锁定 source 的 `archify/` 目录同步到 `personal_codex/skills/archify`，作为正常 private install skill。

## Current State

- Archify 使用 fork 的 `joey-custom` 默认分支；private overlay 不同步 repo-local `.agents/skills/archify-maintenance`。
- source lock 会绑定 Archify 的完整 commit/tree，安装内容只由显式 sync rule 选择。
- `codex-review-workflows` 已在 PR #112 合并 runtime pin 修复；source lock 更新到 merge commit `06d8a1573ab04e6ccc58c79d944d3797fdc2a9bd` / tree `6a2ddb7b8cc6cf533f25703726a5741c8bb06161`。
- CI 发现 PR 分支中的 vendored review workflow skill 仍停留在旧 profile catalog；已同步 canonical merge 中实际变化的 3 个文件，确保 source lock 与安装内容一致。
- `master` 的 #187 更新了相同的 macOS review-gate 文件；#186 已通过签名 merge commit 合入当前 `master`，保留新的 runner pin 与两代 macOS profile 的兼容选择，并重新生成合并后测试文件的 source-lock 条目。
- 后续 review 发现已修复：Archify 的 `test/` 排除现在按根级相对路径处理，嵌套运行时 `test` 目录保留；CLI 的 usage、diagnostic、brand 和 guide 提示现在从当前 Node 与已加载 Skill 目录生成可执行路径。

## Decisions

- 保留 Archify 自身的 repo-local maintenance skill，不把它安装到 private skill 集合。
- 不增加新的 CI 或定期同步 workflow；沿用现有 private overlay release 流程。

## Next Steps

- 集成已完成；后续 source refresh 由人工触发，并继续沿用当前 source-lock 与 sync rule 验证。

## Validation

- private overlay sync and source-lock tests pass (390 tests, 1 skipped), including root-only staging exclusions and loaded-skill CLI path regression coverage.
- installed Archify removes repository-only `scripts`, `devDependencies`, `package-lock.json`, the `test/` tree, and generator scripts while retaining runtime update-contract files.
- Archify runtime instructions resolve the loaded skill directory explicitly while preserving the target repository as the working directory.
- private package contains the `archify` skill without the repo-local `archify-maintenance` skill.

## Evidence

- `private-overlay-source-lock.json`
- `scripts/private_overlay_source_lock.py`
- `scripts/sync_private_overlay_sources.py`
- `.github/workflows/scheduled-sync-release.yml`
