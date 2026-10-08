---
id: 20261005-amr001
title: Parent Models and Remote-First Review Routing
status: completed
created: 2026-10-05
updated: 2026-10-08
branch: codex/agent-model-review-routing-20261005
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/211
supersedes: []
superseded_by:
---

# Parent Models and Remote-First Review Routing

## Decisions and Reasons

- Default orchestration to GPT-6.1 Sol; explicitly permit GPT-6 Luna up to Max for token-intensive work and local review. Keep a user-selected active parent session unchanged.
- Explicit local review defaults to GPT-6.1 Sol at model-default reasoning, not an inherited Extra High/Ultra. Other models/efforts require user opt-in; Luna is the authorized exception, not an automatic fallback.
- PR-bound work with available GitHub Codex or native GitHub Copilot code review uses remote-only review by default. Explicit local/named requests still apply; provider availability and completed review are distinct.
- Claude Code remains explicit opt-in, including named double/triple, and defaults to Opus 5.5 once authorized. Native GitHub Copilot review is not Copilot CLI.
- Keep only short cross-repo routing and consent in personal AGENTS. Canonical review-workflows owns the role, detailed routes, runtime defaults, evidence, and tests; workflow-hygiene owns attribution label mapping.
- Extend exact AGENTS migration and change-delivery description transforms so source sync can install the new rules without accepting arbitrary partial policy text.
- Recognize four exact consent/block pairs: legacy, previous, intermediate (the released #214 consent with its original named-review block), and full current. Only full current is a no-op. The other known pairs require the existing verified canonical migration receipt before migration; arbitrary mixed, duplicated, drifted, or reordered text remains rejected. Preserve all surrounding bytes and second-run inode stability.

## Delivery Dependencies

- Canonical review policy landed through `codex-review-workflows` PR #127 at `ed7fab2dafd813dc2df6e6dc10a61ab64d1319af`; do not patch its generated private role or skills independently.
- Canonical attribution labels are delivered by `codex-workflow-hygiene` PR #89. Promote their locked source commits only after canonical validation and merge.
- Source promotion owns the generated helper and its matching private output assertions. #214 advances the three already-approved governing delivery/consent/attribution clauses and their companion attribution assertions atomically, avoiding a contradictory intermediate release. This PR retains the independent parent/model paragraph, named-review authorization block, exact-state migration, tests, and journal; it does not duplicate or undo #214's delivered changes.
- Integrate the current base with merge commits, preserving #213's complete replacement-definition and exact-one-match checks and #200's per-action exact GitHub identity rules. Strict branch freshness is not a linear-history or rebase requirement.
- Existing installer-recovery work and other threads' GitHub Actions/status-check/ruleset work are separate. Do not absorb unrelated open PRs.
- Historical source-sync preparation failures are not current release evidence: #213 landed at `8e58181581c583ae6fec504d0a9d83162791d5bd`, and its default-branch release run `37535306846` succeeded. The new locked source promotion and companion release still require their own validation.
- Replacement #214 merged the exact remotely reviewed head `63f25c3576c9b5773ffddfb30db0624c210bf21f` as `b6ef34558556243a858cbd56f0ac40ee143c08d9` (tree `5bb8a39984997b5b30bdb2db49c5652abc1447ef`, GitHub signature verified valid). Its current-head clean comment `6044488918`, zero unresolved findings, required CI, and two stable policy/evidence snapshots were accepted. #190 is closed as superseded, with explicit comment `6044840186`; its original head, branch, and dirty local follow-ups remain preserved.

## Checklist

- [x] Record user authorization and ownership boundaries.
- [x] Update short personal AGENTS routing/model defaults.
- [x] Validate exact previous/current AGENTS migration and source-description compatibility.
- [x] Merge canonical policy and attribution PRs, then promote through locked private replacement #214.
- [x] Validate this independent post-#214 exact-state integration and its own current-head hosted review/CI gates.
- [x] Verify default-branch release and native installer deployment on all four authorized account homes, with a separate local native CLI loading smoke.

## Deployment Boundary

- Package installation covers the local account, `BL-mac-mini-m4-hoteng`, `hoteng-srv-01`, and `codex-hoteng-srv-01`, using each account's native Codex home. The two Linux aliases remain separate account destinations.
- The macOS host-role inventory controls controller/headless scheduler behavior, not cross-platform package eligibility. This policy delivery does not authorize a new scheduler, activation migration, or edits to paused automations.
- Use the existing native installer and verify public/private release identities, overlay integrity, and the materialized reviewer role before a fresh native Codex smoke test. Do not isolate `CODEX_HOME` or silently overwrite managed-file drift.

## Evidence

### Final Delivery and Deployment

- [#211](https://github.com/Joey-Tools/codex-private-workflows/pull/211) merged at `2026-10-07T20:40:48Z` as `7793dc1518671cc43474e8f72dffb8776d951dd4`, with actual #214 as its parent. Its merge tree equals frozen reviewed head `466584d7801ebc55730e4a97a1db75e7081ce43b`. Exact-head terminal clean comment `6046160540`, complete conversation with zero unresolved threads, required CI and two stable final readiness snapshots were accepted. Both original P2 findings remain preserved and resolved after their repairs.
- [Private Overlay Release run37683859512](https://github.com/Joey-Tools/codex-private-workflows/actions/runs/37683859512) succeeded and published `personal-codex-20261007-210605-7793dc1` at `2026-10-07T21:06:11Z`. [Forced source-sync run37687265479](https://github.com/Joey-Tools/codex-private-workflows/actions/runs/37687265479) succeeded with `changed=false` and no private source changes; no additional sync PR or publication was required.
- Normal native installation, strict status, overlay integrity and release-identity checks passed on local, `BL-mac-mini-m4-hoteng`, `hoteng-srv-01` and `codex-hoteng-srv-01`. Each account uses private `7793dc1518671cc43474e8f72dffb8776d951dd4` and public base `e716023be83c6c3132ebc530b8530ddf8c6b4c3f`, with matching private/public release-tree SHA256 values `3c39457bbc4ed1f640e3cfb2134802db988d3ac72e2d8dc908c5e26755d5f723` and `236fc5905f71f88edb49523b14af820eaf4824e0882c9e8be1b897bbd0805eec`.
- Each installed reviewer is a unique, native-owner, single-link regular0600 file, SHA256 `d9db59ca773fa930e6a8a08f01592e53e56ce62de84aa3e6ca01d4e2da10c3d2`, with GPT-6.1 Sol/medium/read-only and no config reviewer override. The local newly activated published installer repaired the old role materialization through a normal rerun; its displaced entries remain recoverable in quarantine.
- Native local CLI0.160.1 loading-only smoke returned0. Host-authored child `01a1183f-240e-7382-bf1a-bbfca903d95d` binds parent `01a1183e-fef4-7452-a1c0-1f1f79f0768c` and the fresh reviewer role, proving GPT-6.1 Sol/medium/read-only without a model/effort override or isolated `CODEX_HOME`. The child made zero tool calls and returned the expected missing-input inconclusive response. This is role-loading evidence, not a code-review pass, remote runtime test, existing Desktop-thread reload or backend-weight attestation.
- Joey explicitly authorized the three remote native `github.com/JoeyTeng-Codex` pools; all worked without token transfer, active-account fallback or auth mutation. On2026-10-08 he separately approved removing group-write only from `/home/hoteng/.codex/agents`, `/home/codex/.codex` and `/home/codex/.codex/agents` (0775-to0755, non-recursive). Descriptor-bound verification preserved directory identity, ownership and group. Both Linux normal dry-runs, installs and strict checks then passed; no custody guard was bypassed.
- Complete remote installation logs were retained locally and verified against their staging manifests before the three exact task staging directories were removed non-recursively. Installer quarantine and old #190 follow-ups remain preserved; no scheduler, paused automation, local code-review lane or pre-GPT-6 model was started.

The following sections retain earlier validation epochs. Their pending gates,
failed runs and historical heads do not contradict or replace the final evidence
above. No delivery gate remains for this workstream; old #190 follow-up assessment
is separate from the delivered policy and installation scope.

### Historical Pre-Integration Evidence

The checks below belong to earlier #211 inputs, not the new range above actual #214. They are retained as history and do not supply a current-head pass.

- Desktop task `01a029a7-1fac-7f50-8d62-16206c099cd9`, 2026-10-05.
- Workspace `AGENTS.md` removes the prior mandatory local-plus-GitHub exception.
- Focused root policy tests must exercise the final migrated AGENTS bytes, not only the manually edited source text.
- The Luna worker passed 19 affected sync tests, `py_compile`, and `git diff --check`. Coverage includes exact legacy/previous/current pairs, previous-to-current promotion, repeated-sync inode stability, preservation of surrounding AGENTS bytes, and rejection of partial, duplicated, drifted, or mixed policy.
- The parent reran eight focused sync/global-guidance/attribution tests successfully against the final files. The global guidance explicitly authorizes GPT-6 Luna up to Max for local reviewers while preserving GPT-6.1 Sol at model-default reasoning as the default.
- New and historical change-delivery description prefixes both synchronize; the transform requires exactly one recognized prefix and does not accept arbitrary descriptions.
- No canonical source locks or generated private role/skill copies were advanced. These local checks do not establish full-suite, release, or all-host installation success.

### Post-#214 Integration Evidence

- The actual merged #214 commit was fetched through the existing repository SSH remote and integrated by a normal merge from clean local head `6ac481767826955753e61d0eab79814bd5fcf8d5`. Three expected content conflicts were resolved narrowly in AGENTS, the private sync script, and its tests; #214's source transforms and receipts remain intact. No history was rewritten or old worktree discarded.
- The first focused wrapper invocation resolved its relative path from the wrong directory and exited 2 before starting tests; this is an invocation failure, not a test pass. Retrying the absolute task-local bounded wrapper passed 36 `personal_agents` tests in 8.668 seconds; log SHA-256 `f223d9a27a71df558965145d0484b4e08ab9524e246102842a08f421f2497a67`.
- The first complete sync module ran 355 tests in 53.884 seconds with two failures. Both old fixtures changed current consent to previous consent while retaining the newly different full-current named block; this is deliberately an unknown pair. Correct only those fixtures to the exact previous pair, retain mixed-pair rejection, and explicitly cover legal intermediate input being rejected before any write by legacy-source/no-migration-receipt paths. The failed run remains recorded (log SHA-256 `84ef8a68294bb3fcee3e8897c63ce1e9c9bee8d6c5d9200083e2eeac4881e8b3`), not relabeled as passed.
- The final `review_source` focused subset passed 7 tests in 3.061 seconds, including that intermediate-before-write rejection and unchanged tree assertions; log SHA-256 `84edd017977592d7cc49b8062f16074f32c9cf124f3c92395b5e5e93cbe22691`.
- Parent independently passed all 43 attribution tests in 4.754 seconds on the integrated guidance; log SHA-256 `6d79d6c1c2a47c73769c4f205925c4a610b6cfc40e9460d52bd21e26177c4608`. The attribution test file is byte-identical to #214, so no redundant companion delta remains.
- The final complete private-sync module passed all 355 tests in 45.010 seconds (bounded wall 46.075 seconds), no skips; retained log `pr211-private-overlay-sync-full-20261007-a2.log`, SHA-256 `ac7028b09e4fd258fb6ba45e48d3c943a4568c500d17893edf2407617bc79abc`. It includes both receipt-backed intermediate migration/inode-stable convergence and legacy-source intermediate rejection before writes. Parent verified the retained digest, journal validation, diff check, and absence of conflict markers. The post-#214 range is limited to personal AGENTS, its owning private migration script/tests, and two journals; generated role/skills, source locks/receipts, and attribution helper/test are unchanged. No new full-local-suite or local-code-review result is claimed; the final frozen admission and required new-head hosted matrix remain separate gates.

### Remote Model-Paragraph Finding and Repair Contract

- Signed merge head `531238ddf7be45692deb6f3c778c5546aa5c82b3` was pushed normally and marked ready after complete clean admission-only evidence (`reviewer_started: false`). Remote review `5447343494` produced valid P2 finding `4211122072`, thread `PRRT_kwDOSg870M6qEFQe`: real released #214 AGENTS has no new parent/model-routing paragraph, and migrating only its named block wrongly classified that incomplete result as current. The complete original snapshot is `private211-integrated-progress2`; the earlier 355-test result belongs to this historical candidate, not the following repair.
- Bind the exact unique parent/model paragraph into the current-state contract at its established top-level position. Known legacy/previous/intermediate inputs may lack that newly introduced paragraph or already contain its exact correctly placed form. Receipt-backed migration inserts only the missing exact paragraph after the existing unique short-file guideline anchor; it never duplicates the current paragraph or changes unrelated preamble/prefix/suffix bytes. Full current requires the paragraph as well as its exact consent/named block.
- Reject mutated, duplicated, displaced, or ambiguously anchored owned paragraphs before overlay writes. Keep the existing source receipt, plan sealing, allowlist, tree/ancestry, and unknown consent/block guards; do not hide the finding, relax assertions, or add a local code-review lane.
- Add historical-input-independent migration regressions and separately probe the actual frozen released #214 AGENTS blob for exact candidate convergence. Tests, frozen admission, repaired-finding resolution, and new-head remote/CI evidence remain required for this repair epoch.
- Final repaired-input checks passed: 36 `personal_agents` tests in 7.256 seconds (bounded wall 8.247 seconds, log SHA-256 `58786ccc1bdced68e5daba76d95f5cac006f5daed0c8c0f19c981b401abf4c3c`), eight `review_source` tests in 3.211 seconds (bounded wall 4.192 seconds, log SHA-256 `658c60bf331fa9985e1e001696b8a52a30e8c607d734b17aa6bdcc5d0115e881`), and the complete 356-test sync module in 44.280 seconds (bounded wall 45.272 seconds, log SHA-256 `7e158ad366d5148de7e0dc35bb776886c983cdbef4b1af2d9a8c9e6dff468349`). Earlier focused failures were fixture error-message expectations and one mistakenly replaced test assertion; they were corrected and rerun, with the failed logs retained rather than counted as passes.
- Parent independently read the actual frozen released #214 AGENTS blob at `b6ef34558556243a858cbd56f0ac40ee143c08d9`, SHA-256 `beb0b8a41fad44ecbf6f86faf5dc30394594210dae673c3ad735b2ac3a7c52fa`. The patched receipt-backed migration recognizes it as intermediate, produces bytes exactly equal to the candidate AGENTS (SHA-256 `6802d352f0bdf6a4979f987aaad3ec995c69025e6efd6de88eb2d4cbe49afcb3`), recognizes that result as full current, and performs a byte-identical second migration. The bounded probe passed in 0.159 seconds, log SHA-256 `2da819d53c23881af9eb56852358d5024402bb39b60747bb3dd78a52af9f6107`.
- The repair changes only the private-owned migration script/tests plus these delivery journals. It does not hand-edit the generated reviewer role, skills, source pins, or receipts. No local code-review model was run; fresh frozen admission and current-head hosted review/CI remain separate delivery gates.

### Remote Rewritten-Paragraph Finding

- Signed append head `9eb69b438e8a97d589e3d4c07f6d7b2b3e1d452b` passed complete frozen admission (`reviewer_started: false`, output SHA-256 `b42d01ceed780f60b12ecb77408e1b583e66ddd3ae77983e8dc2fb6519b95dbf`) and was pushed normally. Parent preserved original review `5447343494`, posted repair mapping `6045866442`, and resolved its exact thread only after the repair was on the assigned PR. Automatic controller request `6045847619` binds the new head; no duplicate request was posted.
- New current-head review `5447740973` found valid P2 `4211457243`, thread `PRRT_kwDOSg870M6qE5G6`: a completely rewritten model bullet containing no recognized marker can masquerade as absent, survive insertion, and conflict with the canonical rule. This candidate is not clean or merge-ready. Its complete original snapshot is `private211-model-repair-progress2`; prior successful checks remain historical evidence only.
- Require the known exact legacy insertion-slot successor before treating the owned paragraph as missing. Do not rely solely on wording heuristics, delete unrelated content, or expand the admitted source/receipt contract. Add marker-free rewrite rejection before overlay writes and preserve exact historical migration, full-current convergence, and second-run inode stability. Final tests and a new-head remote review still govern the following repair.
- Historical-slot repair validation passed without failed rounds: 36 `personal_agents` tests in 7.784 seconds (log SHA-256 `d4b7cb19e97f3563b1cc23a0838403049727513b48228b75b0393bdaf6cd99e5`), eight `review_source` tests in 3.127 seconds (log SHA-256 `632403cc74302bab980849983155c0a5fc2ea407b2c630f523e731ffbefcb99b`), and all 356 private-sync tests in 45.437 seconds (log SHA-256 `67d16619b3415581c0194e9d5cf98eea933b9aab7414d8468e76a6303d07b73c`). Parent verified those retained log hashes and reran the actual frozen #214 AGENTS convergence probe after implementation stopped: exact candidate bytes and second byte-no-op passed in 0.140 seconds, unchanged output SHA-256 `2da819d53c23881af9eb56852358d5024402bb39b60747bb3dd78a52af9f6107`. No generated/source-lock change or local code-review lane was introduced.
