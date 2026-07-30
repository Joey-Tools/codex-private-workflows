---
id: 20260730-rrp001
title: Review Runtime Private Release
status: active
created: 2026-07-30
updated: 2026-07-30
branch: codex/private-overlay-bytecode-env
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/140
supersedes: []
superseded_by:
---

# Review Runtime Private Release

## Summary

- Synchronize the canonical review runtime from `Joey-Tools/codex-review-workflows@0f77fb7b1dd59f5eed522fa9699497aa013695fc`.
- Preserve the private-overlay policy and synthetic-token transformations while adding the canonical runtime, supervisor, broker, fixtures, and tests.
- Pin private release jobs to Python 3.10.20 and install `tomli` for the runtime's Python 3.10 TOML dependency.

## Current State

- The private overlay contains the canonical review-control implementation required by the range-local materializer follow-up.
- Release and scheduled-sync workflows use a deterministic Python 3.10.20 environment instead of a floating minor or major selector.
- Private sync rejects a canonical review source that omits the keychain-broker installer or its regression module.
- Workflow contract tests prevent the Python pin and dependency setup from drifting.

## Next Steps

- Verify PR CI and review evidence.
- Merge PR #140 and verify the resulting immutable Private Overlay Release.
- Install and validate that trusted release before resuming the range-local materializer branch.

## Evidence

- Complete private repository suite: `1,332` tests passed on Python 3.10.20.
- Independent review supervisor deterministic suite: `604` tests passed on Python 3.13.
- Python 3.9 compatibility suite: `8` tests passed.
- Python 3.14 platform-safety suite: `303` tests passed.
- Broker developer-byte reproducibility digest: `fcdf6d473ec5c6fa76488da0b115d147fe5e5fa576ed33710ecd3fd7186e0b46`.
- `bash -n`, YAML parsing, source-only tree validation, and `git diff --check` passed locally.
