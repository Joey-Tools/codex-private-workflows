# Profile Contract and Local Provisioning

## Local-only state

同步 release 只安装 wrapper、doctor 和 skill。下列状态必须留在各机器本地，绝不
提交或复制到 overlay：

- profile directory and its hosts.yml
- Keychain, credential helper, or Linux secret-store entries
- tokens, browser/device-login state, and environment exports

Wrapper 和 doctor 使用相同的根目录：

~~~sh
if [ -n "$XDG_CONFIG_HOME" ]; then
    profile_root=$XDG_CONFIG_HOME/gh-profiles
else
    profile_root=$HOME/.config/gh-profiles
fi
~~~

| Profile | Host | Directory below profile_root |
| --- | --- | --- |
| JoeyTeng | github.com | github.com/JoeyTeng |
| JoeyTeng-Codex | github.com | github.com/JoeyTeng-Codex |
| hoteng_cisco | github.com | github.com/hoteng_cisco |
| hoteng | sqbu-github.cisco.com | sqbu-github.cisco.com/hoteng |

## Before provisioning

在任何机器上先运行 gh-profile-doctor。它禁用 gh update notifier 和 telemetry，
只检查 PATH 中的 gh 是否可运行、报告版本并根据可读的 hosts.yml 列出 profile 状态，
不会联网、读取 token 或发起登录。只有 gh-profile-doctor --verify [PROFILE ...]
会调用 gh api user 验证实际 login；未提供 profile 名时，它验证所有已初始化 profile，
并跳过未初始化的 profile。
需要 gh 2.75.0 或更新版本，以便同 host 的不同 profile 各自解析对应的
credential-store 登录。

一个 profile 的 host 与实际 login 必须唯一。若两个 profile 使用相同的 host/login
但需要不同权限的 PAT，GH_CONFIG_DIR 不是足够的隔离边界；先停止并选择不同的
OS credential-store 或 account design。

## Explicit one-profile provisioning

以下是用户明确授权后才可做的本机、单 profile 流程。一次只初始化表中的一个
profile，完成验证后再继续下一个。示例使用 JoeyTeng：

~~~sh
if [ -n "$XDG_CONFIG_HOME" ]; then
    profile_root=$XDG_CONFIG_HOME/gh-profiles
else
    profile_root=$HOME/.config/gh-profiles
fi

profile_dir=$profile_root/github.com/JoeyTeng
(
    unset GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
    unset GH_PATH GH_REPO
    export GH_NO_UPDATE_NOTIFIER=1
    export GH_TELEMETRY=0
    mkdir -p "$profile_dir"
    chmod 700 "$profile_dir"
    GH_CONFIG_DIR="$profile_dir" gh auth login --hostname github.com
    GH_CONFIG_DIR="$profile_dir" GH_HOST=github.com GH_PROMPT_DISABLED=1 \
        gh api user --jq .login
)
gh-profile-doctor --verify JoeyTeng
~~~

将目录、hostname、expected login 分别替换为映射表中的目标值。登录过程中允许
用户选择 GitHub CLI 支持的浏览器、设备码或本机 credential-store 路径；不要把
token 传给 wrapper、脚本、sync manifest 或 agent prompt。

期望的 api user 输出必须与 profile 名完全相同。输出不匹配、请求失败或 doctor
报告失败时，不要调用 auth switch 或尝试共享另一个 profile 的目录；保留现状并向
用户报告实际 login 与目标 profile。

GH_HOST 是 wrapper 的目标 host。对依赖当前 Git remote 的命令，必须选择与 remote
host 匹配的 wrapper；不匹配时 gh 可能报告 remote host 不匹配。wrapper 会清除继承
的 GH_REPO；跨 host 操作时必须显式选择目标 host 的 repository target。

## Repair and removal boundary

普通任务不修复 profile。用户明确要求修复时，先用 gh-profile-doctor --verify
<profile> 获取无交互错误，再仅在该 profile 的 GH_CONFIG_DIR 下执行用户要求的
认证操作，并立即复验。删除 profile directory 或 credential-store entry 是
破坏性操作；在执行前必须确认确切 host/profile，并优先让用户在 gh 的原生流程中
完成。
