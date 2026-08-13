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
- Require the caller to provide the repository and exact ref validated by every
  checkout in the reusable graph.
- Preserve both current required scopes: `test` and
  `Build private overlay release`.

## Current State

- `.github/workflows/required-ci.yml` exposes only `workflow_call` with the
  required string inputs `repository` and `ref`, keeps `contents: read`, and
  omits the existing CI workflow's cancellation group.
- The reusable entry contains the complete CI job graph plus the release
  workflow's PR-active `release` job, while excluding the write-capable
  `publish` job.
- All five `actions/checkout` steps bind `repository` and `ref` to those exact
  caller inputs while preserving their existing fetch-depth and credential
  settings.
- A focused contract test byte-binds both source scopes and rejects input,
  checkout, trigger, permission, secret, publication, or job-graph drift during
  the canary.

## Next Steps

- The central ruleset rollout owns invoking this reusable entry and retiring
  the canary only after live evidence is accepted.

## Evidence

- `python3 -B -m unittest discover -s tests -p 'test_required_ci_workflow.py' -v`
  passes all three focused contract tests under Python 3.13.0.
- `actionlint -shellcheck= .github/workflows/required-ci.yml` validates the
  reusable workflow structure and expressions.
