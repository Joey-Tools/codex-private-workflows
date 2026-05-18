---
name: cisco-trackers-lookup
description: Look up Cisco Jira issues and Cisco GHE PR metadata with narrow read-only helpers. Use when Joey needs authoritative tracker metadata, comments, attachments, closed-PR search, or commit-to-PR mapping before broader bug triage or code review begins.
---

# Cisco Trackers Lookup

## Overview

Use this skill when the task starts from Cisco Jira or Cisco GHE metadata rather than from logs, failing tests, or a known code path.
The goal is to replace issue-specific `curl` commands and repo-specific `gh` literals with narrow helper subcommands whose auth, host, and query scope are explicit.

## Workflow

1. Decide whether this is lookup or triage.
- If the first missing evidence is a Jira issue, Jira comments, a Cisco GHE PR, a closed-PR search, or commit-to-PR mapping, start here.
- If the task has already moved on to Jenkins URLs, console logs, zip archives, crash artifacts, or root-cause ranking, switch to [$bug-triage-playbook](../bug-triage-playbook/SKILL.md) as the top-level owner.
- When a bug triage starts from a Jira issue, fetch the tracker metadata here first, then hand the task back to `bug-triage-playbook` once the evidence source becomes logs, builds, or code.

2. Prefer the narrow helper that matches the tracker.
- For Cisco Jira issue metadata, use `python3 "$HOME/.codex/skills/cisco-trackers-lookup/scripts/jira_issue_probe.py" issue ...`.
- For Jira comments and attachments on the same issue, use `... jira_issue_probe.py issue-extra ...`.
- For Cisco GHE PR metadata, closed-PR search, or commit-to-PR mapping, use `python3 "$HOME/.codex/skills/cisco-trackers-lookup/scripts/cisco_ghe_probe.py" ...`.
- Keep the runtime examples anchored to the installed personal-skill path under `~/.codex/skills/`; the repo mirror under `personal_codex/` is for authoring and sync.

3. Keep auth and scope fixed.
- Jira access stays on `jira-eng-gpk2.cisco.com` and uses a fixed `--auth-profile`, not arbitrary env-var names.
- Cisco GHE access stays on `sqbu-github.cisco.com` and always requires an explicit `--repo`.
- Cisco GHE search text stays plain query text after `--`; the helper does not expose extra `gh` flags, and the query must not add `repo:`, `org:`, or `user:` qualifiers that would override the explicit `--repo` boundary.
- Do not widen these helpers into generic `curl`, generic `gh api`, or arbitrary Jira search surfaces just because one query shape is temporarily missing.

4. If a new lookup pattern repeats, patch the helper instead of the rules.
- Add a new narrow subcommand or a small reference recipe when the same tracker lookup appears across sessions.
- Do not preserve concrete issue URLs, PR numbers, commit SHAs, or `/bin/zsh -lc "GH_HOST=... gh ..."` wrappers in `~/.codex/rules/default.rules` once the helper can express the lookup.

## Guardrails

- Keep this skill read-only.
- Do not let this skill become the top-level owner for log-driven root-cause investigation; that remains `bug-triage-playbook`.
- Do not accept arbitrary auth env names, arbitrary Jira hosts, or generic GHE passthrough APIs.
- If the helper cannot yet express the requested lookup, say which missing subcommand or field is missing instead of silently falling back to a long-lived literal command shape.

## References

- Use [references/workflow.md](references/workflow.md) for the narrow helper entrypoints, allowed auth profile, and the intended handoff boundary to `bug-triage-playbook`.
