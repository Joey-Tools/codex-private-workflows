---
id: 20260806-fci001
title: Make Private CI Faster and Cheaper
status: completed
created: 2026-08-06
updated: 2026-08-06
branch: codex/scheduled-private-overlay-sync
pr:
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
- The installed private review policy now matches the synchronized review
  contracts: terminal GitHub Codex payloads classify evidence but cannot by
  themselves complete triple review or make a pull request merge-ready.

## Next Steps

- None. Future runner changes should preserve the platform-specific evidence
  documented by the reviewed CI fixture rather than treating every macOS job
  as portable to Linux.

## Evidence

- Recent CI PR run `31074970581` completed in about 22.5 minutes; its same-tree
  squash push run `31076151831` repeated the complete graph.
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
