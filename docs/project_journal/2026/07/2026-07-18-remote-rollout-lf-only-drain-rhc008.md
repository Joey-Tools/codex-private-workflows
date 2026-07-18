---
id: 20260718-rhc008
title: Remote Rollout LF-Only Record Drain
status: completed
created: 2026-07-18
updated: 2026-07-18
branch: codex/daily-skill-friction-20260718-codex-private-workflows-remote-bare-cr-drain
pr:
supersedes: []
superseded_by:
---

# Remote Rollout LF-Only Record Drain

## Summary

- Made the local and embedded `rollout-summary` bounded readers treat only LF as a physical JSONL record terminator; CRLF remains valid because it still ends in LF.
- Kept an oversized record draining through bare CR and JSON-like suffix bytes until the next LF, preventing the suffix from becoming separate parseable evidence.
- Bound each reader to the descriptor-derived source size and require a validated byte-zero starting offset before reading. A scan cap that cuts through a record drops the incomplete buffer, while a stable true EOF still preserves a complete final JSON record without LF.
- Kept staged private-overlay integration compatible with the older two-argument retrospective reader: only a byte-zero `io.BytesIO` may omit `source_size` and infer the full snapshot through `getbuffer()`; other readers fail closed. Production local and remote callers still pass descriptor-derived source sizes explicitly.
- Kept the generated private summary metadata aligned with the synchronized root integration contract by reporting `json_error_count`, including bare-CR records that reach true EOF as malformed JSONL input.

## Current State

- Local and embedded `rollout-summary` use the same LF-only record-boundary and oversized-drain semantics.
- A complete LF-terminated record that ends exactly at the scan cap remains available, but a parseable JSON prefix without LF is discarded when the source snapshot has unread bytes.
- Explicit and inferred source-size paths both require the reader to start at byte zero. Missing, invalid, or valid nonzero offsets fail closed before any record bytes are read.
- Normal LF, CRLF, and stable true-EOF-without-LF inputs retain their prior summary behavior.
- Root integration coverage now exercises both the synchronized public retrospective reader and the private remote-host reader for fail-closed byte caps, LF-only generated code, and malformed-record counts.

## Next Steps

- None.

## Evidence

- `tests.test_remote_codex_probe`: 101/101 tests passed in 3.679 seconds.
- The local/embedded regressions cover oversized 64 KiB cross-chunk bare-CR drain, LF/CRLF/cap/EOF boundary behavior, safe byte-zero `BytesIO` snapshot-size inference, explicit and inferred rejection at a valid nonzero LF boundary and mid-record suffix, and unavailable or invalid offset rejection.
- The retrospective integration module passed 409/409 tests in 46.617 seconds, including generated LF-only/source-size and `json_error_count` coverage across both synchronized probes.
- Full private suite with process-local Git signing disabled: 1248/1248 tests passed in 164.733 seconds under a 600-second hard timeout and externally bounded log sink.
- `ruff check --no-cache personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py tests/test_remote_codex_probe.py` passed with Ruff 0.13.2.
- `tests/test_session_retrospective.py` passed Ruff with its pre-existing `F541` baseline ignored; `HEAD` already contains that unrelated finding.
- `python3 -m py_compile personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py tests/test_remote_codex_probe.py tests/test_session_retrospective.py` passed with bytecode redirected to a task-scoped `/tmp` cache because the canonical worktree is outside the sandbox write root.
- Isolated `quick_validate.py` validation passed for `personal_codex/skills/remote-host-context` through `uv run --no-project --with PyYAML==6.0.3`.
- Project-journal validation and `git diff --check` passed for the affected files; Ruff introduced no findings over the unchanged baseline `F541`.
