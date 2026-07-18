---
id: 20260718-rsc002
title: Staged Retrospective Reader Contract
status: completed
created: 2026-07-18
updated: 2026-07-18
branch: codex/sync-removed-link-metadata
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/97
supersedes: []
superseded_by:
---

# Staged Retrospective Reader Contract

## Summary

- Replaced the staged source-code substring checks with observable reader behavior contracts for every synchronized probe that exposes the explicit `source_size` API.
- Kept the current two-argument private overlay compatible while making the source-size branch execute both the direct reader and the actual generated rollout-summary script.

## Current State

- The shared matrix treats bare CR as data, rejects a valid JSON prefix when the scan cap stops before its LF and the actual source is longer, and preserves a complete JSON record at true EOF without LF.
- Generated-script cases run through `subprocess`, require `source_bytes == len(payload)` and `scan_bytes == scan_cap`, and require `scan_truncated` to be `false`, `true`, and `false` for the three cases respectively.
- The current private mirror still contains two-argument readers, so its source-size cases remain staged behind signature detection; the current private full suite does not claim to have executed that dormant branch.
- The same target test methods were also exercised against the canonical source-size-aware retrospective probe to verify that both the direct reader and generated subprocess paths execute the new behavior matrix.

## Next Steps

- Force the canonical source sync after the public retrospective boundary fix merges, then confirm both synchronized private probes activate the source-size behavior branch.

## Evidence

- Five focused current-mirror integration tests passed in 0.008 seconds; the two source-size behavior methods remained dormant because both synchronized readers still have the legacy signature.
- An isolated harness replaced both probe slots with the canonical source-size-aware module and passed the two target behavior methods in 0.466 seconds, including real generated-script subprocess execution.
- The complete private retrospective module passed 402 tests in 57.247 seconds with fixture commit signing disabled through process-local Git configuration.
- `timeout 420 env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false python3 -m unittest discover -s tests` passed all 1,235 private repository tests in 180.313 seconds under narrow permission for the packaging fixture's worktree-local `.codex-tmp` directory.
- `python3 -m py_compile` passed for the modified test module.
- `ruff check --no-cache --ignore F541` passed; unfiltered Ruff reports the pre-existing F541 at `tests/test_session_retrospective.py:6105`, outside this diff.
- Range-scoped Ruff formatting, project-journal validation, and `git diff --check` passed.
