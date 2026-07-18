---
id: 20260718-rsc002
title: Staged Retrospective Reader Contract
status: completed
created: 2026-07-18
updated: 2026-07-18
branch: codex/daily-skill-friction-20260718-codex-private-workflows-retrospective-reader-contract
pr:
supersedes: []
superseded_by:
---

# Staged Retrospective Reader Contract

## Summary

- Replaced the staged source-code substring checks with observable reader behavior contracts for every synchronized probe that exposes the explicit `source_size` API.
- Kept the current two-argument private overlay compatible while making the source-size branch execute both the direct reader and the actual generated rollout-summary script.

## Current State

- The shared matrix treats bare CR as data, rejects a valid JSON prefix when the scan cap stops before its LF and the actual source is longer, and preserves a complete JSON record at true EOF without LF.
- Generated-script cases run through `subprocess`, require `source_bytes == len(payload)` and `scan_bytes == scan_cap`, require `scan_truncated` to be `false`, `true`, and `false`, and require JSON-error counts of `1`, `0`, and `0` for the three cases respectively.
- The current private mirror still contains two-argument readers, so its source-size cases remain staged behind signature detection; the current private full suite does not claim to have executed that dormant branch.
- The same target test methods were also exercised against the canonical source-size-aware retrospective probe to verify that both the direct reader and generated subprocess paths execute the new behavior matrix.

## Next Steps

- Force the canonical source sync after the public retrospective boundary fix merges, then confirm both synchronized private probes activate the source-size behavior branch.

## Evidence

- Five focused current-mirror integration tests passed in 0.007 seconds; the two source-size behavior methods remained dormant because both synchronized readers still have the legacy signature.
- An isolated harness replaced both probe slots with the canonical source-size-aware module and passed the two target behavior methods in 0.632 seconds, including real generated-script subprocess execution.
- The complete private retrospective module passed 402 tests in 47.850 seconds with fixture commit signing disabled through process-local Git configuration.
- `python3 -m unittest discover -s tests -q` passed all 655 clean-master root tests in 64.848 seconds with fixture commit signing disabled through process-local Git configuration; a bounded log sink retained 31,612 bytes without imposing a file-size limit on the tests.
- `python3 -m py_compile` passed for the modified test module.
- `ruff check --no-cache --ignore F541 tests/test_session_retrospective.py` passed; unfiltered targeted Ruff reports the pre-existing F541 at `tests/test_session_retrospective.py:6121`, outside this diff.
- Range-scoped Ruff formatting, project-journal validation, and `git diff --check` passed.
