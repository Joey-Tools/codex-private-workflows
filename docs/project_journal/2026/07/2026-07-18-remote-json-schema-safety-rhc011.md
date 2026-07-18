---
id: 20260718-rhc011
title: Remote JSON Schema Safety
status: completed
created: 2026-07-18
updated: 2026-07-18
branch: codex/daily-skill-friction-20260718-codex-private-workflows-remote-json-schema-safety
pr:
supersedes: []
superseded_by:
---

# Remote JSON Schema Safety

## Summary

- Made local and embedded `session-meta` parsing reject invalid UTF-8 with one stable path-neutral error instead of emitting replacement-decoded locator fields.
- Made timestamp, session metadata, ordinary summary, and chunk summary parsing handle non-object JSON, non-object payload schemas, oversized integer literals, and excessive nesting as malformed evidence without leaking parser exceptions.

## Current State

- Session metadata skips malformed JSON schemas, while invalid UTF-8 fails through the existing rollout-scoped error frame.
- Summary scans count malformed schemas in `json_error_count`, retain later valid evidence, and keep chunk raw-fetch guidance fail closed.

## Next Steps

- None.

## Evidence

- Base: `master` at `5de92d062dbeaed436a741d464bdbe3ed7aecd5c`.
- Branch: `codex/daily-skill-friction-20260718-codex-private-workflows-remote-json-schema-safety`.
- Five focused local and embedded regressions passed for Python 3.10 integer-limit API compatibility, invalid bytes in session id and cwd fields, top-level scalar/array/null JSON, non-object payloads, oversized integer literals, excessive nesting, later valid evidence, timestamps, and chunk metadata.
- The complete remote probe module passed 111/111 tests after the Python 3.10 compatibility follow-up, and the final full repository suite passed 1,258/1,258. Targeted Ruff, task-scoped `py_compile`, skill quick validation, journal validation, and `git diff --check` passed against the current worktree.
