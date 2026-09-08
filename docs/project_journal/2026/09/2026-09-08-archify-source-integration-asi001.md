---
id: 20260908-asi001
title: Archify External Source Integration
status: active
created: 2026-09-08
updated: 2026-09-08
branch: wip/archify-source-sync
pr:
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

## Decisions

- 保留 Archify 自身的 repo-local maintenance skill，不把它安装到 private skill 集合。
- 不增加新的 CI 或定期同步 workflow；沿用现有 private overlay release 流程。

## Next Steps

- 更新六源 source lock、checkout workflow、manifest 和同步测试。
- 验证 private package 中的 `skills/archify` 完整且不包含维护 skill。

## Evidence

- `private-overlay-source-lock.json`
- `scripts/private_overlay_source_lock.py`
- `scripts/sync_private_overlay_sources.py`
- `.github/workflows/scheduled-sync-release.yml`
