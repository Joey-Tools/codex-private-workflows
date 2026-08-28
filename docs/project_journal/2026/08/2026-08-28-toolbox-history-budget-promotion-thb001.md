---
id: 20260828-thb001
title: Promote Toolbox Release-History Budget
status: completed
created: 2026-08-28
updated: 2026-08-28
branch: codex/private-toolbox-255372d
pr:
supersedes: []
superseded_by:
---

# Promote Toolbox Release-History Budget

## Summary

- Advance the private overlay's immutable public base to toolbox commit
  `255372d2b0dd96f39faf1e52a9168ca2aa7ece69` and tree
  `117699605432b44b6af190fdc944fe1f1b08b6a5`.
- Synchronize the canonical Release-history validator and its boundary regression
  from that exact toolbox source while preserving all four non-toolbox pins and
  the receipt-bound generated provenance.
- Keep complete immutable Release-history verification and raise only the finite
  aggregate two-pass expanded-scan ceiling from 2 GiB to 4 GiB.

## Decisions

- The scheduled private sync intentionally never advances the immutable toolbox
  pin, so this dependency change uses the repository's explicit reviewed
  public-base promotion path rather than retrying the old workflow tuple.
- The 4 GiB ceiling plans for the supported maximum of 256 complete Releases at
  a 16 MiB average expanded size per archive pair. The 256 MiB per-archive cap,
  compressed-byte limits, complete-history traversal, and one-byte-over
  fail-closed behavior remain unchanged.
- The valid 164-Release private history exhausted the previous 2 GiB aggregate
  ceiling in workflow run `33128766268`. History truncation and a mutable
  cross-run cache were rejected because either would weaken the complete-history
  admission contract.
- This promotion freezes the other four source identities. After it lands and
  publishes a private release, the ordinary scheduled sync can independently
  refresh those pins and carry the canonical review-workflow release without
  conflating the two policy changes.

## Current State

- Public toolbox PR #30 squash-merged as the signed commit above.
- Immutable public release
  `personal-codex-20260828-011257-255372d` targets that exact commit and contains
  the uploaded archive/checksum pair with GitHub SHA-256 digests.
- The private manifest, source lock, release verifier, and their contract tests
  bind the same public commit; the source lock retains the previous four
  non-toolbox pins and generated-provenance receipt.
- Canonical source synchronization changes only the two expected Release-history
  files in addition to the public-base binding surfaces and this journal.

## Evidence

- The exact five-source standalone checkouts passed source-lock, object-closure,
  strict-fsck, generated-provenance, and pre/post synchronization validation.
- The candidate source-lock SHA-256 before the journal-only addition is
  `77bb866366f14d4b7047c36a19a3e6058231235d41cba1b25222070f251a805e`.
- The four affected source-lock, package, macOS controller, and Release-baseline
  modules passed 400 tests with two expected skips in 125.721 seconds.
- Base-ref manifest validation passed, and authenticated complete Release-history
  validation passed against private release commit
  `4c7c9364e679032e33e0f105aa70e05d42210c31`.
- Before the mechanical public-base binding and journal additions, the exact
  synchronized 4 GiB validator/test bytes passed the private repository's full
  2,058-test suite with four expected skips in 392.453 seconds.

## Next Steps

- Merge the reviewed promotion PR and verify its default-branch private release.
- Trigger the forced scheduled sync once, then review and merge the generated
  non-toolbox source update before installing and verifying the final overlay.
