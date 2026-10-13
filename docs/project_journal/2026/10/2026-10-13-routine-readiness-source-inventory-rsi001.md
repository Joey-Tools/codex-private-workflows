---
id: 20261013-rsi001
title: Routine Readiness Source Inventory Repair
status: completed
created: 2026-10-13
updated: 2026-10-13
branch: wip/routine-readiness-source-inventory-20261013
pr:
supersedes: []
superseded_by:
---

# Routine Readiness Source Inventory Repair

## Summary

The current canonical review inventory now includes the readiness planner and
its regression test. Both are required current-only files; legacy and
independent inventories retain their prior scope.

## Current State

The current profile contains 155 required and 160 exact files, with 77
independent required files. The legacy profile remains at 145 required, 150
exact, and 78 independent required files. Existing raw exact-tree validation
and publication guards are unchanged.

## Evidence

- Canonical review source tree: `b1ddf393c6365d46c8e325609f51c78436494336`.
- Canonical PR #137 merged as `4f181e3dc6576bb86651bd59b67bf0085480ba3d`.
  The official generator projects that exact merged revision; generated skill,
  fixture, workflow, and provenance files are not edited by hand.
- Complete official generation passed in 229.535 seconds. Final six-source
  and generated-provenance verification passed in 30.729 seconds. Source lock
  `f160ad895baaddcc8985d962137c5ffd60a4fc01e73cfc907ab09f54701e5fd1`
  preserves the other five pins, public Toolbox base `c498cf5`, and its complete
  eleven-file personal-sync receipt. Archify retains its approved custom source.
- Focused inventory and sync regressions: 4 tests passed with
  `python3 -m unittest tests.test_private_overlay_sync.PrivateOverlaySyncTests.test_canonical_review_inventory_profiles_are_exact_and_disjoint tests.test_private_overlay_sync.PrivateOverlaySyncTests.test_readiness_plan_files_are_required_current_only tests.test_private_overlay_sync.PrivateOverlaySyncTests.test_readiness_plan_files_sync_byte_for_byte tests.test_private_overlay_sync.PrivateOverlaySyncTests.test_readiness_plan_inventory_rejects_missing_and_unknown_files`.
- Six complete consumer, source-lock, package, controller, manifest, and
  generated-provenance modules passed: 817 tests, two skips, 191.670 seconds
  on native CPython 3.14.7.
- Six complete generated review contract and planner modules passed in the
  actual private layout: 160 tests, no skips, 5.221 seconds on CPython 3.10.19
  using the existing local `tomli` dependency. The earlier missing-dependency
  import failure is not counted as a pass.
- Exact private-base `9f744ef3c1fe3aabddaf574435036ae3b4351eed` manifest
  validation, changed-Python Ruff, source-only syntax, and journal validation
  passed. The standard skill validator could not import PyYAML; fallback
  verification proves frontmatter byte-identical to the independently trusted
  installed 9f bundle, not a fresh YAML parse. Local Python 3.9 is unavailable;
  its declared compatibility job remains mandatory in the required remote CI.

These are complete focused modules, not a claim that the entire private native
suite or future remote review, CI, merge, or release has passed. Failed force-sync
run `38438348142` correctly rejected an unregistered planner file before opening
a sync PR; it is a deterministic compatibility failure, not infrastructure
recovery. No protection, credential, runtime permission, or installed-home state
is weakened or changed by this repair.
