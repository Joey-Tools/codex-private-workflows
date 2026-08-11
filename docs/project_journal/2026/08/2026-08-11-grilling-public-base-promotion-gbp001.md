---
id: 20260811-gbp001
title: Promote Public Base for Grilling
status: completed
created: 2026-08-11
updated: 2026-08-11
branch: codex/toolbox-8b9cc67-private-sync
pr:
supersedes: []
superseded_by:
---

# Promote Public Base for Grilling

## Summary

- Advance the private overlay's immutable public base to the toolbox release
  that contains the explicit-only `grilling` skill.
- Keep `grilling` public-owned; do not duplicate it in the private manifest.
- Preserve all four non-toolbox source pins and the receipt-bound generated
  toolbox provenance.

## Current State

- The private base release points to toolbox commit
  `8b9cc676601e7e4de408d1e8fe3090b510fcb22d` and tree
  `5b583827448dabaa7be2fb76e9a75557193c667e`.
- Public release `personal-codex-20260811-163740-8b9cc67` is immutable and
  targets that exact commit.
- The layered installer will obtain `skills/grilling` from the public base
  before applying the private overlay.

## Evidence

- Toolbox PR #23 passed required CI and the current-head Codex review gate,
  then squash-merged with the same reviewed tree.
- The public release uploaded the archive and checksum assets with GitHub
  SHA-256 digests.
- Source-lock verification passed before and after canonical source sync; the
  sync produced no additional tracked change.
- Layered-install coverage now proves that `skills/grilling` is present in the
  public fixture, absent from private package targets, and still linked through
  `personal-sync/current` after both supported private installation paths.
- The two focused pin/package modules passed 89 tests with one expected skip.
  Root discovery passed 2,023 tests with three expected skips under Python
  3.13.
- Review-runtime discovery exercised 2,924 tests with 15 expected skips. Its
  sole local failure is a macOS dynamic-library limitation when `venv` copies
  the uv-managed interpreter; the exact failure reproduces on unchanged base
  `183fc4713459192e5188920dbb863f811371a502`, so hosted clean-runner CI remains
  authoritative for that environment-specific test.
- Source-only compilation, project-journal validation, and `git diff --check`
  passed.

## Next Steps

- None within the tracked change. Default-branch release publication and
  scheduler convergence are downstream delivery gates.
- Toolbox PR #22's collaboration guardrails still require the separate private
  AGENTS reconciliation tracked by `20260721-rpm001`.
