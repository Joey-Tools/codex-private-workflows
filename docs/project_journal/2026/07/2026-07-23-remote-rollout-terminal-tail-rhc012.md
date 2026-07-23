---
id: 20260723-rhc012
title: Remote Rollout Terminal Tail
status: completed
created: 2026-07-23
updated: 2026-07-23
branch: wip/remote-rollout-terminal-tail-128m
pr:
supersedes: []
superseded_by:
---

# Remote Rollout Terminal Tail

## Summary

- Refined RHC002's original bounded-read policy without rewriting its historical decision: the single 16 MiB ceiling is now split by operation and paired with a lossless terminal-only path.
- Split rollout read budgets by operation: direct whole-file `fetch-rollout` remains capped at 16 MiB, individual range fetches remain capped at 2 MiB, and automatic complete reconstruction is allowed through 128 MiB.
- Added lossless `terminal-tail` recovery for the narrow question of whether a large Codex rollout reached its current terminal answer.
- Added an independent fixed budget of 1,000,000 complete JSONL records per terminal-tail attempt so a dense 128 MiB suffix cannot cause tens of millions of JSON parses.
- Kept the terminal answer out of stdout: success writes the exact `event_msg.task_complete.last_agent_message` UTF-8 text to one private task-scoped output file and returns compact status metadata.

## Current State

- `terminal-tail` freezes the opened descriptor's initial EOF as `S0` and reads backward with absolute-offset `pread` windows of at most 4 MiB, covering at most 128 MiB of unique tail bytes.
- The protected property is fixed-`S0` reading for the normal Codex append/prefix-update producer model, not complete-content immutability or adversarial same-inode tamper resistance. The source object and current path entry stay pinned, the file cannot shrink below `S0`, and a distinctive substring near `S0` must remain at its recorded absolute offset.
- Append growth and prefix metadata overwrites that preserve the frozen coordinates are allowed. `mtime` and `ctime` changes alone do not fail the attempt.
- The anchor is a bounded coordinate witness. The helper does not hash the prefix or every scanned range, does not relocate a moved anchor, and does not retry automatically; a deliberate same-inode rewrite that restores identical bytes at the witness offset remains an explicit limitation.
- A trailing partial JSONL record at `S0` returns `source_in_progress`. A complete user turn after the newest task completion returns `terminal_not_reached`. Success requires the latest explicit `event_msg.task_complete.last_agent_message`.
- Local and embedded scanners check the record budget before slicing or parsing another complete record. Exhausting it before terminal or complete-coverage resolution revalidates the anchor and returns the explicit non-success coverage status `record_limit_exceeded`; no terminal payload or output file is published.
- Results, remote frames, and CLI metadata carry `records_examined`. The strict parent requires an integer from zero through 1,000,000, exact cap equality for `record_limit_exceeded`, and at least three scanned bytes per examined record based on the shortest complete object record `{}\n`; cross-window carry cannot evade this bound because it only joins non-overlapping bytes in the contiguous scanned span.
- Direct whole-file transfer still uses its independent 16 MiB cap; callers needing whole-rollout history use the identity-bound chunk plan and its 128 MiB automatic cumulative limit instead of treating `terminal-tail` as full reconstruction.

## Next Steps

- Keep the byte/record budget split, fixed-`S0` coordinates, anchor behavior, append tolerance, partial-record handling, later-user ordering, record-limit parity, and boundary success covered by focused probe regressions.
- Revisit the 128 MiB caps only from measured resource evidence; do not silently widen the direct whole-file Base64 path.

## Evidence

- Design decision thread: `019f8cf4-63a7-7632-b29c-a28719891d53`.
- Large-rollout acceptance target: BL task `019f17fc-5756-7fb2-8d9f-34c0330bd59b`, whose observed rollout size was 127,076,342 bytes.
- `personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py`
- `personal_codex/skills/remote-host-context/SKILL.md`
- `personal_codex/skills/remote-host-context/references/hosts.md`
- `tests/test_remote_codex_probe.py`
