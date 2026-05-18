---
name: apple-notes-db-guardrails
description: Safely inspect or patch Apple Notes container databases on macOS. Use when Codex needs to probe TCC or Full Disk Access, copy `NoteStore.sqlite` to `/tmp` for tag or metadata analysis, validate Apple Notes database backups before edits, or guard a writeback by keeping Notes quit and checking source fingerprints.
---

# Apple Notes DB Guardrails

## Overview

Use this repo-local skill when Apple Notes work moves below the `Notes.app` AppleScript layer and into the protected Notes container database.

Default to read-only analysis.
Treat any writeback as a separate high-risk phase with extra gates.

## Trigger Phrases

- "看看 Apple Notes 底层数据库里有没有这个 tag"
- "先复制一份 Notes DB 到 /tmp 再分析"
- "写回前先备份并确认数据库没有变化"
- "检查 Apple Notes 的 sqlite / wal / shm"

## Authorization Gate

1. Probe authorization before planning database work.
- Check access to `~/Library/Group Containers/group.com.apple.notes` and `~/Library/Containers/com.apple.Notes`.
- If access is blocked, stop and either ask Joey to grant the needed macOS permission or ask Joey to copy the files from a privileged terminal.
2. Do not treat "file exists" as enough.
- Confirm the probe can actually list or copy the candidate database files.
3. Record which path is authoritative for this run.
- Live container access, or a user-provided `/tmp` copy.

## Safe Operating Mode

- Prefer `Notes.app` quit for any container-level work.
- For read-only investigation, a copied `/tmp` dataset is acceptable even if Notes remains open, but treat the result as tentative only.
- Do not use an "open-Notes" copy as the basis for absence checks, exact tag counts, or any writeback plan.
- For any planned writeback, require `Notes.app` to stay quit from pre-backup through post-write verification.
- Never run mutation tooling against the live container while Notes is open.

## Copy-First Workflow

Prefer the repo-local wrapper `bash scripts/apple_notes_helper.sh` for folder-level `Notes.app` preflight, DB copy, merge, and hashtag queries before falling back to ad-hoc shell pipelines.

1. Identify the source file set.
- Main target is usually `NoteStore.sqlite` plus `NoteStore.sqlite-wal` and `NoteStore.sqlite-shm`.
- Include adjacent supporting files only when they are relevant to the specific question.
2. Copy the source set into a unique timestamped directory under `/tmp`.
3. If Notes was open during the copy, treat the result as quick triage only.
- Recopy after quitting Notes before making any critical judgment or preparing a writeback.
4. Validate the copy immediately.
- Confirm the expected files exist.
- Confirm required files are non-empty when they should contain data.
5. If WAL or lock state makes direct inspection awkward, create a merged analysis database from the copied sqlite file.
- Run `sqlite3 "$copied_db" ".backup '$merged_db'"`.
- Perform all `sqlite3`, `strings`, `plutil`, and similar inspection against the merged copy, not the production file.
6. Never inspect or patch the live database in place.

## Readback Heuristics

- AppleScript `body`, `plaintext`, and `attachments` do not reliably expose Notes UI hashtags.
- In the copied database, note rows commonly live in `ZICCLOUDSYNCINGOBJECT`.
- A note title may appear in `ZTITLE1` even when `ZTITLE` is empty.
- Hashtag rows can appear as child objects where:
  - `ZNOTE1 = <note_pk>`
  - `ZTYPEUTI1 = com.apple.notes.inlinetextattachment.hashtag`
- Read `ZALTTEXT` for the visible `#tag`.
- Read `ZTOKENCONTENTIDENTIFIER` for the normalized token form.
- When tag placement relative to surrounding text matters, follow the note-data path too:
  - Read `ZNOTEDATA` from the note row in `ZICCLOUDSYNCINGOBJECT`.
  - Fetch the matching `ZICNOTEDATA.ZDATA` blob.
  - On the Notes versions tested here, `ZDATA` is gzip-compressed note content. If the blob no longer looks gzip-compressed, stop and report an unexpected blob encoding or extraction mismatch instead of guessing.
  - Inspect a decompressed copy, not the live blob.
  - Text runs often appear in-order in the decompressed payload even when AppleScript body export reflows them.
  - Inline tags usually do not survive as literal `#tag` text in the main text stream; instead, the text stream contains an inline-object placeholder while the actual hashtag lives in the child rows above.
  - Use raw-byte placeholder offsets plus neighboring text offsets to infer where an inline tag placeholder sits relative to a paragraph block.
  - Do not claim that a specific hashtag child row maps to a specific placeholder unless another structure signal proves that mapping.

## Pre-Write Safety Gates

1. Prepare and verify the intended change only on a temporary copy.
2. Create a fresh backup of the live database file set before any writeback attempt.
3. Validate that backup twice.
- Confirm the backup files exist and are non-empty where expected.
- Perform an independent readback or checksum comparison so the backup is proven usable, not merely present.
4. Capture a baseline fingerprint of the live source set before staging the final writeback.
- Use checksums plus file metadata for `sqlite`, `-wal`, and `-shm`.
5. Right before writeback, recompute the live fingerprint.
- If anything changed, abort the writeback, recopy from source, and restart analysis on the fresh snapshot.
6. Present Joey with the planned change summary and wait for explicit confirmation before touching production.

## Writeback Guardrails

- Treat true multi-file atomic swap as unavailable by default.
- Approximate atomicity by keeping Notes quit, preparing the replacement file set in advance, and making the live replacement window as short as possible.
- Never patch the production sqlite file incrementally.
- Never mix a newly edited main sqlite file with stale live WAL or SHM files.
- Prefer replacing the whole related file set from the prepared copy after the final fingerprint check.
- If the replacement mechanics are not proven safe for the current case, stop and ask Joey instead of improvising.
- Immediately verify that the live files match the staged replacement set after writeback.
- Keep the pre-write backup until Joey confirms the result is acceptable.

## Reporting Back To Joey

- State whether authorization came from direct container access or from a Joey-provided `/tmp` copy.
- State the exact temp directory used for analysis.
- State whether Notes was quit during the sensitive steps.
- Separate read-only findings from writeback actions.
- If writeback was blocked by authorization, a fingerprint mismatch, or unproven replacement mechanics, say so explicitly.
