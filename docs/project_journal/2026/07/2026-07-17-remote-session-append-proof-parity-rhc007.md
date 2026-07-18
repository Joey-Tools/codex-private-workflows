---
id: 20260717-rhc007
title: Remote Session Append-Proof Parity
status: completed
created: 2026-07-17
updated: 2026-07-18
branch: codex/daily-skill-friction-20260717-codex-private-workflows-remote-host-context-append-proof-parity-final
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/113
supersedes: []
superseded_by:
---

# Remote Session Append-Proof Parity

## Summary

- Aligned the private remote-host probe with public `codex-workflow-hygiene` PR #53 by opening the raw Codex root with no-follow descriptor checks and binding each in-scope candidate through its pinned parent.
- Replaced the non-portable `DirEntry.inode()` and parent-device assumptions exposed by the `ubuntu-slim` OverlayFS runner with name-only `scandir` discovery, scope-filtered descriptor-relative no-follow raw metadata snapshots, and consumption-time descriptor opens and proofs.
- Limited append relaxation to active `sessions/**` rollouts, captured prefix proofs lazily for only the first `limit + 1` consumed candidates, and kept the descriptor high-water checkpoint separate from the newline-aligned immutable parsing snapshot.
- Moved the initial active prefix proof onto the held consumption descriptor and kept the exact inventory identity through that proof plus post-proof descriptor and fresh no-follow path rechecks. Pre-anchor append and grow-and-rewrite now fail closed as retryable coverage gaps; after the baseline exists, later growth is accepted only when the proved prefix remains unchanged, while rewrite, truncation, replacement, and rollback fail closed.
- Added one bounded refreshed-snapshot parse when an active rollout without metadata grows after the initial parse. A second aligned advance without metadata now returns the existing explicit scan-truncated coverage error instead of silently skipping a potentially arriving first metadata record.
- Closed the checkpoint late window where the returned high-water identity could advance beyond its aligned verified snapshot; no-metadata scans now expose that state as the same explicit coverage gap.
- Carried the original inventory identity into the initial active checkpoint, preventing truncate, same-size rewrite, append-grow, or rewrite-grow between enumeration, open, first proof, and the post-proof exact rechecks from becoming a new proof baseline.
- Parsed active metadata from the immutable aligned snapshot and accepted later growth only when the captured prefix proof, descriptor identity, and append-only high-water conditions remained valid.
- Kept flat and date-nested archived rollouts on exact snapshot semantics with no append relaxation.
- Preserved the private LF/CRLF record-boundary behavior, output and remote-capture caps, and the 31-day file-descriptor bound.
- Made cross-probe compatibility coverage capability-aware so both the currently vendored probe and a real synchronization of public PR #53 retain strict assertions without synthetic `DirEntry` behavior.

## Current State

- Local and embedded `session-meta` use the same raw-root, descriptor-relative identity, lazy proof, high-water, and immutable-snapshot contracts without assuming cached dirent inode or parent/child device equality.
- Enumeration captures a raw metadata identity for each scoped name without opening rollout files. Consumption rejects replacement, disappearance, and all pre-anchor growth against that exact inventory snapshot; archives remain exact and symlink/non-regular classification stays deferred without opening a FIFO.
- Regression coverage proves that unconsumed names perform no rollout-open or proof I/O, consumption keeps a one-descriptor peak, and local and embedded pre-consumption, first-proof-read, and post-proof-recheck append-grow and rewrite-grow fail closed while a stable retry succeeds.
- The first held-descriptor proof is the active content baseline. Safe later growth is accepted, while truncation, rewrite, rollback, or identity replacement after that baseline and every archive mutation during its exact consumption window fail closed.
- Local and embedded scanners parse at most one refreshed aligned snapshot; repeated growth or a high-water identity that outpaces that snapshot remains a visible coverage gap rather than an unbounded retry loop.
- Exhausting the proof budget remains ordinary truncation; callers can narrow the date or host scope or raise the existing limit.

## Next Steps

- No remaining implementation work is tracked for this completed parity slice; retain the focused race, proof-budget, archive, and cross-probe regressions.

## Evidence

- Initial PR CI run `29600214365`, job `87950238935`, reproduced the portability defect on `ubuntu-slim` with 44 failures and 18 errors, all rooted in `rollout identity changed during enumeration`.
- `python3 -m unittest tests/test_remote_codex_probe.py -q`: 92 of 92 tests passed in 3.836 seconds after the pre-anchor exact-identity follow-up, including local and embedded pre-consumption, checkpoint-entry, first-proof-read, and post-proof-recheck append-grow and rewrite-grow rejection with stable retry success; post-anchor append acceptance remains covered.
- Review-fix focused run, `python3 -m unittest tests.test_remote_codex_probe.RemoteCodexProbeChunkTests -q`: 83 of 83 tests passed, including a first metadata record appended after the initial parse, a repeated-growth coverage gap, and a late-checkpoint high-water advance.
- Python 3.13.0 full suite, final private worktree: 650 of 650 tests passed in 68.201 seconds (`real 68.96s`).
- Python 3.14.3 full suite, final private worktree: 650 of 650 tests passed in 69.744 seconds (`real 70.17s`).
- Isolated `quick_validate.py` validation passed for the updated private skill.
- Ruff 0.13.2 passed for both changed Python files.
- Python 3.13 and Python 3.14 byte compilation, project-journal validation, and `git diff --check` passed for the affected files.
- Read-only review reported one P2 documentation finding: the full-suite counts and public PR #54 state were stale. This evidence update resolves that finding without changing the implementation.
- Public sources: `Joey-Tools/codex-workflow-hygiene#53`, merged as `1dad693d1e51e82270f2f4d472a4f6d0dfcb749c`, and follow-up PR #54, head `987416549636cfbfb1d2dbed2fd020bb951cb6b1`, merged as `d78f50fdc694b28e2397d59b1883a4072347fcd6`.
