---
id: 20260721-dsf006
title: Filesystem Hardening Evidence Boundaries
status: completed
created: 2026-07-21
updated: 2026-07-21
branch: wip/filesystem-hardening-evidence
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/127
supersedes: []
superseded_by:
---

# Filesystem Hardening Evidence Boundaries

## Summary

- Added one cross-repository guardrail for choosing filesystem race and tamper evidence without turning benign metadata changes into false-positive mutations.

## Current State

- Filesystem hardening now starts by naming the protected property: object identity, content stability, or access policy.
- Every compared signal must be justified against that property instead of treating all `stat` fields as equivalent mutation evidence.
- Tests must pair an allowed metadata-only transition with a rejected true change to the selected property: object replacement, content mutation, or an access-policy change.
- Read or revalidation failures remain distinct from missing or mismatched data so error handling cannot silently trigger a misleading fallback.

## Next Steps

- None.

## Evidence

- Session `019f7bc9-c25d-7b71-a8b4-58024d61e258`: legal review-state child churn changed `st_nlink`, `mtime`, and `ctime`, causing the same directory inode to be misreported as replaced.
- Session `019f6953-31dc-7ef1-b8b5-ca95c392c29b`: OneDrive File Provider materialization changed file `ctime` without changing identity, size, or `mtime`, causing stable archive data to be misreported as concurrently modified and later as mismatched.
- The detailed review-cleanup contract and executable regression evidence remain in `2026-07-20-review-cleanup-identity-race-dsf005.md` and its linked canonical/public fixes.
