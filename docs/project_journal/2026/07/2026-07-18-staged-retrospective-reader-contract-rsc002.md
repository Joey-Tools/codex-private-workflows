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

- Made private root integration tests recognize the canonical retrospective reader's staged transition from a two-argument API to an explicit source-size API.
- Kept the legacy assertions only while the synchronized public skill still exposes the old signature; once the canonical skill lands, the same tests require LF-only parsing and fail-closed capped prefixes.

## Current State

- Source-size-aware probes receive the complete in-memory snapshot size in bounded-input and multibyte-cap tests.
- Generated-probe assertions require LF-only scanning and reject bare-CR terminators whenever the synchronized reader exposes `source_size`.
- The private repository can validate both sides of the atomic public-overlay sync without hand-editing the mirrored skill.

## Next Steps

- Force the canonical source sync after the public retrospective boundary fix merges.

## Evidence

- The three affected root integration tests passed in 0.005 seconds against the current two-argument synchronized probe.
- The complete private root retrospective module passed 400 tests in 53.116 seconds.
- The final private repository suite passed 1,233 tests in 244.675 seconds.
- Project-journal validation and `git diff --check` passed.
