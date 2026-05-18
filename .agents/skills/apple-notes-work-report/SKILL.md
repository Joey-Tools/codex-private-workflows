---
name: apple-notes-work-report
description: Audit and safely patch Joey's Apple Notes daily work reports on macOS. Use when Codex needs to compare notes in the `Work Report` folder against `~/.codex` history, identify likely omissions or misreports, or edit note content while preserving existing list structure, `#tag` markers, project grouping, and whole-store raw-restore preconditions for tagged-note states.
---

# Apple Notes Work Report

## Overview

Use this skill for Joey's Apple Notes-based daily work report workflow on macOS.
It covers read-only auditing, evidence collection from `~/.codex`, two-tier backup before edits, and minimal note updates that preserve Joey's original structure and tags.

## Trigger Phrases

- "帮我检查 Apple Notes 里的日报有没有漏报"
- "对照 ~/.codex 看最近一周 Work Report 写得对不对"
- "把这几条补进 Apple Notes 日报里"
- "修一下这篇 Notes，但别破坏原来的 list 和 #tag"
- "先完整备份这个有 tag 的 note，确保可以原样还原"
- "试试看能不能在新建的 note 里写 #tag"

## Evidence Sources

- Primary source for note content: `Notes.app` AppleScript automation.
- Primary source for local work evidence: `~/.codex/history.jsonl`, `~/.codex/session_index.jsonl`, and relevant files under `~/.codex/sessions/`.
- For daily work-report audits and draft generation, start with `$remote-host-context` preflight across `miku-bot-dev` and `hoteng-srv-01` before deciding the evidence set is complete, even when the local `~/.codex` history already looks dense.
- Treat long-lived remote sessions as a default risk for false local completeness; merge remote `~/.codex` evidence from `miku-bot-dev` and `hoteng-srv-01` before deciding an item is missing or concluding the day was fully covered by local-only traces.
- For local manual terminal work that may not have clean Codex traces, use supplemental read-only evidence from `~/.codex/shell_snapshots/`, local shell history such as `~/.zsh_history` / `~/.bash_history`, and iTerm text logs under `~/.dotfiles/.iterm_input_logs/`.
- Treat those shell and iTerm sources as secondary evidence that can confirm manual commands, cwd, and host context, but do not let them override stronger note text or Codex rollout evidence on their own.
- Use [references/supplemental-shell-evidence.md](references/supplemental-shell-evidence.md) for the bounded search order, the known parsing traps, and the preferred extraction recipes.
- Use [references/remote-rollout-bounding.md](references/remote-rollout-bounding.md) when a relevant remote rollout exceeds the helper fetch limit and the report still needs a defensible inclusion decision.
- Do not assume the local machine's `~/.codex` is complete just because remote-host work is not obvious from the local traces.
- Do not silently fall back from Apple Notes to stale local copies. If Notes access is blocked, say so and resolve permissions or ask the user to export the target notes.

## Preferred Access Path

- Prefer `Notes.app` AppleScript for reading and writing note content.
- When available, prefer the repo-local wrapper `bash scripts/apple_notes_helper.sh` for folder-level AppleScript preflight, `Work Report` note readback by title prefix, DB authorization probe, copy-to-`/tmp`, merged-analysis DB creation, and hashtag readback. Earlier Codex-hosted failures suggested a wrapped-`osascript` caveat, but a later user-side probe plus an escalated wrapper run showed that this is host-context-specific rather than a universal scripting limit.
- Do not assume direct access to the Apple Notes container database will work; macOS privacy protections may block it even when normal shell reads succeed elsewhere.
- If the user names a specific folder, verify that folder exists first. If not specified, confirm the exact folder before editing.
- Treat AppleScript note exports as incomplete for hashtag fidelity.
- `body` and `plaintext` may omit `#tag` markers that are visible in the Notes UI.
- Do not conclude that a tag is absent just because AppleScript export does not show it.
- If the user says a tag exists, treat that as authoritative unless a stronger source disproves it.
- If exact restoration of an existing tagged note matters, AppleScript export is not enough.
- Require a physical backup of `NoteStore.sqlite`, `NoteStore.sqlite-wal`, and `NoteStore.sqlite-shm` from a Notes-quit state before claiming raw restore is possible.
- If a local Apple Notes DB guardrail workflow or skill is available, use it for authorization probing, copy-first analysis, fingerprint capture, and writeback gating.
- If Joey asks where a `#tag` sits relative to nearby report lines, upgrade from AppleScript readback to copied-DB analysis.
- Use the note row's `ZNOTEDATA` pointer plus hashtag child rows to reconstruct likely text order and infer where inline tag placeholders sit relative to neighboring paragraphs.

## Audit Workflow

1. Export the candidate notes from the target Apple Notes folder.
- If the target is a daily `Work Report` note and the title prefix is already known, prefer `bash scripts/apple_notes_helper.sh show-note-prefix --folder "Work Report" --prefix YYYY.MM.DD` before falling back to ad hoc inline AppleScript.
2. Collect evidence for the same date range from `~/.codex`, but for daily report audits or drafts run `$remote-host-context` preflight first and merge any matching remote rollout evidence before deciding coverage is complete.
3. If a relevant remote rollout fails to copy because `remote_codex_probe.py fetch-rollout` hits the helper size limit, do not stop at that first failure.
- Use `session-meta` to bracket the same-day activity for that repo/path.
- Fetch one or more smaller same-day rollouts from the same repo/path as representative samples.
- If those bounded samples already establish reportable work, draft a conservative bucket from the proven subset and keep only the missing later-session detail as a caveat outside the English draft.
4. If the day may include local manual terminal work outside the clean Codex transcript, add bounded supplemental evidence from `~/.codex/shell_snapshots/`, `~/.zsh_history` / `~/.bash_history`, and `~/.dotfiles/.iterm_input_logs/` before deciding the current draft is complete.
5. Use shell history as the cleaner command source and use iTerm text logs mainly to confirm terminal path, host, and interactive context around those commands.
6. Classify findings:
- High confidence: directly supported by note text, session text, or clearly matching command/session evidence.
- Medium confidence: strongly implied by the same day's sessions but not written as a clean completed item.
- Low confidence: plausible inference only. Do not auto-apply these edits without the user's approval.
7. Present omissions separately from confirmed matches. Do not blur "missing from note" with "not proven by the available evidence".

## Drafting Style

- When Joey asks for a draft rather than a direct Apple Notes edit, prefer a concise day-by-day report that he can paste manually.
- Default to the shorter accepted style: one date header per day, a few numbered project buckets, and one or two short lines per bucket.
- Keep caveats, confidence notes, and scope-boundary explanations outside the English draft unless Joey explicitly asks for them inline.
- Use [references/report-drafting-style.md](references/report-drafting-style.md) for the preferred shape and fallback rules.
- When the exact bucket/list formatting matters, also follow [references/report-drafting-example.md](references/report-drafting-example.md) instead of improvising a new layout.

## Default Inclusion Boundary

- Do not assume every scanned session belongs in Joey's work daily report.
- Only auto-exclude the specific repos Joey has already confirmed should stay out of the daily report:
  - `chaldea`
  - `Joey-IM-bot-personal`
  - `ESP/ESP32-S3-LVGL`
- Also auto-exclude the already-confirmed non-work workflow family around `plan.md`, team-battle strategy planning, and related `Flutter` / `FVM` setup chatter, even when the scanned prompt does not expose the underlying repo path.
- For any other scanned repo or task that looks personal, hobby-like, or non-reportable, do not auto-exclude it. Surface it to Joey first and ask whether it should count.
- Do not silently exclude generic local housekeeping such as cache cleanup, one-off machine tidying, or other non-project maintenance unless Joey has pre-cleared that category.
- Work-adjacent meta/tooling can still count when Joey has kept it in reports, for example Apple Notes / Codex workflow maintenance or HDR prototyping work.
- Active engineering/tooling repos remain reportable even when a given same-day slice is docs-heavy, review-heavy, or mostly project-record maintenance, as long as the evidence shows they were being advanced rather than merely browsed.
- `webex-message-archiver` is reportable work by default. Do not demote it to "needs scope confirmation" just because a given slice is short or review-only.
- `copilot-code-review-tool` is reportable work by default. Do not demote it to "needs scope confirmation" just because a given slice is remote-only on `miku-bot-dev`, docs-heavy, review-heavy, or centered on project-record cleanup.
- When the boundary is unclear, classify the item as "needs scope confirmation" instead of counting it as either a missing work-report item or an excluded item.

## Edit Workflow

1. Prefer the smallest edit that satisfies the request.
- Usually this means appending one or two bullets or a short sub-block rather than rewriting the whole note.
2. Preserve Joey's original structure.
- Keep numbered-list rhythm, blank-line grouping, nested list shape, `#tag` markers, project grouping, and existing phrasing style unless the user explicitly asks for normalization.
- Treat `#tag` text as structure, not decoration. Preserve exact spelling and casing.
3. Handle Notes hashtag limitations explicitly.
- Do not rewrite a whole note just to "clean up" or reflow sections near `#tag` markers.
- Prefer minimal append-only or tightly scoped edits when a note contains UI-visible tags, because AppleScript round-trip may not preserve them faithfully.
- If script readback cannot confirm a user-visible tag, report that limitation instead of normalizing the note around the missing tag.
4. Escalate backup requirements when tagged notes are involved.
- Do not claim AppleScript-only writeback is fully reversible for an existing note that has, or may have, UI-visible tags.
- If the user wants to preserve a whole-store raw-restore path that includes such a note's current tagged state, finish a physical Notes DB file-set backup before writing.
- If only logical export is available, either keep the edit extremely small and state the limitation, or stop and ask Joey before making a riskier rewrite.
5. If the note body is HTML-like, avoid broad normalization.
- Do not "clean up" list HTML just because it looks odd in exported form.
- If a structural rewrite becomes necessary, warn the user and keep the change tightly scoped.
6. Re-read the modified note immediately after writing and compare the resulting body against expectations.
- If Notes reserializes the body in a surprising way, stop and report it before making wider edits.

## Backup Requirements Before Any Write

Do not collapse logical export backup and full-fidelity restore backup into one concept.

1. Use logical backup for ordinary audits and low-risk text edits.
- Export the target notes to a local file.
- Validate the backup file immediately.
- Confirm it exists and is non-empty.
- Confirm it contains the real note body, not placeholder text, object references, or only metadata lines.
- Perform an independent readback.
- Either re-export again to a second buffer/file or read the target notes back from Apple Notes and compare note titles plus representative body content.
2. Use full-fidelity physical backup before claiming a whole-store raw-restore path is available for the current tagged-note state.
- Quit Notes before capturing the backup.
- Copy the live `NoteStore.sqlite`, `NoteStore.sqlite-wal`, and `NoteStore.sqlite-shm` file set into a separate backup directory.
- Validate that the copied file set exists and is non-empty where expected.
- Capture checksums and file metadata as a baseline fingerprint.
- Keep the untouched restore copy separate from any analysis copy.
- This protects whole-store raw restore, not a supported per-note surgical restore path.
3. If only logical backup exists, say explicitly that text can be recovered but tag fidelity is not guaranteed.
4. Never claim a backup succeeded without reporting which level was completed and how it was validated.

## New Note Tag Creation

1. Only attempt tag creation in a newly created note or a disposable probe note.
- Do not use a risky tag-writing experiment on an existing note unless Joey explicitly wants that risk.
2. Write standalone `#tag` tokens.
- Keep them as separate text tokens with clear whitespace or line boundaries.
- Avoid embedding them inside URLs, iCloud links, or other long strings.
3. Re-read the new note through AppleScript after writing.
- Confirm that the literal `#tag` text survived the round-trip.
4. When database access is available, confirm actual Notes hashtag recognition from a copied snapshot.
- Look for child rows with `ZTYPEUTI1 = com.apple.notes.inlinetextattachment.hashtag`.
- Read `ZALTTEXT` to confirm the visible `#tag`.
5. When Joey also cares about the tag's position in the note, inspect the copied note-data blob too.
- Read the note row's `ZNOTEDATA` pointer.
- Export `ZICNOTEDATA.ZDATA`, verify the blob header first, and only decompress it when it still matches the expected gzip encoding.
- Use surrounding text offsets plus raw-byte inline-object placeholder offsets to infer whether a tag placeholder sits before the expected block.
- Do not claim an exact mapping from a specific hashtag child row to a specific placeholder unless another structure signal proves it.
6. If DB-level confirmation is unavailable, report "tag text written" rather than "tag created".
7. If a disposable probe note fails to materialize the expected tag, do not assume the same write pattern is safe for the real note.

## Reporting Back To Joey

- State which notes were inspected.
- State which evidence hosts were checked (`local`, `miku-bot-dev`, `hoteng-srv-01`) and which ones contributed matching evidence, were stale, or had no relevant sessions.
- State whether supplemental local shell evidence (`shell_snapshots`, shell history, iTerm text logs) changed the result, and if so whether it only corroborated existing items or surfaced likely missing manual work.
- Separate "confirmed OK", "likely omitted", and "uncertain" items.
- Reserve "uncertain" for item-level evidence gaps that could still materially change what belongs in the paste-ready draft.
- Do not keep an otherwise clearly reportable repo outside the draft merely because some later same-day remote rollouts exceeded the helper fetch limit; if smaller sampled rollouts already establish the work, include a conservative bucket and mention only the bounded-detail caveat outside the code block.
- When relevant, add a separate "auto-excluded by prior agreement" bucket for the pre-cleared repos and task families above.
- Use "needs scope confirmation" only for genuine inclusion-boundary ambiguity after applying the default inclusion rules above, not for active reportable repos already established by same-day commit, review, tests, or project-record maintenance evidence.
- If edits were applied, state exactly which note titles changed.
- State whether backup reached the logical level only, or the full-fidelity physical level.
- For tagged-note edits, state explicitly whether a whole-store raw restore of the original file set remains available.
- For new-note tag attempts, state whether confirmation is text-level only or DB-level.
- If AppleScript export could not see UI-visible `#tag` markers, state that limitation explicitly and avoid claiming the tag was removed.
- If backup validation failed or list/tag preservation remains uncertain, state that explicitly instead of implying the note is safely updated.
