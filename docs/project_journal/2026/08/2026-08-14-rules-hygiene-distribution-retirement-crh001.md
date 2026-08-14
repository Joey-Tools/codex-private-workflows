---
id: 20260814-crh001
title: Retire Codex Rules Hygiene Distribution
status: completed
created: 2026-08-14
updated: 2026-08-14
branch: codex/retire-rules-hygiene-distribution
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/170
supersedes: []
superseded_by:
---

# Retire Codex Rules Hygiene Distribution

## Summary

- Stop installing and implicitly routing `codex-rules-hygiene` from the
  private overlay.
- Publish a no-replacement removal tombstone so reconciliation removes the
  private-owned `~/.codex/skills/codex-rules-hygiene` link safely.
- Keep the `codex-workflow-hygiene` source lock because the repository still
  supplies other active skills.

## Current State

- The private manifest no longer installs `codex-rules-hygiene`; its
  no-replacement tombstone tells reconciliation to remove the private-owned
  installed link.
- Scheduled source sync treats the generated private skill target as retired,
  and the generated tree is absent from the overlay source.
- Personal AGENTS routing and the Daily Skill Friction ownership map no longer
  advertise rules hygiene.
- The `codex-workflow-hygiene` source lock remains active for the other skills
  still supplied by that repository.

## Next Steps

- None within this tracked change. Canonical source archival is a separate
  downstream workstream after the private release is published.

## Evidence

- Delivery PR: https://github.com/Joey-Tools/codex-private-workflows/pull/170
- Retirement precedent: private-workflows PR #147, commit `1f716fd`.
- Canonical donor-only rules work: codex-workflow-hygiene PR #63, head
  `5b769d3742d2b48377060373146638a3558d7d5d`.
- The three directly affected test modules passed 390 tests.
- The complete repository suite passed 2,065 tests with three expected skips.
- Append-only sync-manifest validation, project-journal validation, and
  `git diff --check` passed.
