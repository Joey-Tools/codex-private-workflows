---
id: 20260720-dsf005
title: Review Cleanup Identity Race
status: completed
created: 2026-07-20
updated: 2026-07-20
branch: codex/daily-skill-friction-20260720-review-cleanup-identity
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/125
supersedes: []
superseded_by:
---

# Review Cleanup Identity Race

## Summary

- Prevented concurrent cleanup waiters from treating legitimate state-directory child-entry changes as directory replacement.

## Current State

- State-directory identity uses stable device, inode, mode, and owner metadata.
- Content-derived link counts and timestamps no longer invalidate an otherwise unchanged directory.
- Descriptor-to-path replacement detection and directory ownership and mode checks remain enforced.

## Next Steps

- None.

## Evidence

- https://github.com/Joey-Tools/codex-private-workflows/pull/125
- `python3.14 -m unittest discover -s personal_codex/skills/review-orchestration-playbook/tests -p 'test_*.py'` passed 1,061 tests with 5 skips.
- `python3 -m unittest tests/test_private_overlay_package.py` passed 45 tests.
- The concurrent cleanup regression passed 20 consecutive iterations.
