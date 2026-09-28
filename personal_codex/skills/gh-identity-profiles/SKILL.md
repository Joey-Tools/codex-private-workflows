---
name: gh-identity-profiles
description: "Select, verify, or troubleshoot Joey's named GitHub CLI identities on macOS or Linux using explicit wrappers backed by the ordinary global gh auth pool. Use when choosing a gh identity, checking whether an optional account is available, or diagnosing identity selection; not for Git or SSH identity setup."
---

# GH Identity Profiles

本 skill 管理跨 macOS 和 Linux 的 GitHub CLI 命名身份选择。它分发规则和
wrapper，不分发 token、普通 `gh auth` 配置、Keychain、Linux secret store 或浏览器
登录状态。

## 身份映射

| Wrapper | Host | Expected login | Action token variable |
| --- | --- | --- | --- |
| gh-JoeyTeng | github.com | JoeyTeng | GH_TOKEN |
| gh-JoeyTeng-Codex | github.com | JoeyTeng-Codex | GH_TOKEN |
| gh-hoteng_cisco | github.com | hoteng_cisco | GH_TOKEN |
| gh-hoteng | sqbu-github.cisco.com | hoteng | GH_ENTERPRISE_TOKEN |

每个 wrapper 都从普通默认 `gh auth` 账号池精确执行
`gh auth token --hostname <host> --user <identity>`。它会先清除继承的
`GH_CONFIG_DIR`、token 和 GitHub CLI 路由变量，再仅为这一次 `gh` action 注入上表
对应的 token 变量，并设置目标 `GH_HOST`。wrapper 不创建、读取或要求专属
`GH_CONFIG_DIR` profile，也不会输出 token。

## 日常 agent 使用

1. 根据用户明确指定的身份选择对应的 `gh-<identity>` wrapper；不要从仓库 owner、
   remote 或当前 `gh` active account 推断身份。
2. 对非交互 agent 调用添加 `GH_PROMPT_DISABLED=1`。例如：

~~~sh
GH_PROMPT_DISABLED=1 gh-JoeyTeng pr view 123
~~~

3. 不要使用裸 `gh` 执行 GitHub action。不要通过 wrapper 调用 `gh auth login`、
   `logout` 或 `switch`；wrapper 内部按 host/user 调用只读的 `gh auth token` 是其
   正常身份选择机制。wrapper 也不允许 `gh alias` 或已配置的全局 alias，以避免 alias
   改写认证命令；改用对应的规范 `gh` 子命令。维护普通账号池仍需要用户明确授权。
4. 某个命名身份在普通账号池中缺失是正常的可选配置状态，不是安装或 release 失败。
   停下并报告该身份不可用；不要创建 profile 目录、复制凭据或擅自登录。
5. 对依赖当前仓库 remote 的命令，选择 `GH_HOST` 与 remote host 匹配的 wrapper；
   不匹配时 `gh` 可能直接报 remote host 不匹配。跨 host 或非当前仓库操作时，使用
   目标 host 的 `HOST/OWNER/REPO` 形式 `--repo` 参数，或该子命令的 `--hostname`
   参数。

## 账号可用性、验证与配置

先运行：

~~~sh
gh-profile-doctor
~~~

无参数的 doctor 检查 PATH 中的 `gh` 及其 `auth token` 支持，并针对每个映射以相同的
host/user token lookup 报告 `available` 或 `unavailable`；它不打印 token、不联网且不
修改认证状态。
`gh-profile-doctor --verify <profile>` 会无提示地以该身份执行 `gh api user`，验证实际
login。省略 `--verify` 后的 profile 名会验证所有可用身份，并跳过不可用身份；显式要求
验证一个不可用身份会失败。

若普通账号池已经有目标 host/login，该 wrapper 可立刻使用，无需为它再登录或初始化
profile。只有用户明确授权新增或修复一个缺失的可选身份时，才遵循
`references/profile-contract.md` 的单账号流程。完成后以
`gh-profile-doctor --verify <profile>` 的实际 login 验收。

## 边界

- 普通 `gh auth` 账号池是机器本地状态；它不能替代 Git commit identity、SSH key、
  remote authorization 或 credential-store policy。
- wrapper 为单次 action 注入 token，但不应把 token 写入脚本、日志、sync manifest、
  agent prompt 或命令输出。
- `GH_HOST` 选择 wrapper 的目标 host。对 current-repository 命令，它必须与 remote
  host 匹配；wrapper 清除继承的 `GH_REPO`，但显式 repository target 仍应使用目标
  host 的完整形式。
- 每个映射必须对应唯一的 host 和实际 login。同一 host/login 的不同 PAT 权限集合不能
  由此机制区分；需要不同权限模型时，先停止并选择适当的 credential-store 或账号设计。
