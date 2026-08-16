---
id: 20260816-csr001
title: Retire Legacy Session Retrospective Distribution
status: completed
created: 2026-08-16
updated: 2026-08-16
branch: codex/daily-skill-friction-20260816-codex-private-workflows-retire-legacy-retrospective-sync
pr:
supersedes: []
superseded_by:
---

# Retire Legacy Session Retrospective Distribution

## Summary

- Temporarily retire the private `codex-session-retrospective` distribution and
  its daily and weekly automations without introducing a replacement source.

## Current State

- The active manifest and sync rules no longer distribute the skill; a
  no-replacement tombstone and retired target preserve installed-link and stale
  generated-tree cleanup.
- The shared `codex-workflow-hygiene` source lock remains because that repository
  still supplies other active skills.

## Next Steps

- Introduce a standalone repository only in a separately scoped follow-up.

## Evidence

- Retirement precedent: private-workflows PR #170, commit `450fba1`.
- Six focused sync, stale-target, source-lock, routing, and package regressions
  passed.
- Manifest change validation, project-journal validation, changed-file Ruff and
  Python compilation, JSON parsing, and `git diff --check` passed.
