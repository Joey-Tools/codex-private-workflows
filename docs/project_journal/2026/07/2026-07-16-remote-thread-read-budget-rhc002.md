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
- Classified rollout summaries as locator and triage output only, then routed task reconstruction through bounded exact chunk fetches and `codex-session-mining` so multiple later substantive human follow-ups survive wrapper filtering.

## Current State

- `remote-host-context` no longer treats post-fetch projection or per-item limits as a whole-response budget.
- Focused documentation tests lock the server-side controls, exact byte ceiling, complete `read_thread` bypass, creation-date discovery, and lossless chunk handoff.
- A behavior test proves the lossy-to-lossless transition: the chunk summary retains only one redacted last-user locator, while the exact fetched byte range retains both substantive user follow-ups verbatim.

## Next Steps

- Monitor whether a future thread service adds all three server-side controls; until then, keep `read_thread` out of this recovery flow.

## Evidence

- Daily Skill Friction session `019f662f-2c12-7483-bb7f-9e2be4a71259` returned `original_token_count` values of 109,375 and 34,342 for thread reads on 2026-07-15.
- Pinned whole-range review identified that `turnLimit: 1`, a per-item cap, and caller-side projection still allowed an unbounded item count and whole response; it also required a concrete maximum, lossless follow-up recovery, and creation-date-only `session-meta` scans.
- Focused remote probe suite: 12 tests passed.
- Full repository suite: 565 tests passed with 2 skipped after rerunning outside the sandbox for signed temporary Git fixtures and the package-test scratch directory.
- `personal_codex/skills/remote-host-context/SKILL.md`
- `personal_codex/skills/remote-host-context/references/hosts.md`
- `tests/test_remote_codex_probe.py`
