---
id: 20260717-rhc005
title: Session Meta Root Error Handling
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-fail-closed-session-meta-root
pr:
supersedes: []
superseded_by:
---

# Session Meta Root Error Handling

## Summary

- Kept a missing Codex root equivalent to an empty session corpus.
- Stopped treating permission and other I/O failures while resolving an existing Codex root as empty results.
- Routed root I/O failures through the existing path-neutral `SessionMetaRolloutError` and CLI error channel.

## Current State

- A missing root returns an empty, non-truncated scan.
- Permission and other root I/O failures report `session directory unreadable` without exposing the configured root path.
- Session directory and rollout handling after root resolution remains unchanged.

## Next Steps

- Monitor remote-host probes for other availability failures that could still be mistaken for empty evidence.

## Evidence

- Focused remote-host probe suite: 37 tests passed, including missing-root, permission-error, generic I/O-error, and CLI redaction coverage.
- Full repository suite: 592 tests passed (`skipped=2`).
- Python byte compilation, Ruff lint, remote-host-context skill quick validation, project journal validation, and `git diff --check` passed.
- The touched test file passes Ruff format validation; the helper retains its existing whole-file Ruff format baseline, and the formatter diff does not touch this change.
- `personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py`
- `tests/test_remote_codex_probe.py`
