---
id: 20260715-stf001
title: Synthetic Token Fixtures Private Overlay
status: completed
created: 2026-07-15
updated: 2026-07-16
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
- The private authoring pool contains ten `joey-private-v1` exact values. The legacy catalog contains three envelopes: one GitHub-token value and one JWT value for `codex-workflow-hygiene`, plus 16 exact scanner-capturable generic-assignment values representing 37 source occurrences in the verified `portable-codex-runtime` master history.
- Trusted legacy entries store canonical Base64 as a reversible raw-equivalent together with counts, rules, and pinned master provenance. Metadata, logs, and evidence expose only IDs, digests, lengths, and counts.
- Four exact substring relationships required by the admitted portable history are confined to the same selected envelope. Raw and unembedded occurrence counts must both remain monotonic; cross-envelope and authoring overlaps remain invalid.
- The secure review rule is a one-rule trust barrier: every plain sync and retired-target cleanup finishes first, then the repository root is pinned once and the private catalog is read through descriptor-relative no-follow traversal.
- The public review skill is prepared in an external temporary directory. A randomized current-user-only `0700` recovery scope is then created under `.codex-tmp/private-overlay-recovery`, and a descriptor-relative recursive copy writes the private catalog bytes directly at the fixed overlay path. The repo-side candidate never contains a staged public catalog and never performs a file-level public-to-private replacement.
- Before either live mutation, the recovery scope must contain the exact expected names, retained-entry count must fit its fixed bound, every registered inode must remain pinned, and the candidate's private bytes and mode must match exactly. The prior whole-tree target is moved into recovery with no-replace semantics and registered immediately; the prepared whole-tree candidate is then installed with a second no-replace rename. Installed bytes, inode bindings, target-parent lineage, and retained recovery evidence are validated before the transaction is marked complete. A completed scope only closes capabilities, and all global validation runs before the secure commit.
- Failure handling is forward-only: recovery basenames are never moved back into the live target, and an installed candidate is never moved back into recovery. The command reports every candidate/prior-target location that still matches a pinned inode and otherwise reports the binding as unknown. Portable macOS/Linux rename APIs resolve each source basename relative to a pinned parent descriptor, but cannot atomically require that basename to still name the previously pinned source-entry inode. The randomized `0700` scope excludes other UIDs, but a concurrent same-UID rebind of the first source can move the prior target to an attacker-selected basename, while a rebind of the second can place an untrusted tree at the live target. Identity checks detect both cases and fail without publishing success; the helper retains known evidence but intentionally neither guesses an unknown location nor performs automatic restoration.
- The generated review catalog is byte-equal to the trusted override, while the override source directory is excluded from release archives.
- The pre-existing session-retrospective JWT redaction fixture is assembled from three source fragments, preserving its runtime JWT coverage without leaving a credential-shaped token in the frozen repository head.
- `personal_codex/private-sync-manifest.json` installs the thin skill, and `personal_codex/AGENTS.md` contains one concise trigger rule.
- Private CI exercises the minimum Python 3.10 runtime on Ubuntu and macOS while preserving the aggregate `test` gate.

## Delivery State

- Public source commit `4da59bf424f941f61bf36fd1f9871ad09dff8d3a` contains the complete synthetic-token facility, printable legacy correction, exhaustive pinned-master audit, required Claude runtime hardening, the explicit-override fail-closed follow-up, the audited JWT legacy migration, the merged per-model-attempt Claude OAuth freshness fix, and the reviewed Node extra-CA support.
- The private release is generated from that complete public skill plus the fixed trusted catalog replacement; public and private catalogs are not unioned.
- Default-branch release packaging installs the generated review skill and thin fixture skill, never the private override source directory.

## Evidence

- Digest-bound recovery verified all 26 historical IDs against their pinned master Git objects. Admission retained the one hygiene value and 16 portable values totaling 37 source occurrences; nine portable IDs with no exact eligible scanner capture were excluded from the runtime catalog.
- The refreshed public parser accepted the migrated catalog as schema version 1 with pool `joey-private-v1`, 10 authoring values, 3 legacy envelopes, and 18 legacy values representing 39 source occurrences.
- All three pinned-master audits passed against the hygiene tip and portable runtime tip; emitted evidence contained IDs, rules, digests, lengths, and counts without raw or Base64 values.
- The real private source sync from public commit `4da59bf424f941f61bf36fd1f9871ad09dff8d3a` completed after private sync commit `e484d46df985f4a58209f82d5d14f2d77a729e50` was merged into the feature branch. It produced no tracked source drift, and `cmp` proved the generated catalog is byte-equal to the trusted override source.
- `/usr/bin/env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false python3 -m unittest discover -s tests -v` passed before review (`488` tests, `2` skipped). The override only neutralized Joey's global signing configuration for four ephemeral merge fixtures.
- The independent review gate identified an ancestor-directory TOCTOU gap in the first overlay implementation. Directory-fd traversal now protects both source reads and target writes; deterministic source-swap, target-swap, and missing-dirfd tests pass.
- The next independent pass identified an unbounded target read-back loop. Verification now reads at most `len(data) + 1` bytes and rejects excess content; its deterministic append simulation passes. The complete private suite then passed again (`492` tests, `2` skipped).
- Successive independent passes identified ancestor/root rebinding, unbounded read-back, final-EOF mutation, destination overwrite, stale-name cleanup, rollback-through-mutable-basename, late registry validation, and post-commit failure windows in earlier implementations. Secure-sync v2 removes the file-level transaction entirely: public preparation stays outside the repository, the private value is created directly during descriptor-relative candidate construction, and only two whole-tree no-replace moves can touch live state.
- Deterministic focused coverage now proves direct private substitution, external public preparation and cleanup before live mutation, plain/retired cleanup ordering, canonical validator ordering, exact recovery registry and capacity before both live mutations, repo-root/target-parent/scope-container rebinding, source and destination basename races, bounded reads, byte/mode equality, forward-only `BaseException` handling, no recovery-to-live restoration, and a close-only post-commit scope. The focused suite passes 95 tests on macOS.
- `python3 -m unittest discover -s personal_codex/skills/review-orchestration-playbook/tests -p 'test_*.py' -v` passed (`709` tests, `11` skipped), including both explicit Claude override fail-closed paths, the merged per-attempt OAuth freshness coverage, and Node extra-CA support.
- Final integration validation passed the complete private suite (`532` tests, `2` skipped), the complete generated review-helper suite (`709` tests, `11` skipped), and the waited-delivery contract suite (`40` tests). Both skill validators passed when the Joey wrapper was run inside the task-scoped `uv` environment that supplies PyYAML.
- The added public-compatible JWT envelope and the existing GitHub-token envelope passed local pinned-master audits. The portable envelope passed against the original BL Mac checkout at its fixed master tip; the temporary private helper copy was removed immediately after the raw-free audit.
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
