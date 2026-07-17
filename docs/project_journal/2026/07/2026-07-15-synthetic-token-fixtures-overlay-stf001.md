---
id: 20260715-stf001
title: Synthetic Token Fixtures Private Overlay
status: completed
created: 2026-07-15
updated: 2026-07-17
branch: codex/synthetic-token-v1-private
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/88
supersedes: []
superseded_by:
---

# Synthetic Token Fixtures Private Overlay

## Summary

- Added Joey's wholesale private catalog replacement and a restricted regular-file overlay mechanism for the review helper.
- Synchronized the complete public review skill after both the synthetic-token facility and Claude runtime hardening reached public master, then replaced only the fixed catalog target.
- Routed the public thin `synthetic-token-fixtures` skill into the private overlay without duplicating templates or catalog literals.

## Current State

- The review sync rule first copies the complete public skill and then replaces only `scripts/review_runtime/synthetic-token-catalog.json` with a helper-relative private catalog.
- The private authoring pool now contains 52 `joey-private-v3` exact values: ten active IDs for each supported role, plus the existing expired access-token and consumed refresh-token fixtures. The legacy catalog remains unchanged with two envelopes: one GitHub-token value for `codex-workflow-hygiene`, plus 16 exact scanner-capturable generic-assignment values representing 37 source occurrences in the verified `portable-codex-runtime` master history. See `2026-07-17-synthetic-refresh-capacity-stf002.md` for the capacity update.
- Trusted legacy entries store canonical Base64 as a reversible raw-equivalent together with counts, rules, and pinned master provenance. Metadata, logs, and evidence expose only IDs, digests, lengths, and counts.
- Four exact substring relationships required by the admitted portable history are confined to the same selected envelope. Raw and unembedded occurrence counts must both remain monotonic; cross-envelope and authoring overlaps remain invalid.
- The secure review rule is a one-rule trust barrier: every plain sync and retired-target cleanup finishes first, then the repository root is pinned once and the private catalog is read through descriptor-relative no-follow traversal.
- The public review skill is prepared in an external temporary directory. A randomized current-user-only `0700` recovery scope is then created under `.codex-tmp/private-overlay-recovery`, and a descriptor-relative recursive copy writes the private catalog bytes directly at the fixed overlay path. The repo-side candidate never contains a staged public catalog and never performs a file-level public-to-private replacement.
- The external preparation path is created relative to a pinned system-temporary-directory descriptor. Its container and prepared root remain pinned from construction through the second copy, and both passes bind every directory and regular file to a bounded exact manifest containing type, inode identity, size, mode, and SHA-256 evidence. Canonical semantic policy is evaluated on those exact copied bytes instead of reopening mutable pathnames.
- The external public-only preparation tree is never automatically deleted. Immediately before the first live rename, its parent, container, and exact manifest are checked again through pinned descriptors; the randomized `0700` container is then returned and printed as a retained absolute path. This deliberately trades bounded system-temporary-directory retention for avoiding a non-portable compare-and-delete race in which a same-UID process could replace a validated basename before `unlink` or `rmdir`. Private catalog bytes are created only in the repo-side candidate and never enter the retained external tree.
- Before either live mutation, the recovery scope must contain the exact expected names, retained-entry count must fit its fixed bound, every registered inode must remain pinned, and the candidate's private bytes and mode must match exactly. The prior whole-tree target is moved into recovery with no-replace semantics and registered immediately; the prepared whole-tree candidate is then installed with a second no-replace rename. Installed bytes, inode bindings, target-parent lineage, and retained recovery evidence are validated before the transaction is marked complete. A completed scope only closes capabilities, and all global validation runs before the secure commit.
- Failure handling is forward-only: recovery basenames are never moved back into the live target, and an installed candidate is never moved back into recovery. The command includes the original transaction error, distinguishes root-identity evidence from exact-content evidence, and explicitly marks candidate and prior-target contents unverified after a failed transaction. Portable macOS/Linux rename APIs resolve each source basename relative to a pinned parent descriptor, but cannot atomically require that basename to still name the previously pinned source-entry inode. The randomized `0700` scope excludes other UIDs, but a concurrent same-UID rebind of the first source can move the prior target to an attacker-selected basename, while a rebind of the second can place an untrusted tree at the live target. Identity checks detect both cases and fail without publishing success; the helper retains known evidence but intentionally neither guesses an unknown location nor performs automatic restoration.
- The generated review catalog is byte-equal to the trusted override, while the override source directory is excluded from release archives.
- The pre-existing session-retrospective JWT redaction fixture is assembled from three source fragments, preserving its runtime JWT coverage without leaving a credential-shaped token in the frozen repository head.
- `personal_codex/private-sync-manifest.json` installs the thin skill, and `personal_codex/AGENTS.md` contains one concise trigger rule.
- Private CI exercises the minimum Python 3.10 runtime on Ubuntu and macOS while preserving the aggregate `test` gate.

## Delivery State

- Public source commit `8c095454d2d5cb25b6a2c1fb544de5e7487ba423` contains the complete synthetic-token facility, printable legacy correction, exhaustive pinned-master audit, required Claude runtime hardening, the explicit-override fail-closed follow-up, the merged per-model-attempt Claude OAuth freshness fix, the reviewed Node extra-CA support, and retirement of the temporary JWT migration envelope after the historical fixture was split.
- The private release is generated from that complete public skill plus the fixed trusted catalog replacement; public and private catalogs are not unioned.
- Default-branch release packaging installs the generated review skill and thin fixture skill, never the private override source directory.

## Evidence

- Digest-bound recovery verified all 26 historical IDs against their pinned master Git objects. Admission retained the one hygiene value and 16 portable values totaling 37 source occurrences; nine portable IDs with no exact eligible scanner capture were excluded from the runtime catalog.
- The refreshed public parser accepts the catalog as schema version 1 with pool `joey-private-v1`, 10 authoring values, 2 legacy envelopes, and 17 legacy values representing 38 source occurrences.
- All three original pinned-master audits passed against the hygiene tip and portable runtime tip; emitted evidence contained IDs, rules, digests, lengths, and counts without raw or Base64 values.
- The real private source sync from public commit `4da59bf424f941f61bf36fd1f9871ad09dff8d3a` completed after private sync commit `e484d46df985f4a58209f82d5d14f2d77a729e50` was merged into the feature branch. A post-review rerun against all strict-ready canonical mirrors emitted the repo recovery path plus the intentional external retained path and produced no additional tracked drift. `cmp` proved the generated catalog is byte-equal to the trusted override source and the retained external catalog is byte-equal to the public catalog.
- `/usr/bin/env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false python3 -m unittest discover -s tests -v` passed before review (`488` tests, `2` skipped). The override only neutralized Joey's global signing configuration for four ephemeral merge fixtures.
- The independent review gate identified an ancestor-directory TOCTOU gap in the first overlay implementation. Directory-fd traversal now protects both source reads and target writes; deterministic source-swap, target-swap, and missing-dirfd tests pass.
- The next independent pass identified an unbounded target read-back loop. Verification now reads at most `len(data) + 1` bytes and rejects excess content; its deterministic append simulation passes. The complete private suite then passed again (`492` tests, `2` skipped).
- Successive independent passes identified ancestor/root rebinding, unbounded read-back, final-EOF mutation, destination overwrite, stale-name cleanup, rollback-through-mutable-basename, late registry validation, and post-commit failure windows in earlier implementations. Secure-sync v2 removes the file-level transaction entirely: public preparation stays outside the repository, the private value is created directly during descriptor-relative candidate construction, and only two whole-tree no-replace moves can touch live state.
- Final secure-sync review also identified path-based semantic validation, Python 3.10 recovery-message loss, mutable external cleanup, root-only recovery overclaims, cleanup-authority laundering after partial copies, and an unpinned temporary-container resolution window. A subsequent exact-range review found that even manifest-bound pathname deletion remained racy, Python 3.10 could still lose `KeyboardInterrupt`/`SystemExit` recovery paths, and wide trees accumulated descriptors by entry count. The final implementation uses byte-bound policy checks, descriptor-created and creation-identity-bound temporary containers, exact two-pass manifests, conservative unverified recovery wording, non-destructive external retention, Python 3.10 stderr fallback reporting, and per-subtree descriptor lifetimes bounded by tree depth.
- The final independent security rereview reported no P0-P2 findings after verifying the non-destructive retention model, both post-`mkdir` interrupt windows, and depth-bounded descriptors; it reran all 125 focused sync/release tests successfully.
- Deterministic focused coverage now proves direct private substitution, public-only external preparation and retention before live mutation, plain/retired cleanup ordering, canonical validator ordering, exact recovery registry and capacity before both live mutations, repo-root/target-parent/scope-container rebinding, source and destination basename races, bounded reads, byte/mode equality, forward-only `BaseException` handling, Python 3.10 fallback path reporting, no recovery-to-live restoration, and a close-only post-commit scope. It also covers swap-and-restore races, transient add/remove, partial preparation failures, pre-pin symlink rebinding, post-`mkdir` interrupts, initial-manifest injection, final retention-manifest rebinding, and a low-`RLIMIT_NOFILE` wide-tree regression. The focused suite passes 125 tests on macOS.
- `python3 -m unittest discover -s personal_codex/skills/review-orchestration-playbook/tests -p 'test_*.py' -v` passed (`709` tests, `11` skipped), including both explicit Claude override fail-closed paths, the merged per-attempt OAuth freshness coverage, and Node extra-CA support.
- Final integration validation passed the complete private suite (`563` tests, `2` skipped), the complete generated review-helper suite (`709` tests, `11` skipped), and the waited-delivery contract suite (`40` tests). Both skill validators passed when the Joey wrapper was run inside the task-scoped `uv` environment that supplies PyYAML.
- The temporary public-compatible JWT envelope passed its original pinned-master audit and was retired after the historical fixture no longer required it. The remaining GitHub-token envelope and portable envelope retain their pinned-master provenance; the portable audit used the original BL Mac checkout at its fixed master tip, and the temporary private helper copy was removed immediately after the raw-free audit.
- Scheduled sync run `29512291896` proved the cross-repository retirement contract by failing closed when the refreshed parser encountered the stale private JWT rule. Removing that single private envelope keeps the wholesale override compatible without restoring parser support for a retired rule.
- Post-retirement validation passed the complete private suite (`563` tests, `2` skipped), the complete generated review-helper suite (`705` tests, `5` skipped), both affected skill validators, focused catalog tests, Python compilation, Ruff `0.13.2`, project-journal validation, package build and verification, catalog CLI validation, and `git diff --check`.
- `python3 -m py_compile`, generated-skill `compileall`, Ruff checks, both skill validators, `actionlint .github/workflows/ci.yml`, catalog validation, and staged/unstaged `git diff --check` passed. Ruff format validation passed for private-owned Python; a whole generated-tree format probe identified 15 byte-for-byte upstream files and was intentionally not applied.
- Package build and `private_overlay_release.py verify-package` passed; archive inspection found both generated skills and the generated catalog while excluding the private override source directory.

## Files

- `scripts/sync_private_overlay_sources.py`
- `personal_codex/private-overrides/review-orchestration-playbook/synthetic-token-catalog.json`
- `personal_codex/private-sync-manifest.json`
- `personal_codex/AGENTS.md`
- `.github/workflows/ci.yml`
- `README.md`
- `personal_codex/skills/review-orchestration-playbook/**`
- `personal_codex/skills/synthetic-token-fixtures/**`
- `tests/test_private_overlay_sync.py`
- `tests/test_private_synthetic_catalog.py`
- `tests/test_private_overlay_package.py`
