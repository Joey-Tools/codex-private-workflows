---
id: 20261005-amr001
title: Parent Models and Remote-First Review Routing
status: active
created: 2026-10-05
updated: 2026-10-07
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
- [ ] Validate this independent post-#214 exact-state integration and its own current-head hosted review/CI gates.
- [ ] Verify default-branch release and native installer deployment on each reachable target host.

## Deployment Boundary

- Package installation covers the local account, `BL-mac-mini-m4-hoteng`, `hoteng-srv-01`, and `codex-hoteng-srv-01`, using each account's native Codex home. The two Linux aliases remain separate account destinations.
- The macOS host-role inventory controls controller/headless scheduler behavior, not cross-platform package eligibility. This policy delivery does not authorize a new scheduler, activation migration, or edits to paused automations.
- Use the existing native installer and verify public/private release identities, overlay integrity, and the materialized reviewer role before a fresh native Codex smoke test. Do not isolate `CODEX_HOME` or silently overwrite managed-file drift.

## Evidence

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
