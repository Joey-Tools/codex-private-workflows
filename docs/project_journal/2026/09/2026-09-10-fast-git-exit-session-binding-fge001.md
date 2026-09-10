---
id: 20260910-fge001
title: Classify Fast Git Exit During Session Binding
status: completed
created: 2026-09-10
updated: 2026-09-10
branch: codex/readonly-process-race
pr:
supersedes: []
superseded_by:
---

# Classify Fast Git Exit During Session Binding

## Decision

- Treat `ProcessLookupError` during the initial `gitraw.run_bounded` session bind as a completed fast-exit path only after `terminal_status` proves that the original child is an unreaped terminal child.
- Fence and reap the fresh session before draining stdout and stderr with the existing byte limits, then return the child's real exit code.
- Keep live identity lookup failures fatal; do not weaken PID, PGID, session, or start-identity validation.
- Screen related CI process launchers and retain their existing fail-closed or authenticated cleanup behavior; no parallel changes are required.

## Rationale

- CI attempt 1 of run 34464882227 failed in two tests with Darwin `ProcessLookupError: [Errno 3] No such process` between `Popen(start_new_session=True)` and the first process identity read; the same head passed on attempt 2.
- The failure is a startup observation race for short-lived Git commands. The protected property remains process ownership and session identity while the process is live, and terminal-state proof makes the unanchored cleanup PID-specific.

## Validation

- Added deterministic regression coverage for terminal fast exit and for a live lookup failure remaining fatal.
- Covered `tests.test_runtime_process` and `tests.test_git_checkout` locally.
