---
id: 20261017-sri001
title: Semantic Review Source Inventory Compatibility
status: completed
created: 2026-10-17
updated: 2026-10-17
branch: wip/semantic-source-inventory-20261017
pr:
supersedes: []
superseded_by:
---

# Semantic Review Source Inventory Compatibility

## Summary

The private consumer explicitly admits the two new references from canonical
[review PR #140](https://github.com/Joey-Tools/codex-review-workflows/pull/140):
`github-codex-machine-evidence-v1.md` and
`github-codex-semantic-scenarios.json`. The inventory, exact merged source pin,
and normal generated skill projection change together. Human semantic review
guidance remains separate from the strict machine-evidence reference.

## Boundaries

The current profile has 157 required and 162 exact files, with 77 independent
required files. Both additions are current-only. The legacy profile remains
145 required, 150 exact, and 78 independent required files. Missing required
files and unknown documentation/runtime files still fail before publication.
No raw-tree, content, identity, access-policy, provenance, or publication guard
is relaxed. Generated files and receipts are not edited by hand.

Source review is actual merged `7c11e820e2db55ed23488258032a724d927ec572`,
tree `ba3773bb347e62fb31b39bdc092d8787ee91bf2e`. The other five source pins,
Toolbox `c498cf5aa132e28e9020d60ec4a9ebb1b884aec1`, eleven-file receipt
`0935f2b57b585c8a3620a6995554ad9f15917c67a6a4c4f6ed795a273328994b`,
and approved Archify custom source remain fixed. Source lock SHA-256 is
`6c519b049a91df53c187e2cf401a974aa75135931b9025b5bc3ecfc22192e3a5`.
The sync manifest and generated Toolbox provenance receipt are unchanged.

## Validation

- The focused regression first reproduced the old exact-inventory rejection.
  Four focused tests then passed, including independent rejection of each new
  file against an otherwise valid legacy tree and no partial publication on
  missing or unknown inputs. Tests assert behaviour and inventory structure,
  not the wording of review prose.
- Official full six-source refresh passed in 44.063 seconds. Full generation
  passed in 209.447 seconds; log SHA-256
  `b22fdb2ea0eba4902b3f8d8c4ce245fbd8f8ecbc50fcdfb8f07cf4df26ae7b87`.
  Final official checkout/provenance verification passed in 30.067 seconds.
  Both added references match canonical bytes exactly. Generated recovery and
  external prepared-tree locators remain in the retained task generation log.
- Six complete private modules covering source sync, source lock, generated
  provenance, package, controller, and manifest baseline passed on CPython
  3.10.19: 798 tests, two skips, 189.918 seconds. Supervisor exit zero and
  incomplete null; log SHA-256
  `a42e96477c64fee1f5dbb549670085ee32dcf6e4e0aff509378075bb612e6820`.
- Six complete generated review contract/planner modules passed in the actual
  private layout on CPython 3.10.19: 162 tests, no skips, 6.810 seconds;
  log SHA-256
  `a2a3c8f15bde0e2d4e0aad3953751014c38121eaef7991efd1b8f39173ce7070`.
- All eight declared CPython 3.9 compatibility tests passed on native 3.9.6
  in 2.609 seconds, using the existing pure-Python tomli 2.2.1 dependency;
  log SHA-256
  `9effc5a00c0ce0c9bbb98462a6652c9b863634e360b29965dc7ea1084dc0c931`.
  The three declared runtime scripts also passed source-only syntax checking.
- Exact-base `c0c7d8edf8c34c24d9f72ca8449b9d0b4785bd29` manifest validation,
  changed-Python Ruff, source-only syntax, whitespace, and the standard skill
  validator passed. These are focused complete modules, not a claim of a full
  native suite, future remote CI/review, release, or installed-home acceptance.

The failed private force-sync `38977181659` correctly rejected an unregistered
canonical file. It is a compatibility failure, not an infrastructure retry.
This repair does not change credentials, protections, or installed homes.
