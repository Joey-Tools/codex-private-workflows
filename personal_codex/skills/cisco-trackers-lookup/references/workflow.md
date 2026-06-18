# Cisco Trackers Lookup Recipes

Use these helpers when the repeated work is:

- fetch one Cisco Jira issue's stable metadata
- fetch comments and attachments on that issue
- inspect one Cisco GHE PR
- search closed Cisco GHE PRs inside one repo
- map one commit SHA to the PRs that contain it

Keep recurring approvals anchored to the installed personal-skill paths under `~/.codex/skills/`.

## 1. Cisco Jira

Check whether the fixed bearer-token auth env var is present:

```bash
printenv Jira_token
```

The `jira_eng_gpk2_default` profile sends `Jira_token` as `Authorization: Bearer ...`.
`Jira_email` and `Jira_username` may exist in Joey's shell environment, but this Jira instance rejects Basic Auth and the helper must not use those values for Jira REST calls.

Read one issue:

```bash
python3 "$HOME/.codex/skills/cisco-trackers-lookup/scripts/jira_issue_probe.py" issue \
  SPARK-786996 \
  --auth-profile jira_eng_gpk2_default
```

Read the same issue plus comments and attachments:

```bash
python3 "$HOME/.codex/skills/cisco-trackers-lookup/scripts/jira_issue_probe.py" issue-extra \
  https://jira-eng-gpk2.cisco.com/jira/browse/SPARK-786996 \
  --auth-profile jira_eng_gpk2_default
```

The helper intentionally rejects:

- non-`https` Jira URLs
- hosts outside `jira-eng-gpk2.cisco.com`
- arbitrary auth env names
- broad search APIs

## 2. Cisco GHE

Read one PR:

```bash
python3 "$HOME/.codex/skills/cisco-trackers-lookup/scripts/cisco_ghe_probe.py" pr-view \
  --repo WebexApps/webex-apps \
  --pr 69830
```

Search closed PRs in one repo:

```bash
python3 "$HOME/.codex/skills/cisco-trackers-lookup/scripts/cisco_ghe_probe.py" search-prs \
  --repo WebexApps/webex-apps \
  --query 'SPARK-518260 is:closed' \
  --limit 10
```

Map one commit to PRs:

```bash
python3 "$HOME/.codex/skills/cisco-trackers-lookup/scripts/cisco_ghe_probe.py" commit-pulls \
  --repo WebexApps/webex-apps \
  --commit 9b4f4d850f5e6c4e56a580c2f14d093fa0f14e16
```

Keep the helper scope repo-bounded. Do not reintroduce raw `env GH_HOST=... gh api ...` or shell-wrapped `gh` literals when these subcommands already cover the lookup.
For `search-prs`, keep the query repo-bounded: the helper keeps the query as plain text after `--`, allows negative filters such as `-label:bot`, but rejects `repo:`, `org:`, and `user:` qualifiers so the explicit `--repo` cannot be silently overridden from inside the query text.

## 3. Handoff To Bug Triage

When the next step is to inspect Jenkins URLs, archives, crash logs, or compare code-level hypotheses, switch to [$bug-triage-playbook](../../bug-triage-playbook/SKILL.md).
This skill is for tracker metadata, not for root-cause ownership.
