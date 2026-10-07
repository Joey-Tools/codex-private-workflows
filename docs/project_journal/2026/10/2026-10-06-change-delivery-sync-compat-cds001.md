---
id: 20261006-cds001
title: Change-Delivery Source Sync Compatibility
status: completed
created: 2026-10-06
updated: 2026-10-06
branch: codex/private-sync-description-compat-20261006
pr:
supersedes: []
superseded_by:
---

# Change-Delivery Source Sync Compatibility

## Scope and Decisions

- Repair the private source transformer before continuing model-policy promotion. Keep global AGENTS/model routing, source-lock advancement, generated skill copies, Actions/status checks, release, and installers out of this slice.
- Canonical review-workflows PR #127 changed the public description from `Run a local ...` to `Run the implementation-to-commit gate for ...`. The private default-branch transformer recognizes only the historical prefix and rejects the locked prepared source.
- Accept the two explicit public prefix families and their private equivalents. Individual alternatives are optional, but exactly one recognized frontmatter description must be transformed; unknown or duplicated descriptions and body/cross-file bait must still fail closed.
- Reuse only the description-related compatibility changes from draft private PR #211. Start on current private master `2fe20f0aefa876c25951e39fbcd712d04f3b55e2` instead of absorbing the broader policy changes or rewriting its branch.
- Test both ordinary and locked-source paths; the latter is the production failure boundary. Preserve source pinning, flat frontmatter validation, target preservation on failure, and repeated-sync behavior.

## Checklist

- [x] Bind the failure to its exact run and confirm the current private baseline.
- [x] Add a regression reproducing the locked prepared-source rejection.
- [x] Implement the explicit alternative-prefix transform and cardinality check.
- [x] Run focused and broader relevant Python 3.13 tests plus journal/diff validation.
- [x] Isolate the compatibility repair from broader model-policy promotion, source-sync dispatch, and installation.

## Validation and Next Steps

- Python 3.13.0: all 10 selected change-delivery tests passed; the complete private overlay sync module passed 348 tests; generated source-lock and sync-manifest modules passed 112 tests. The two broader runs contain 460 tests in total, without counting the repeated focused subset twice.
- Python syntax compilation, `git diff --check`, and project-journal validation passed. No bytecode artifacts were introduced under `scripts/` or `tests/`.
- This entry describes the focused repair's target-branch state after squash merge. PR review, CI, and transient delivery state belong in the repair PR rather than this journal. The broader model-policy migration remains separate.
- Draft PR #211 overlaps this compatibility slice. Reconcile that overlap with the landed repair when continuing the broader policy work; keep its global model/AGENTS migration separate.
- Full live source-checkout admission, successful scheduled sync, release publication, and installation remain unverified. No new default-branch success is inferred from unit or fixture replay.

## Evidence

- Canonical source: https://github.com/Joey-Tools/codex-review-workflows/pull/127, merged master `ed7fab2dafd813dc2df6e6dc10a61ab64d1319af`.
- Failed promotion: https://github.com/Joey-Tools/codex-private-workflows/actions/runs/37376539543, job `111986862961`, step `Sync private overlay sources`.
- Exact diagnostic: `change-delivery SKILL.md frontmatter description is not the recognized private legacy/current form at prepared public source`.
- Related broader policy work: https://github.com/Joey-Tools/codex-private-workflows/pull/211.
- The new locked-source positive regression reproduced the exact prepared-public-source rejection before the implementation change and passed after it.
- The complete committed public `SKILL.md` from `ed7fab2dafd813dc2df6e6dc10a61ab64d1319af` (7,173 bytes, SHA-256 `24d7d959d4f02cc2cb0778b2d0c01829cb4af94a8be04d89419a6c066b9aad5a`) passed locked fixture replay with exact transformed bytes, stable repeat content, and unchanged public source. Checkout-admission receipts in that smoke test are test fixtures, not a live full-CLI admission proof.
- Plain locked skill-tree sync can replace file objects on repeat. Check content stability, not inode stability; AGENTS no-op inode behavior is a different contract and is not changed here.
- No retry of the deterministic failed Actions run, release dispatch, or installation is part of this repair.
