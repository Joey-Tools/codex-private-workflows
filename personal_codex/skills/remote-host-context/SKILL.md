---
name: remote-host-context
description: Collect read-only task evidence across Joey's local machine, BL-mac-mini-m4-hoteng, miku-bot-dev, hoteng-srv-01, and codex-hoteng-srv-01. Use when Apple Notes work reports, session/history scans, repo-state recovery, or similar workflow summaries might miss work done on remote hosts.
---

# Remote Host Context

## Overview

Use this skill when the answer depends on where Joey worked, not just which local repository is open.
It standardizes a small read-only SSH preflight across Joey's default remote hosts before summarizing activity, judging missing evidence, or recovering recent context.

## Default Host Policy

- Treat these hosts as Joey's default evidence scope unless the user explicitly narrows it:
  - local machine
  - `BL-mac-mini-m4-hoteng`
  - `miku-bot-dev` (Joey may also refer to it as `miku-server-dev`)
  - `hoteng-srv-01`
  - `codex-hoteng-srv-01`
- Do not require Joey to mention any default remote separately in future conversations. Include all four SSH aliases in the default preflight.
- Treat `hoteng-srv-01` and `codex-hoteng-srv-01` as distinct evidence roots even though they reach the same remote hostname; they use different login users and Codex homes.
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
- The helper takes a repeatable `--host` option for `preflight` and `session-meta`; do not pass positional host names or a plural `--hosts` flag.
- For the default evidence scope, use `python3 /Users/hoteng/.codex/skills/remote-host-context/scripts/remote_codex_probe.py preflight --host local --host BL-mac-mini-m4-hoteng --host miku-bot-dev --host hoteng-srv-01 --host codex-hoteng-srv-01`.
- For required remote evidence workflows, if `remote_codex_probe.py preflight`, `session-meta`, `session-shards`, `rollout-summary`, `chunked-rollout-summary`, `fetch-rollout`, or `fetch-rollout-chunk` fails with a local sandbox/network error such as `Operation not permitted`, a DNS/network gate, or a connection blocked before SSH authentication, immediately rerun the same helper command with `sandbox_permissions=require_escalated`.
- Only report a host as unreachable after that escalated retry also fails, and include the exact gate from the failed retry.
- Use `... remote_codex_probe.py session-meta ...` only to list candidate session ids, cwd values, and canonical rollout paths from bounded `sessions/YYYY/MM/DD/` date trees.
- `session-meta` expects `--date YYYY/MM/DD`; do not pass ISO `YYYY-MM-DD`.
- `session-shards` is the only SSH/transport primitive for Session Retrospective v2. Use `--emit descriptors` to plan content-free ranges, then use `--emit records` only for one exact ready range with the matching `source_token` and, for a non-zero start, the descriptor's resume cursor.
- Only an explicitly requested shadow Daily partial/backfill qualification may use `--emit holdout-receipt`. It requires `--qualification-mode shadow`, `--controlled-missing-host`, and the status-issued host/window/source-kind/source-lease binding. Before snapshot or execution, the shadow runner requires the exact closed holdout option set and compares every present authenticated binding to the action and lease. In the acceptance campaign, create the run-local mode-0700 identity first through the shadow runner bootstrap and make the leased transport use `--require-existing-shadow-identity`; never create it inside the leased action.
- Treat the authenticated holdout receipt as a dedicated controlled-missing-host terminal state with `backfill_required`, never as `no_activity`, invalid JSON, timeout, authentication failure, unreachable transport, or a processing-budget gap. Production mode, local-host holdout, a day ending after the latest closed UTC midnight, unsafe identities, replay, and cross-binding use fail closed without a receipt.
- A later backfill must use real `session-shards` evidence for only the held-out host, preserve the exact window/source kind, name the partial run lineage, and replace the authenticated `holdout_ref`; the authenticated partial coverage and sole gap must bind that exact ref while all other canonical hosts are covered. Another holdout or second gap cannot satisfy backfill. The runner binds each authenticated coverage receipt to its status mode and window, derives the persisted outcome from the exact source cell, requires the host aggregate to match, and permits no-activity only with a zero-record manifest. The backfill status may contain only that closed source cell, its one manifest, and its exact snapshot/transport receipt references and single-source units. Its source transport receipt and backfill source lease are one-time bindings consumed with the holdout replacement in the persistent atomic ledger transaction; cross-holdout replay is rejected. Coordinator identity and coverage verification run only in the bounded sandboxed verifier subprocess.
- The Session Retrospective v2 shadow automation must route every coordinator invocation and accepted backfill through `scripts/session_retrospective_v2_shadow_runner.py`, including no-argument `help` and one-time run-local coordinator `identity` bootstrap. The runner owns the command/finalize-phase allowlist, macOS pre-execution write sandbox, production/provider-state rejection, per-host process/thread lock, and coordinator process-group timeouts. It requires the authenticated source command to use the runner Python with `-I`, verifies the transport-program SHA-256 commitment, executes an owner-only snapshot of those exact bytes, pins source identity creation to the invocation root, and refreshes its output-idle limit from either published output or the helper's bounded owner-only raw-chunk progress sidecar while retaining a lease-derived total wall-clock limit. It propagates a supervisor cleanup failure instead of masking an unreaped descendant behind the original timeout. It atomically publishes the transient stream and calls the matching `accept-source` action under that same host lock; a runner, capture, timeout, cleanup, commitment, progress-sidecar, or sandbox failure blocks the scenario.
- The exact installed v2 coordinator path is derived from `<effective HOME>/.codex/skills/codex-session-retrospective/scripts/session_retrospective_v2.py`; on Joey's automation host it resolves to `/Users/hoteng/.codex/skills/codex-session-retrospective/scripts/session_retrospective_v2.py`. The shadow runner exposes the resolved path in `--help` and does not accept a CLI path override. On a fresh stable workspace it may create only the fixed ignored `.codex-local` parent with mode `0700`; an existing parent must be a real current-user directory that is not group/world writable, while every shadow artifact directory remains exact mode `0700`. Reference-only Daily and Weekly automation TOMLs are release inputs, not proof of a live registration, and the shadow runner has no automation registration or update action.
- Generate the Daily qualification pair only with the runner's `bootstrap-daily-holdout-identity`, `start-daily-pair`, and `start-daily-pair-successor` actions. Bootstrap one direct-child run-local holdout identity before the partial start and pass that exact path with `--holdout-identity-path`; bootstrap creates identity material only and cannot emit a receipt, consume a source lease, or run transport. The later status-authenticated source action must retain its exact lease and use `--require-existing-shadow-identity`; the runner rejects leased attempts to create or replace the identity. The runner persists its key ID before launch, and the receipt and successor must retain that exact identity. The partial start always passes every canonical host and records `production_source_suppressed: false`; its only allowed missing source is an authenticated controlled holdout for one status-issued lease. The successor verifies the terminal partial status and coverage receipt, exact run ref, sole gap, all-other-host coverage, empty active leases, identity key ID, and the receipt's host/window binding before atomically recording lineage and starting the same-window, same-config, single-host backfill. A missing or replaced holdout identity blocks the pair. A failure leaves a non-retryable inspection state instead of silently starting another run. Once created, the inert `simulation-history` directory is checked after every coordinator action and must remain empty.
- Keep `session-meta`, `fetch-rollout`, `fetch-rollout-chunk`, `rollout-summary`, and `chunked-rollout-summary` unchanged for their existing callers. Do not use them, bare `ssh`, or a child agent to fill a Retrospective v2 transport gap.
- Resume descriptor pagination only from `stream_end.next_byte_start`, `stream_end.next_record_start`, and `stream_end.next_resume_cursor` with the same token. The cursor supplies the next record index without rescanning the source prefix. A stale token, invalid cursor, missing terminal frame, missing remote end marker, source mutation, SSH failure, or timeout makes that invocation incomplete.
- A single JSONL record scan stops at the advertised hard byte ceiling even when no newline appears. A hard-scan failure has no terminal completion and must not be retried through a weaker or unbounded reader.
- The generated remote `session-shards` program must remain write-free and execute through `python3 -I -B -`: validate and hash records in a bounded first source pass, then re-read exact source offsets for bounded frame emission without a remote spool or temporary file. Every content-free gap owns exactly one record, `invalid_json` gaps stay within the processing budget, and all gaps stay within the hard scan ceiling. The local receiver refreshes its idle watchdog on each bounded raw output chunk, not only after a complete line, and may append one content-free progress byte per chunk only to the runner-authenticated owner-only sidecar below the shadow invocation root.
- Treat `session-shards` record, fragment, and record-mode gap frames as raw transient evidence. Route them only to the bounded supervisor intake; never copy them into normal logs, prompts, retained reports, or committed artifacts.
- Read [references/session-shards-v1.md](references/session-shards-v1.md) before integrating this protocol; it defines range ownership, frame schemas, limits, and recovery.
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
- `BL-mac-mini-m4-hoteng` currently stores Codex history directly in `/Users/hoteng/.codex`.
- `miku-bot-dev` currently stores Codex history directly in `/home/hoteng/.codex`.
- `hoteng-srv-01` currently stores Codex history directly in `/home/hoteng/.codex`.
- `codex-hoteng-srv-01` logs in as user `codex` on host `hoteng-srv-01` and stores Codex history directly in `/home/codex/.codex`; keep it in the default evidence scope because it is distinct from the `hoteng` account's history.
- On `hoteng-srv-01`, dev-shell-kit containers may mount the host `~/.codex` into the container. Treat the host path as canonical by default instead of entering containers first.
- If a host is stale relative to the requested date range, say that explicitly and deprioritize it instead of silently dropping it.

6. Feed the result into the parent workflow.
- For `$apple-notes-work-report`, merge remote host evidence before deciding an item is missing from the report.
- For `$apple-notes-work-report`, a truncated or raw-fetch-recommended remote rollout is an evidence gap until `chunked-rollout-summary` and any necessary bounded chunk fetches have been handled or explicitly blocked.
- For session-mining or workflow-summary tasks, list which hosts contributed evidence and which hosts were stale or unavailable.
- For repo recovery tasks, read remote repo journals only after the host preflight confirms the host was active.
- Outside Session Retrospective v2, if helper-level bounded reads are still insufficient and the remote host already has Codex installed, a remote read-only Codex agent is an acceptable last-resort fallback. Scope it to one verified rollout or one repo/day question, require citations to session ids or rollout paths, and keep it read-only on the remote host. Never use that fallback for v2 source transport, holdout, or backfill.

## Guardrails

- Keep remote work read-only unless Joey explicitly authorizes modification.
- Do not silently fall back to local-only evidence when the user expects cross-host coverage.
- Do not enter `hoteng-srv-01` containers by default just because they exist; host-level `~/.codex` is the first source of truth.
- Do not collapse `hoteng-srv-01` and `codex-hoteng-srv-01` by remote hostname; both account-specific Codex roots belong in the default preflight.
- When SSH preflight fails, stop at the smallest useful decision point instead of guessing what happened on the remote host.
- When `remote_codex_probe.py` already covers the repeated host read, do not regress to host-specific bare `ssh`, remote `jq`, or remote `rg` literals just because they seem faster in the moment.

## References

- Use `references/hosts.md` for the currently verified aliases, paths, and host-specific notes.
- Use `references/session-shards-v1.md` for the streamed descriptor/record contract and recovery rules.
