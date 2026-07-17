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
- Any U+FEFF BOM is rejected before structural parsing, including one placed
  after leading comments or blank lines, so decoder-dependent root keys cannot
  hide workflow defaults or job structure.
- `needs` block sequences support bare-dash ordinary job-ID scalars without
  accepting partial results. Inline sequences accept YAML's legal single
  trailing comma while empty or repeated-comma items still invalidate the
  complete dependency list. Exactly one structural `needs` key is required;
  duplicate scalar, list, or quoted spellings invalidate the declaration.
  Block-scalar payloads are excluded from physical mapping-key checks by
  treating an explicit indentation indicator as the minimum payload indent,
  while an implicit scalar still infers its indent from the first non-empty
  line. The aggregate job requires one structural `steps` block header while
  retaining ordinary quoted/spaced/commented spellings.
- Each direct dependency job must propagate its own failure: job-level
  `continue-on-error` accepts only an absent value or the canonical unquoted
  `false` boolean. Tolerant values, expressions, duplicates, aliases, tags,
  anchors, flow nodes, block scalars, and malformed values fail closed.
  Ordinary dependency jobs expose exactly one `steps` block whose steps each
  use exactly one `run` or `uses` key without tolerance. Reusable-workflow
  dependencies instead expose one job-level `uses` without `steps`, `runs-on`,
  or `continue-on-error`; mixed and duplicate job shapes are rejected.
- Literal `run` body collection follows the same explicit minimum instead of
  locking its boundary to a more-indented first command. A later payload line
  cannot hide a `GITHUB_ENV` update that installs `BASH_ENV` and redefines
  `test` for a subsequent dependency-check step; folded `run` scalars remain
  rejected. Multiline single- and double-quoted YAML scalars are outside the
  accepted structural subset, so quoted text cannot impersonate step-level
  `env` or `run` keys. Tags and anchors are stripped in either order and for
  any number of prefixes before checking the quote boundary. The aggregate
  `test` job rejects job-level `uses`; every accepted guard step has exactly
  one real `run` key, exactly one real `env` block, and no `uses` key.

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
- The sequence/structure regression update passed both contract files (`17`
  tests each), Ruff, Python compilation, and five Actionlint-valid needs,
  `steps`, alias, and block-scalar-payload fixtures; project-journal validation
  and diff checks also passed. The explicit-indentation payload regression also
  passed focused contract tests, Ruff, and an Actionlint-valid de-indent fixture.
- The explicit-indentation `run` regression passed both focused and complete
  contract files (`17` tests each), Ruff, Python compilation, Actionlint 1.7.12
  coverage for both chomping/indicator orders and quoted/sequence `run` keys,
  project-journal validation, normalized copy comparison, and diff checks.
- The exact-head dependency-propagation, multiline-quoted-scalar, and inline
  trailing-comma regressions passed both focused contract files (`19` tests
  each), Ruff, Python compilation, Actionlint 1.7.12 checks for the live
  workflows plus an Actionlint-valid combined decoy/trailing-comma fixture,
  project-journal validation, normalized copy comparison, and diff checks.
- The follow-up exact-head structural regressions passed both focused contract
  files (`19` tests each), Ruff, Python compilation, Actionlint 1.7.12 checks
  for both live workflows and Actionlint-valid tagged/anchored multiline-job
  plus tolerated-dependency fixtures, project-journal validation, normalized
  copy comparison, and diff checks.
- The U+FEFF follow-up passed both focused contract files (`19` tests each),
  Ruff, Python compilation, both live workflow checks, project-journal
  validation, normalized copy comparison, and diff checks. Actionlint 1.7.12
  rejected the post-comment BOM fixture as an unexpected key, while the
  contract now rejects the character independently of YAML decoder behavior.
- `scripts/sync_private_overlay_sources.py`
- `personal_codex/private-sync-manifest.json`
- `.github/workflows/ci.yml`
- `tests/test_private_overlay_sync.py`
