---
id: 20260727-rbs001
title: Review Runtime Bytecode Sync
status: completed
created: 2026-07-27
updated: 2026-08-01
branch: wip/review-runtime-bytecode-sync
pr: 139
supersedes: []
superseded_by:
---

# Review Runtime Bytecode Sync

## Summary

- Propagate the review runtime's no-bytecode contract through private CI, release, scheduled sync, and documented local validation.
- Align private CI exactly with the canonical reviewed fixture, including broker reproducibility and independent-supervisor jobs.
- Sync the hardened review control plane without introducing repository-root duplicates of private policy files.

## Current State

- All three private Python workflows set `PYTHONDONTWRITEBYTECODE=1` at workflow scope, so child Python processes inherit the contract.
- Release build, release publish, and scheduled sync-release pin Python 3.13 so the independent supervisor is never launched by a drifting `3.x` selector.
- Documented local tests combine `PYTHONDONTWRITEBYTECODE=1` with `-B`; documented syntax validation compiles source bytes without writing cache files.
- Private CI matches the canonical private-profile fixture byte for byte.
- Private policy-scope validation resolves `agents/reviewer.toml` beneath `personal_codex/`, while canonical validation remains repository-rooted.
- Private sync rewrites the trusted-Mac operator command to the `personal_codex/` layout and fail-closes on any missing or unreviewed file in the independent-supervisor subtree.
- The private review tree now matches the actual canonical PR #85 squash-merge tree for the complete reviewed file inventory, including caller-owned child-outcome receipts, read-only child isolation, and double-fork custody recovery.

## Next Steps

- None after the private overlay release is published and installed-release entrypoints confirm the immutable tree remains bytecode-free.

## Evidence

- Scheduled sync run `30238744153` failed before this change because a normal test interpreter imported the new fail-closed `review_runtime` package without disabling bytecode.
- Python 3.13 reproduces that failure without `-B`; the same focused contract passes with the no-bytecode control enabled.
- The complete private Python 3.13 suite passed 1,330/1,330 in 427.345 seconds with the environment propagated to child processes.
- The post-suite repository inventory contains no `__pycache__`, `.pyc`, or `.pyo` entry.
- Focused private workflow, synthetic-token, private policy-scope, and installed-bundle no-bytecode regressions passed.
- Canonical follow-up https://github.com/Joey-Tools/codex-review-workflows/pull/82 merged as `739ee04bb6813b00f590f2ce70d2ac8087c66562` and corrects the private policy-scope path used by the shared contract.
- Canonical https://github.com/Joey-Tools/codex-review-workflows/pull/84 merged as `0f77fb7b1dd59f5eed522fa9699497aa013695fc`; its tree exactly matches the reviewed signed head `4aec88368dcf5c101174fa3838ac870933e8bfa8`.
- Fresh Codex review of `b4caaf5dcc5e266a4022f5c9fa2999427c56145d..e296f4b00f5bfc1525bf57be3c6df87827c319cd` found the private trusted-Mac path and sync completeness gaps; both are corrected in the follow-up head with focused regressions.
- The final private root suite passed 1,333/1,333 in 167.831 seconds.
- Private PR #139 release run `30517877149` initially failed because `actions/setup-python` resolved `3.x` to Python 3.14.6 while the independent supervisor requires Python 3.13. Release and scheduled-sync selectors now pin 3.13, with a workflow contract regression.
- After the runtime-pin repair, the private root suite passed 1,334/1,334 in 156.461 seconds under Python 3.13; the focused workflow contract and `actionlint` also passed.
- The complete synced review suite ran 2,820 tests with 13 skips; its only parent-sandbox failure was the nested `sandbox-exec` broker case, which passed 1/1 outside the parent sandbox.
- The independent-supervisor deterministic runner passed 604/604 in 215.849 seconds.
- The installed-release immutability selector, canonical exact-inventory checks, source-only syntax compilation, and independent supervisor CLI smoke test passed under Python 3.13.
- `actionlint`, canonical CI fixture equality, Ruff lint, project-journal validation, and `git diff --check` passed.
- Whole-file Ruff format remains non-clean on pre-existing private test formatting outside this change; the new hunk matches Ruff's expected output and no unrelated formatting rewrite was applied.
- No local Python 3.10 run was performed.
- `Claude lane temporarily waived by Joey before 2026-08-01 00:00 Asia/Shanghai`; the unrun lane is not counted as a completed named double or triple.
- Canonical PR #85 merged as `b807cf90a2c8235ea79ef5013655bd7c52e4c886` with parent `0f77fb7b1dd59f5eed522fa9699497aa013695fc`; its tree `7a9246c7b3b9d47c9694956cefb8f43f9c8ebb87` exactly matches the final reviewed signed head tree.
- The final private sync used that merge tree with Git's `0644`/`0755` access policy preserved. The installed-release immutability contract then passed and confirmed that preflight state is account-local while the release tree remains unchanged.
- All final validation used uv 0.11.18 with its managed CPython 3.13.13 runtime; no second Python version was used.
- The final private root suite passed 1,334/1,334 in 86.429 seconds with the fixture-required `umask 022` and isolated Git configuration.
- The final host-level synced review suite passed 2,825/2,825 with 15 expected skips in 532.557 seconds. The process-local open-file limit was raised to 4,096 so the intentional 254-level accepted cleanup boundary could run on macOS; the 255-level rejected boundary remained covered.
- The deterministic independent-supervisor gate passed 802/802 in 104.234 seconds with selected-identity SHA-256 `d937a349ec87ffbd440be7e73734f5ea7533331c7212d5977c7661481b0a3516`.
- The complete read-only installation module passed 111/111 in 9.929 seconds, ten ordered normal/failure-injection double-fork rounds passed 20/20 in 67.387 seconds, and the repository contract module passed 105/105 with eight expected skips in 4.325 seconds.
- The exact independent-supervisor inventory, private trusted-Mac path rewrite, installed-release immutability, and private CI fixture selectors passed. Source-only compilation covered 109 Python/entrypoint files, Ruff 0.13.2 lint passed, Bash syntax passed, the private root CI remained byte-identical to its reviewed fixture, project-journal validation passed, and no bytecode artifact remained.
- Ruff 0.13.2 format reports the same nine pre-existing files on both PR head `960a234e953e390cc1720877a7dcdf8d1947ca16` and the final synchronized tree, so no unrelated formatter-only rewrite was added. Local `shellcheck` and `actionlint` executables were unavailable; Bash syntax, repository workflow contracts, fixture equality, and the canonical merged CI evidence provide the local fallbacks.
