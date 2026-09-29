---
id: 20260924-ghip001
title: Global GH Auth Identity Policy
status: active
created: 2026-09-24
updated: 2026-09-29
branch: wip/gh-identity-profiles
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/200
supersedes: []
superseded_by:
---

# Global GH Auth Identity Policy

## Summary

- 在 private overlay 的全局 `AGENTS.md` 中加入 GitHub CLI 身份选择规则；该文件由
  manifest 同步至每台机器的 `~/.codex/AGENTS.md`。
- 每个认证 action 从普通全局 `gh auth` 账号池按精确 host/login 读取 token，并仅对该
  action 注入 `GH_TOKEN` 或 `GH_ENTERPRISE_TOKEN`；不会创建、同步或切换
  `GH_CONFIG_DIR` profile。
- 身份映射为 `github.com` 的 JoeyTeng、JoeyTeng-Codex、hoteng_cisco，以及
  `sqbu-github.cisco.com` 的 hoteng。账号可按机器选择性配置，凭据始终保留在本机。

## Current State

- `personal_codex/AGENTS.md` 已是 manifest 的普通文件链接，因此安装器会将同一条规则
  同步到每台机器；没有新的 wrapper、doctor 或 identity skill 需要安装。
- 认证型 `gh` action 必须在窄范围、非 sandbox 且允许网络的执行中运行。规则要求 token
  lookup 失败或为空时停止，禁止回退到当前 active account，并清除继承的认证变量。
- 未经明确授权不得运行 `gh auth login`、`logout`、`switch` 或 `refresh`。需要的可选
  账号应由操作者按需在本机普通账号池中初始化。
- 注入 token 的调用只允许直接核心 `gh` 子命令，不能使用 aliases 或 extensions；Cisco
  GHE 使用 `GH_ENTERPRISE_TOKEN`，公网 GitHub 使用 `GH_TOKEN`。
- 此 workstream 保持 active，直到 private overlay release 可用并完成新的 policy 迁移验证。

## Validation

- 已通过 private overlay package、sync routing 与 Cisco GHE probe 聚焦测试：395 项通过。
- 已通过全量 Python 测试：2,070 项通过、4 项跳过。
- 已通过 private sync manifest JSON 解析与 project journal validation。

## Follow-up

- 合并并发布 private overlay 后，按新的 `AGENTS.md` 规则对需要使用的身份运行一次窄范围
  `gh api user --jq .login` 验证；普通账号池已有的身份无需重复登录。
