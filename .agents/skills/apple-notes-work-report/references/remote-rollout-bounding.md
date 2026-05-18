# Remote Rollout Bounding

Use this note when remote daily-report evidence is clearly relevant but one or more `remote_codex_probe.py fetch-rollout` calls fail because the rollout file is too large to copy.

## Problem

- Long-lived remote sessions can produce rollout files larger than the helper limit (`>16 MiB`).
- If the draft logic treats that first copy failure as "repo still uncertain", it will overproduce repo-level `uncertain` buckets even when smaller same-day evidence already proves the work happened.

## Correct Fallback

1. Start with `session-meta`.
- Confirm the host, date, repo path, and how many same-day sessions exist for that repo.

2. Fetch smaller same-day rollouts from the same repo/path.
- Prefer a smaller earlier or mid-day rollout that still captures representative work.
- One review/test sample plus one docs/commit sample is usually enough to establish the repo's work shape for a daily report.

3. Decide inclusion from bounded evidence, not from the missing large rollout alone.
- If the sampled rollouts already show concrete work such as docs restructuring, AGENTS updates, project-record maintenance, tests, review, or a signed commit, treat the repo as reportable and draft a conservative bucket.
- Mention the missing large-rollout detail outside the code block only as a caveat about bounded coverage.

4. Keep `uncertain` narrow.
- Use `uncertain` only when the missing evidence could still change whether an item belongs in the draft, or materially change the content of the drafted line.
- Do not use repo-level `uncertain` just because later same-day rollouts were too large to fetch.

5. Escalate to `needs scope confirmation` only when the boundary is genuinely unclear.
- Size-limit failures are an evidence-shape problem, not an inclusion-boundary problem.
- If the repo is already default-reportable or has prior reporting precedent, do not reclassify it as scope-unclear merely because some rollouts were too large.

## Worked Example

- On `2026-03-25`, `miku-bot-dev:/home/hoteng/copilot-code-review-tool` had many same-day sessions.
- Some later rollouts exceeded the helper fetch limit, but smaller fetched rollouts still established:
  - README / AGENTS / archive-doc restructuring work;
  - external review with a conclusive `No findings.` result;
  - a signed commit `docs: archive project records and codify review flow`.
- Correct outcome:
  - include a conservative `copilot-code-review-tool` bucket in the paste-ready draft;
  - keep any remaining "large rollout not fully sampled" note outside the code block as a bounded-detail caveat;
  - do not emit repo-level `needs scope confirmation`, and do not leave the whole repo under repo-level `uncertain`.
