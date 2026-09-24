---
name: gh-identity-profiles
description: "Select, initialize, verify, or troubleshoot Joey's host-local GitHub CLI profiles on macOS or Linux using explicit GH_CONFIG_DIR wrappers. Use when choosing a gh identity or diagnosing profile isolation; not for Git or SSH identity setup."
---

# GH Identity Profiles

本 skill 管理跨 macOS 和 Linux 的 GitHub CLI 身份隔离。它分发规则和
wrapper，不分发 token、hosts.yml、Keychain、Linux secret store 或任何 profile
目录。

## 身份映射

| Wrapper | Host | Expected login |
| --- | --- | --- |
| gh-JoeyTeng | github.com | JoeyTeng |
| gh-JoeyTeng-Codex | github.com | JoeyTeng-Codex |
| gh-hoteng_cisco | github.com | hoteng_cisco |
| gh-hoteng | sqbu-github.cisco.com | hoteng |

每个 wrapper 使用 XDG_CONFIG_HOME/gh-profiles；未设置
XDG_CONFIG_HOME 时使用 HOME/.config/gh-profiles。它覆盖 GH_CONFIG_DIR、设置
GH_HOST 为目标 host、清除可能覆盖 profile 的 token/GH_REPO 环境变量，并通过
运行时 PATH 执行 gh。

## 日常 agent 使用

1. 根据用户明确指定的身份选择对应的 gh-<identity> wrapper；不要从仓库 owner、
remote 或当前 gh active account 推断身份。
2. 对非交互 agent 调用添加 GH_PROMPT_DISABLED=1。例如：

~~~sh
GH_PROMPT_DISABLED=1 gh-JoeyTeng pr view 123
~~~

3. 不要使用裸 gh。agent 不得调用 gh auth login、logout 或 switch；wrapper 对这
三个直接调用也会拒绝。其他认证状态变更仍需要用户明确授权。
4. wrapper 报 profile 未初始化时停下并报告；不要以登录、切换账号或读取 token 的
方式自行修复。
5. 对依赖当前仓库 remote 的命令，选择 GH_HOST 与 remote host 匹配的 wrapper；
不匹配时 gh 可能直接报 remote host 不匹配。跨 host 或非当前仓库操作时，使用目标
host 的 HOST/OWNER/REPO 形式 --repo 参数，或该子命令的 --hostname 参数。

## 初始化、验证与修复

只有用户明确授权配置或修复某一个命名 profile 时，才可执行认证状态变更。先运行：

~~~sh
gh-profile-doctor
~~~

然后遵循 references/profile-contract.md 的单 profile 流程。完成后以
gh-profile-doctor --verify <profile> 和 gh api user 的实际 login 验收。profile
缺失在只有两个身份的机器上是正常状态；不要把它当成整个安装失败。

无参数的 doctor 只做本机检查；--verify 会对已初始化 profile 发起 gh api user
请求。省略 --verify 后的 profile 名会验证所有已初始化 profile，并跳过缺失的 profile。

## 边界

- GH_CONFIG_DIR 隔离 gh 配置，但不能替代 Git commit identity、SSH key、remote
  authorization 或 credential-store policy。
- GH_HOST 选择 wrapper 的目标 host。对 current-repository 命令，它必须与 remote
  host 匹配；wrapper 清除继承的 GH_REPO，但显式 repository target 仍应使用目标
  host 的完整形式。
- 每个 profile 必须对应唯一的 host 和实际 login。相同 host/login 的不同 PAT
  权限集合不适合用此机制作为隔离边界。
- wrapper 使用 PATH 中的 gh，不绑定某个包管理器路径；需要 gh 2.75.0 或更高版本，
  以保证同 host 的不同 profile 不会使用旧版 Keychain 的共享 active-token 槽位。
