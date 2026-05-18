# Apple Notes DB Commands

## Repo-Local Helper

Prefer the repo-local helper before ad-hoc shell quoting:

```bash
bash scripts/apple_notes_helper.sh probe-notes
bash scripts/apple_notes_helper.sh list-folders
bash scripts/apple_notes_helper.sh probe-db-access
bash scripts/apple_notes_helper.sh copy-db --require-notes-quit
bash scripts/apple_notes_helper.sh merge-db --src /tmp/apple-notes-probe-YYYYMMDD-HHMMSS/group.com.apple.notes/NoteStore.sqlite
bash scripts/apple_notes_helper.sh note-tags --db /tmp/apple-notes-probe-YYYYMMDD-HHMMSS/NoteStore-merged-for-analysis.sqlite --title "2026.03.06 (Fri) 82/1 AV1"
bash scripts/apple_notes_helper.sh fingerprint-db
```

This keeps future approval prefixes narrow and avoids repeating fragile inline `osascript` / `sqlite3` quoting.
In the Codex-hosted run that motivated this helper, wrapped AppleScript sometimes behaved differently from the same direct command. A later user-side probe plus an escalated wrapper run showed that this is not a universal property of AppleScript wrapping, so prefer the wrapper as the stable approved-prefix entrypoint.

## Authorization Probe

Use a real listing or copy probe, not just path existence:

```bash
for path in \
  "$HOME/Library/Group Containers/group.com.apple.notes" \
  "$HOME/Library/Containers/com.apple.Notes"
do
  printf '== %s ==\n' "$path"
  find "$path" -maxdepth 1 -mindepth 1 | head -n 5
done
```

If that still hits `Operation not permitted`, Joey needs to grant permission or run the copy from a privileged terminal.

## Copy To `/tmp`

Prefer to quit Notes first, even for read-only work.
If Joey keeps Notes open, treat the copied dataset as tentative and recopy after quitting Notes before any writeback planning.
Do not use an "open-Notes" copy for absence checks or exact tag-count claims.

```bash
dest="/tmp/apple-notes-probe-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$dest/group.com.apple.notes" "$dest/com.apple.Notes"

src1="$HOME/Library/Group Containers/group.com.apple.notes"
src2="$HOME/Library/Containers/com.apple.Notes"

rsync -a "$src1/NoteStore.sqlite" "$dest/group.com.apple.notes/"
rsync -a "$src1/NoteStore.sqlite-wal" "$dest/group.com.apple.notes/" 2>/dev/null || true
rsync -a "$src1/NoteStore.sqlite-shm" "$dest/group.com.apple.notes/" 2>/dev/null || true

# Copy additional sqlite files only when the task actually depends on them.
# Example:
# rsync -a "$src2/Data/Library/HTTPStorages/com.apple.Notes/httpstorages.sqlite"* "$dest/com.apple.Notes/" 2>/dev/null || true

printf 'dest=%s\n' "$dest"
find "$dest" -type f -print -exec ls -lh {} \;
```

## Create A Merged Analysis Copy

```bash
src_db="$dest/group.com.apple.notes/NoteStore.sqlite"
merged_db="$dest/NoteStore-merged-for-analysis.sqlite"

sqlite3 "$src_db" ".backup '$merged_db'"
```

Run `sqlite3` and `strings` against `"$merged_db"` after that.

## Find A Note And Its Hashtags

Find the note row first:

```sql
SELECT Z_PK, ZIDENTIFIER, ZTITLE1
FROM ZICCLOUDSYNCINGOBJECT
WHERE ZTITLE1 = '2026.03.06 (Fri) 82/1 AV1';
```

Then fetch hashtag child rows:

```sql
SELECT Z_PK, ZIDENTIFIER, ZNOTE1, ZALTTEXT, ZTOKENCONTENTIDENTIFIER, ZTYPEUTI1
FROM ZICCLOUDSYNCINGOBJECT
WHERE ZNOTE1 = 1677
  AND ZTYPEUTI1 = 'com.apple.notes.inlinetextattachment.hashtag'
ORDER BY Z_PK;
```

## Recover Note Text Order And Tag Placement

When AppleScript body export is too lossy, follow the note-data blob referenced by `ZNOTEDATA`.

Find the note row and note-data pointer:

```sql
SELECT Z_PK, ZTITLE1, ZNOTEDATA
FROM ZICCLOUDSYNCINGOBJECT
WHERE ZTITLE1 = '<note title>';
```

Check the raw note-data record:

```sql
SELECT Z_PK, ZNOTE, length(ZDATA) AS data_len
FROM ZICNOTEDATA
WHERE Z_PK = <note_data_pk>;
```

Export the blob, verify the header, then decompress it only if it still looks gzip-encoded:

```bash
sqlite3 "$merged_db" \
  "SELECT quote(ZDATA) FROM ZICNOTEDATA WHERE Z_PK=<note_data_pk>;" \
| sed -e "s/^X'//" -e "s/'$//" \
| xxd -r -p \
> "$dest/note-data.blob"

xxd -l 16 "$dest/note-data.blob"

if xxd -p -l 2 "$dest/note-data.blob" | grep -Fxq '1f8b'; then
  gzip -dc "$dest/note-data.blob" > "$dest/note-data.raw"
else
  echo "Unexpected blob encoding or extraction mismatch; stop and inspect before continuing." >&2
  exit 1
fi
```

If the exported blob does not start with the gzip header `1f 8b`, do not assume schema drift immediately.
The problem may be a wrong `ZNOTEDATA` pointer, an incomplete copy, or a broken export step.

Read text runs with byte offsets:

```bash
strings -a -t d "$dest/note-data.raw" \
| rg "<title or nearby text>"
```

Search exact byte offsets for the surrounding text:

```bash
rg -aob \
  "<title or nearby text 1>|<nearby text 2>|<nearby text 3>" \
  "$dest/note-data.raw"
```

Notes often stores inline tag attachments as placeholders in the main text stream.
To locate those placeholders:

```bash
perl -0777 -ne 'while(/\xEF\xBF\xBC/g){ print((pos() - 3), "\n") }' "$dest/note-data.raw"
```

`efbfbc` is the UTF-8 encoding of `U+FFFC`, the inline object replacement character.
This prints raw-byte offsets in the decompressed note-data stream, so the offsets are directly comparable to `strings -t d` and `rg -aob`.
Use those placeholder offsets together with the hashtag child rows to infer where an inline tag placeholder sits relative to neighboring paragraphs.
Do not overclaim a one-to-one mapping between a specific hashtag child row and a specific placeholder unless additional note structure proves it.

Confirm the corresponding hashtag child rows separately:

```sql
SELECT Z_PK, ZNOTE1, ZALTTEXT, ZTOKENCONTENTIDENTIFIER, ZTYPEUTI1
FROM ZICCLOUDSYNCINGOBJECT
WHERE ZNOTE1 = <note_pk>
  AND ZTYPEUTI1 = 'com.apple.notes.inlinetextattachment.hashtag'
ORDER BY Z_PK;
```

## Capture A Source Fingerprint

Run this before a planned writeback, then run it again immediately before replacement:

```bash
db_dir="$HOME/Library/Group Containers/group.com.apple.notes"

find "$db_dir" -maxdepth 1 -type f \
  \( -name 'NoteStore.sqlite' -o -name 'NoteStore.sqlite-wal' -o -name 'NoteStore.sqlite-shm' \) \
  -print0 \
| sort -z \
| xargs -0 shasum -a 256

find "$db_dir" -maxdepth 1 -type f \
  \( -name 'NoteStore.sqlite' -o -name 'NoteStore.sqlite-wal' -o -name 'NoteStore.sqlite-shm' \) \
  -exec stat -f '%z %m %N' {} \; \
| sort
```

If the second fingerprint differs, abort and recopy from source.
