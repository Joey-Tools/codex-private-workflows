---
id: 20260721-rpm001
title: Review Policy Overlay Migration
status: completed
created: 2026-07-21
updated: 2026-07-22
branch: wip/review-policy-private-migration
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/128
supersedes: []
superseded_by:
---

# Review Policy Overlay Migration

## Summary
- Sync the canonical review-policy migration and its private-overlay portability follow-up into the private overlay.
- Align private global guidance with the hardened named-lane, trusted-bundle, and guarded-validation contracts.

## Current State
- Named single and internal review use one dedicated fresh-context Codex `reviewer` with `fork_turns="none"` in a clean, read-only Git worktree over a frozen range.
- Named double adds actual Claude Code in a separate read-only workspace; legacy supplied-diff helpers do not count as named lanes.
- Self-policy review materializes the candidate Markdown only as review subject and runs controls from an independently trusted bundle pinned outside the candidate range.
- Direct Claude validation is mediated by `named_lane_guard`, which pins the trusted control bundle, validates the materialized workspace and runtime contract, and seals accepted evidence.
- PR readiness adds CI, conversation-resolution, base/head, and merge-policy checks without hidden extra Codex gates.
- Private overlay staging now requires the complete named-lane guard, runtime, schema, result, and test set and rejects an incomplete prepared source before mutating the live target.

## Next Steps
- None after the migration PR is squash-merged and the default-branch Private Overlay Release completes.

## Evidence
- Canonical policy migration: `Joey-Tools/codex-review-workflows@bea5e7ad1312be1c15a0af7785eda74a8fb5282d` via https://github.com/Joey-Tools/codex-review-workflows/pull/72.
- Canonical private-overlay portability follow-up: `Joey-Tools/codex-review-workflows@35271bec152f1ccaf484ffa738948d17107f42f9` via https://github.com/Joey-Tools/codex-review-workflows/pull/79.
- Pre-migration sync evidence: https://github.com/Joey-Tools/codex-private-workflows/actions/runs/29923546683; ordinary overlay validation passed and the canonical-policy gate correctly exposed the missing private migration and portability gaps.
- Local validation covers the 1,270-test private repository suite, the 2,395-test generated canonical review-policy suite, focused private-sync and policy-contract tests, Ruff, compile and launcher checks, journal validation, and fixed-base sync-manifest validation. Sandbox-only GPG and loopback failures were rerun successfully with only the required test-process capability exposed.
