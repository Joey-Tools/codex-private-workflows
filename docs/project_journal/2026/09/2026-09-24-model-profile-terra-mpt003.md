---
id: 20260924-mpt003
title: Private Model Profile Terra Migration
status: active
created: 2026-09-24
updated: 2026-09-24
branch: codex/daily-skill-friction-20260924-codex-private-workflows-model-profile-terra
pr:
supersedes: []
superseded_by:
---

# Private Model Profile Terra Migration

## Summary
- Align the private reviewer and runtime policy with Terra/Ultra, Terra-to-Luna/max, and Opus 5/max.
- Set the private PR attribution fallback to Terra Ultra while keeping the AGENTS.md fallback at GPT-5.6.
- Set the private daily skill-friction automation source to Terra/xhigh.

## Current State
- Private review contracts, stream validation, attribution guidance, and regression tests are aligned with the requested policy.
- Claude and Copilot retry slots stay within the Opus 5 family: `claude-opus-5` is primary and `claude-opus-5.0` is the closed wire alias; the stream validator accepts both current identities.
- Runtime model matching canonicalizes only the exact dotted Opus 5 alias, keeping lookalike model IDs rejected.
- Attribution maps an observed Luna runtime explicitly while retaining Terra Ultra as the no-evidence fallback.
- The four host automations were updated through the Codex app to Terra/xhigh.

## Next Steps
- Land the validated local change and refresh the installed private overlay through its normal release path.

## Evidence
- Worktree: `codex/daily-skill-friction-20260924-codex-private-workflows-model-profile-terra`
- Skill validation: `codex_skill_validate.py` reports `Skill is valid!`.
