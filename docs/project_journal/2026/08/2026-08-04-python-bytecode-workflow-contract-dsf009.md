---
id: 20260804-dsf009
title: Python Bytecode Workflow Contract
status: completed
created: 2026-08-04
updated: 2026-08-04
branch: codex/fix-python-bytecode-workflow-contract
pr:
supersedes: []
superseded_by:
---

# Python Bytecode Workflow Contract

## Summary

- Disable Python bytecode generation in every private workflow that imports the synced review runtime.

## Current State

- The synced review runtime rejects ordinary package imports unless Python bytecode generation is disabled before interpreter startup.
- The public private-CI fixture already declares `PYTHONDONTWRITEBYTECODE=1`, while the private repository's CI, release, and scheduled-sync workflows did not.
- All three Python workflows now declare the environment contract globally, including the scheduled sync path that validates a newly generated overlay before opening its PR.
- Regression coverage requires the declaration to appear exactly once in each workflow preamble.
- A locally synchronized overlay reproduces the failure without the environment contract and passes all 1,333 private tests with the contract enabled.

## Next Steps

- None.

## Evidence

- https://github.com/Joey-Tools/codex-private-workflows/actions/runs/30902462487
- `RuntimeError: review_runtime requires bytecode to be disabled before import`
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `.github/workflows/scheduled-sync-release.yml`
- `tests/test_private_overlay_sync.py`
