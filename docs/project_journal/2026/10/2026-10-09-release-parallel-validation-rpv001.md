---
id: 20261009-rpv001
title: Parallel Release Validation
status: completed
created: 2026-10-09
updated: 2026-10-09
branch: codex/release-parallel-validation
pr:
supersedes: []
superseded_by:
---

# Parallel Release Validation

## Summary
- Default-branch release validation runs the full private and canonical review suites in eight complete-module groups alongside complete Release-history validation and controller checks.
- The existing required build check aggregates every prerequisite before package construction and publication.

## Decision and Rationale
- Release run 37951584197 spent 8m05s on private tests, 3m46s on history validation, and 13m43s on review tests in series; package construction and verification took approximately four seconds.
- Preserve all tests, Python 3.13, published-history verification, package verification, and source-only guards. Parallelism removes serial waiting without relying on Python 3.10 PR evidence for the merged release commit.
- Grouping dynamically discovers all modules. Historical timing weights affect assignment only; discovery failures and empty groups fail closed.

## Current State
- Four groups per suite run independently. Module and class fixtures remain together, including multiprocessing-spawn imports.
- PRs retain release-specific manifest/controller/package validation and explicitly expect full test groups to be skipped. Push/manual publication requires every test group to succeed.
- A 5–7 minute release is an estimate from the previous Ubuntu module timings, not a measured result of this change.

## Validation
- Python 3.13.0 ran all 2,516 private and 3,264 canonical review tests across the eight groups, with 11 platform/environment skips. One macOS broker fixture required an isolated rerun outside the outer sandbox so its nested `sandbox-exec` could start; that rerun passed.
- Actionlint 1.7.12, `bash -n`, and ShellCheck passed for the release workflow; regression tests exercised prerequisite failure/cancellation/skip states, discovery errors, newly discovered modules, and module fixtures.
- Local package build and verification passed with clean source enforcement. GitHub runner timing remains unmeasured until the workflow runs.

## Evidence
- [Release run 37951584197](https://github.com/Joey-Tools/codex-private-workflows/actions/runs/37951584197)
- [PR #219 CI run 37949290538](https://github.com/Joey-Tools/codex-private-workflows/actions/runs/37949290538)
- `.github/workflows/release.yml`
- `scripts/run_release_test_group.py`
- `tests/test_unittest_shard.py`
- `tests/test_private_overlay_sync.py`
