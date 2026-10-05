---
id: 20261005-amr001
title: Parent Models and Remote-First Review Routing
status: active
created: 2026-10-05
updated: 2026-10-05
branch: codex/agent-model-review-routing-20261005
pr:
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

## Delivery Dependencies

- Canonical review policy is being prepared separately on `codex/review-model-routing-policy-20261005`; do not patch its generated private role or skills independently.
- Canonical attribution labels are being prepared separately in workflow-hygiene. Promote their locked source commits only after canonical validation and merge.
- Existing installer-recovery work and other threads' GitHub Actions/status-check/ruleset work are separate. Do not absorb unrelated open PRs.
- The previous private-sync baseline has unrelated Archify generation and test-inventory failures; a successful focused policy test does not prove that a release or all-host install is ready.

## Checklist

- [x] Record user authorization and ownership boundaries.
- [x] Update short personal AGENTS routing/model defaults.
- [x] Validate exact previous/current AGENTS migration and source-description compatibility.
- [ ] Merge canonical policy and attribution PRs, then promote through a locked private source-sync PR.
- [ ] Verify default-branch release and native installer deployment on each reachable target host.

## Evidence

- Desktop task `01a029a7-1fac-7f50-8d62-16206c099cd9`, 2026-10-05.
- Workspace `AGENTS.md` removes the prior mandatory local-plus-GitHub exception.
- Focused root policy tests must exercise the final migrated AGENTS bytes, not only the manually edited source text.
- The Luna worker passed 19 affected sync tests, `py_compile`, and `git diff --check`. Coverage includes exact legacy/previous/current pairs, previous-to-current promotion, repeated-sync inode stability, preservation of surrounding AGENTS bytes, and rejection of partial, duplicated, drifted, or mixed policy.
- The parent reran eight focused sync/global-guidance/attribution tests successfully against the final files. The global guidance explicitly authorizes GPT-6 Luna up to Max for local reviewers while preserving GPT-6.1 Sol at model-default reasoning as the default.
- New and historical change-delivery description prefixes both synchronize; the transform requires exactly one recognized prefix and does not accept arbitrary descriptions.
- No canonical source locks or generated private role/skill copies were advanced. These local checks do not establish full-suite, release, or all-host installation success.
