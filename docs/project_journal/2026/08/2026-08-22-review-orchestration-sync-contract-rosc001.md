---
id: 20260822-rosc001
title: Review Orchestration Private Sync Contract
status: active
created: 2026-08-22
updated: 2026-08-24
branch: wip/review-orchestration-sync-contract
pr:
supersedes: []
superseded_by:
---

# Review Orchestration Private Sync Contract

## Summary

- Align the private-overlay source-sync contract with the canonical review
  simplification recorded by `20260822-ros001` in
  `Joey-Tools/codex-review-workflows`.
- Keep the private synthetic-token catalog as the only substantive private
  overlay inside the canonical review skill.
- Move review algorithms out of the global personal `AGENTS.md`; retain only
  cross-repository routing and scoped reviewer authorization. Keep the
  seven-repo `skill-repo-codex-gate` exception in the `codex-workspace`
  mother-repository guidance or each affected repository, not globally. The
  mother-repository guidance landed through `Joey-Tools/codex-workspace#12`,
  so that scoped ownership is now complete.

## Decisions and Rationale

- The sync rule no longer rewrites old GitHub receipt, history-sampling, or
  thumbs-up-only wording in `github-pr-probes.md` and `test_contracts.py`.
  Those exact replacements coupled the private release to superseded public
  prose and would make a canonical policy-only edit fail before it could be
  reviewed or released.
- `github-codex-evidence-authority.md`, `local-codex-lane.md`,
  `review-workspace.md`, `review_workspace.py`, and its focused test are
  required private-release inventory. This keeps each active review authority
  and the new independent-clean-workspace implementation from being omitted
  silently during source sync.
- `github-codex-terminal-carriers-v1.json` and
  `test_github_terminal_carriers.py` are also exact required inventory. The
  private release must carry the canonical terminal-carrier grammar and its
  focused contract test together.
- `test_github_recovery_contracts.py`, `test_local_codex_lane_contracts.py`,
  and `test_trusted_mac_gate_manifest.py` are exact required inventory as
  well. They protect the recovery, peer local-adapter, and trusted macOS gate
  contracts that accompany the simplified review policy.
- `base-only-retarget-state-machine.json` is no longer required inventory.
  The file may remain temporarily as deprecated source history, but a future
  canonical removal must not block private sync.
- Obsolete public review artifacts and the unlinked compatibility reference are
  no longer required inventory. Internal compatibility implementation and tests
  stay exact-inventory protected, while canonical whole-directory sync removes
  stale copies of deleted public surfaces from the target.
- The retained internal supervisor inventory includes the canonical tracked
  test fixture `internal_supervisor_child_fixture.py`. This preserves real
  argv/child-process test coverage without restoring the public launcher,
  README, helper contract, or any global `AGENTS.md` navigation to the retired
  utility.
- The live generated overlay may omit those six final canonical additions
  only while `private-overlay-source-lock.json` binds
  `Joey-Tools/codex-review-workflows` to SHA
  `c8df0f5d17e93a7b22d5fe5294baf9884ab2ba51` and tree
  `e4081b640384cd885783637fa5aad8d21d4499d5`. Any other pin requires all six
  live files. This test-only transition rule does not weaken canonical staging
  validation, which always requires the complete final inventory.
- The obsolete `TOOL_REL` layout rewrite is removed because the canonical
  skill no longer carries that legacy launcher variable. Keeping it as a
  required replacement would reject the new canonical tree before staging.
  Common Joey text personalization and the exact-byte private synthetic-token
  catalog overlay remain unchanged.
- The global personal guidance names review shapes and consent boundaries but
  delegates adapter choice, workspace preparation, Claude runtime validation,
  GitHub evidence and recovery, and PR-readiness behavior to
  `$review-orchestration-playbook`. One source of algorithmic policy reduces
  drift between installed guidance and the canonical skill.
- The seven-repository default gate is intentionally absent from global
  personal guidance. Its scoped mother-repository `AGENTS.md` change is now on
  the default branch through `Joey-Tools/codex-workspace#12`; a child
  repository may repeat the exception when it must work outside the mother
  tree.

## Release Sequence

1. Merge the independent `codex-workspace` mother-repository `AGENTS.md`
   change so the scoped seven-repository gate is present on the default
   branch.
2. Merge the canonical `codex-review-workflows` PR that completes
   `20260822-ros001`.
3. Refresh this companion branch against the resulting canonical identity,
   run focused and repository validation, and merge this private sync-contract
   PR.
4. Complete the private-overlay release for this companion commit so the
   default branch has the updated sync engine and inventory contract.
5. Only then force `scheduled-sync-release.yml`. Merge the generated source
   lock and overlay update after it proves the new canonical inventory and the
   private synthetic-token overlay.
6. Confirm the post-sync default-branch private release, run the local
   installer, and verify that the active review skill, reviewer role, and
   workspace helper come from that release.

This ordering avoids asking the old private sync engine to interpret the new
canonical layout and prevents generated overlay files from being edited by
hand in this companion PR.

## Current State

- The companion implementation is complete and updates only the sync contract,
  its focused tests, global personal routing, and this journal.
- Cross-repository ownership is complete: `Joey-Tools/codex-workspace#12`
  squash-merged the scoped `AGENTS.md` rule as
  `1c3b9c9662ef8c3ed5ddad2c3e272fb6a0eec526`.
- Generated `personal_codex/skills/review-orchestration-playbook/**` and
  `personal_codex/agents/reviewer.toml` remain source-sync owned and are not
  modified here.
- The focused source-sync inventory and stale-surface removal regressions pass.
  This final inventory follow-up also covers the terminal-carrier pair, the
  three focused recovery/local-lane/trusted-gate contract tests, the canonical
  internal supervisor child fixture, and the absence of global retired-helper
  navigation. The live-tree allowance is bound to the exact prior source pin;
  every other source identity requires the complete new inventory.
- A clean-context audit found that the old required `TOOL_REL` replacement
  would reject the new canonical tree even though the synthetic tests still
  injected that retired token. The replacement and its artificial assertions
  are removed; the post-fix audit reports no findings.
- The post-fix 260-test source-sync module and 1,972-test private repository
  suite pass on Python 3.13.0, with four conditional skips in the repository
  suite. These gates must run again if the companion branch changes while it
  is refreshed for delivery.

## Next Steps

- Complete the canonical merge, then execute the remaining release sequence
  above.

## Evidence

- `Joey-Tools/codex-review-workflows` journal `20260822-ros001`
- `Joey-Tools/codex-workspace#12`, squash merge
  `1c3b9c9662ef8c3ed5ddad2c3e272fb6a0eec526`
- `scripts/sync_private_overlay_sources.py`
- `tests/test_private_overlay_sync.py`
- `personal_codex/AGENTS.md`
- Targeted `tests.test_private_overlay_sync.PrivateOverlaySyncTests` checks for
  self-contained canonical sync, required policy inventory, exact internal
  compatibility inventory, stale public-surface removal, and canonical global
  routing, including exact-old-pin transition binding (`8` final-delivery
  regressions, pass)
- Canonical target-content validation against the current `20260822-ros001`
  worktree, including the three new focused contract tests (pass)
- Independent post-fix dirty-delta audit (`No findings.`)
- `ruff 0.13.2 check scripts/sync_private_overlay_sources.py
  tests/test_private_overlay_sync.py` (pass)
- `$project-journal` `validate` for the companion repository (pass)
- `python3 -B -m unittest -q tests.test_private_overlay_sync` (`260` tests)
- `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p
  'test_*.py' -q` (`1,972` tests, `4` conditional skips)
