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
- The private authoring pool contains ten `joey-private-v1` exact values. The legacy catalog contains two envelopes: one GitHub-token value for `codex-workflow-hygiene`, plus 16 exact scanner-capturable generic-assignment values representing 37 source occurrences in the verified `portable-codex-runtime` master history.
- Trusted legacy entries store canonical Base64 as a reversible raw-equivalent together with counts, rules, and pinned master provenance. Metadata, logs, and evidence expose only IDs, digests, lengths, and counts.
- Four exact substring relationships required by the admitted portable history are confined to the same selected envelope. Raw and unembedded occurrence counts must both remain monotonic; cross-envelope and authoring overlaps remain invalid.
- The regular-file overlay rejects unsafe paths, symlinks, hard links, missing or non-regular inputs and targets, oversized sources, unsafe ownership or permissions, duplicate outputs, and observed path or file-identity drift. Source and target roots are pinned before pathname preflight; parent traversal is derived from the pinned descriptor, every descendant component uses `O_DIRECTORY|O_NOFOLLOW`, and pre/post secure reopen identity checks reject both symlink and ordinary-directory root replacement. Unavailable secure primitives fail closed.
- The generated review catalog is byte-equal to the trusted override, while the override source directory is excluded from release archives.
- The pre-existing session-retrospective JWT redaction fixture is assembled from three source fragments, preserving its runtime JWT coverage without leaving a credential-shaped token in the frozen repository head.
- `personal_codex/private-sync-manifest.json` installs the thin skill, and `personal_codex/AGENTS.md` contains one concise trigger rule.
- Private CI exercises the minimum Python 3.10 runtime on Ubuntu and macOS while preserving the aggregate `test` gate.

## Delivery State

- Public source commit `d8d310da7f40abc7ca12ea4839580fe00df7b84e` contains the complete synthetic-token facility, printable legacy correction, exhaustive pinned-master audit, required Claude runtime hardening, and the explicit-override fail-closed follow-up.
- The private release is generated from that complete public skill plus the fixed trusted catalog replacement; public and private catalogs are not unioned.
- Default-branch release packaging installs the generated review skill and thin fixture skill, never the private override source directory.

## Evidence

- Digest-bound recovery verified all 26 historical IDs against their pinned master Git objects. Admission retained the one hygiene value and 16 portable values totaling 37 source occurrences; nine portable IDs with no exact eligible scanner capture were excluded from the runtime catalog.
- The refreshed public parser accepted the migrated catalog as schema version 1 with pool `joey-private-v1`, 10 authoring values, 2 legacy envelopes, and 17 legacy values.
- Both pinned-master audits passed against the hygiene tip and portable runtime tip; emitted evidence contained IDs, rules, digests, lengths, and counts without raw or Base64 values.
- The real private source sync from public commit `d8d310da7f40abc7ca12ea4839580fe00df7b84e` completed, and `cmp` proved the generated catalog is byte-equal to the trusted override source.
- `/usr/bin/env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false python3 -m unittest discover -s tests -v` passed before review (`488` tests, `2` skipped). The override only neutralized Joey's global signing configuration for four ephemeral merge fixtures.
- The independent review gate identified an ancestor-directory TOCTOU gap in the first overlay implementation. Directory-fd traversal now protects both source reads and target writes; deterministic source-swap, target-swap, and missing-dirfd tests pass.
- The next independent pass identified an unbounded target read-back loop. Verification now reads at most `len(data) + 1` bytes and rejects excess content; its deterministic append simulation passes. The complete private suite then passed again (`492` tests, `2` skipped).
- A current-base independent pass then identified that resolving the complete overlay root inside the secure opener could follow a root symlink introduced after preflight. A follow-up pass proved that no-follow traversal alone could still reopen a replacement ordinary directory. The final opener pins the raw root descriptor before preflight, derives all I/O from it, and compares pre/post secure-reopen root identities; deterministic symlink, ordinary-directory, preflight, and post-binding source/target swaps all fail closed while recorded I/O remains bound to the original inode. A final target race finding added a write-complete content-identity snapshot, so a same-size overwrite after read-back cannot pass the later metadata checks. The complete private suite passed with the sandbox-only Git signing override used by its ephemeral merge fixtures (`499` tests, `2` skipped).
- `python3 -m unittest discover -s personal_codex/skills/review-orchestration-playbook/tests -p 'test_*.py' -v` passed (`679` tests, `11` skipped), including both explicit Claude override fail-closed paths found by the independent review gate.
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
- `tests/test_private_overlay_package.py`
