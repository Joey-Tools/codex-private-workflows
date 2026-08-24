---
id: 20260811-gbp001
title: Promote Adaptive Grilling Public Base
status: completed
created: 2026-08-11
updated: 2026-08-24
branch: wip/grilling-public-base-promotion
pr:
supersedes: []
superseded_by:
---

# Promote Adaptive Grilling Public Base

## Summary

- Advance the private overlay's immutable public base to the toolbox release
  that lets `grilling` adapt between proposal and alternatives mode.
- Keep `grilling` public-owned; do not duplicate it in the private manifest.
- Preserve all four non-toolbox source pins and the receipt-bound generated
  toolbox provenance.

## Current State

- The private base release points to toolbox commit
  `b5694a8057b03b8e7e5dba56083a738383ad463a` and tree
  `92b53d7ddb5e3c26f002bffd72ff00585788e765`.
- Public release `personal-codex-20260824-092257-b5694a8` is immutable and
  targets that exact commit.
- The layered installer obtains the adaptive `skills/grilling` from the public
  base before applying the private overlay.

## Evidence

- Toolbox PR #29 passed required CI and both current-head review processors,
  then squash-merged with the reviewed tree.
- The fresh-context local processor used the pending strongest-default fallback
  contract because the dedicated reviewer role was unavailable; GPT-5.6 Sol
  Ultra returned no findings.
- The current-head GitHub Codex terminal comment was clean and accepted as
  positive evidence under the pending evidence-authority rule.
- The public release uploaded an archive and checksum asset bound to the exact
  merge commit.
- The private manifest, release verifier, source lock, and contract tests bind
  the same public commit while leaving every non-toolbox pin unchanged.
- The focused source-lock, package, and macOS controller suites passed 329 tests
  with two expected skips under Python 3.13.
- Root discovery passed 1,971 tests with four expected skips under Python 3.13.
- Source-only compilation, project-journal validation, source-lock provenance
  validation, and `git diff --check` passed.

## Next Steps

- None within the tracked change. Default-branch release publication and
  scheduler convergence are downstream delivery gates.
