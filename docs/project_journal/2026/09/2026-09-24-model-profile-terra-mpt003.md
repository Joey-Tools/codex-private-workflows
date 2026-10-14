---
id: 20260924-mpt003
title: Private Model Profile Preference Alignment
status: completed
created: 2026-09-24
updated: 2026-10-14
branch: codex/daily-skill-friction-20260924-codex-private-workflows-model-profile-terra
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/201
supersedes: []
superseded_by:
---

# Private Model Profile Preference Alignment

## Summary
- Set the tracked Daily Skill Friction automation profile to GPT-6.1 Sol/high while keeping it paused and preserving its schedule, prompt, execution environment, and workspace cwd.
- Record the current cross-repo model preference scope without taking ownership of the public canonical source changes assigned elsewhere.

## Current State
- The tracked Daily Skill Friction automation uses `model = "gpt-6.1-sol"` and `reasoning_effort = "high"`; its status remains `PAUSED`.
- The requested execution preference is GPT-6.1 Sol/high, with no execution model below GPT-6. Claude Code and Copilot use Claude Opus 5.5/high; attribution falls back to GPT-6.1 Sol High when execution evidence is unavailable.
- Reviewer and review-routing settings remain those brought in from latest master; this private automation update does not alter them.
- Public runtime, provider, and attribution source updates remain with their canonical owners. Generated review and attribution copies in the private overlay must follow canonical source promotion rather than being edited by hand.

## Next Steps
- No further automation-source changes. Promote the separately owned public canonical updates through the normal source-promotion path after their merge.

## Evidence
- Worktree: `codex/daily-skill-friction-20260924-codex-private-workflows-model-profile-terra`
- Private automation source: `personal_codex/automations/daily-skill-friction/automation.toml`.
