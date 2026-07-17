---
id: 20260717-rhc007
title: Remote Session Append-Proof Parity
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-remote-host-context-append-proof-parity-final
pr:
supersedes: []
superseded_by:
---

# Remote Session Append-Proof Parity

## Summary

- Aligned the private remote-host probe with public `codex-workflow-hygiene` PR #52 by opening the raw Codex root with no-follow descriptor checks and requiring same-`scandir` entry identity after scope filtering.
- Limited append relaxation to active `sessions/**` rollouts, captured prefix proofs lazily for only the first `limit + 1` eligible candidates, and kept the descriptor high-water checkpoint separate from the newline-aligned immutable parsing snapshot.
- Parsed active metadata from the immutable aligned snapshot and accepted later growth only when the captured prefix proof, descriptor identity, and append-only high-water conditions remained valid.
- Kept flat and date-nested archived rollouts on exact snapshot semantics with no append relaxation.
- Preserved the private LF/CRLF record-boundary behavior, output and remote-capture caps, and the 31-day file-descriptor bound.
- Made cross-probe compatibility coverage capability-aware so both the currently vendored probe and a real synchronization of public PR #52 retain strict assertions without synthetic `DirEntry` behavior.

## Current State

- Local and embedded `session-meta` use the same raw-root, descriptor-relative identity, lazy proof, high-water, and immutable-snapshot contracts.
- Safe append-only growth of active session rollouts is accepted, while truncation, rewrite, rollback, identity replacement, and archive mutation fail closed.
- Exhausting the proof budget remains ordinary truncation; callers can narrow the date or host scope or raise the existing limit.

## Next Steps

- No remaining implementation work is tracked for this completed parity slice; retain the focused race, proof-budget, archive, and cross-probe regressions.

## Evidence

- `python3 tests/test_remote_codex_probe.py`: 81 of 81 tests passed.
- Python 3.13.0 full suite, current private worktree: 639 of 639 tests passed in 65.083 seconds.
- Python 3.13.0 full suite, real sync-rule materialization: 639 of 639 tests passed in 63.329 seconds.
- Python 3.14.3 full suite, current private worktree: 639 of 639 tests passed in 66.063 seconds.
- Python 3.14.3 full suite, real sync-rule materialization: 639 of 639 tests passed in 64.464 seconds.
- Isolated `quick_validate.py` validation passed for both the current private skill and the real sync-rule materialization.
- Python 3.13 and Python 3.14 byte compilation passed for the probe and both affected test modules in both trees.
- `git diff --check` passed; the implementation validation worktree contained only the expected probe, skill, and two test changes before this journal was added.
- Public source: `Joey-Tools/codex-workflow-hygiene#52`, head `542a525f1c93149302d9ab351f1fa37eefa8df53`.
