---
id: 20261011-rri001
title: Review Routing Source Inventory Repair
status: completed
created: 2026-10-11
updated: 2026-10-11
branch: wip/review-routing-inventory-20261011
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/223
supersedes: []
superseded_by:
---

# Review Routing Source Inventory Repair

## Outcome

The current canonical review inventory includes both routed references,
`default-review-routing.md` and `github-codex-producer-and-recovery.md`.
They are required exact files in the current profile only. Historical migration
inventories remain unchanged, including their 145 required and 150 exact files.
The current inventory contains 153 required and 158 exact files; the independent
trusted subset remains 77 files.

The official generator projects merged canonical PRs #135 and #136 at
`98d0753a65e4459c2fdf2bd0ee44c1cdb98bce85`, including the private-layout
report-only test correction. Generated files and provenance are not edited by
hand. Missing required references and unknown documentation or runtime files
still fail before publication and preserve the old target.

The coupled public base now points to merged Toolbox PR #49 at
`c498cf5aa132e28e9020d60ec4a9ebb1b884aec1`, tree
`e790d82fd13b22683c2491b5af5b212808e6b288`. Its actual default-branch
immutable release `personal-codex-20261011-201715-c498cf5` passed 1922 tests
with 25 skips; direct tag identity, both API asset digests and stock safe
extraction were verified before changing the private pins. The official
generator projects its finite 1 GiB compressed-history ceiling and regressions.

## Boundary

Raw inventory, source locking, descriptor-bound transactions, object identity,
content stability and access-policy checks are retained. This change does not
change runtime permission, personal AGENTS, installed homes or historical
migration compatibility. The 256-release count, per-asset, checksum,
4 GiB expanded-scan, exact archive/Git tree and immutable-release guards remain
unchanged; no history is deleted or truncated. The five non-Toolbox pins retain
their already approved projection revisions. Archify remains on `joey-custom`.

The failed force-sync run was a deterministic inventory rejection. It is not
classified as infrastructure recovery or retried before the consumer repair
lands. The later 188-archive, 271793148-byte rejection exceeded the former
256 MiB compressed cap; it is repaired in the canonical Toolbox consumer rather
than by editing private generated copies or retrying unchanged CI.
Remote review, CI and release evidence belong to their own exact head;
canonical PR evidence does not replace private PR gates.

## Validation

- The full official generator completed against all six locked sources;
  strict checkout and generated provenance verification passed.
- All six related consumer, source-lock, package, controller and manifest
  modules passed: 850 tests, two skips, 201.585 seconds.
- All five generated review modules passed in the actual private layout:
  138 tests, no skips, 5.490 seconds.
- The eight declared native Python 3.9 compatibility regressions passed.
- Manifest validation used exact private base
  `b47a626b0f6e4432af466be97d52ef8b903fb451`; changed Python Ruff, skill metadata
  validation, source-only syntax and whitespace checks passed. Native Python
  3.9 compiled its three supported runtime files without writing bytecode.

These are focused complete-module checks, not a claim that the entire private
native suite or future remote CI has passed. The unchanged canonical personal
sync revision and complete eleven-file receipt remain coupled to the Toolbox
projection.
