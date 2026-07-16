---
id: 20260716-rhc002
title: Remote Thread Read Budget
status: completed
created: 2026-07-16
updated: 2026-07-17
branch: codex/daily-skill-friction-20260716-codex-private-workflows-remote-thread-read-budget-v2
pr:
supersedes: []
superseded_by:
---

# Remote Thread Read Budget

## Summary

- Added a bounded locator contract for `codex://threads/<id>` evidence reads.
- Resolved pinned review P1 by requiring server-side item-type, item-count, and a concrete 32 KiB (32,768-byte) encoded whole-response cap in addition to the one-thread, one-turn, no-output, and per-item limits.
- Forbade `read_thread` when any server-side whole-response control is unavailable and limited direct `session-meta` scans to a known creation date; activity-only or unknown dates now require a bounded exact metadata lookup that derives the creation date first.
- Classified rollout summaries as locator and triage output only, then required full task reconstruction to fetch every range from every `chunk_meta` row in byte order before delegating the complete exact stream to `codex-session-mining`.
- Closed the cumulative-read gap by exposing the 16 MiB (16,777,216-byte) full-fetch limit and a fail-closed reconstruction decision on every `chunk_meta` row, including remote summary output.
- Required consumers to validate the complete gap-free range plan and cumulative byte total before the first chunk fetch; over-limit rollouts now require zero automatic fetches and Joey's exact-rollout, exact-byte-count authorization.

## Current State

- `remote-host-context` no longer treats post-fetch projection or per-item limits as a whole-response budget.
- Focused documentation tests lock the server-side controls, exact byte ceiling, complete `read_thread` bypass, creation-date discovery, and lossless chunk handoff.
- A multi-chunk behavior test proves the lossy-to-lossless transition: wrappers obscure two substantive follow-ups in summary output, while sequentially fetching every advertised range reconstructs the original JSONL byte-for-byte and retains both follow-ups.
- The reconstruction behavior test now proves that every metadata row agrees on source size and global limit, the planned ranges cover the source exactly, and the cumulative planned bytes stay within the global limit before any fetch begins.
- A constant-sized unit fixture proves an over-limit source is marked ineligible for automatic full reconstruction without materializing a large rollout, and a remote-command test locks propagation of the global limit into the embedded summary helper.

## Next Steps

- Monitor whether a future thread service adds all three server-side controls; until then, keep `read_thread` out of this recovery flow.

## Evidence

- Daily Skill Friction session `019f662f-2c12-7483-bb7f-9e2be4a71259` returned `original_token_count` values of 109,375 and 34,342 for thread reads on 2026-07-15.
- Pinned whole-range review identified that `turnLimit: 1`, a per-item cap, and caller-side projection still allowed an unbounded item count and whole response; it also required a concrete maximum, lossless follow-up recovery, and creation-date-only `session-meta` scans.
- Independent Codex PR review for #96 identified that lossy summary content could not safely choose `relevant` or `user-bearing` chunks for omission; full reconstruction now consumes the complete ordered `chunk_meta` range map.
- Pinned whole-range review of `7d44fb81ef2baf221f3ee2f8d2998aba22c95c18` identified that sequential 2 MiB chunk reads could still bypass the existing 16 MiB full-rollout budget without a cumulative plan gate.
- Focused remote probe suite: 14 tests passed.
- Full repository suite: 567 tests passed with 2 skipped after running outside the sandbox for signed temporary Git fixtures and the package-test scratch directory.
- Ruff lint, Python byte compilation, skill quick validation, project journal validation, and `git diff --check` passed. The changed test file also passes Ruff format checking; the helper's exact `7d44fb81ef2baf221f3ee2f8d2998aba22c95c18` baseline already fails the current formatter and would require a broad unrelated mechanical rewrite, while the new helper hunks do not appear in the formatter diff.
- `personal_codex/skills/remote-host-context/SKILL.md`
- `personal_codex/skills/remote-host-context/references/hosts.md`
- `tests/test_remote_codex_probe.py`
