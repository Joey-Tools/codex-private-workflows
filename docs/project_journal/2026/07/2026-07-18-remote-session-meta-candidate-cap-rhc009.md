---
id: 20260718-rhc009
title: Remote Session Meta Candidate Cap
status: completed
created: 2026-07-18
updated: 2026-07-18
branch: codex/daily-skill-friction-20260718-codex-private-workflows-remote-bare-cr-drain
pr:
supersedes: []
superseded_by:
---

# Remote Session Meta Candidate Cap

## Summary

- Decoupled the requested `session-meta --limit` from active rollout prefix-proof work.
- Fixed the active-candidate safety cap at 501 and kept no-metadata candidates from consuming result-row allowance.
- Added a distinct candidate-cap truncation marker and classifiable local scan metadata.
- Split parent diagnostics so only true row overflow recommends raising `--limit`.

## Current State

- Local and embedded scans share the same fixed candidate cap and marker payload.
- Candidate-cap coverage gaps tell callers to narrow date or host scope.
- True row overflow retains the existing `session_meta_limit_truncated` behavior.

## Next Steps

- None.

## Evidence

- `python3 -m py_compile` passed for the helper and shared retrospective tests.
- Ten focused public/private, local/embedded, compatibility, marker-classification, and row-overflow tests passed.
- The remote-host module passed 101/101 tests in 3.679s.
- The retrospective integration module passed 409/409 tests in 46.617s.
- The private repository root suite passed 1248/1248 tests in 164.733s.
- Isolated skill validation, journal validation, `py_compile`, and `git diff --check` passed; Ruff introduced no findings over the unchanged baseline `F541`.
