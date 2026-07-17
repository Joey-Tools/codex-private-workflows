---
id: 20260716-rsc001
title: Review Workflow Overlay Sync Contracts
status: completed
created: 2026-07-16
updated: 2026-07-17
branch: codex/review-sync-contracts
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/86
supersedes: []
superseded_by:
---

# Review Workflow Overlay Sync Contracts

## Summary

- Synced the current canonical review helper through public merge `8acf51a` and installed its thin synthetic-token fixture skill in the same private release.
- Aligned private CI with the canonical helper's Python 3.10, Ubuntu, macOS, and aggregate-status contract.

## Current State

- `synthetic-token-fixtures` is a first-class sync rule and manifest link; its complete skill interface and placeholder-only templates are present before synced review tests run.
- The canonical review helper and the thin skill land atomically, so the installed skill never points at missing `synthetic-tokens` subcommands.
- Private CI validates the synced review helper on Ubuntu and macOS with Python 3.10 and keeps `test` as the aggregate required context.
- Linux CI still owns private packaging/sync tests, waited-delivery tests, and the opt-in real isolation integration.
- The repository README records the helper's minimum Python runtime.
- The synchronized aggregate-status contract accepts only an ordinary block
  mapping with one inline `working-directory` scalar under workflow/job
  `defaults.run`. Custom shells and unstructured YAML nodes fail closed.

## Next Steps

- Re-run the forced source sync and confirm that the private overlay is already at the canonical fixed point, or merge and publish any unrelated generated source updates.

## Evidence

- Scheduled sync run `29460675975` reproduced three path-contract failures before this repair.
- Public review-workflow PRs `#46` and `#48` supplied the canonical helper state synchronized here; both completed their required CI and review gates before merge.
- A temporary post-sync private tree passed all 671 canonical review tests with 9 expected skips.
- The final worktree passed 672 canonical review tests (9 skipped), 474 private overlay tests (2 skipped), and 40 waited-delivery tests.
- `actionlint`, Ruff, Python compilation, package build/verification, both skill validators, project-journal validation, and `git diff --check` passed.
- The `defaults.run` regression updates passed both focused contract files
  (`17` tests each), Ruff, Python compilation, Actionlint checks for four valid
  flow-mapping variants and six valid alias/anchor/tag/block-scalar variants,
  project-journal validation, and diff checks.
- `scripts/sync_private_overlay_sources.py`
- `personal_codex/private-sync-manifest.json`
- `.github/workflows/ci.yml`
- `tests/test_private_overlay_sync.py`
