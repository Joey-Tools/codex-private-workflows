---
id: 20260716-dsf001
title: Daily Skill Friction Archived Corpus
status: completed
created: 2026-07-16
updated: 2026-07-17
branch: codex/daily-skill-friction-20260716-codex-private-workflows-daily-skill-friction-archive-corpus-v2
pr:
supersedes: []
superseded_by:
---

# Daily Skill Friction Archived Corpus

## Summary

- Aligned the canonical Daily Skill Friction automation prompt with the live active-plus-archived rollout corpus contract.

## Current State

- The automation scans both active and archived session roots, including dated and flat archive layouts.
- Cross-root duplicates and replayed history are removed by rollout lifecycle identity and normalized content fingerprint while genuine later human follow-ups remain in scope.
- Final reports include active, archived, and union candidate, parsed, and accepted counts plus helper-produced cross-root, collapsed-rollout, and replayed-prefix metrics.

## Next Steps

- Monitor the helper metrics in later Daily Skill Friction reports for any new active/archive corpus drift.

## Evidence

- `personal_codex/automations/daily-skill-friction/automation.toml`
- `tests/test_private_overlay_package.py`
- `README.md`
- The focused private automation prompt contract tests passed (3 tests).
- The complete private suite passed (`564` tests, `2` skipped) on Python `3.13.0`.
- Python compilation, Ruff `0.13.2`, project-journal validation, and `git diff --check` passed.
