---
id: 20260717-rhc006
title: Remote Rollout Input Race Guards
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-pin-rollout-input-ancestors
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/110
supersedes: []
superseded_by:
---

# Remote Rollout Input Race Guards

## Summary

- Pinned the resolved Codex root and every rollout ancestor with descriptor-relative, no-follow opens before reading the final regular file.
- Kept the rollout parent descriptor through each read so descriptor and current-entry identity checks cannot be redirected by mutable ancestor paths.
- Made `session-meta` read through raw descriptor calls without buffered prefetch, accept only complete LF-terminated JSONL records inside its exact byte cap, reject bare CR termination, and propagate post-open directory-enumeration failures through path-neutral errors while preserving optional directories that are absent before a descriptor is opened.
- Opened final rollout entries with `O_NONBLOCK` before the regular-file `fstat` check so a stat-to-open FIFO replacement cannot hang the probe.
- Rejected absolute rollout paths in both local and embedded descriptor traversal, and kept expected embedded safety rejections and post-preflight disappearance inside closed, structured frames.
- Bounded remote `fetch-rollout-chunk` stdout capture to the exact base64-frame budget derived from the 2 MiB chunk ceiling, and added producer-backed parent caps for remote `session-meta` and `rollout-summary`.
- Made an ancestor that disappears between descriptor-relative stat and open a hard identity-change error in both local and embedded traversal, while preserving a genuinely absent initial stat as optional evidence.
- Made local `session-meta` return limit truncation before validating the oversized serialized output of the next row, matching the embedded producer.

## Current State

- `rollout-stat`, `rollout-summary`, `chunked-rollout-summary`, `fetch-rollout`, `fetch-rollout-chunk`, and `session-meta` share the pinned input traversal in both local and embedded-remote implementations.
- Root disappearance, replacement, or a symlink loop after the initial existence check fails closed across resolution, pre-stat, and open; ancestor replacement between pre-stat and open also fails closed, while replacement after a parent is opened remains confined to the pinned descriptor tree.
- A session metadata cap that ends at a valid JSON `}` but excludes trailing bytes or the record newline is reported as truncated rather than parsed as a complete record.
- Local and embedded scans count actual raw descriptor reads against the cap; buffered file-object prefetch cannot consume hidden bytes beyond it.
- CRLF records remain valid because they end in LF; bare CR records fail as truncated coverage rather than being parsed as JSON whitespace.
- Missing Codex roots and optional session/archive directories remain empty evidence only when they are absent before their descriptor is opened. Post-open enumeration failures, permission, I/O, and rollout failures remain path-neutral and fail closed.
- An optional directory that disappears after its successful initial stat now fails closed instead of being misclassified as originally absent.
- Once `session-meta` has collected its requested row limit, the next valid metadata row proves truncation without allowing its output size to replace the limit result with a row-size error.
- Local and remote `session-meta` reject the same serialized row above 64 KiB, and the remote parent caps complete stdout at 32,899,072 bytes; `rollout-summary` enforces its serialized-output budget and caps complete stdout at 31,462,656 bytes. Capture breaches return before any partial frame is parsed.

## Next Steps

- Keep ancestor/FIFO-swap, LF record-boundary, directory-enumeration, and parent-capture budget regressions in the directly affected probe suite.

## Evidence

- Final raw-read, local/remote row-boundary, root-race, descriptor-cleanup, directory-enumeration, and parent/producer-cap regressions: 9 tests passed.
- Complete remote probe regression module: 66 tests passed.
- Direct cross-probe compatibility branches: 4 tests passed; the broader bounded-fetch compatibility group passed 8 tests.
- Complete repository regression suite: 621 tests passed, 1 skipped.
- Ruff check passed for the probe and its direct tests; the retrospective compatibility file passed with its pre-existing, out-of-diff `F541` excluded. Ruff format check passed for the two directly formatted files.
- The isolated OpenAI skill validator, project journal validator, and `git diff --check` passed.
- GitHub Codex review comments `3601680440` and `3601680451` are covered by 2 focused local-plus-embedded regressions; the directly related race, scan-cap, row-budget, and limit group passed 8 tests.
- The complete remote probe module passed 68 tests after the review fixes, including the updated embedded ancestor-swap fixture.
- Python byte compilation, Ruff lint, and `git diff --check` passed for the review fixes.
- `personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py`
- `personal_codex/skills/remote-host-context/SKILL.md`
- `tests/test_remote_codex_probe.py`
- `tests/test_session_retrospective.py`
