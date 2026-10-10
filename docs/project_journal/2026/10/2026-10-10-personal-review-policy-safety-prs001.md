---
id: 20261010-prs001
title: Personal Review Retry and Landing Order Safety
status: completed
created: 2026-10-10
updated: 2026-10-10
branch: codex/review-policy-safety-20261010
pr:
supersedes: []
superseded_by:
---

# Personal Review Retry and Landing Order Safety

## Outcome

Personal AGENTS retains the existing detailed routing, processor consent and
Actions authority paragraphs. Only its ambiguous-delivery retry exception is
replaced with exact-head read-only request discovery plus independent
non-delivery proof. An empty GET cannot authorize a second POST.

Local squash/amend and signed landing-commit creation precede final exact-head
review. Any later head change invalidates that review. Server-side squash merge
is separate: it follows complete PR readiness and binds the reviewed head.

## Migration And Protected Properties

The sync helper recognizes exact prior review and landing text. Missing,
unknown, partial and duplicated guidance fails closed. Approved locked
canonical source requirements and the descriptor-bound transaction remain
unchanged. Protected properties are object identity, content stability and
access policy; no timestamp guard or generic drift exception is added.

Direct byte migration and the production locked-source transaction have
regressions, including idempotence and the unchanged no-op inode.

## Validation

Python 3.13.0 passed all 441 consumer/source-lock tests, with one platform-only
skip. Native Python 3.9.6 passed all eight declared runtime compatibility tests
and the direct byte-migration regression. Selected Ruff, journal validation,
whitespace and source-only checks passed. These checks do not claim a full
Python 3.9 source-sync tool pass or formal review/remote CI result.

## Boundary

Instruction slimming is deferred. Canonical source locks, generated skill and
fixture files, releases and installed homes are unchanged. The canonical
companion adds request preflight and the exact official clean disclosure;
its new carrier bytes are not claimed to be installed by this PR.
