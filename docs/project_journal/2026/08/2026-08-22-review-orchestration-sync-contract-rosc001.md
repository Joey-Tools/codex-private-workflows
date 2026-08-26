---
id: 20260822-rosc001
title: Review Orchestration Private Sync Contract
status: completed
created: 2026-08-22
updated: 2026-08-26
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
- The first scheduled source sync after the canonical merge exposed an exact
  fail-closed frontmatter mismatch: the public change-delivery description now
  says `Run a local delivery gate`, while the private specialization still
  required the retired `pre-commit` phrase. The private rule now transforms the
  shared legacy/current top-level description prefix exactly once through a
  frontmatter-field-scoped transform shared by the plain and descriptor-bound
  copy paths. The transform accepts only a closed flat opening YAML mapping:
  every semantic field uses one unique unquoted, unindented ASCII simple key,
  and every value is either a single-line JSON double-quoted string or a
  single-line scalar from the declared plain-character set. Only exact empty
  lines and column-zero comments are ignored. Quoted keys, explicit keys,
  merge keys, YAML 1.1/1.2 implicit bool/null keys, nested or block values,
  indented comments, decoded line separators, and duplicate keys fail closed
  before transformation. It changes only the unique `description` field,
  leaves body and cross-file wording untouched, and validates the private
  legacy/current postcondition from the copied bytes. This keeps the exact
  legacy source lock replayable, preserves the current landing-commit-then-
  frozen-review sequence, and keeps the Joey-specific specialization mandatory
  instead of weakening it to an optional replacement.
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
- Every locked authoritative review sync now classifies `personal_codex/AGENTS.md`
  through one descriptor-bound snapshot before any sync implementation can
  write a target. A sealed plan couples that file binding to the validated
  source pin and source-migration receipt: the exact legacy source accepts only
  exact legacy guidance and keeps it unchanged; an approved candidate or
  descendant accepts exact legacy and migrates it after skill installation, or
  accepts exact current as an inode-stable idempotent no-op. Mixed, compacted,
  byte-drifted, missing, or unsafe states fail before target replacement.
  Preflight opens the already-existing `personal_codex` directory without
  creation or symlink following and requires current-user ownership plus no
  group/world write permission. Its held identity and access policy remain
  part of every later plan revalidation; a missing parent/file or a `0022`
  permission-bit drift cannot be repaired or accepted implicitly.
  After every bounded AGENTS reread, sync repeats the parent identity/access
  check and its repo-relative named-root match before the final repository-root
  check, so post-read directory replacement or mode drift also fails closed.
  Legacy/current no-op plans retain the original descriptor, bytes, and access
  policy through skill installation and revalidate them again at the final
  sync boundary. Content, object replacement, mode, or link-policy drift after
  preflight therefore fails without overwriting the concurrent state, while
  the already committed skill replacement keeps its ordinary recovery copy.
- Migration is triggered only by the exact authoritative canonical review sync
  rule and an immutable candidate/trees proof. Runtime policy binds reviewed
  candidate revision `b160b6fd0b3a0da4e25a74fbdb6bd3750c7a9bb2`, its
  bounded raw commit payload, the approved repository tree
  `69475da88941082e2557ca875c82e4a0d38a173f` and approved review subtree
  `7b08cb84a07c4a846d26ecde538c740e7772f9e7`. Runtime recomputes the Git commit
  object ID from the payload to prove candidate `C`, parses its exact `tree T`,
  and, for nonlegacy activation, resolves the exact live or selected
  root-`T` ancestor revision's `skills/review-orchestration-playbook` path to
  prove `ST`. This is an offline immutable proof of `C -> T`; a post-squash
  checkout need not retain the candidate commit object itself. The exact legacy
  source pair continues to sync without migrating guidance even when candidate
  `C` and root tree `T` are unreachable and pruned; it still rejects a malformed
  or tampered offline `C -> T` proof. Activation requires either the approved
  repository tree itself or a locally complete, bounded full-DAG ancestry that
  contains that tree. The selected anchor's review subtree must equal `ST`,
  while a later descendant may evolve the live review subtree. The exact live
  subtree must instead agree with both the locked manifest and the bound source
  migration receipt. The live source-lock SHA/tree, checkout, and manifest
  subtree are bound before any sync write; unknown, incomplete, non-descendant,
  missing/tampered candidate proof, or receipt/manifest drift fails closed.
- Checkout verification is a point-in-time proof of the exact immutable Git
  identities and local completeness. A sealed structured receipt replaces the
  former boolean and binds the live source-lock digest/pins, source-root,
  checkout, `.git`, and object-directory identities, detached `HEAD` and local
  config file identity/content/access policy, exact HEAD/tree, and the complete
  source-safety contract. Before receipt publication, both checkout control
  files are opened with required no-follow, close-on-exec, and nonblocking
  flags, then must be regular, current-user-owned, single-link, and free of
  group or world write permission. Nonblocking open makes a raced FIFO fail at
  the regular-file check without waiting for a writer; mode and link count are
  stable across each bounded read. Each receipt is captured across two full
  verification passes; the live source lock is reloaded before and after. Fresh
  receipts run after manifest/receipt construction, again inside `sync_sources`
  before its first write, and around the canonical install/AGENTS gate. Object
  deletion, checkout replacement, same-inode config mutation, access-policy
  drift, or source-lock drift in those intervals is therefore rejected before
  the corresponding gate.
- The sync consumer independently validates each structured checkout receipt
  instead of trusting tuple arity: directory bindings and control-file object
  identities contain exact integer primitives, file size is a bounded
  non-boolean integer, and the digest is lowercase hexadecimal SHA-256.
  `.git/HEAD` and `.git/config` access policy must name the current uid, contain
  no group/world write bit, and retain exactly one hard link. A producer-side
  or test-double forged receipt cannot authorize migration merely by preserving
  the outer receipt shape.
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
  binding/content revalidation after classification. It is still a candidate
  migration-source epoch: before tree install it refreshes checkout proof, then
  binds the same sealed installed-target migration receipt as legacy-to-current,
  revalidates that receipt and checkout before and after the AGENTS no-op, and
  only skips the file-publication operation itself.
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
  full-DAG ancestry containing a commit with root `T`; matching either the
  approved or a newer live subtree alone is never sufficient. Exact/ancestry
  classification precedes the anchor's `T -> ST` path read, so a base-move
  squash whose clone has already pruned old `T` still reports stable
  `anchor-refresh-required` rather than a raw missing-object error. A legitimate
  merge commit that retains `T` in its ancestry remains accepted, including
  when later descendant commits intentionally change the live review subtree.
- The final delivery freeze advanced to signed candidate
  `b160b6fd0b3a0da4e25a74fbdb6bd3750c7a9bb2`, root tree
  `69475da88941082e2557ca875c82e4a0d38a173f`, and review subtree
  `7b08cb84a07c4a846d26ecde538c740e7772f9e7`. The companion stores that exact
  signed commit object's 585 raw payload bytes as strict Base64; runtime
  recomputation binds the payload back to the frozen candidate before any
  migration write. The raw payload's SHA-256 is
  `6926de8605af7c3bbccb7fd94a4386c878d7400196bf84a52e3a96f01a1c8103`.
  The last canonical delta changed only documentation, tests, and its project
  journal; this companion therefore changes no sync runtime logic beyond
  advancing the immutable source anchor and payload. Exact final-anchor/legacy
  transition validation passed below; the prior full-suite results remain
  historical evidence for their recorded heads.
- Final focused validation counts are recorded after the post-audit fixes in
  the Evidence section. The broader repository suite remains parent-owned final
  delivery evidence and is not inferred from these focused runs.

## Post-merge Descendant Sync Correction

- Canonical journal `20260826-ros002` and portability PR
  `Joey-Tools/codex-review-workflows#109` merged as
  `4f634a18ba711a1d35c4c1c8841e0c9821b39c8a` after removing a repository-only
  journal assertion from the distributed skill tests. Forced source-sync run
  `32927762585`, on private workflow head
  `d6e9583549a2b4395d377e96e1954533aa20b5f6`, then failed before any generated
  PR or release write with
  `canonical review live subtree is not approved for migration`.
- The source checkout and lock were valid: the new head remained a bounded
  full-DAG descendant of the reviewed approved-root tree, but its live review
  subtree changed legitimately because PR #109 edited that subtree. The live
  root tree was `4a9995aad7c38ebce55ff599713adf200307cb25`, and the live review
  subtree was `29c43c3f5acc65b75c7d2548f0dae5617518e5c7`; its parent activation
  squash `1483bf62400ee82ea7609cb553fddbb21f06640c` retained approved root tree
  `69475da88941082e2557ca875c82e4a0d38a173f`. The sync gate had coupled
  one-time activation proof to permanent byte equality with original subtree
  `7b08cb84a07c4a846d26ecde538c740e7772f9e7`, which would reject every later
  canonical skill update.
- The repair preserves the immutable candidate payload, approved root/tree-path
  proof, exact source-lock SHA/tree, complete non-shallow checkout receipt,
  bounded full-DAG ancestry, and live manifest binding. It removes only the two
  obsolete equalities between the current live subtree and the historical
  approved subtree. The migration receipt now binds the exact current live
  subtree, and downstream admission still requires type-preserving equality
  with the locked manifest.
- The positive regression puts the approved-root anchor exclusively behind a
  merge commit's second-parent history and then changes the live review subtree
  in a descendant. It proves that first-parent or linear-history inference
  would fail while the complete DAG succeeds. Adjacent negative coverage keeps
  non-descendants, base-move squashes without the anchor, incomplete history,
  wrong source-lock SHA/tree, and receipt/manifest drift fail closed.

## Activation Delivery Recovery

- Forced scheduled sync run `32932573984` selected canonical review source
  `4f634a18ba711a1d35c4c1c8841e0c9821b39c8a` with root tree
  `4a9995aad7c38ebce55ff599713adf200307cb25` and produced source-lock digest
  `3df9c166be0917fc584c210b6463a1f10443ce4b26b85a080c991515dcd68eab`.
  Source sync, both complete test suites, manifest validation, and canonical
  workflow validation succeeded.
- The run failed only when its configured Personal Access Token attempted to
  push the generated `.github/workflows/ci.yml` update. GitHub rejected that
  write because the token lacked workflow-file scope. No activation PR was
  created and no auto-merge request was enabled.
- Recovery deliberately leaves repository secrets and token scope unchanged.
  A task-scoped clean worktree reproduces the candidate from the same five
  detached source identities through the repository's source-lock and sync
  scripts, requires the exact source-lock digest above, and carries the result
  through an ordinary signed user-authorized PR. This is a delivery-transport
  recovery, not a new source-sync or review-policy path.
- The recovered activation retains the full pre-merge gate: exact-secret
  admission, one fresh local Codex lane at `gpt-5.6-sol`/`ultra`, one exact
  current-head GitHub `@codex review`, complete CI and conversation checks,
  immutable release verification, and strict local overlay installation. A
  base-refresh requirement is satisfied by a signed base-to-feature merge and
  a complete gate rerun, never by rebasing or linearizing the feature DAG.

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
- Final fresh review found that the exact legacy source pin was still evaluated
  against the candidate inventory before it could take the intended no-migration
  path. The sync now selects one frozen inventory profile from the authoritative
  rule and its validated locked-source receipt, then carries that same profile
  through raw source capture, prepared and staged manifests, Markdown policy,
  installation, and the final target read. Exact legacy selects its 150-file
  tree (145 required files and 78 files in the independent-review subtree);
  an accepted candidate or descendant receipt selects the 156-file current
  tree (151 required files and 77 independent-review files). Unknown identity,
  a missing candidate receipt, or a receipt on the legacy pin fails closed.
- Legacy-only Markdown may retain the old reference vocabulary during the
  no-migration release, while current inventory still rejects it. This is an
  inventory-profile property rather than a second migration boolean, so source,
  staging, and post-install validators cannot silently disagree.
- The bounded-descendant regression places the reviewed approved-root commit
  exclusively on a merge commit's second-parent history, then changes the live
  review subtree in a later descendant. The descendant's first-parent chain does
  not contain the approved-root commit, while its complete DAG does. Acceptance
  therefore cannot regress to linear-history, first-parent-only inference, or a
  permanent historical-subtree equality.
- Post-activation canonical changes no longer require advancing the immutable
  candidate payload merely because the live review subtree changed; advancing
  the anchor remains necessary when the bounded full DAG no longer contains the
  approved root tree.
- Complete checkout receipts now reject group/world-writable or multiply linked
  `.git/HEAD` and `.git/config` control files before publishing access-policy
  proof. Owner-private and ordinary owner-writable read-only-to-others modes
  remain accepted, and the existing two-pass structured-state comparison stays
  authoritative for later mutation detection. A nonblocking descriptor open
  prevents a FIFO replacement from stalling the verifier, while directed tests
  bind in-flight mode/link drift and individually safe cross-capture drift to
  their respective failure boundaries.
- The authoritative sync now binds guidance and source identity before entering
  any per-rule writer. Exact legacy/current source-and-guidance combinations
  follow the sealed three-action plan above; legacy with current guidance and
  either source with mixed or byte-drifted guidance fail before the old target
  marker is touched. Post-install content, inode, mode, and link-count races all
  fail at descriptor revalidation while preserving the installed-tree recovery
  state and leaving the concurrent guidance object untouched.
- The existing `personal_codex` parent is now a read-only precondition rather
  than a create-if-missing traversal. Missing directory/file, synthetic wrong
  owner, initial mode `0777`, and post-preflight `mode | 0022` drift all stop
  before prepared-tree creation. Candidate current-noop additionally proves
  that checkout refresh and installed-target receipt gates execute and block
  independently while the original AGENTS inode and bytes remain unchanged.
- The two AGENTS migration renames now repeat the parent identity, access-policy,
  and repository-root-to-`personal_codex` lineage check immediately before the
  mutation, after the corresponding final file read and installed-tree receipt
  check. Deterministic replacement and mode-drift tests bind the exact event
  order `final read -> receipt -> scope guard -> rename`; their race variants
  mutate only after receipt return, prove the guard stops the matching rename,
  and retain exact legacy and migrated recovery payloads. A final independent
  read-only audit reported `No findings.`.

## Next Steps

- No tracked implementation remains after the activation change is merged.
  Final delivery verifies the exact immutable default-branch release and then
  installs and strictly validates that release on the current host.

## Evidence

- `Joey-Tools/codex-review-workflows` journal `20260822-ros001`
- `Joey-Tools/codex-workspace#12`, squash merge
  `1c3b9c9662ef8c3ed5ddad2c3e272fb6a0eec526`
- `scripts/sync_private_overlay_sources.py`
- `tests/test_private_overlay_sync.py`
- `personal_codex/AGENTS.md`
- Descendant sync-gate repair validation: the changed-subtree/full-DAG,
  second-parent merge, non-descendant, base-move, missing-object, and
  receipt/manifest-drift focused cases passed; the complete source-sync module
  passed all 338 tests in 39.451 seconds. Warning-strict repository discovery
  passed all 2,057 tests in 321.643 seconds with four conditional skips. Ruff
  lint/format checks, project-journal validation, and `git diff --check` passed.
- Scheduled activation run `32932573984` passed source sync, repository tests,
  source-lock/manifest validation, and canonical workflow validation before the
  push-only token-scope rejection. Its generated source-lock digest was
  `3df9c166be0917fc584c210b6463a1f10443ce4b26b85a080c991515dcd68eab`;
  the run created no PR and enabled no auto-merge request.
- The post-merge change-delivery replacement regression passed against the
  actual `SYNC_RULES` entry and current canonical description. It proves the
  path- and frontmatter-field-scoped exact one-count private specialization,
  locked legacy replay through the descriptor-bound copy path, unchanged body
  and cross-file wording, fail-closed private, duplicate, quoted-key,
  explicit-key, merge-key, implicit-typed-key, nested, block-scalar,
  indented-comment, decoded-line-separator, or otherwise ambiguous frontmatter
  inputs, the preserved landing-commit/frozen-review sequence, and the common
  `the user` to `Joey` transform. The complete
  `tests.test_private_overlay_sync` module passed all 338 tests in 29.711
  seconds; Ruff lint/format checks, project-journal validation, and `git diff
  --check` also passed.
- Targeted `tests.test_private_overlay_sync.PrivateOverlaySyncTests` checks for
  self-contained canonical sync, required policy inventory, exact internal
  compatibility inventory, stale public-surface removal, and canonical global
  routing, including exact-old-pin transition binding (`8` final-delivery
  regressions, pass)
- Canonical target-content validation against the current `20260822-ros001`
  worktree, including the three new focused contract tests (pass)
- Transition-focused tests cover locked and unlocked authoritative sync,
  legacy no-migration, approved same-tree squash, bounded merge descendants,
  changed-subtree merge descendants, base-move anchor refresh, non-descendants,
  wrong SHA/tree, receipt/manifest drift,
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
- Final feature commit `ee9f66b36fae499c420212ab3c236ca6e88be021`
  and the two-parent base refresh merge
  `c8bec03abc18b257e4d2fc1209600c6b35e96df0` are signed and verified. The
  merge incorporates current `origin/master`
  `284f0f54daba1e9e17e922e4fa87aa6b586e37a4` without rebasing or
  linearizing the feature DAG.
- `ruff 0.13.2 check scripts/sync_private_overlay_sources.py
  tests/test_private_overlay_sync.py` (pass)
- Focused authoritative guidance and checkout-consumer validation:
  `personal_agents` (`33` tests), `canonical_review` (`15`), `review_sync`
  (`6`), `migration` (`25`), `locked_authoritative` (`2`), and `checkout`
  (`5`) filters, plus `candidate_review_source` (`2`) and `current_noop` (`2`)
  filters (pass). These include legacy/current source-state ordering,
  candidate idempotence, descriptor-bound post-install content/object/mode/link
  races, retained target recovery, and forged checkout uid/mode/link-count plus
  primitive-type rejection. They also cover read-only parent admission and
  candidate-current checkout/install-receipt failure gates, plus deterministic
  post-file-read parent replacement and access-policy drift. The two final race
  tests additionally pass a live installed-tree receipt and assert the complete
  final-read/receipt/scope/rename ordering in a clean control plus fail-closed
  race run.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS=error::ResourceWarning python3 -B
  -m unittest -q tests.test_private_overlay_source_lock` (`49` tests, one
  conditional skip, pass); HEAD/config safe-mode, writable-mode, real hard-link,
  writer-free FIFO, in-flight policy drift, and cross-capture safe-state drift
  regressions are included.
- `ruff check scripts/private_overlay_source_lock.py
  tests/test_private_overlay_source_lock.py` and matching `ruff format --check`
  (pass)
- `$project-journal` `validate` for the companion repository (pass)
- `python3 -B -m unittest -q tests.test_private_overlay_sync` (`308` tests)
- `PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS=error::ResourceWarning python3 -B
  -m unittest discover -s tests -p 'test_*.py' -q` (`2,020` tests, `4`
  conditional skips, `266.382` seconds)
- The earlier `6f404532fe39df560ce2898430ed15aedf4fe6ae`
  migration authorization passed all `11` selected policy tests in `7.207`
  seconds. After advancing authorization to signed candidate
  `0c58178f8d7999c35f71d255720d9703825a8839`, the exact candidate-payload/tree,
  exact legacy-source allowance, and live transition-state tests passed all
  `3` cases in `0.010` seconds.
- Post-final-review focused validation: `15` canonical-review tests passed in
  `10.402` seconds; `24` migration tests passed in `9.152` seconds; the six
  review-sync tests, exact legacy/current inventory regression, selector drift,
  ignored-source inventory, and targeted secure-sync checks all passed. Ruff
  format and lint validation covered the changed sync and test modules.
- After restoring the low-level keyword-only defaults and making the test seams
  forward the selected profile, warning-strict repository discovery passed all
  `2,022` tests in `247.827` seconds with `4` conditional skips. Project-journal
  validation and `git diff --check` also passed.
- After the checkout-control and AGENTS mutation-boundary remediations,
  warning-strict repository discovery passed all `2,049` tests in `281.476`
  seconds with `4` conditional skips. The final two trace-hardened race tests
  and all `33` `personal_agents` tests then passed independently under the same
  `ResourceWarning` policy.
- After replacing the offline authorization with signed candidate
  `0c58178f8d7999c35f71d255720d9703825a8839`, the final warning-strict
  repository discovery again passed all `2,049` tests in `255.450` seconds with
  `4` conditional skips.
- The final CLI-policy repair advanced the authorization to signed candidate
  `929cfc8daf1b5111ac7059567687a02718ea1475`. Its exact
  candidate-payload/tree, legacy-source allowance, and live transition-state
  tests passed all `3` cases in `0.006` seconds; no sync runtime logic changed
  after the preceding full discovery.
- The final authority-carrier correction then advanced only the immutable
  authorization anchor to signed candidate
  `713e296d3768d967a43e6ff8d73dd0e1d98f4d44`; its exact candidate-payload/tree,
  legacy-source allowance, and live transition-state tests passed all `3`
  cases in `1.477` seconds. No new full-suite result is inferred for this final
  anchor.
- The final lazy-fetch documentation clarification advanced only the immutable
  authorization anchor to signed candidate
  `b160b6fd0b3a0da4e25a74fbdb6bd3750c7a9bb2`; its exact
  candidate-payload/tree, legacy-source allowance, and live transition-state
  tests passed all `3` cases in `1.380` seconds. No full private suite was rerun
  for this anchor.
- Broad-test follow-up preserved the internal helper ABI by giving low-level
  prepared-file and prepared-directory copy calls an explicit CURRENT-profile
  default. Test seams now accept and transparently forward the selected profile,
  with ordering tests proving that public, prepared, and staged validation see
  the same object. The descriptor-depth resource test now uses an ordinary
  noncanonical secure tree instead of intentionally adding unreviewed files to
  the closed canonical inventory. All `11` affected seam tests pass.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS=error::ResourceWarning python3 -B
  -m unittest discover -s
  personal_codex/skills/review-orchestration-playbook/tests -p 'test_*.py' -q`
  (`2,965` tests: `2,949` pass, `15` conditional skips, one restricted-host
  nested-`sandbox-exec` denial); the exact denied test passed in a single
  approved outside-host-sandbox rerun (`1` test, `0` failures, `2.044`
  seconds).
