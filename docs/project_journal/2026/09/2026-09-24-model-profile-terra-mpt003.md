---
id: 20260924-mpt003
title: Private Automation Profile Alignment
status: completed
created: 2026-09-24
updated: 2026-10-14
branch: codex/daily-skill-friction-20260924-codex-private-workflows-model-profile-terra
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/201
supersedes: []
superseded_by:
---

# Private Automation Profile Alignment

## Summary
- Set the tracked Daily Skill Friction automation profile to GPT-6.1 Sol/high while keeping it paused and preserving its schedule, prompt, execution environment, and workspace cwd.
- Upgrade the active tracked Daily Work Report Draft automation from GPT-5.5 to GPT-6.1 Sol/high without changing its active status, prompt, schedule, or environment.

## Current State
- The tracked Daily Skill Friction automation uses `model = "gpt-6.1-sol"` and `reasoning_effort = "high"`; its status remains `PAUSED`.
- The tracked Daily Work Report Draft automation uses `gpt-6.1-sol` / `high` and remains `ACTIVE`. These are the two automation sources tracked in this repository.
- Reviewer and review-routing settings remain those brought in from latest master; this private automation update does not alter them.
- This completed workstream covers automation source configuration only. Generated runtime, Claude/Copilot, and attribution copies are unchanged by this PR; it does not claim that the requested High profiles have shipped in the overlay.
- The requested companion profiles are GPT-6.1 Sol/high for low-level Codex helpers, Opus 5.5/high for opted-in Claude/Copilot helpers, and GPT-6.1 Sol High for missing-evidence attribution. Their canonical changes and subsequent source promotion remain separate delivery work.

## Next Steps
- No further automation-source changes. Promote the separately owned public canonical updates through the normal source-promotion path after their merge.

## Evidence
- Worktree: `codex/daily-skill-friction-20260924-codex-private-workflows-model-profile-terra`
- Private automation source: `personal_codex/automations/daily-skill-friction/automation.toml`.
- Active report automation source: `personal_codex/automations/daily-work-report-draft/automation.toml`.
