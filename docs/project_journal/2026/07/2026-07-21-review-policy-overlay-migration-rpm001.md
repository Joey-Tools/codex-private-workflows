---
id: 20260721-rpm001
title: Review Policy Overlay Migration
status: completed
created: 2026-07-21
updated: 2026-08-11
branch: wip/review-policy-private-migration
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/128
supersedes: []
superseded_by:
---

# Review Policy Overlay Migration

## Summary
- Sync the canonical review-policy migration and its private-overlay portability follow-up into the private overlay.
- Align private global guidance with the hardened named-lane, trusted-bundle, and guarded-validation contracts.
- Record Joey's private cross-repository Codex-only gate for repositories that deliver Codex skills and personal guidance.

## Current State
- Named single and internal review use one dedicated fresh-context Codex `reviewer` with `fork_turns="none"` in a clean, read-only Git worktree over a frozen range.
- Named double adds actual Claude Code in a separate read-only workspace; legacy supplied-diff helpers do not count as named lanes.
- Self-policy review materializes the candidate Markdown only as review subject and runs controls from an independently trusted bundle pinned outside the candidate range.
- Direct Claude validation is mediated by `named_lane_guard`, which pins the trusted control bundle, validates the materialized workspace and runtime contract, and seals accepted evidence.
- PR readiness adds CI, conversation-resolution, base/head, and merge-policy checks without hidden extra Codex gates.
- Private overlay staging now requires the complete named-lane guard, runtime, schema, result, and test set and rejects an incomplete prepared source before mutating the live target.
- PR-bound delivery in the listed Joey-Tools skill repositories defaults to the non-named `skill-repo-codex-gate`: exactly one fresh-context local Codex reviewer followed by one current-head GitHub `@codex review` processor. Explicit named single, double, or triple requests retain their canonical meanings; Claude remains outside the default gate unless Joey explicitly opts in.
- The custom gate starts GitHub Codex after its sole local lane is terminal, retains the playbook's one-request and evidence-authority rules, and has no local-only fallback. Provider findings block, while unavailable or inconclusive GitHub evidence leaves the gate incomplete and the PR not ready.

## Next Steps
- Advance the frozen `codex-toolbox` public-base pin and reconcile later public AGENTS additions in a separate reviewed workstream before claiming the collaboration guardrails from toolbox PR #22 are active in the private overlay.
- Update the skill-repository list and its contract test whenever a canonical skill repository is added, removed, renamed, transferred, or changes responsibility.

## Evidence
- Canonical policy migration: `Joey-Tools/codex-review-workflows@bea5e7ad1312be1c15a0af7785eda74a8fb5282d` via https://github.com/Joey-Tools/codex-review-workflows/pull/72.
- Canonical private-overlay portability follow-up: `Joey-Tools/codex-review-workflows@35271bec152f1ccaf484ffa738948d17107f42f9` via https://github.com/Joey-Tools/codex-review-workflows/pull/79.
- Pre-migration sync evidence: https://github.com/Joey-Tools/codex-private-workflows/actions/runs/29923546683; ordinary overlay validation passed and the canonical-policy gate correctly exposed the missing private migration and portability gaps.
- Historical PR #128 migration evidence covered the then-current 1,270-test private repository suite, the 2,395-test generated canonical review-policy suite, focused private-sync and policy-contract tests, Ruff, compile and launcher checks, journal validation, and fixed-base sync-manifest validation. Sandbox-only GPG and loopback failures were rerun successfully with only the required test-process capability exposed.
- Repository-family gate validation on 2026-08-11: all 259 tests in `tests.test_private_overlay_sync` passed, the two focused AGENTS policy tests passed independently, `tests/test_private_overlay_sync.py` compiled, and project-journal validation passed.
- The complete 2,023-test repository suite reached only four temporary-merge signing errors and one Unix-socket failure under the restricted sandbox, with three skips; those exact five environment-sensitive tests then passed together with the required GPG-agent and Unix-socket capabilities.
- Repository-family gate: `personal_codex/AGENTS.md`, `tests/test_private_overlay_sync.py`, and Codex task `019feaf3-58de-7ee0-8093-0d6277bf9a22`.
