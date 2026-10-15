---
id: 20261015-twd001
title: Decouple Tests From Human Wording
status: completed
created: 2026-10-15
updated: 2026-10-15
branch: codex/private-test-prose-cleanup-20261015
pr:
supersedes: []
superseded_by:
---

# Decouple Tests From Human Wording

## Summary
- Remove seven private-owned tests that only search guidance or automation prompts for fixed policy wording.
- Preserve attribution selection and provenance cases without pinning their human-facing sentence wrapper.
- Drop illustrative report numbering and sample-count anchors while retaining repository-to-report-bucket checks.

## Boundaries
- Runtime code, configuration, permissions, generated source receipts, and canonical skill copies remain unchanged.
- Filesystem, installation, recovery, parser, model/effort selection, and evidence-provenance regressions remain in place.
- Guidance still requires semantic review; keyword presence was not proof that an agent enforced the policy.

## Evidence
- Python 3.13.0 passed all 454 tests in the three affected modules in 136.798 seconds. The seven removed methods are offset by two new label-extraction regressions; no runtime tests were filtered from those modules.
- Ruff, whitespace checks, and project-journal validation passed.
- A follow-up review regression permits `GPT-generated` wrapper text while rejecting duplicate, conflicting, and unsupported model-shaped labels. All 46 attribution module tests passed on Python 3.13.0 in 3.819 seconds after this correction; the other two tested modules are unchanged.
- A second follow-up rejects token-like effort suffixes such as `Max-preview` and `Max.preview`, including an extra suffixed label beside a valid label, while allowing ordinary wrapper punctuation. The frozen predecessor failed the suffix control as expected; all 47 attribution tests passed after the fix in 3.597 seconds.
- `tests/test_private_overlay_sync.py`
- `tests/test_private_overlay_package.py`
- `tests/test_pr_attribution.py`

## Follow-up
- Scheduled-sync test partitioning is a separate stacked change; this test-only cleanup makes no performance claim.
