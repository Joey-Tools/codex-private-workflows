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
- Bound each reader to the descriptor-derived source size. A scan cap that cuts through a record drops the incomplete buffer, while a stable true EOF still preserves a complete final JSON record without LF.
- Kept staged private-overlay integration compatible with the older two-argument retrospective reader: only an actual `io.BytesIO` may omit `source_size` and infer the full snapshot through `getbuffer()`; other readers fail closed. Production local and remote callers still pass descriptor-derived source sizes explicitly.

## Current State

- Local and embedded `rollout-summary` use the same LF-only record-boundary and oversized-drain semantics.
- A complete LF-terminated record that ends exactly at the scan cap remains available, but a parseable JSON prefix without LF is discarded when the source snapshot has unread bytes.
- Normal LF, CRLF, and stable true-EOF-without-LF inputs retain their prior summary behavior.
- Root integration coverage detects the reader signature during the public-overlay transition. The new API enforces fail-closed character-count caps and LF-only generated code, while the unsynchronized retrospective mirror retains its existing two-argument assertions until the canonical skill is synced.

## Next Steps

- None.

## Evidence

- `python3 -m unittest tests/test_remote_codex_probe.py -q`: 98 tests passed in 4.673 seconds.
- The local/embedded regressions cover oversized 64 KiB cross-chunk bare-CR drain, LF/CRLF/cap/EOF boundary behavior, safe `BytesIO` snapshot-size inference, and non-buffer omission rejection.
- Three focused root integration tests passed for bounded input, multibyte byte-count caps, and generated LF-only/source-size code across the staged old/new probe APIs.
- Full private suite with process-local synthetic Git signing disabled: 656 tests passed in 60.991 seconds.
- `ruff check --no-cache personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py tests/test_remote_codex_probe.py` passed with Ruff 0.13.2.
- `tests/test_session_retrospective.py` passed Ruff with its pre-existing `F541` baseline ignored; `HEAD` already contains that unrelated finding.
- `python3 -m py_compile personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py tests/test_remote_codex_probe.py tests/test_session_retrospective.py` passed with bytecode redirected to a task-scoped `/tmp` cache because the canonical worktree is outside the sandbox write root.
- Isolated `quick_validate.py` validation passed for `personal_codex/skills/remote-host-context` through `uv run --no-project --with PyYAML==6.0.3`.
- Project-journal validation and `git diff --check` passed for the affected files.
