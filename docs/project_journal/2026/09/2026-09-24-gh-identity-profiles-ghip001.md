---
id: 20260924-ghip001
title: Host-local GH Identity Profiles
status: active
created: 2026-09-24
updated: 2026-09-24
branch: wip/gh-identity-profiles
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/200
supersedes: []
superseded_by:
---

# Host-local GH Identity Profiles

## Summary

- 在 private overlay 中新增四个明确命名的 GitHub CLI wrapper：JoeyTeng、
  JoeyTeng-Codex、hoteng_cisco 与 hoteng。
- wrapper 使用 host-local GH_CONFIG_DIR，设置目标 host，清除 token/GH_REPO
  环境变量，并在缺少可读的 hosts.yml、gh 版本过旧或找不到 PATH 中的 gh 时给出明确错误。
- 新增 gh-profile-doctor 与 gh-identity-profiles skill，检查最低 gh 版本、提供无提示
  实际 login 验证及逐 profile 的本机初始化合同。

## Current State

- rules、wrapper、doctor 与 skill 均已加入 private sync manifest；profile directory、
  hosts.yml、Keychain、Linux secret store 和 token 不会进入 release。
- wrapper 使用运行时 PATH 中的 gh，不绑定 Homebrew、apt 或其他机器特定路径。
- Cisco GHE probe 通过 gh-hoteng wrapper 访问 sqbu-github.cisco.com，不会落回裸 gh
  或环境默认身份；wrapper 的 gh auth 路径只允许只读的 status。
- 当前 Mac 已完成 JoeyTeng、JoeyTeng-Codex、hoteng_cisco 与 hoteng 的显式、本机
  认证；gh-profile-doctor --verify 已逐一确认四个实际 login 与目标 host。凭据仍只
  存在于本机，不会随 release 同步。
- 尚未发布到其他机器；release 安装完成后，各机器仍需按需逐一进行显式、本机认证和
  doctor 验证，因此此 workstream 保持 active。

## Validation

- 已通过 wrapper 的 shell syntax 与 shellcheck 校验。
- 已通过聚焦的 private overlay package、安装 symlink、wrapper 环境隔离、doctor
  版本/login 验证与 manifest/skill 路由测试。
- 已通过全量 Python 测试套件：2,070 项通过、4 项跳过。
- 已完成 GPT-5.6 Terra Ultra 本地 review，并采纳最低 gh 版本与 remote-host 匹配
  规则的两个有效发现。

## Follow-up

- 合并并发布 private overlay 后，在每台目标机器运行 gh-profile-doctor，随后按
  gh-identity-profiles skill 的单 profile 流程逐一初始化实际需要的身份。
