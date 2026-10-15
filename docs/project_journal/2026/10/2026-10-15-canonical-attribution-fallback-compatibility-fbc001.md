---
id: 20261015-fbc001
title: Canonical Attribution Fallback Compatibility
status: completed
created: 2026-10-15
updated: 2026-10-15
branch: codex/private-attribution-fallback-compatibility-20261015
pr:
supersedes: []
superseded_by:
---

# Canonical Attribution Fallback Compatibility

## Scope
- Keep private attribution tests focused on evidence selection and fail-closed behavior rather than a duplicated current fallback model/effort policy.
- Preserve exact supported model/effort selection, invalid provenance, ambiguous or unsafe evidence, and single-label parsing checks.
- Keep invalid evidence distinguishable from a legitimate non-fallback selection under either canonical fallback profile.
- Do not change the canonical helper, generated sources, source pins, installed policy, or native accounts.

## Evidence
- The two preceding test-delivery PRs [#225](https://github.com/Joey-Tools/codex-private-workflows/pull/225) and [#227](https://github.com/Joey-Tools/codex-private-workflows/pull/227) merged and their default-branch publications passed.
- [Scheduled source-sync run 38728078862](https://github.com/Joey-Tools/codex-private-workflows/actions/runs/38728078862) prepared its exact candidate and launched all four overlay groups concurrently. Three groups passed; group 2 ran 997 tests in 440.118 seconds and failed with 36 identical High-versus-Medium assertions. Its final writer was skipped, so it created no sync PR.
- The source update selected verified hygiene commit [`1fb26ca`](https://github.com/Joey-Tools/codex-workflow-hygiene/commit/1fb26caac8d90ed308b2ce0f96b0ab83e487045c), whose merged PR #81 intentionally changes the missing-evidence fallback from Medium to High. The private tests duplicated the old Medium profile.
- The failed group raw log SHA-256 is `04e2d273b0de1cf71f56d1e2266eddc38df2b8b1e54327dbba2a720862b38532`; terminal run/jobs receipts are `6a3d38cb241dc22b974c9f27f2ee21bed12743e612bc6718024716314d4d621c` and `85dddec7403fcde2026541e757f74822aa9efa4f61f1116c73423a094b5f8d31`.

## Implementation
- Discover the selected helper's missing-evidence label once from an independent empty fixture instead of duplicating its current profile in the test suite.
- Keep exact positive model/effort mappings and strict single-supported-label parsing. Wrapper punctuation examples use a concrete selection independent of the fallback.
- Check that valid evidence selects a distinct supported label while the same tempting selection under invalid provenance falls back. Malformed evidence also carries a tempting valid selection, so merely accepting it cannot satisfy the fallback assertion.
- All 47 attribution tests remain. No runtime implementation, generated helper, source lock, credential selection, or installed account changes are included.

## Validation
- Native Python 3.13.0: all 47 attribution tests passed in 3.874 seconds. The four complete affected attribution, overlay sync/package, and unittest-sharding modules passed all 471 tests in 125.283 seconds; supervisor exit 0 with no incomplete marker. Raw log SHA-256: `ce9f653a434c77221ae1b753744045185467dc299eeec571b5e39504a92dba71`.
- A parent-executed controlled snapshot matrix used the exact base `b2b8a9a2f2e845dcfec8bbf0687d57fb1f0ec276` and the independently verified canonical High helper, with the two unchanged support documents copied from that base:

  | Test source | Helper source | Actual result |
  | --- | --- | --- |
  | Base tests | Base Medium helper | 47 passed |
  | Base tests | Canonical High helper | 47 ran; 36 expected High-versus-Medium assertion failures; zero errors |
  | Updated tests | Base Medium helper | 47 passed |
  | Updated tests | Canonical High helper | 47 passed |

- The High helper was bound to canonical commit `1fb26caac8d90ed308b2ce0f96b0ab83e487045c`, Git blob `25d8af63c82c67ece1a05c2106001f1bddac3b6b`, 46,337 bytes, and source SHA-256 `093c68e8f27a980817ac5e842d75ce90432fffbab9b2f5bf1c919796609481da`.
- Candidate test SHA-256: `6ca59f0dd5c4c725122712a58969ab79567e9c7924fe83a838bd2365df0e92f1`. The exact inputs and derived snapshot contents were re-read unchanged after execution. Each case retained its raw summary and independently matched supervisor exit, incomplete marker, byte count, and log hash. Matrix supervisor exit 0 / 14.271 seconds; aggregate raw SHA-256 `0dffed4d26f5856c4ed687682d86956a740a8c4d227aa350b1993b677716545e`.
- The snapshot matrix is compatibility evidence, not a source refresh, full High-overlay suite, release publication, or native installation. Those delivery stages remain separate from this focused test-only change.
