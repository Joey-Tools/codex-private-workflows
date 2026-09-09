---
id: 20260909-cid001
title: Parallelize Private CI as an Explicit DAG
status: completed
created: 2026-09-09
updated: 2026-09-09
branch: codex/ci-dag-parallel
pr:
supersedes: []
superseded_by:
---

# Parallelize Private CI as an Explicit DAG

## Summary

- Replace the sequential private CI platform test path with an explicit GitHub Actions DAG.
- Run review and private-overlay test modules as independent matrix children on their applicable runners.
- Preserve full existing platform coverage and keep the original `test` check as the aggregate merge gate.

## Current State

- The previous CI run spent about 40 minutes in the macOS canonical review test suite.
- Review syntax, project-journal tests, private-overlay synchronization, Linux isolation, private-overlay modules, and contract validation are separate parallel leaves.
- The aggregate `test` job waits for every leaf with `if: always()` and fails unless every leaf reports success.
- Visible internal checks may increase; no new required checks are introduced.
- During validation, GitHub's macOS runner advanced to `26.6.2/25G83`; its exact `codesign` digest was added to the reviewed profile while retaining the previous `26.5.2/25F84` OS and digest branch plus fail-closed matching.
- Keep `26.5.2/25F84` as an additional hosted branch because the `macos-26` label may still assign the previously reviewed image during runner transition.
- Keep Linux isolation package installation only in `linux_isolation_tests`; other Linux matrix leaves do not need the integration tools and should not repeat apt operations.

## Next Steps

- Keep the explicit module matrices synchronized with the test inventory when modules are added or removed.
- Reassess runner granularity using subsequent CI timing data.

## Evidence

- PR #186 CI run: https://github.com/Joey-Tools/codex-private-workflows/actions/runs/34333210121
- PR #187 CI run after the matrix-inventory fix: https://github.com/Joey-Tools/codex-private-workflows/actions/runs/34345610384
- Source workflow: `.github/workflows/ci.yml`
