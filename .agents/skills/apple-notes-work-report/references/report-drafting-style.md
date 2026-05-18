# Report Drafting Style

Use this when Joey asks for a daily work-report draft instead of a direct Apple Notes edit.

## Default Shape

- Draft one date block per day.
- Use Joey's usual short title style with an abbreviated weekday token, for example `YYYY.MM.DD (Fri)` rather than `YYYY.MM.DD (Friday)`.
- Under each day, group work by project or another stable report bucket rather than by raw session/thread title.
- Prefer a short top-level numbered list where each top-level item is the bucket label itself.
- For stable project buckets, the top-level item should normally be the project tag itself, such as `1. #HDR-streaming`.
- For recurring meeting/sprint buckets that already have a literal house style in Joey's notes, preserve that literal label instead of forcing a `#tag`, for example `1. 83/5 Virtual Scrum`.
- Under each top-level bucket, prefer indented numbered sub-items with one or two concise lines.
- Do not flatten the bucket label into prose such as `1. HDR-streaming` followed by free-standing paragraphs.

## Preferred Tone

- Concise, report-ready, and easy to paste.
- More polished than raw evidence, but still faithful to the actual work.
- Do not turn the report into a forensic transcript.
- Do not over-explain confidence levels inside the English draft unless Joey asks for that.

## What To Optimise For

- Joey's accepted default is the shorter report style rather than the more exhaustive reconstruction.
- The draft should usually capture the main project buckets, the user-visible outcome, and one or two concrete work themes.
- Prefer about `2-4` top-level buckets for an ordinary day unless the evidence clearly justifies more.
- Collapse repeated review/re-run/recheck loops into one concise line unless the distinction matters.
- Prefer project grouping such as `#HDR-streaming`, `#Miku-bot`, `#GitHub-agent`, `#Codex-maintenance`, `#WME`, or another known report bucket from Joey's mapping.

## Empty Or Weak-Evidence Days

- If a day has no clear Codex-traceable work, use a single short line such as `No clear Codex-traceable work captured.`
- If some evidence looks personal or non-reportable and Joey has not pre-cleared that category, keep it out of the draft and explain the caveat outside the code block.

## Notes For The Assistant

- Put evidence caveats, remote-host coverage notes, and exclusion decisions in Chinese outside the draft.
- Keep the English draft itself clean and pasteable.
- When in doubt, copy the exact visual rhythm from [report-drafting-example.md](report-drafting-example.md) rather than inventing a new bucket/list style.
- If Joey asks for a "more comprehensive" version, expand each project bucket modestly before switching to a much longer exhaustive style.
