---
id: 20261013-rsi001
title: Routine Readiness Source Inventory Repair
status: completed
created: 2026-10-13
updated: 2026-10-13
branch: wip/routine-readiness-source-inventory-20261013
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/224
supersedes: []
superseded_by:
---

# Routine Readiness Source Inventory Repair

## Summary

The current canonical review inventory includes the readiness planner and
its regression test as required current-only files. Normal source generation
also projects the canonical fixes for mandatory global queue revalidation and
recursive rejection of duplicate JSON keys. Legacy and independent inventories
retain their prior scope.

## Current State

The current profile contains 155 required and 160 exact files, with 77
independent required files. The legacy profile remains at 145 required, 150
exact, and 78 independent required files. Existing raw exact-tree validation
and publication guards are unchanged. Queue retention requires complete global
gate coverage or a proved non-bypassed completion hold. Both JSON CLI input
routes reject duplicate keys rather than accepting the last value.

## Initial Inventory Validation

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

## Corrective Source Projection

- The two applicable private PR #224 findings were corrected in canonical
  PR #138, merged as `99c86dbfdf19c5e4ef8e12799f8c4aac6c1b2781` with tree
  `c696ecaa90b42715d193cbadf5c97a585b940157`. Its own review and CI passed;
  those results do not approve the private PR.
- Complete six-source refresh passed in 44.391 seconds, official generation
  in 220.172 seconds, and final checkout verification in 31.591 seconds.
  Source lock `d5511306c630f1ac8722c4b98b6076c8b79207e8924cdacbb9a8916b0f88a8a3`
  changes only the review pin; other five pins, Toolbox base, eleven-file
  receipt, and approved Archify custom source remain fixed. All generated
  recovery and prepared-tree locators remain in the task generation log.
- Generated planner and test match actual canonical bytes exactly, with
  SHA-256 `041dccce196b278ebc8d5244543b0a550b09764c6767023063ef5479c25284e1`
  and `6c97e806307c5ec203ec0cbb1a43948054880a8b04f2456a2a090a076b5efc60`.
  The reference matches the established user-to-Joey personalization, not raw
  canonical bytes. Fixture, workflow, consumer implementation and complete
  skill bytes are unchanged from initial private head `e3bdba7`.
- Six complete consumer/source-lock/package/controller/manifest/provenance
  modules passed: 817 tests, two skips, 185.398 seconds on CPython 3.10.19.
  Supervisor zero/no incomplete output; log SHA-256
  `4b667e0f524aaacb563d53cec92a0e2db9abf177f5ad35c4696e980cab01a4cb`.
- Six complete generated review-contract/planner modules passed in the actual
  private layout: 162 tests, no skips, 5.864 seconds on CPython 3.10.19 with
  existing local `tomli`; log SHA-256
  `35bfe983f3e6927e07da64da98d9f51db16e63f6e181f5455b8ba73d6ae01530`.
- Exact-base9f manifest, changed-Python Ruff and source-only syntax passed.
  Offline skill validation lacked cached PyYAML; its direct Python fallback
  also failed to import it. Unchanged complete skill bytes and independent-9f
  frontmatter equality are fallback evidence, not fresh YAML parsing. The
  declared Python 3.9 remote job remains required; no new private whole-suite,
  remote review, CI, merge, release or installed-home result is asserted here.
