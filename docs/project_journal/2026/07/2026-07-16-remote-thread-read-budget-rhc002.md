---
id: 20260716-rhc002
title: Remote Thread Read Budget
status: completed
created: 2026-07-16
updated: 2026-07-17
branch: codex/daily-skill-friction-20260716-codex-private-workflows-remote-thread-read-budget-v2
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/96
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
- Hardened that plan into an identity-bound snapshot protocol: metadata-only `rollout-stat` freezes the expected device, inode, size, mtime, and ctime before summary or chunk reads; every content command must receive that versioned identity and exact byte count.
- Enforced the 16 MiB source budget before summary scanning, with an exact-size authorization as the only over-limit override, a 64 KiB minimum chunk, and a 4 MiB final serialized summary-output cap.
- Added same-descriptor SHA-256 during the frozen summary scan, pre/post descriptor and current-path identity checks, identity-bound chunk reads, reconstructed digest verification, and a final stat check so append, truncation, or pathname replacement invalidates the attempt.
- Replaced unbounded parent capture for remote stat and chunk-summary commands with concurrent bounded stdout/stderr readers that terminate noisy producers at the configured limit.
- Ensured the bounded parent kills and reaps a child that closes both output pipes but remains alive until the command deadline.
- Rejected oversized explicit fetch-range plans before list allocation, using a 4,096-entry ceiling derived conservatively from the 4 MiB summary-output budget in both local and embedded remote producers.

## Current State

- `remote-host-context` no longer treats post-fetch projection or per-item limits as a whole-response budget.
- Focused documentation tests lock the server-side controls, exact byte ceiling, complete `read_thread` bypass, creation-date discovery, and lossless chunk handoff.
- A multi-chunk behavior test proves the lossy-to-lossless transition: wrappers obscure two substantive follow-ups in summary output, while sequentially fetching every advertised range reconstructs the original JSONL byte-for-byte and retains both follow-ups.
- The reconstruction behavior test now proves that every metadata row agrees on source size and global limit, the planned ranges cover the source exactly, and the cumulative planned bytes stay within the global limit before any fetch begins.
- A constant-sized unit fixture proves an over-limit source is marked ineligible for automatic full reconstruction without materializing a large rollout, and a remote-command test locks propagation of the global limit into the embedded summary helper.
- Targeted snapshot tests now prove over-limit rejection occurs before chunk iteration, only exact-byte authorization lifts it, tiny chunks are rejected before path open, output-cap failures emit no partial plan, and append/replacement races fail both before and after reads.
- The complete reconstruction test verifies the reconstructed SHA-256 against `rollout_meta` and repeats `rollout-stat` against the original identity after the last chunk; a separate subprocess test proves both noisy stdout and noisy stderr are stopped at the parent capture cap.

## Next Steps

- Monitor whether a future thread service adds all three server-side controls; until then, keep `read_thread` out of this recovery flow.

## Evidence

- Daily Skill Friction session `019f662f-2c12-7483-bb7f-9e2be4a71259` returned `original_token_count` values of 109,375 and 34,342 for thread reads on 2026-07-15.
- Pinned whole-range review identified that `turnLimit: 1`, a per-item cap, and caller-side projection still allowed an unbounded item count and whole response; it also required a concrete maximum, lossless follow-up recovery, and creation-date-only `session-meta` scans.
- Independent Codex PR review for #96 identified that lossy summary content could not safely choose `relevant` or `user-bearing` chunks for omission; full reconstruction now consumes the complete ordered `chunk_meta` range map.
- Pinned whole-range review of `7d44fb81ef2baf221f3ee2f8d2998aba22c95c18` identified that sequential 2 MiB chunk reads could still bypass the existing 16 MiB full-rollout budget without a cumulative plan gate.
- Pinned whole-range review of `132d05962ab2ac415346e93cf8356644a4098799..63c4a7613d32ef2123d2aa9ad30795d4c5d6a49d` identified that the remote producer and parent capture could still emit or retain an unbounded summary, and that separate chunk reads were not bound to one immutable source snapshot.
- Independent Codex PR review of `132d05962ab2ac415346e93cf8356644a4098799..24c3f6473b8bbb1ee4f7ee6b280b964fb4c579eb` identified that a child closing its pipes before the deadline could escape the existing timeout cleanup without being killed and reaped.
- Pinned whole-range review of `132d05962ab2ac415346e93cf8356644a4098799..abf63e9e18adfd5fcf498d312d405533b78ff1a8` identified that an authorized rollout with a huge single JSONL record could allocate an unbounded `fetch_ranges` list before the 4 MiB serialized-output check.
- Focused remote probe suite: 25 tests passed.
- Full repository suite: 579 tests passed with 2 skipped after running outside the sandbox for signed temporary Git fixtures and the package-test scratch directory.
- Ruff lint, Python byte compilation, skill quick validation, project journal validation, and `git diff --check` passed. The changed test file also passes Ruff format checking; the helper's exact `7d44fb81ef2baf221f3ee2f8d2998aba22c95c18` baseline already fails the current formatter and would require a broad unrelated mechanical rewrite, while the new helper hunks do not appear in the formatter diff.
- `personal_codex/skills/remote-host-context/SKILL.md`
- `personal_codex/skills/remote-host-context/references/hosts.md`
- `tests/test_remote_codex_probe.py`
