---
id: 20260822-rosc001
title: Review Orchestration Private Sync Contract
status: active
created: 2026-08-22
updated: 2026-08-24
branch: wip/review-orchestration-sync-contract
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/177
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
  so that scoped ownership is now complete. The tracked private companion
  temporarily retains the exact legacy review block while its source lock
  still selects the legacy canonical skill; the successful canonical source
  sync performs the final compact-guidance migration.

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
  drift between installed guidance and the canonical skill. That compact form
  is the post-sync state, not the pre-sync companion state.
- The Ubuntu platform-test job runs the installed canonical review skill's own
  test directory in addition to the private repository suite. The legacy
  source pin still contains contract assertions over the detailed global
  review block, so publishing the compact `AGENTS.md` before updating the
  canonical tree caused 17 CI-only failures even though all 1,972 repository
  tests passed locally.
- The sync engine now recognizes exactly two personal-guidance states. The
  legacy state binds the complete old review block by SHA-256
  `6d093c17f2bbcaef9a085937891f5e029044b10dace7e3e7972aebb819630a62`
  plus its complete old consent line; the current state binds the exact compact
  three-line block plus its complete current consent line. Missing, mixed,
  duplicated, half-migrated, or locally drifted states fail closed instead of
  being guessed or repaired piecemeal.
- Migration is triggered only by the exact authoritative canonical review sync
  rule and an immutable candidate/trees proof. Runtime policy binds reviewed
  candidate revision `cd5ccd2ddd2a0975db6c5286765d4aab838bc736`, its
  bounded raw commit payload, the approved repository tree
  `aef4bef7a45adab762a1b671da48fbc2d1f44064` and approved review subtree
  `6dab70713244598e3aaaa132eb082211b348bcdf`. Runtime recomputes the Git commit
  object ID from the payload to prove candidate `C`, parses its exact `tree T`,
  and, for nonlegacy activation, resolves the exact live or selected
  root-`T` ancestor revision's `skills/review-orchestration-playbook` path to
  prove `ST`. This is an offline immutable proof of `C -> T`; a post-squash
  checkout need not retain the candidate commit object itself. The exact legacy
  source pair continues to sync without migrating guidance even when candidate
  `C` and root tree `T` are unreachable and pruned; it still rejects a malformed
  or tampered offline `C -> T` proof. Activation requires either the approved
  repository tree itself or a locally complete, bounded ancestry that contains
  that tree while the live review subtree remains exact. The live source-lock
  SHA/tree, checkout, and manifest subtree are bound into receipts before any
  sync write; unknown, incomplete, non-descendant, missing/tampered candidate
  proof, or subtree-drifted sources fail closed.
- Checkout verification is a point-in-time proof of the exact immutable Git
  identities and local completeness. A sealed structured receipt replaces the
  former boolean and binds the live source-lock digest/pins, source-root,
  checkout, `.git`, and object-directory identities, detached `HEAD` and local
  config file identity/content/access policy, exact HEAD/tree, and the complete
  source-safety contract. Each receipt is captured across two full verification
  passes; the live source lock is reloaded before and after. Fresh receipts run
  after manifest/receipt construction, again inside `sync_sources` before its
  first write, and around the canonical install/AGENTS gate. Object deletion,
  checkout replacement, same-inode config mutation, or source-lock drift in
  those intervals is therefore rejected before the corresponding gate.
- After source activation, the descriptor-bound new review tree is committed
  first. A sealed install-migration receipt then binds the exact source
  migration receipt, prepared source manifest, final candidate manifest,
  pinned target parent/basename, installed root identity, and held descriptor;
  only then is one owner-owned, single-link, mode-0644 `AGENTS.md`
  candidate written and flushed. The prior file is revalidated, moved with
  no-replace semantics into an owner-private recovery scope, and revalidated
  again before the candidate is published with a second no-replace rename. The
  exact prior bytes remain a recovery artifact after success. The installed
  root descriptor, pinned parent, root identity, and exact candidate manifest
  remain live through migration and are revalidated immediately before and
  after AGENTS publication, including a final check directly before the second
  no-replace rename that publishes compact guidance. A root replacement,
  same-inode content mutation,
  forged installed manifest, or source/target receipt mismatch injected after
  tree install returns but before migration is rejected before AGENTS changes.
  These checks
  provide parent-controlled sequencing and bounded detection, not an atomic
  cross-path invariant against an active same-UID mutation between two system
  calls; a post-check failure can require recovery from the retained exact prior
  guidance. A current state is an inode-stable no-op with a final exact
  binding/content revalidation after classification.
- Personal-guidance revalidation protects three properties explicitly: object
  identity (`dev`, `ino`, and file type), content (bounded bytes, size, and
  digest), and access policy (`uid`, exact mode, and link count). Timestamp
  changes are hints that cause exactly one same-descriptor semantic reread and
  pathname revalidation; they are not mutation evidence. Missing paths,
  unreadable paths, other revalidation failures, and observed property
  mismatches remain distinct outcomes.
- Bare triple review authorizes one exact `@codex review` producer operation
  and the skill's single-owner, single-flight ambiguous-delivery recovery by
  repeating that exact POST for the same logical request. It does not authorize
  GitHub Actions mutation. An Actions rerun,
  dispatch, or reconciliation requires both repository-predeclared exact
  frozen-scope idempotent/reentrant inputs and separate current-task delivery or
  readiness authorization; branch, PR metadata, scope, and unrelated mutations
  remain outside that authority.
- The seven-repository default gate is intentionally absent from global
  personal guidance. Its scoped mother-repository `AGENTS.md` change is now on
  the default branch through `Joey-Tools/codex-workspace#12`; a child
  repository may repeat the exception when it must work outside the mother
  tree.

## Release Sequence

1. Merge the independent `codex-workspace` mother-repository `AGENTS.md`
   change so the scoped seven-repository gate is present on the default
   branch.
2. Merge this private sync-contract PR and complete its private-overlay release.
   While the source lock remains on the exact legacy pair, sync continues to
   install the legacy tree and keeps the legacy global guidance.
3. Merge the canonical `codex-review-workflows` PR that completes
   `20260822-ros001`. A same-tree squash commit is accepted without predicting
   its future SHA.
4. Force `scheduled-sync-release.yml`. Merge the generated source
   lock and overlay update after it proves the new canonical inventory and the
   private synthetic-token overlay.
5. Confirm the post-sync default-branch private release, run the local
   installer, and verify that the active review skill, reviewer role, and
   workspace helper come from that release.

This ordering installs the transition-aware private sync engine before the
canonical squash can be selected, while the exact legacy pair keeps the old
tree and guidance compatible until activation. Generated overlay files remain
source-sync owned and are not edited by hand in this companion PR.

If the canonical base moves, the final squash root can change without retaining
the old approved root in its ancestry; matching only the review subtree is not
sufficient. In that case rerun the Q44 merge-base and full delivery gate, derive
the new approved root/subtree anchors, merge and release that companion anchor
refresh, and only then squash the canonical PR. A final merge commit whose
bounded ancestry contains the approved root remains eligible without a refresh.

## Post-audit Migration Gate Remediation

- A clean-context companion audit identified four fail-closed gaps: `C` was
  journal-only provenance, installed-target identity was not explicitly coupled
  to the source migration receipt, checkout completeness could outlive its
  point-in-time proof, and base-move squash behavior lacked an explicit operator
  result.
- The candidate payload proof now survives an unreachable or pruned candidate
  commit while still proving the exact reviewed `C/T/ST` relation. Missing,
  malformed, tampered, wrong-C, wrong-T, and wrong-ST proofs fail before sync.
- Live admission remains intentionally narrow: exact root `T`, or a bounded
  full-DAG ancestry containing a commit with root `T`; unchanged `ST` alone is
  never sufficient. Exact/ancestry classification precedes the `T -> ST` path
  read, so a base-move squash whose clone has already pruned old `T` still
  reports stable `anchor-refresh-required` rather than a raw missing-object
  error. A legitimate merge commit that retains `T` in its ancestry remains
  accepted.
- The canonical delivery freeze selected signed candidate
  `cd5ccd2ddd2a0975db6c5286765d4aab838bc736`, root tree
  `aef4bef7a45adab762a1b671da48fbc2d1f44064`, and review subtree
  `6dab70713244598e3aaaa132eb082211b348bcdf`. The companion stores that exact
  commit object's 856 raw payload bytes as strict Base64; runtime recomputation
  binds the payload back to the frozen candidate before any migration write.
- Final focused validation counts are recorded after the post-audit fixes in
  the Evidence section. The broader repository suite remains parent-owned final
  delivery evidence and is not inferred from these focused runs.

## Current State

- The companion implementation now stages global personal routing safely: the
  tracked file matches the exact legacy source pin, while the sync contract and
  focused tests own the source-identity-gated final migration after canonical
  source sync. The companion may therefore merge and release before the
  canonical squash without activating compact guidance early.
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
- The final transition follow-up passes the 271-test source-sync module and the
  1,983-test private repository suite on Python 3.13.0, with four conditional
  skips in the repository suite. The 2,965-test pinned canonical suite completed
  2,949 tests successfully with 15 conditional skips in the restricted host
  sandbox; its sole failure was the expected nested macOS `sandbox-exec` denial,
  and that exact test passed in the approved unrestricted rerun.

## Next Steps

- Merge and release this transition-aware companion, then complete the
  canonical squash and execute the generated-sync activation sequence above.

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
- Transition-focused tests cover locked and unlocked authoritative sync,
  legacy no-migration, approved same-tree squash, bounded merge descendants,
  base-move anchor refresh, non-descendants, wrong SHA/tree, changed subtrees,
  missing ancestry objects, sealed checkout proof, second-verification object
  deletion and checkout replacement before all writes,
  tree-before-guidance ordering, exact-state failure, validation-before-
  migration, installed-root replacement and content mutation before guidance,
  successful install-validation/publication/revalidation ordering,
  unrelated-rule isolation, inode-stable idempotence, concurrent
  target replacement inside the publication primitive, current-state no-op
  drift, failed publication, and retry (pass).
- Post-audit migration-gate validation ran the source-lock and source-sync
  modules together: 350 tests passed in 54.734 seconds with one conditional
  skip. This includes pruned offline candidate/root proof, explicit
  `anchor-refresh-required`, structured safety-state drift, exact installed
  manifest/identity coupling, and final-prepublication tree-drift regressions.
- The final fresh read-only migration-gate audit reported `No findings.` after
  the legacy unreachable-tree and base-move missing-tree ordering fixes.
- Property-scoped migration tests cover timestamp-only `utime`, a synthetic
  materialization hint, bounded persistent timestamp churn, content mutation,
  inode replacement, uid/mode/link-count drift, and distinct missing,
  unreadable, and failed pathname revalidation outcomes (pass).
- Cross-policy guidance assertions bind same-logical-request ambiguous-delivery
  recovery, the absence of bare-review Actions authority, and the required
  repository-policy plus current-task authorization gates (pass).
- The previously failing pinned canonical contract test
  `test_canonical_claude_auth_control_plane_is_not_helper_broker` passes with
  the exact legacy pre-sync guidance restored.
- Independent post-fix dirty-delta audit (`No findings.`)
- `ruff 0.13.2 check scripts/sync_private_overlay_sources.py
  tests/test_private_overlay_sync.py` (pass)
- `$project-journal` `validate` for the companion repository (pass)
- `python3 -B -m unittest -q tests.test_private_overlay_sync` (`298` tests)
- `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p
  'test_*.py' -q` (`2,005` tests, `4` conditional skips)
- `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s
  personal_codex/skills/review-orchestration-playbook/tests -p 'test_*.py' -q`
  (`2,965` tests: `2,949` pass, `15` conditional skips, one restricted-host
  nested-`sandbox-exec` denial); the exact denied test passes outside the host
  sandbox.
