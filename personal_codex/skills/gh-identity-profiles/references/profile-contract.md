# Named Identity Contract and Global gh Auth Pool

## Local-only state

同步 release 只安装 wrapper、doctor 和 skill。下列状态必须留在各机器本地，绝不
提交或复制到 overlay：

- ordinary global `gh auth` configuration and its host/account entries
- Keychain, credential helper, or Linux secret-store entries
- tokens, browser/device-login state, and environment exports

本机制不创建 `GH_CONFIG_DIR` profile directory 或专属 `hosts.yml`。每个 wrapper 在
解析身份时都会清除继承的 `GH_CONFIG_DIR`、`GH_TOKEN`、`GITHUB_TOKEN`、
`GH_ENTERPRISE_TOKEN`、`GITHUB_ENTERPRISE_TOKEN`、`GH_HOST`、`GH_PATH` 和
`GH_REPO`，因此 lookup 固定使用普通默认 `gh auth` 账号池。

| Profile | Host | Account passed to `--user` | Action token variable |
| --- | --- | --- | --- |
| JoeyTeng | github.com | JoeyTeng | GH_TOKEN |
| JoeyTeng-Codex | github.com | JoeyTeng-Codex | GH_TOKEN |
| hoteng_cisco | github.com | hoteng_cisco | GH_TOKEN |
| hoteng | sqbu-github.cisco.com | hoteng | GH_ENTERPRISE_TOKEN |

wrapper 以表中的精确 host 和 account 执行 `gh auth token --hostname <host> --user
<account>`，把结果保留在进程内，并仅为随后的一个 `gh` action 设置相应 token 变量。
它不打印 token、不把它写入文件，也不以默认 active account 作为选择依据。
wrapper 不允许 `gh alias` 管理命令，且会在 token lookup 前拒绝已配置全局 alias 的名称；
这避免 alias 把看似普通的 wrapper action 改写为认证命令。请使用对应的规范 `gh`
子命令，而不是 alias。

## Before account setup

在任何机器上先运行 `gh-profile-doctor`。无参数模式禁用 `gh` update notifier 和
telemetry，检查 PATH 中的 `gh` 是否可运行且支持 `auth token`，并用与 wrapper 相同的
精确 token lookup 报告每个映射身份的可用性。它不会输出 token、联网或修改认证状态。

`gh-profile-doctor --verify [PROFILE ...]` 会对可用身份无提示执行 `gh api user --jq
.login`，确认实际 login 与映射一致。未传 profile 时，它验证所有可用身份并跳过不可用
身份；显式传入不可用或未知身份则失败。可用性检查不要求一个机器拥有四个账号：只有两个
或四个账号的机器都属于正常状态。

一个 profile 的 host 与实际 login 必须唯一。若两个 profile 使用相同的 host/login
但需要不同权限的 PAT，普通 global auth pool 不是足够的区分边界；先停止并选择不同的
OS credential-store 或 account design。

## Explicit one-account configuration

以下仅在用户明确授权后执行。普通账号池已包含目标 host/login 时，不要重新登录；该
账号已可被对应 wrapper 使用。只有某个可选身份缺失且用户要求新增或修复时，才通过普通
`gh auth login` 配置该身份。一次只处理映射表中的一个账号，完成验证后再继续下一个。
示例使用 `JoeyTeng`：

~~~sh
(
    unset GH_CONFIG_DIR GH_TOKEN GITHUB_TOKEN
    unset GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
    unset GH_HOST GH_PATH GH_REPO
    export GH_NO_UPDATE_NOTIFIER=1
    export GH_TELEMETRY=0
    gh auth login --hostname github.com
)
gh-profile-doctor --verify JoeyTeng
~~~

将 hostname 和目标 profile 替换为映射表中的值。登录过程中，用户必须以期望的账号
完成 GitHub CLI 支持的浏览器、设备码或本机 credential-store 流程；不将 token 传给
wrapper、脚本、sync manifest 或 agent prompt。对于 `hoteng`，使用
`--hostname sqbu-github.cisco.com`；其 wrapper 会为 action 使用
`GH_ENTERPRISE_TOKEN`，而非 `GH_TOKEN`。

期望的 `api user` 输出必须与 profile 名完全相同。输出不匹配、token lookup 失败或
doctor 报告失败时，不要调用 `auth switch`、借用另一个账号或创建隔离 profile；保留
现状并向用户报告实际 login 与目标 profile。

`GH_HOST` 是 wrapper 的目标 host。对依赖当前 Git remote 的命令，必须选择与 remote
host 匹配的 wrapper；不匹配时 `gh` 可能报告 remote host 不匹配。wrapper 清除继承的
`GH_REPO`；跨 host 操作时必须显式选择目标 host 的 repository target。

## Repair and removal boundary

普通任务不修复账号池。用户明确要求修复时，先用
`gh-profile-doctor --verify <profile>` 获得无交互错误，再仅对普通 global auth pool
执行用户要求的认证操作，并立即复验。`gh auth logout` 或删除 credential-store entry
会影响该 host/login 的全部 wrapper 使用，属于破坏性操作；执行前必须确认确切账号，
并优先让用户在 `gh` 的原生流程中完成。
