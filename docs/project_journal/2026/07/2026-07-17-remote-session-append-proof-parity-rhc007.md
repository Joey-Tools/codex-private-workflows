---
id: 20260717-rhc007
title: Remote Session Append-Proof Parity
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-remote-host-context-append-proof-parity-final
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/113
supersedes: []
superseded_by:
---

# Remote Session Append-Proof Parity

## Summary

- Aligned the private remote-host probe with public `codex-workflow-hygiene` PR #52 by opening the raw Codex root with no-follow descriptor checks and binding each in-scope candidate through its pinned parent.
- Replaced the non-portable `DirEntry.inode()` and parent-device assumptions exposed by the `ubuntu-slim` OverlayFS runner with name-only `scandir` discovery plus a no-follow descriptor open, authoritative `fstat`, and two fresh descriptor-relative no-follow stats.
- Limited append relaxation to active `sessions/**` rollouts, captured prefix proofs lazily for only the first `limit + 1` eligible candidates, and kept the descriptor high-water checkpoint separate from the newline-aligned immutable parsing snapshot.
- Parsed active metadata from the immutable aligned snapshot and accepted later growth only when the captured prefix proof, descriptor identity, and append-only high-water conditions remained valid.
- Kept flat and date-nested archived rollouts on exact snapshot semantics with no append relaxation.
- Preserved the private LF/CRLF record-boundary behavior, output and remote-capture caps, and the 31-day file-descriptor bound.
- Made cross-probe compatibility coverage capability-aware so both the currently vendored probe and a real synchronization of public PR #52 retain strict assertions without synthetic `DirEntry` behavior.

## Current State

- Local and embedded `session-meta` use the same raw-root, descriptor-relative identity, lazy proof, high-water, and immutable-snapshot contracts without assuming cached dirent inode or parent/child device equality.
- Enumeration closes each transient candidate descriptor immediately; regression coverage proves a one-descriptor peak, precise symlink rejection, and detection of replacement before or after the first fresh path stat.
- Safe append-only growth of active session rollouts is accepted, while truncation, rewrite, rollback, identity replacement, and archive mutation fail closed.
- Exhausting the proof budget remains ordinary truncation; callers can narrow the date or host scope or raise the existing limit.

## Next Steps

- No remaining implementation work is tracked for this completed parity slice; retain the focused race, proof-budget, archive, and cross-probe regressions.

## Evidence

- Initial PR CI run `29600214365`, job `87950238935`, reproduced the portability defect on `ubuntu-slim` with 44 failures and 18 errors, all rooted in `rollout identity changed during enumeration`.
- `python3 tests/test_remote_codex_probe.py`: 84 of 84 tests passed.
- Python 3.13.0 full suite, current private worktree: 642 of 642 tests passed in 66.239 seconds.
- Python 3.14.3 full suite, current private worktree: 642 of 642 tests passed in 66.833 seconds.
- Isolated `quick_validate.py` validation passed for the updated private skill.
- Ruff passed for both changed Python files; a broader three-file invocation found only the pre-existing out-of-diff `F541` in `tests/test_session_retrospective.py:5942`.
- Python 3.13 and Python 3.14 byte compilation and `git diff --check` passed for the affected files.
- Public source: `Joey-Tools/codex-workflow-hygiene#52`, head `542a525f1c93149302d9ab351f1fa37eefa8df53`.
