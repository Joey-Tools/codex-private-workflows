---
id: 20261011-rri001
title: Review Routing Source Inventory Repair
status: completed
created: 2026-10-11
updated: 2026-10-11
branch: wip/review-routing-inventory-20261011
pr:
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

## Boundary

Raw inventory, source locking, descriptor-bound transactions, object identity,
content stability and access-policy checks are retained. This change does not
change runtime permission, personal AGENTS, public base, installed homes or
historical migration compatibility. Archify remains on `joey-custom`.

The failed force-sync run was a deterministic inventory rejection. It is not
classified as infrastructure recovery or retried before the consumer repair
lands. Remote review, CI and release evidence belong to their own exact head;
canonical PR evidence does not replace private PR gates.

## Validation

- The full official generator completed against all six locked sources;
  strict checkout and generated provenance verification passed.
- All three consumer/source-lock modules passed: 444 tests, one skip.
- All five generated review modules passed in the actual private layout:
  138 tests, no skips.
- The eight declared native Python 3.9 compatibility regressions passed.
- Manifest validation used exact private base
  `b47a626b0f6e4432af466be97d52ef8b903fb451`; changed Python Ruff, skill metadata
  validation, source-only syntax and whitespace checks passed. Native Python
  3.9 compiled its three supported runtime files without writing bytecode.

These are focused complete-module checks, not a claim that the entire private
native suite or future remote CI has passed. The unrelated five source pins and
the public Toolbox base remain unchanged.
