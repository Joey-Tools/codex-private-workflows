---
id: 20260813-rci001
title: Add the Required CI v2 Entry
status: completed
created: 2026-08-13
updated: 2026-08-13
branch: codex/daily-skill-friction-20260813-codex-private-workflows-codex-review-v2
pr:
supersedes: []
superseded_by:
---

# Add the Required CI v2 Entry

## Summary

- Add a caller-only, read-only reusable workflow for the central Required CI
  rollout without changing the existing CI or release workflows.
- Keep the reusable interface closed to caller-provided `repository` and `ref`
  inputs, and bind every checkout to the validated caller event context.
- Preserve both current required scopes: `test` and
  `Build private overlay release`.

## Current State

- `.github/workflows/required-ci.yml` exposes only input-free `workflow_call`,
  keeps `contents: read`, and omits the existing CI workflow's cancellation
  group.
- The reusable entry contains the complete CI job graph plus the release
  workflow's PR-active `release` job, while excluding the write-capable
  `publish` job.
- Each of the five `actions/checkout` steps is preceded by a fail-closed guard
  that requires exact `github.repository == 'Joey-Tools/codex-private-workflows'`.
  Every checkout uses the literal `Joey-Tools/codex-private-workflows`
  repository, binds `ref` to the caller event's `github.sha`, disables
  credential persistence, and preserves its existing fetch depth.
- The supported caller contract is limited to event contexts where
  `github.repository` is that exact target repository and `github.sha` is the
  commit under validation. Callers cannot supply `repository` or `ref` inputs
  to reinterpret an incompatible event context.
- A focused contract test byte-binds both source scopes and rejects input,
  checkout, trigger, permission, secret, publication, or job-graph drift during
  the canary.

## Next Steps

- The central ruleset rollout must invoke this reusable entry without
  `repository` or `ref` inputs and only from a caller event satisfying the
  exact `github.repository` and `github.sha` contract above. Retire the canary
  only after that live event path is accepted.

## Evidence

- `python3 -B -m unittest discover -s tests -p 'test_required_ci_workflow.py' -v`
  passes all three focused contract tests under Python 3.13.0.
- `actionlint -shellcheck= .github/workflows/required-ci.yml` validates the
  reusable workflow structure and expressions.
