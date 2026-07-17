---
id: 20260717-stf002
title: Synthetic Refresh Token Capacity
status: completed
created: 2026-07-17
updated: 2026-07-17
branch: codex/daily-skill-friction-20260717-codex-private-workflows-refresh-c
pr:
supersedes: []
superseded_by:
---

# Synthetic Refresh Token Capacity

## Summary

- Expanded the private authoring catalog from ten to eleven exact values by adding the third active refresh-token ID, `refresh-c`.
- Advanced the pool version from `joey-private-v1` to `joey-private-v2`, as required whenever trusted authoring values change.

## Current State

- `refresh-a`, `refresh-b`, and `refresh-c` are active refresh-token IDs; `refresh-consumed` remains the consumed-state fixture.
- Runtime matching remains finite and exact. Regex namespaces, appended counters, prefixes, suffixes, and embedded variants remain blocked.
- The trusted override and generated helper catalog are byte-identical, and no legacy exemption changed.

## Next Steps

- No implementation follow-up remains. Publish the catalog through the ordinary private overlay release flow after the change lands on the default branch.

## Evidence

- The catalog CLI validates schema version 1 with pool `joey-private-v2` and reports 11 authoring entries.
- The focused private catalog and overlay contract suite passed 3 tests.
- The generated helper synthetic-token module passed 84 tests with 1 skip.
- The complete private repository suite passed 592 tests with 2 skips.
- The review skill passed the OpenAI quick validator through an isolated PyYAML environment, both catalog copies compare byte-for-byte equal, and `git diff --check` passed.
- Source context: `codex://threads/019f17fc-5756-7fb2-8d9f-34c0330bd59b` on `BL-mac-mini-m4-hoteng`.

## Files

- `personal_codex/private-overrides/review-orchestration-playbook/synthetic-token-catalog.json`
- `personal_codex/skills/review-orchestration-playbook/scripts/review_runtime/synthetic-token-catalog.json`
- `tests/test_private_overlay_sync.py`
- `tests/test_private_synthetic_catalog.py`
