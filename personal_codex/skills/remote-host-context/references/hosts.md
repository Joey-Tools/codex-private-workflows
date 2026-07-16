# Verified Hosts

The original hosts were verified on 2026-03-10.
`codex-hoteng-srv-01` was added on 2026-06-19 and re-verified on 2026-07-15.
`BL-mac-mini-m4-hoteng` was added and verified on 2026-07-15.
Re-check these assumptions if a host starts returning stale or missing evidence.

## Local Machine

- Primary evidence root: `~/.codex`
- Use for local GUI state, Apple Notes, local repo worktrees, and the current desktop session.

## BL-mac-mini-m4-hoteng

- SSH alias: `BL-mac-mini-m4-hoteng`
- Verified remote hostname on 2026-07-15: `mac-m4-bl-06.local`
- Verified login user: `hoteng`
- Verified login home: `/Users/hoteng`
- Verified Codex root: `/Users/hoteng/.codex`
- Verified tools:
  - `rg`
  - `python3`

## miku-bot-dev

- User-facing names seen so far:
  - `miku-bot-dev` (SSH alias)
  - `miku-server-dev` (Joey shorthand)
- Verified login home: `/home/hoteng`
- Verified Codex root: `/home/hoteng/.codex`
- Verified subdirectories:
  - `/home/hoteng/.codex/sessions`
  - `/home/hoteng/.codex/skills`
- Verified recent rollout window on 2026-03-10:
  - recent files reached `2026-03-10`

## hoteng-srv-01

- SSH alias: `hoteng-srv-01`
- Verified login home: `/home/hoteng`
- Verified Codex root: `/home/hoteng/.codex`
- Verified subdirectories:
  - `/home/hoteng/.codex/sessions`
  - `/home/hoteng/.codex/skills`
- Verified recent rollout window on 2026-03-10:
  - recent files reached `2026-02-20`
- Verified container note:
  - at least one dev-shell-kit container mounted `/home/hoteng/.codex` into the container
  - default policy should still treat the host path as canonical

## codex-hoteng-srv-01

- SSH alias: `codex-hoteng-srv-01`
- Verified remote hostname on 2026-06-19: `hoteng-srv-01`
- Verified login user: `codex`
- Verified login home: `/home/codex`
- Verified Codex root: `/home/codex/.codex`
- Verified tools:
  - `rg`
  - `python3`
- Host note:
  - this reaches the same machine hostname as `hoteng-srv-01`, but with a distinct login user and Codex evidence root
  - keep both aliases in the default evidence scope; do not deduplicate them by hostname

## Preflight Shape

Use a minimal read-only preflight before deeper reads:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 <host> \
  'printf "HOST=%s\nUSER=%s\nHOME=%s\n" "$(hostname)" "$(whoami)" "$HOME";
   test -d "$HOME/.codex" && echo CODEX_DIR=present || echo CODEX_DIR=missing'
```

Then narrow to date-bounded session trees or rollout files before opening repo-specific paths.

Keep recurring approvals anchored to this short preflight shape.
If deeper remote reads start repeating across sessions, add a helper under `~/.codex/skills/remote-host-context/` instead of preserving host- or query-specific shell literals in `default.rules`.

## Codex Thread Locator Skim

When Joey supplies a `codex://threads/<id>` URL, `read_thread` is forbidden unless the service supports and the caller sets every one of these server-side controls:

- accepted item-type filtering that admits only selected thread metadata and user or agent messages
- an item-count limit that bounds the entire result and permits no more than 12 message snippets
- a whole-response byte cap of at most 32 KiB (32,768 bytes) on the encoded response, applied before any payload reaches the caller

Those controls are additional to reading one exact thread per call with:

```text
turnLimit: 1
includeOutputs: false
maxOutputCharsPerItem: 400
```

The service itself must emit only thread id, host id, title, status, cwd, created/updated timestamps, and at most 12 user or agent message snippets of at most 400 characters each, rendered on one line per snippet. The 32 KiB cap is an upper bound, not a promise that every snippet will be returned at maximum length: 12 snippets at 400 Unicode characters can consume up to 19,200 raw UTF-8 bytes, leaving 13,568 bytes for metadata and encoding overhead, and the service must shorten or omit snippets whenever the final encoded response would exceed 32,768 bytes. It must exclude reasoning, tool calls, tool outputs, file-change payloads, and other retained artifacts before returning. `turnLimit`, a per-item character cap, an output cap on the parent tool call, and caller-side projection do not bound item count or whole-response bytes. Do not batch several thread reads or stringify or serialize the raw result.

If any required server-side control is unavailable, including the whole-response controls missing from the observed API, do not call `read_thread` at all:

- When the creation date is known, run `session-meta` only against that creation-date directory and, when the creation timestamp crosses a calendar boundary, its bounded UTC/host-local date variant; filter the bounded results by the exact session id.
- When only an activity or updated date is known, or no date is known, first use a bounded metadata-only exact-thread or session-index lookup that cannot return transcript items to derive the creation date. Then run `session-meta` against the derived creation-date directory and filter by the exact session id. If that lookup is unavailable, ask Joey for or derive the creation date from adjacent metadata. Never scan activity-date directories as a proxy, and never widen `read_thread` to discover the date.

The canonical rollout remains under its creation date when a thread continues across days. After locating it, use `rollout-summary` for coarse triage or `chunked-rollout-summary` to enumerate the complete byte/record range map rather than widening `read_thread`.

Treat the bounded latest turn and both summary commands as locator and triage output, not as permission to discard task history. A summary may emit only a representative or last user message from a chunk, so a later wrapper or noise record can obscure a substantive follow-up in that same chunk. Summary content may choose candidates for coarse triage only; it must never decide omissions for full task reconstruction.

For full task reconstruction, sort every `chunk_meta` row by `byte_start` and consume all of them. For each row, fetch every listed `fetch_ranges[]` entry in order, or fetch the row's complete `byte_start`/`byte_end` range when no split ranges are listed. Use sequential bounded `fetch-rollout-chunk` calls, verify that the combined ranges start at byte 0, remain gap-free and non-overlapping, and end at `source_bytes`, then reassemble the complete exact JSONL record stream in byte order. Hand that complete stream to `codex-session-mining` for replay-prefix and wrapper filtering; do not pre-filter chunks based on summary text, `raw_fetch_recommended`, or whether the summary exposed a user message.

Current dedicated helper path for those repeated remote Codex reads:

```bash
python3 "$HOME/.codex/skills/remote-host-context/scripts/remote_codex_probe.py" preflight --host local --host BL-mac-mini-m4-hoteng --host miku-bot-dev --host hoteng-srv-01 --host codex-hoteng-srv-01
```

Use `session-meta` only to enumerate canonical rollout candidates, then hand the copied rollout back to `codex-session-mining` locally for the actual transcript search and filtering.
Concrete helper shapes for the two most common follow-up reads:

```bash
python3 "$HOME/.codex/skills/remote-host-context/scripts/remote_codex_probe.py" session-meta \
  --host miku-bot-dev \
  --date 2026/03/29

python3 "$HOME/.codex/skills/remote-host-context/scripts/remote_codex_probe.py" fetch-rollout \
  --host miku-bot-dev \
  --rollout sessions/2026/03/29/rollout-2026-03-29T08-27-20-019d38b4-6875-7ba1-acf1-491c64a875b3.jsonl \
  --output .codex-tmp/remote-host-context/miku-bot-dev-019d38b4-6875-7ba1-acf1-491c64a875b3.jsonl
```

`session-meta` takes `--date YYYY/MM/DD`, not `YYYY-MM-DD`.
`fetch-rollout` takes one destination file via `--output`; point it at a task-scoped file path, not a directory.
Use `rollout-summary` only for fast bounded prefix triage. If a rollout is too large to copy cleanly, use `chunked-rollout-summary` for the complete whole-rollout range map. For full task reconstruction, fetch every range from every `chunk_meta` row with sequential `fetch-rollout-chunk` calls; summary rows alone are not sufficient evidence of all human follow-ups and must not choose omissions.
Concrete bounded-read shape:

```bash
python3 "$HOME/.codex/skills/remote-host-context/scripts/remote_codex_probe.py" rollout-summary \
  --host miku-bot-dev \
  --rollout sessions/2026/03/25/rollout-2026-03-25T21-45-29-019d16fd-9cc9-7ef4-8f8e-69d3bca6a3f6.jsonl \
  --keyword "git commit" \
  --keyword "No findings" \
  --limit 20 \
  --tail-records 6 \
  --max-text-chars 400
```

`rollout-summary` scans only a bounded prefix and emits a small structured skim. `chunked-rollout-summary` scans the file in bounded chunks but still emits lossy summaries; for full reconstruction, use all of its coordinates to fetch the complete exact record stream before delegating interpretation to `codex-session-mining`.
If a host has an explicitly verified `archived_sessions/rollout-*.jsonl` path, use `fetch-rollout` directly for that one file instead of turning `session-meta` into a broad archived-session search.
