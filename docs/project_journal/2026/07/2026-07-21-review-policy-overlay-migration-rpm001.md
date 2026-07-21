---
id: 20260721-rpm001
title: Review Policy Overlay Migration
status: completed
created: 2026-07-21
updated: 2026-07-21
branch: wip/review-policy-private-migration
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/128
supersedes: []
superseded_by:
---

# Review Policy Overlay Migration

## Summary
- Sync the canonical review-policy migration into the private overlay and align the private global guidance with the new named-lane contract.

## Current State
- Named single and internal review use one dedicated fresh-context Codex `reviewer` with `fork_turns="none"` in a clean, read-only Git worktree over a frozen range.
- Named double adds actual Claude Code in a separate read-only workspace; legacy supplied-diff helpers do not count as named lanes.
- PR readiness adds CI, conversation-resolution, base/head, and merge-policy checks without hidden extra Codex gates.
- Private overlay staging treats `references/canonical-claude-lane.md` as required and rejects an incomplete prepared source before mutating the live target.

## Next Steps
- None after the migration PR is squash-merged and the default-branch Private Overlay Release completes.

## Evidence
- Canonical policy: `Joey-Tools/codex-review-workflows@4288868360239863aa35ff4c30e5ad7e4cae39df`.
- Canonical PRs: https://github.com/Joey-Tools/codex-review-workflows/pull/68 and https://github.com/Joey-Tools/codex-review-workflows/pull/69.
- Bootstrap failure: https://github.com/Joey-Tools/codex-private-workflows/actions/runs/29798109360.
- Local validation covers the 1,268-test repository suite, 1,056-test review-policy suite, 168 private-sync tests, 40 waited-delivery tests, focused private-policy contract checks, journal validation, compile checks, and fixed-base sync-manifest validation. Sandbox-only GPG and loopback failures were rerun successfully with only the required test-process capability exposed.
