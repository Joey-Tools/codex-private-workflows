---
id: 20260717-rhc006
title: Remote Rollout Input Race Guards
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-pin-rollout-input-ancestors
pr:
supersedes: []
superseded_by:
---

# Remote Rollout Input Race Guards

## Summary

- Pinned the resolved Codex root and every rollout ancestor with descriptor-relative, no-follow opens before reading the final regular file.
- Kept the rollout parent descriptor through each read so descriptor and current-entry identity checks cannot be redirected by mutable ancestor paths.
- Made `session-meta` accept only complete LF-terminated JSONL records inside its byte cap, reject bare CR termination, and propagate directory-enumeration failures through path-neutral errors while preserving optional directories that disappear during enumeration.
- Opened final rollout entries with `O_NONBLOCK` before the regular-file `fstat` check so a stat-to-open FIFO replacement cannot hang the probe.
- Rejected absolute rollout paths in both local and embedded descriptor traversal, and kept expected embedded safety rejections and post-preflight disappearance inside closed, structured frames.
- Bounded remote `fetch-rollout-chunk` stdout capture to the exact base64-frame budget derived from the 2 MiB chunk ceiling.

## Current State

- `rollout-stat`, `rollout-summary`, `chunked-rollout-summary`, `fetch-rollout`, `fetch-rollout-chunk`, and `session-meta` share the pinned input traversal in both local and embedded-remote implementations.
- Root replacement between final-component inspection, resolution, pre-stat, and open fails closed; ancestor replacement between pre-stat and open also fails closed, while replacement after a parent is opened remains confined to the pinned descriptor tree.
- A session metadata cap that ends at a valid JSON `}` but excludes trailing bytes or the record newline is reported as truncated rather than parsed as a complete record.
- CRLF records remain valid because they end in LF; bare CR records fail as truncated coverage rather than being parsed as JSON whitespace.
- Missing Codex roots and optional session/archive directories remain empty evidence, while permission, I/O, and rollout failures remain path-neutral.

## Next Steps

- Keep ancestor/FIFO-swap, LF record-boundary, directory-enumeration, and parent-capture budget regressions in the directly affected probe suite.

## Evidence

- Focused FIFO and record-termination regressions: 6 tests passed.
- Final absolute-path, framed-safety-rejection, and post-preflight disappearance regressions: 3 tests passed.
- Complete remote probe regression module: 59 tests passed.
- Nine previously failing cross-probe compatibility regressions: 9 tests passed.
- Complete retrospective regression module: 397 tests passed, 1 skipped.
- Complete repository regression suite: 614 tests passed, 1 skipped.
- Ruff check passed for the probe and its direct tests; the retrospective compatibility file passed with its pre-existing, out-of-diff `F541` excluded. Ruff format check passed for the two directly formatted files.
- The isolated OpenAI skill validator, project journal validator, and `git diff --check` passed.
- `personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py`
- `personal_codex/skills/remote-host-context/SKILL.md`
- `tests/test_remote_codex_probe.py`
- `tests/test_session_retrospective.py`
