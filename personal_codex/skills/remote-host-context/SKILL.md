---
name: remote-host-context
description: Collect read-only task evidence across Joey's local machine, miku-bot-dev, and hoteng-srv-01. Use when Apple Notes work reports, session/history scans, repo-state recovery, or similar workflow summaries might miss work done on remote hosts.
---

# Remote Host Context

## Overview

Use this skill when the answer depends on where Joey worked, not just which local repository is open.
It standardizes a small read-only SSH preflight across Joey's default remote hosts before summarizing activity, judging missing evidence, or recovering recent context.

## Default Host Policy

- Treat these hosts as Joey's default evidence scope unless the user explicitly narrows it:
  - local machine
  - `miku-bot-dev` (Joey may also refer to it as `miku-server-dev`)
  - `hoteng-srv-01`
- Do not require Joey to mention `hoteng-srv-01` separately in future conversations. Include it in the default preflight.
- For tasks rooted in local mutable state such as Apple Notes, local GUI apps, or local databases, keep all writes local. Remote hosts contribute evidence only unless Joey explicitly asks to modify them.

## Workflow

1. Start with a read-only SSH preflight.
- Check each remote host for reachability, user/home identity, and presence of `~/.codex`.
- If the task will need text search inside remote history, also check whether `rg` is available on that host.
- Report the exact missing gate: SSH/auth failure, host unreachable, missing `~/.codex`, or no matching evidence in the requested date range.
- Prefer the installed helper path `/Users/hoteng/.codex/skills/remote-host-context/scripts/remote_codex_probe.py` when available, so approved installed-helper prefix rules match; use the checked-out skill helper only for local development or when the installed helper is absent.
- Treat that preflight as a bounded host check, not a license to keep host-specific `jq`, `rg`, or shell-wrapped remote queries in `~/.codex/rules/default.rules`.
- If the same remote summary or mining pattern starts repeating, factor it into a dedicated helper under this skill instead of widening bare `ssh` approvals with more literal commands.

2. Use the helper for bounded remote Codex reads once the preflight is clear.
- Prefer `python3 /Users/hoteng/.codex/skills/remote-host-context/scripts/remote_codex_probe.py preflight ...` for recurring host checks.
- For required remote evidence workflows, if `remote_codex_probe.py preflight`, `session-meta`, `rollout-summary`, `chunked-rollout-summary`, `fetch-rollout`, or `fetch-rollout-chunk` fails with a local sandbox/network error such as `Operation not permitted`, a DNS/network gate, or a connection blocked before SSH authentication, immediately rerun the same helper command with `sandbox_permissions=require_escalated`.
- Only report a host as unreachable after that escalated retry also fails, and include the exact gate from the failed retry.
- Use `... remote_codex_probe.py session-meta ...` only to list candidate session ids, cwd values, and canonical rollout paths from bounded `sessions/YYYY/MM/DD/` date trees.
- `session-meta` expects `--date YYYY/MM/DD`; do not pass ISO `YYYY-MM-DD`.
- Use `... remote_codex_probe.py fetch-rollout ...` only to copy one validated rollout file under task-scoped `.codex-tmp/remote-host-context/` beneath the current workspace, or under `/tmp`.
- `fetch-rollout` writes to a single file path via `--output <file>`; do not invent directory flags such as `--output-dir`.
- When a validated rollout is too large to copy cleanly, use `... remote_codex_probe.py chunked-rollout-summary ...` to scan the whole file in JSONL-record chunks and return structured evidence with byte/record ranges.
- `chunked-rollout-summary` is the default large-rollout fallback; it aims for whole-rollout semantic coverage without copying all raw transcript text.
- Oversized single JSONL records are not parsed in the summary path; the helper emits `chunk_meta` and bounded `fetch_ranges` so exact wording can be fetched explicitly without loading the whole record during summary.
- `rollout-summary` remains a bounded prefix skim. If its `scan_meta.scan_truncated` is `true`, treat it as candidate selection only and upgrade to `chunked-rollout-summary` before writing an activity or work-report conclusion.
- `rollout-summary` emits a `scan_meta` row. If `scan_truncated` is `true`, treat the result as partial evidence and surface a coverage gap; do not summarize it as a complete scan.
- `chunked-rollout-summary` emits `chunk_meta` rows. If a chunk has `raw_fetch_recommended=true`, use `... remote_codex_probe.py fetch-rollout-chunk ...` for the listed `fetch_ranges`; oversized JSONL records may require fetching multiple ranges and concatenating them locally for exact wording.
- `rollout-summary` and `chunked-rollout-summary` output text is signal-only, not raw prompt/tool output text. Use it for coarse friction flags, coverage, and candidate selection; fetch a specific safe rollout/chunk or delegate to `codex-session-mining` when exact local-only context is required.
- `fetch-rollout-chunk` writes one bounded byte range via `--byte-start`, `--byte-end`, and `--output <file>`; use `chunk_meta.fetch_ranges[]` when present, or the chunk `byte_start`/`byte_end` only when no split range is listed.
- `fetch-rollout` may materialize an explicitly verified `archived_sessions/rollout-*.jsonl` path, but the helper should not widen `session-meta` into an unbounded archived-session crawler.
- Keep the helper focused on remote access and bounded file transfer. Do not turn it into a generic remote search shell.

3. Narrow the evidence before reading widely.
- For activity reports, inspect recent `~/.codex/sessions` trees and date-bounded rollout files first.
- Once a canonical remote rollout has been copied locally, prefer local filtering there over adding another remote `rg` command shape.
- Reserve bare remote `rg` for genuinely one-off preflight/debug checks that the helper cannot yet express, and patch the helper if that shape starts repeating.
- For multi-pattern remote searches, prefer repeated `-e` or fixed-string `-F` forms over a single shell-exposed regex such as `foo|bar`; if quoting would get brittle, stream the file back and filter locally instead.
- For repo-specific questions, inspect repo journals, worktrees, or nearby paths only after `~/.codex` indicates that host was actually active for the requested period.
- Keep remote reads bounded by date, repo, or task. Do not turn this into an unbounded home-directory crawl.

4. Delegate deeper rollout mining back to `codex-session-mining`.
- Once `fetch-rollout` has materialized the canonical remote rollout locally, let [$codex-session-mining](../codex-session-mining/SKILL.md) own the extraction, filtering, wrapper-noise skipping, and skill-friction classification.
- If `fetch-rollout` is blocked only by rollout size, prefer `chunked-rollout-summary` first, then fetch only the `raw_fetch_recommended` chunk ranges that materially affect the answer.
- Do not keep a second remote-only search flow here that duplicates `codex-session-mining` semantics.
- If the helper lacks one bounded remote-read primitive that keeps recurring, patch the helper or its references instead of approving another host-specific `ssh ... jq ...` literal.

5. Interpret host-specific structure correctly.
- `miku-bot-dev` currently stores Codex history directly in `/home/hoteng/.codex`.
- `hoteng-srv-01` currently stores Codex history directly in `/home/hoteng/.codex`.
- On `hoteng-srv-01`, dev-shell-kit containers may mount the host `~/.codex` into the container. Treat the host path as canonical by default instead of entering containers first.
- If a host is stale relative to the requested date range, say that explicitly and deprioritize it instead of silently dropping it.

6. Feed the result into the parent workflow.
- For `$apple-notes-work-report`, merge remote host evidence before deciding an item is missing from the report.
- For `$apple-notes-work-report`, a truncated or raw-fetch-recommended remote rollout is an evidence gap until `chunked-rollout-summary` and any necessary bounded chunk fetches have been handled or explicitly blocked.
- For session-mining or workflow-summary tasks, list which hosts contributed evidence and which hosts were stale or unavailable.
- For repo recovery tasks, read remote repo journals only after the host preflight confirms the host was active.
- If helper-level bounded reads are still insufficient and the remote host already has Codex installed, a remote read-only Codex agent is an acceptable last-resort fallback. Scope it to one verified rollout or one repo/day question, require citations to session ids or rollout paths, and keep it read-only on the remote host.

## Guardrails

- Keep remote work read-only unless Joey explicitly authorizes modification.
- Do not silently fall back to local-only evidence when the user expects cross-host coverage.
- Do not enter `hoteng-srv-01` containers by default just because they exist; host-level `~/.codex` is the first source of truth.
- When SSH preflight fails, stop at the smallest useful decision point instead of guessing what happened on the remote host.
- When `remote_codex_probe.py` already covers the repeated host read, do not regress to host-specific bare `ssh`, remote `jq`, or remote `rg` literals just because they seem faster in the moment.

## References

- Use `references/hosts.md` for the currently verified aliases, paths, and host-specific notes.
