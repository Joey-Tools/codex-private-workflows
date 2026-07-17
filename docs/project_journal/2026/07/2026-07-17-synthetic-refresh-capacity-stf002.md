---
id: 20260717-stf002
title: Synthetic Authoring Token Capacity
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-refresh-c
pr:
supersedes: []
superseded_by:
---

# Synthetic Authoring Token Capacity

## Summary

- Expanded the private authoring catalog from ten to 52 exact values so every supported role has ten active IDs, including the third refresh-token ID, while retaining the existing lifecycle fixtures.
- Advanced the pool version from `joey-private-v1` to `joey-private-v3` across the complete change; `joey-private-v2` was the intermediate refresh-capacity state.

## Current State

- `access`, `refresh`, `id`, `api-key`, and `bearer` each provide ten active IDs from `a` through `j`; `access-expired` and `refresh-consumed` remain the lifecycle-state fixtures.
- Runtime matching remains finite and exact. No regex namespace or runtime allocator was introduced, and unlisted mutations, prefixes, suffixes, and embedded variants remain blocked.
- The trusted override and generated helper catalog are byte-identical, and no legacy exemption changed.

## Next Steps

- No implementation follow-up remains. Publish the catalog through the ordinary private overlay release flow after the change lands on the default branch.

## Evidence

- The catalog CLI validates schema version 1 with pool `joey-private-v3` and reports 52 authoring entries: 50 active values with ten for each supported role, plus one expired and one consumed fixture.
- The focused private catalog and overlay contract suite passed 3 tests.
- The generated helper synthetic-token module passed 84 tests with 1 skip.
- The complete private repository suite passed 595 tests with 2 skips after synchronizing the current `master` baseline.
- The review skill passed the OpenAI quick validator through an isolated PyYAML environment, both catalog copies compare byte-for-byte equal, and `git diff --check` passed.
- Source context: `codex://threads/019f17fc-5756-7fb2-8d9f-34c0330bd59b` on `BL-mac-mini-m4-hoteng`.

## Files

- `personal_codex/private-overrides/review-orchestration-playbook/synthetic-token-catalog.json`
- `personal_codex/skills/review-orchestration-playbook/scripts/review_runtime/synthetic-token-catalog.json`
- `tests/test_private_overlay_sync.py`
- `tests/test_private_synthetic_catalog.py`
