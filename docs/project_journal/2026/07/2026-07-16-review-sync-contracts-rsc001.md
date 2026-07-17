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

- Keep the synchronized review helper and its private CI contract aligned with
  the canonical `codex-review-workflows` source.
- Replace the handwritten YAML parser with reviewed canonical/private workflow
  snapshots that travel with the synchronized skill.

## Current State

- `synthetic-token-fixtures` remains a first-class sync rule and manifest link;
  the canonical review helper and thin fixture skill land atomically.
- Private CI retains Python 3.10 Ubuntu/macOS coverage, Linux packaging and sync
  tests, Python 3.9 compatibility, platform-safety coverage, waited-delivery
  tests, and the aggregate required `test` context.
- The synchronized skill contains `tests/fixtures/ci/canonical.yml` and
  `tests/fixtures/ci/private.yml`. The private source-sync required-file list
  includes both, so an incomplete canonical review target is rejected.
- The contract chooses `private.yml` from the exact
  `personal_codex/skills/review-orchestration-playbook` layout relative to the
  repository root, then requires `.github/workflows/ci.yml` to match it byte
  for byte. Unknown layouts fail; repository names are not consulted.
- Human-readable assertions document that private aggregate CI directly needs
  `platform_tests`, `python-39-compatibility`, and `platform-safety` and checks
  each result for `success`. No generic YAML parser semantics are claimed.

## Evidence

- Both focused canonical and private contract files passed (`16` tests each)
  after the snapshot redesign.
- The complete canonical review suite passed (`707` tests; `9` skipped), and
  the synchronized private review suite passed (`707` tests; `10` skipped).
- Both synchronized fixture copies byte-match their canonical skill sources;
  each profile fixture byte-matches its corresponding live CI workflow.
- Ruff, Python compilation, Actionlint 1.7.12 for both live workflows, the two
  focused required-file regressions and complete private source-sync suite
  (`133` tests), project-journal validation, normalized test comparison,
  fixture comparisons, and diff checks passed.
- `scripts/sync_private_overlay_sources.py`
- `personal_codex/skills/review-orchestration-playbook/tests/fixtures/ci/`
- `.github/workflows/ci.yml`

## Next Steps

- Publish the private overlay after the canonical snapshot contract merges and
  the forced source sync reaches the same reviewed fixture state.
