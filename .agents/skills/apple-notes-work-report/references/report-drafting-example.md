# Report Drafting Example

当 automation 或其他 Codex 实例需要一个“不要自由发挥”的具体版式时，优先贴近下面这份短版样例。

## Canonical Short Example

```text
2026.03.25 (Wed)

1. 83/5 Virtual Scrum
   1. Synced on release-delivery blockers and the Janus support needed for the dav1d migration.

2. #HDR-streaming
   1. Extended the P010 render-semantics harness and followed up on the DCM alignment probe work.

3. #GitHub-agent
   1. Hardened WorkerActionService and iterated the WorkerRuntimeService shell through review and test passes.

4. #webex-message-archiver
   1. Refreshed the archive: 628 spaces and 628 message files passed integrity checks; recorded the OneDrive eviction limitation.
```

## What This Example Fixes

- 日期标题使用缩写星期，而不是完整 weekday 名称。
- 顶层编号项先写 bucket label；项目类 bucket 通常直接写成 `#project`。
- 具体工作点放在缩进的二级编号里，而不是把 bucket label 改写成普通 prose heading。
- `83/5 Virtual Scrum` 这类已有固定 house style 的 bucket 保留原样，不强行改成 `#tag`。
- `webex-message-archiver` 使用独立的 `#webex-message-archiver` bucket，不因为产品名里有 Webex 就推断成 `#WME`。
- 整体保持短版节奏：通常 `2-4` 个顶层 bucket，每个 bucket `1-2` 条子项。
