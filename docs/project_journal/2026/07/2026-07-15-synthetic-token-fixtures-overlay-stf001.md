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
- The regular-file overlay rejects unsafe paths, symlinks, hard links, missing or non-regular inputs and targets, oversized sources, unsafe ownership or permissions, duplicate outputs, and observed path or file-identity drift. Source and target roots plus every descendant directory are pinned before pathname preflight, all file I/O uses the pinned final-parent descriptor, visible-file reads require nonblocking no-follow opens, and complete raw-path chain rechecks reject symlink or ordinary-directory replacement. Target installation uses a random exclusive temporary inode and keeps the expected bytes, complete staging chain, and file descriptor pinned through final installation. The final directory move uses platform-backed no-replace semantics with pinned staging-container and target-parent descriptors; the prior target's type and inode identity stay pinned across the backup transaction, including `BaseException` recovery, and visible target bytes and inode bindings are verified before recovery handoff. Because portable POSIX APIs cannot atomically unlink a basename only if it still names a specific inode, the transaction never deletes the old target. It retains the verified backup in a current-user-only, Git-ignored `.codex-tmp/private-overlay-recovery` scope, reports only the bounded repo-relative recovery path, and fails closed when the recovery root reaches 64 entries. Unknown or rebound state is likewise retained and fails closed. Unsupported secure primitives fail closed before target mutation. Git checkouts cannot encode `0600`, so the trusted source contract is current-user ownership with no group/other write bits; generated catalogs are installed as exact mode `0644`.
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
- A current-base independent pass then identified that resolving the complete overlay root inside the secure opener could follow a root symlink introduced after preflight. Follow-up passes proved that no-follow root traversal alone could still reopen an ordinary replacement directory, that ordinary descendants needed equivalent identity binding, and that an in-place target writer retained a final-EOF race. Final-install reviews then found that closing those bindings before the directory move reopened a mutation window, plain rename could overwrite an unknown candidate, split install/validation handlers could leave an unverified live target after an asynchronous exception, and check-then-unlink cleanup could delete a rebound basename. The final implementation retains the complete chain and file binding across one stateful install transaction, uses macOS `renameatx_np(RENAME_EXCL)` or Linux `renameat2(RENAME_NOREPLACE)`, rolls an expected but unverified candidate back into recovery before restoring the old target, and retains rather than deletes the verified backup. Deterministic tests reject staged-file and staging-root mutation, restore the prior target after post-install mutation and both backup/final-rename `KeyboardInterrupt` windows, retain candidate and backup when an unknown target appears, preserve pre-backup swaps and post-backup probe failures without deletion, fail before mutation when no-replace support is unavailable, reject blocking FIFO and missing-nonblocking capability paths, preserve rebound staging state, bound recovery entries, exclude recovery state from Git and release packages, and verify the Darwin/Linux no-replace ABI and errno mapping. macOS CI now runs this focused suite under Python 3.10. `python3 -m unittest discover -s tests -p 'test_private_overlay_sync.py' -v` passed (`86` tests).
- `python3 -m unittest discover -s personal_codex/skills/review-orchestration-playbook/tests -p 'test_*.py' -v` passed (`709` tests, `11` skipped), including both explicit Claude override fail-closed paths, the merged per-attempt OAuth freshness coverage, and Node extra-CA support.
- Final integration validation passed the complete private suite (`523` tests, `2` skipped), the complete generated review-helper suite (`709` tests, `11` skipped), and the waited-delivery contract suite (`40` tests). Both skill validators passed when the Joey wrapper was run inside the task-scoped `uv` environment that supplies PyYAML.
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
