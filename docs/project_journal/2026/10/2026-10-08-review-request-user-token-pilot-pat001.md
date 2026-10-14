---
id: 20261008-pat001
title: Review Request User Token Pilot
status: active
created: 2026-10-08
updated: 2026-10-08
branch: codex/review-request-token-pilot
pr:
supersedes: []
superseded_by:
---

# Review Request User Token Pilot

## Summary
- The controller passes the optional `CODEX_REVIEW_GATE_REQUEST_TOKEN` secret for request-only use. The default `github_token` remains responsible for verifier, evidence, and check scope; an invalid override fails without falling back.
- This is a focused pilot, not a wider consumer or organization rollout. The optional request-only User token addresses an observed intermittent Bot-request failure, not a universal Bot failure: the source release PR #112 obtained clean Bot-request evidence.

## Current State
- The target-branch state adds `review_request_token: ${{ secrets.CODEX_REVIEW_GATE_REQUEST_TOKEN }}` only to the controller. The verifier continues to use `github.token` and receives no User token.
- Event subscriptions, permissions, the `ubuntu-slim` default, variables, and concurrency are unchanged. The configured fine-grained PAT organization secret exists in `Joey-Tools` and `Joey-Project`; this pilot applies only to `Joey-Tools/codex-private-workflows`. Secret values were not inspected.
- Source feature PR #111 and release-intent PR #112 are merged. Source version v2.2.0 was published successfully in [release workflow run 37831837099](https://github.com/Joey-Tools/codex-review-gate/actions/runs/37831837099). The action's `master`, `v2.2.0`, and `v2` targets point to commit `e1a097027698316ef4e35c0e2acea05de6c61fbf`, tree `4c202c4d60bfdab1d8e623b5459dec9fc3b4a6a9`; the full-version [release](https://github.com/JoeyTeng/codex-review-gate-action/releases/tag/v2.2.0) is immutable.

## Next Steps
- Use a separate temporary canary PR after installation to verify an automatic User-authored `@codex review` request and its exact-head clean/pass result; close it without merging. Do not roll out to other consumers or organizations yet.

## Evidence
- Source repository: `Joey-Tools/codex-review-gate`; source PRs #111 (feature) and #112 (release intent).
- Published source v2.2.0 workflow: https://github.com/Joey-Tools/codex-review-gate/actions/runs/37831837099
- Immutable action release: https://github.com/JoeyTeng/codex-review-gate-action/releases/tag/v2.2.0
- Consumer baseline: `0e6278b0b360726b101d78f9b75997dc4f743978`. The controller delta is exactly one added line; the verifier is unchanged.
- Focused verifier/controller and completion-routing unit tests (2), actionlint, journal validation, and `git diff --check` passed. Live User-request and clean/pass evidence remains to be collected.
