---
id: 20260717-rhc003
title: Remote Probe Output and Overlay Sync Safety
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260716-codex-private-workflows-remote-probe-output-safety
pr:
supersedes: []
superseded_by:
---

# Remote Probe Output and Overlay Sync Safety

## Summary

- Closed the local fetch-output parent-directory race by traversing or creating every parent with descriptor-relative `O_DIRECTORY` and `O_NOFOLLOW` operations.
- Kept temporary-file creation, cleanup, and the final rename relative to one pinned parent descriptor while preserving `0600` output permissions.
- Stopped collapsing active, flat archived, and date-nested archived rollout paths because they share a basename or parsed session id; local and embedded `session-meta` now preserve every distinct relative path and deduplicate exact paths only.
- Materialized the merged public session-corpus record bound and retrospective remote-probe fixes into the private overlay, then aligned the private cross-probe regression with the exact-path lifecycle contract.

## Current State

- A parent replaced by a symlink after output-path resolution is rejected before any fetched bytes are written.
- A parent pathname replaced after the temporary file is written cannot redirect the final rename; the output remains in the originally opened directory.
- Active, flat archived, and date-nested archived rollouts with the same filename and session id but different cwd values and follow-up content are all returned by local and embedded probes.
- The private overlay now reflects `codex-workflow-hygiene` merges `eb93b9586eb0f97bfcbab5c5ac7587b5bd1e212f` and `e5a4d56856dfb023ea7b9e5bb56e34c4fcd4a3d6`; its transformed retrospective helper and retained tests agree on distinct lifecycle locators.

## Next Steps

- Keep descriptor-relative output writes limited to platforms that expose `O_DIRECTORY` and `O_NOFOLLOW`; fail closed if either primitive is unavailable.

## Evidence

- Fixed-range review reported the `/tmp` output-parent TOCTOU plus basename- and session-id-based lifecycle-copy deduplication defects after PR #96.
- Focused remote probe suite: 28 tests passed.
- Full repository suite after canonical source sync: 582 tests passed with 2 skipped.
- Private retrospective lifecycle regression: 1 test passed after replacing the stale basename-winner expectation.
- Python byte compilation, Ruff lint, changed-test formatting, skill quick validation, project journal validation, and `git diff --check` passed.
- `personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py`
- `personal_codex/skills/remote-host-context/SKILL.md`
- `tests/test_remote_codex_probe.py`
