---
id: 20260924-ghip001
title: Named GH Identity Wrappers
status: active
created: 2026-09-24
updated: 2026-09-28
branch: wip/gh-identity-profiles
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/200
supersedes: []
superseded_by:
---

# Named GH Identity Wrappers

## Summary

- 在 private overlay 中新增四个明确命名的 GitHub CLI wrapper：JoeyTeng、
  JoeyTeng-Codex、hoteng_cisco 与 hoteng。
- wrapper 从普通全局 `gh auth` 账号池按精确 host/login 执行 `gh auth token`，仅为
  单次 action 注入 `GH_TOKEN` 或 `GH_ENTERPRISE_TOKEN`，并设置目标 host。
- 新增 `gh-profile-doctor` 与 `gh-identity-profiles` skill，分别报告可选账号可用性、
  提供无提示实际 login 验证，以及按需的普通 `gh auth login` 配置合同。

## Current State

- rules、wrapper、doctor 与 skill 均已加入 private sync manifest；普通 `gh auth`
  配置、Keychain、Linux secret store 和 token 不会进入 release。此设计不再创建或同步
  `GH_CONFIG_DIR` profile directory 与专属 `hosts.yml`。
- wrapper 使用运行时 PATH 中的 `gh`，不绑定 Homebrew、apt 或其他机器特定路径；每次
  action 前清除继承的配置、token 和路由变量，从默认账号池以 `--hostname` 与 `--user`
  精确取得目标账号的 token。
- Cisco GHE probe 从已安装 overlay 的确定性路径调用 `gh-hoteng`，使 GHE action 选择
  `hoteng@sqbu-github.cisco.com`，而非裸 `gh` 或环境默认身份。
- 目标机器可先运行 `gh-profile-doctor` 检查当前普通账号池。已有的 host/login 可立即由
  对应 wrapper 使用；缺少的命名身份是正常的可选状态，只在实际需要且获授权时进行一次
  普通 `gh auth login`，随后用 doctor 验证。凭据仍只存在于各机器，不会随 release 同步。
- 当前 Mac 的默认账号池已确认包含全部四个映射；新 doctor 已逐一验证
  JoeyTeng、JoeyTeng-Codex、hoteng_cisco 与 hoteng 的实际 login 和目标 host，未触发
  任何重新登录。
- 此 workstream 保持 active，直到 private overlay release 可用并已在实际需要的目标机器上
  通过 doctor 完成可用性或 login 验证。

## Validation

- 已通过 wrapper 的 shell syntax 与 shellcheck 校验。
- 已通过聚焦的 private overlay package、安装 symlink、wrapper 身份选择、doctor
  可用性/login 验证与 manifest/skill 路由测试。
- 已通过 token-wrapper 迁移后的全量 Python 测试套件：2,072 项通过、4 项跳过。
- 已完成 GPT-5.6 Terra Ultra 本地 review，并采纳 wrapper 身份选择与 remote-host
  匹配规则的有效发现。

## Follow-up

- 合并并发布 private overlay 后，在每台目标机器运行 `gh-profile-doctor`；普通账号池
  已有的身份无需重复登录，只按需为缺失身份执行一次普通 `gh auth login` 并复验。
