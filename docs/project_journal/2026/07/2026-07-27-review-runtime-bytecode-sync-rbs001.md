---
id: 20260727-rbs001
title: Review Runtime Bytecode Sync
status: active
created: 2026-07-27
updated: 2026-08-05
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
- Consume the canonical personal-sync surface only through its receipt-bound toolbox mirror and immutable public base release.

## Current State

- All three private Python workflows set `PYTHONDONTWRITEBYTECODE=1` at workflow scope, so child Python processes inherit the contract.
- Release build, release publish, and scheduled sync-release pin Python 3.13 so the independent supervisor is never launched by a drifting `3.x` selector.
- Documented local tests combine `PYTHONDONTWRITEBYTECODE=1` with `-B`; documented syntax validation compiles source bytes without writing cache files.
- Private CI matches the canonical private-profile fixture byte for byte.
- Private policy-scope validation resolves `agents/reviewer.toml` beneath `personal_codex/`, while canonical validation remains repository-rooted.
- Private sync rewrites the trusted-Mac operator command to the `personal_codex/` layout and fail-closes on any missing or unreviewed file in the independent-supervisor subtree.
- The private review tree now matches the actual canonical PR #85 squash-merge tree for the complete reviewed file inventory, including caller-owned child-outcome receipts, read-only child isolation, and double-fork custody recovery.
- The private base release is pinned to immutable toolbox commit `20f37f4703715393480d550086980bb1fa44c7b3`; its published tree is byte-identical to reviewed toolbox head `ac7275f2064531bde05bfe0502617efc44f573b3`.
- The final source reconciliation freezes six direct inputs: toolbox `20f37f4703715393480d550086980bb1fa44c7b3`, review workflows `80d3fc9c7d9f4842d0fa247a7c0b974c00052124`, debug triage `d3b6fd26b021ef1a6aad8561a92a354c27510fbd`, workflow hygiene `c69a0b1a92a349179ed41b0a378c08fe70e8160f`, project journal `4f53fd1bf9ba0a7c85db8d183016210d3d0089e5`, and waited delivery `2cc1f97efc86dfbcb582743e5f0eb46440f2f713`.
- Canonical personal-sync `e57140e16a68db24dbdd883de665283538234730` is recorded only as toolbox receipt provenance; private never mirrors it directly.
- On macOS the source-lock verifier ignores PATH and every parent developer-tool or loader selector, then directly executes fixed `/Library/Developer/CommandLineTools/usr/bin/git` under a closed environment. It binds that root-owned actual executable and every root-owned, non-group/world-writable ancestor by object identity and access policy before and after each command. The `/usr/bin/git` xcrun shim and current-user-owned or group-writable Homebrew and Xcode ancestors are intentionally outside this trust path rather than newly admitted.

## Next Steps

- Freeze and sign the final combined source tree, then run exact-head admission, CI, conversation, lifecycle, mergeability, target-tree, and applicable same-head review gates.
- Re-run those exact-head gates on the signed Git trust-root repair; the coordinating root owns the sole fresh named single and all remote gate orchestration.
- Publish and install the immutable private release, prove a real successful scheduler run, and dispatch scheduled reconciliation only as a no-diff/no-new-PR check.
- After the merged tree and release prove complete behavioral supersession, close draft PR #140 as superseded by PR #139.

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
- Current private master `9bbdf969dfd048c4431ca051a1d776789cdf4ac8` was ordinary-merged into the delivery line as signed checkpoint `cd90d518c951f9a2683a036946ee75e5ec72a525`; its two merge-resolution contract tests passed under uv-managed Python 3.13.
- Toolbox squash commit `20f37f4703715393480d550086980bb1fa44c7b3` has tree `10fe688d89732bbb4eae1ad7cae753f5089fc749`, valid GitHub signature, successful default-branch CI and public-release workflows, and immutable release `personal-codex-20260805-163744-20f37f4`.
- The toolbox archive asset is 195,351 bytes with SHA-256 `b9a4d126a1f6041ccb055d3b07ec150cc81179fb75220e4fd487ad5c2b899dd7`; the checksum asset is 129 bytes with SHA-256 `256e92a04e59b9df9516b1f5e30330a837d404d5fa8449c62a59b40592201f91`.
- The toolbox generated-source receipt binds canonical personal-sync `e57140e16a68db24dbdd883de665283538234730`, mapping digest `3e26648dd65526e759089c5acf5a9f429f3df0f5adc8dbe94b3856954b801ece`, file-set digest `c280b934568b6bc8df0c993b91d3e2e051970a8395870bf0419fc475556af7ad`, and tree digest `7a273c8533839cd7efd13d96d4f6783ccce75442d00d1528015bed3290a6e505`.
- Fresh custody for all six direct sources proved exact commit/tree/parent identity, clean detached worktrees, complete reachable objects, strict full `fsck`, and absence of shallow, promisor, or alternate object dependencies.
- Owner-authored PR comment `5195454437` supersedes inference comment `5195337028`; the final review-workflows direct source remains trusted epoch `80d3fc9c7d9f4842d0fa247a7c0b974c00052124`, while `aec747a9265a55f702c4df01a511336f2738e51b` remains read-only chronology and compatibility evidence.
- The exact six-source lock has SHA-256 `e6c869dc96fb7f45a31b26156bd3b4bd1a542a48966bd320300e66755286543c`; the generated toolbox receipt has SHA-256 `ff1a2dad1b3d473568c0a7b785110dfbe5094747f8d3fa31ade7ab5b2a0fdb9e`; and the private sync manifest has SHA-256 `0d1e3731baa1c5b70048eee73b7575089ebc4e7bc1eda3118d5d9b727a04ef96`.
- The counting combined generator completed once and then repeated with identical input as a no-op. The tracked diff SHA-256 remained `0228435b42cc224e0d3a7cc59008ad3023eb6172bd5f5dfe2dc7f291aae7c2ee`, and the tracked/untracked status SHA-256 remained `daed04222e87b7912c364c56f963c0492934f3554c07d05125c6b2083e1d91eb`.
- The final uv-managed Python 3.13 root suite passed 1,945/1,945 with three expected skips in 132.443 seconds under `umask 022`, no bytecode, and isolated system/global Git configuration. The waited-delivery suite passed 58/58 with two expected skips.
- The canonical review suite completed 2,835 tests with 15 expected skips. Its only parent-sandbox failure was the nested `sandbox-exec` broker case, which passed 1/1 through the direct-local no-outer-Seatbelt channel; the combined gate is 2,835/2,835.
- Receipt-bound generated-source verification installed the exact receipt plus all six managed files into a fresh owner-private snapshot and passed; the snapshot root was then identity-checked, proved process/FD-free, and removed.
- Source-only compilation passed for 115 Python files, the private CI workflow is byte-identical to its canonical private fixture, project-journal validation passed with the trusted Homebrew Git runtime, `git diff --check` passed, and the repository contains no bytecode artifact.
- Ruff 0.13.2 lint passed for every changed or added Python file. Its format check reports seven exact synchronized/upstream files that would be reformatted, so no consumer-side formatter rewrite was applied; `actionlint` and `shellcheck` remain unavailable locally and the workflow contract tests, fixture equality, source compilation, and hosted CI are the declared fallbacks.
- Joey's current skills/AGENTS authorization keeps Claude lanes out of scope for this delivery; no Claude result is counted or claimed. The final applicable review, CI, conversation, lifecycle, mergeability, and target-tree gates remain mandatory on the unchanged signed head.
- The sole consumable named single on `e1b85cfec954053d8d00a9bb61197a09ed7959d0` found that `shutil.which("git")` selected `/opt/homebrew/bin/git`, whose symlink entry was rejected before source verification. The first repair narrowed macOS launch selection to fixed `/usr/bin/git`; it bound that shim's exact object identity plus owner/group/mode access policy rather than PATH-entry stability. The later `c5a49e1` review proved that this still did not bind the final executable selected by xcrun.
- That first repair's source-lock module passed 40/40 tests with one expected skip under both CI-compatible Python 3.10 and Python 3.13 while PATH preferred `/opt/homebrew/bin`; the coverage proved fixed shim selection, non-macOS rejection of the `0775` Homebrew chain, world-writable ancestor rejection, and bound-file replacement detection, but did not prove the final macOS Git payload.
- Source-only compilation and `git diff --check` passed. Ruff was unavailable in the current local tool environment, so no Ruff result is claimed for this append.
- The fresh named single on `c5a49e1cc80d353b6677ae944f68c94d7dfe0d94` found that `/usr/bin/git` is an `xcrun` selector rather than the final Git executable: inherited `DEVELOPER_DIR`, `SDKROOT`, or `TOOLCHAINS` could redirect execution after the shim itself had passed identity validation. The superseding repair binds and directly executes the root-owned actual Git at `/Library/Developer/CommandLineTools/usr/bin/git` and supplies a closed fixed environment rather than inheriting parent tool selectors, dynamic-loader variables, HOME, PATH, or unrelated values. The protected property is the final executable and every ancestor's object identity plus owner/group/mode access policy, together with the child environment-selection policy; digest, mtime, and ctime stability are not claimed.
- The replacement regression now creates its executable in a cleanup-scoped directory under the already trusted repository instead of the sticky default system temporary root or the real account home. Python 3.10.19 and Python 3.13.0 each passed the complete source-lock module 41/41 with one expected skip. Focused macOS coverage passed against the real Command Line Tools Git under hostile developer-tool selectors, and the macOS private-sync suite now contains a hosted integration assertion that binds and revalidates that actual non-shim path.
- The broader private-overlay sync module ran 197 tests locally and stopped with 17 failures plus four errors at the pre-existing `catalog.json` regular-file binding check. A fresh task-scoped clone of unchanged signed head `e1b85cfec954053d8d00a9bb61197a09ed7959d0` reproduced the same first failure under the same Python 3.10 command, proving that local APFS-sensitive baseline failure is independent of this Git trust-root patch; the comparison root was process/FD-free before removal and is now absent.
- A later duplicate reviewer root `/private/tmp/codex-private139-final-review.gWckvQ` was interrupted before consumption, its findings were not read or used, and it was marked non-counting. The root was bound as device `16777232`, inode `44296468`, uid `502`, gid `0`, mode `0700`; zero task processes and recursive file descriptors were observed before identity-safe cleanup, and the path is now absent.
