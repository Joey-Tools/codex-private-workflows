---
id: 20260806-fci001
title: Make Private CI Faster and Cheaper
status: completed
created: 2026-08-06
updated: 2026-08-07
branch: codex/scheduled-private-overlay-sync
pr: 153
supersedes: []
superseded_by:
---

# Make Private CI Faster and Cheaper

## Summary

- Consume the reviewed pull-request-only CI fixture from
  `codex-review-workflows`, cancelling superseded heads and reserving macOS for
  tests with real Darwin, Xcode, codesign, Keychain, or Seatbelt semantics.
- Use `ubuntu-slim` for bounded status, Python compatibility, aggregate, and
  pull-request release-validation jobs.
- Keep the required `Build private overlay release` check on pull requests but
  avoid repeating test suites already owned by the required CI workflow.

## Current State

- Pull requests still require `test`, `Build private overlay release`, and
  `codex/review-gate`; the required check names are unchanged.
- Pull-request release validation still checks the sync manifest, builds and
  verifies the package, and rejects Python bytecode artifacts.
- Default-branch and manual release runs retain the full test and canonical
  review suites on `ubuntu-latest` before publication.
- Superseded pull-request CI and release runs cancel, while default-branch
  publication and scheduled sync remain non-cancelling.
- The private-owned workflow contract now follows the compact generated graph
  and no longer requires the retired standalone broker or platform-safety jobs.
- A fresh-context source review identified a P2 timeout-budget risk: the
  consolidated private macOS gates could consume the original 15-minute job
  budget before later failure-independent diagnostics were scheduled.
- The synchronized independent-supervisor job now has a 20-minute job budget.
  Its deterministic suite is capped at 10 minutes, while `setup_latest_python`,
  reconciliation, and broker reproduction are each capped at 2 minutes.
- The landed source-reconciliation baseline supplies the private review policy
  matching the synchronized review
  contracts: terminal GitHub Codex payloads classify evidence but cannot by
  themselves complete triple review or make a pull request merge-ready.
- That baseline also retains the synchronized legacy-receipt
  migration rule: an old artifact is never adopted retroactively, and the
  agent cannot perform or repeat the caller-owned manual trigger.

## Next Steps

- None. Future runner changes should preserve the platform-specific evidence
  documented by the reviewed CI fixture rather than treating every macOS job
  as portable to Linux.

## Evidence

- Recent CI PR run `31074970581` completed in about 22.5 minutes; its same-tree
  squash push run `31076151831` repeated the complete graph.
- In run `31074970581`, the independent supervisor took 5m48s, broker
  reproduction took 24s, and macOS reconciliation took 45s, for about 6m57s
  combined.
- The 20-minute job budget leaves about 13m03s of empirical headroom against
  that observed combined runtime. The 10/2/2/2-minute step caps retain
  practical margin for later failure-independent diagnostics; this is not a
  formal timing guarantee.
- Across the most recent 30 repository runs, macOS jobs consumed about 252 raw
  minutes; all of that macOS execution came from CI.
- The target repository rulesets require squash merges and the three status
  contexts named above, with no bypass actor.
- Forced sync run `31105834769` exposed the stale private-owned
  `platform-safety` assertion before opening a PR; the updated contract and
  release-specific checks pass together on the locally generated final tree.
- A fresh Codex review exposed a second stale private-owned policy assertion;
  both the review-policy contract selector and the private installation-policy
  selector pass after aligning `personal_codex/AGENTS.md` with the locked
  `codex-review-workflows` policy.
- Source PR `Joey-Tools/codex-review-workflows#96` landed as
  `9a90db95cebe2d66c669e2991a8ede62f66563aa` with tree
  `2fd8907b9dfb25fa1551a9e8bd023a6ca1d2649b`; private source-reconciliation
  PR #154 then landed the synchronized baseline as
  `a916e2491b70a8fcd9614df30ddd3baaa0d5cc58`.
- Baseline release run `31135055434` completed successfully for exact private
  master `a916e2491b70a8fcd9614df30ddd3baaa0d5cc58` before the CI-only merge.
- The final ordinary-merge tree passes all 2,012 repository tests and all 257
  focused private-overlay contracts. A 2,876-test review-runtime sweep exposed
  only sandbox-denied socket binds; all 16 affected test methods pass when
  rerun outside Seatbelt.
