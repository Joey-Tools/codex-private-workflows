# Session Shards v1

## Scope And Compatibility

`session-shards` is the only SSH/transport primitive for Session Retrospective
v2. It provides a bounded, read-only stream for one validated Codex rollout.
The supervisor must not fill a v2 transport gap with bare SSH, a child agent, or
another `remote-host-context` command.

The command is additive. It does not replace or change `session-meta`,
`fetch-rollout`, `fetch-rollout-chunk`, `rollout-summary`, or
`chunked-rollout-summary`; existing non-v2 callers keep their current behavior.
Only `session-shards` additionally accepts a root-level `rollout-*.jsonl` path.

The transport has two rollout-stream modes:

- `descriptors` emits content-free shard or gap ranges.
- `records` emits the valid JSONL records, or explicit content-free gaps, for
  one exact descriptor range.

There is also one terminal-only shadow qualification mode:

- `holdout-receipt` emits one authenticated, content-free controlled
  missing-host receipt. It never opens a rollout or starts SSH and is not a
  source scan, `no_activity` result, record gap, or transport failure.

Accepted rollout references are:

- `sessions/YYYY/MM/DD/rollout-*.jsonl`
- `archived_sessions/rollout-*.jsonl`
- `archived_sessions/YYYY/MM/DD/rollout-*.jsonl`
- `rollout-*.jsonl` for `session-shards` only

## CLI

Start descriptor enumeration at byte zero:

```bash
python3 /Users/hoteng/.codex/skills/remote-host-context/scripts/remote_codex_probe.py \
  session-shards \
  --host miku-bot-dev \
  --rollout sessions/2026/07/14/rollout-example.jsonl \
  --emit descriptors
```

Resume from an accepted paginated terminal frame:

```bash
python3 /Users/hoteng/.codex/skills/remote-host-context/scripts/remote_codex_probe.py \
  session-shards \
  --host miku-bot-dev \
  --rollout sessions/2026/07/14/rollout-example.jsonl \
  --emit descriptors \
  --byte-start 524288 \
  --source-token session_shards_source_v1:0123456789abcdef \
  --resume-cursor session_shards_resume_v1:opaque-authenticated-value
```

Transfer one exact ready range:

```bash
python3 /Users/hoteng/.codex/skills/remote-host-context/scripts/remote_codex_probe.py \
  session-shards \
  --host miku-bot-dev \
  --rollout sessions/2026/07/14/rollout-example.jsonl \
  --emit records \
  --byte-start 0 \
  --byte-end 524288 \
  --source-token session_shards_source_v1:0123456789abcdef
```

Create one controlled Daily holdout receipt after creating the owner-only
invocation directory:

```bash
python3 /Users/hoteng/.codex/skills/remote-host-context/scripts/remote_codex_probe.py \
  session-shards \
  --host hoteng-srv-01 \
  --emit holdout-receipt \
  --qualification-mode shadow \
  --controlled-missing-host \
  --window-start 2026-07-13T00:00:00Z \
  --window-end 2026-07-14T00:00:00Z \
  --source-kind codex_session_history \
  --source-lease-ref source-lease:daily-partial:hoteng-srv-01:1 \
  --shadow-identity-path /Users/hoteng/Program/GitHub/Joey-Tools/codex-workspace/.codex-local/session-retrospective-v2-shadow/INVOCATION/identity \
  --create-shadow-identity
```

Use `--require-existing-shadow-identity` instead of
`--create-shadow-identity` when the invocation already has the intended
identity. Never silently create a replacement identity after a receipt has
been accepted.

Protocol limits are fixed or hard-bounded:

- `--shard-bytes` defaults to 512 KiB and cannot exceed 512 KiB.
- `--max-shards` defaults to 64 and must stay between 1 and 1024.
- `--record-processing-budget-bytes` defaults to 64 MiB, must be at least
  4 MiB and at least `shard_bytes`, and cannot exceed 256 MiB.
- Scanning one JSONL record has an independent hard 256 MiB byte ceiling. The
  producer stops at the ceiling when no delimiter has appeared; it does not
  continue reading to discover the eventual newline or EOF.
- A `records` range is non-empty, boundary-aligned, and at most 256 MiB.
- A non-zero `--byte-start` requires both the matching source token and resume
  cursor. `records` always requires a source token and `--byte-end`.
- `--byte-end` is invalid in descriptor mode.
- `holdout-receipt` accepts no rollout, byte range, source token, or resume
  cursor. It requires one canonical remote host, exactly one closed midnight
  UTC day whose end is no later than `floor(now_utc, day)`, a bounded source
  kind, the status-issued source lease ref, and one explicit owner-only shadow
  identity mode. The current UTC clock is injectable for deterministic tests.
- Normal descriptor and record requests reject every holdout-only option.
  `qualification-mode` defaults to `production`; a holdout requires both
  `--qualification-mode shadow` and `--controlled-missing-host`.

## Range Ownership And Pagination

Byte and record ranges are zero-based and half-open. Byte offsets count original
source bytes, not decoded characters. A record owns its JSON bytes and trailing
`\n` or `\r\n`; the final record may end at EOF without a delimiter.

Every source byte and record belongs to exactly one descriptor. A normal ready
descriptor contains one or more valid whole records and stays within
`shard_bytes`. One valid record larger than `shard_bytes` owns a single ready
descriptor marked for base64 fragment transport. Invalid JSON and records that
cross a processing ceiling become explicit content-free gap descriptors.

At a page boundary the producer stops after the last emitted descriptor. It
does not read, parse, or validate the next descriptor to decide whether another
page exists. The terminal frame reports the last emitted byte and record end as
`next_byte_start` and `next_record_start`, plus the matching
`next_resume_cursor`. The next page starts at those exact coordinates without a
prefix rescan.

## Integrity Bindings

### Source Token

The source token is a SHA-256 commitment over the opened file descriptor's
device, inode, mode, size, nanosecond mtime, and nanosecond ctime. The helper
validates a regular file, derives the token from `fstat`, repeats `fstat` after
the scan, and emits no terminal frame if the identity changed.

The token detects replacement, append, and in-place mutation. It is not a
content hash, EOF proof, coverage proof, authorization value, or retained
retrospective output.

### Resume Cursor

The opaque cursor is HMAC-authenticated and binds the source token, byte offset,
and next record index. A resumed request supplies the token, byte offset, and
cursor together. Forgery, stale source identity, or a coordinate mismatch is
rejected before frames are accepted.

The cursor is the only supported high-page record-index mechanism. The helper
must not recover that index by scanning from byte zero.

### Request Binding

Every frame carries one request binding derived from the schema, rollout, mode,
requested token, requested cursor, byte range, shard size, page size, and record
processing budget. The remote receiver recomputes it from its exact request and
rejects a mismatch on metadata, data, or terminal frames.

### Controlled Holdout Identity And Binding

The holdout identity path is a real current-user directory with exact mode
`0700`. Its `holdout-hmac-v1.key` is a current-user, single-link, regular
32-byte file with exact mode `0600`. Creating an identity is allowed only under
the run-local shadow artifact root or a system temporary root, and the existing
parent must already be mode `0700`. `--create-shadow-identity` fails if the
identity exists; `--require-existing-shadow-identity` never creates or repairs
it. Symlink components fail closed.
When the shadow runner launches the helper, it supplies the owner-only
invocation directory as `CODEX_SESSION_SHARDS_SHADOW_ROOT`; that explicit root
is authoritative even though the capture subprocess uses the invocation
directory as its cwd. The helper does not append another `.codex-local` suffix.

The receipt binds the canonical host, exact `[window_start, window_end)` UTC
day, source kind, and status-issued source lease ref. `holdout_ref` is the
SHA-256 commitment to that terminal state and binding. `identity_key_id`
identifies the shadow key without exposing it, and `authentication_tag` is an
HMAC-SHA-256 over the canonical receipt body and a protocol-domain separator.

The source lease ref is a one-time challenge, not reusable metadata. The
consumer validates the closed schema, receipt ref, key id, HMAC, and every
expected binding before acceptance. Acceptance uses an owner-only SQLite
ledger with `BEGIN IMMEDIATE`: insertion of the consumed `holdout_ref` and its
real backfill replacement are one transaction. Primary-key and unique
constraints reject concurrent or later replay even when every other field
still matches; a replacement uniqueness failure rolls back the consumption
insert too. A valid tag for another host, window, source kind, or source lease
is also rejected.

### Shadow Runner Enforcement

The v2 shadow automation routes every coordinator invocation through
`scripts/session_retrospective_v2_shadow_runner.py`. The runner, rather than
prompt text, enforces these boundaries before execution:

- no-argument `help` and one-time `identity --create-identity ... --shadow`
  bootstrap a fresh invocation without bypassing the runner;
- only `help`, `identity`, `doctor`, `start`, `status`, `accept-source`,
  `accept-agent-result`, `advance`, `export`, and `finalize` are accepted;
- `finalize` requires `--shadow` and one allowlisted shadow phase;
- provider, cursor, retained, publication, push, send, and production-state
  options are rejected;
- declared write paths must remain below the owner-only invocation directory,
  and the macOS child process runs under a `sandbox-exec` policy that denies
  undeclared writes outside that directory;
- `accept-source` requires a canonical host, and a thread lock plus `flock`
  serializes all source activity for that host without globally serializing
  different hosts.

The runner's `record-backfill` action holds the same host mutex while calling
the persistent holdout ledger transaction. If the runner, write sandbox, lock,
or transaction is unavailable, the scenario is blocked; direct coordinator
execution is not a fallback.

## Secure Open And Fixed Bounds

The trusted Codex root is opened once as a directory file descriptor. Each
relative component is traversed with `dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW`;
the final object is opened with `O_NOFOLLOW` plus `O_NONBLOCK` when available,
then must pass `fstat` as a regular file. This prevents a final-name FIFO swap
from blocking before type validation. If the required traversal primitives are
missing, the command fails closed. A name-based check followed by an ordinary
path reopen is not sufficient.

Records are scanned incrementally in chunks no larger than 64 KiB. A record
stays in memory only through 64 KiB, then rolls to an owner-only spool. Valid
large records are emitted in 256 KiB base64 fragments. The fixed in-memory
envelope is 4 MiB even when the configured processing budget or exact range is
larger.

The record scanner checks the independent hard byte ceiling before every read
and caps each `readline` request to the remaining allowance. A first record
with no newline therefore reads at most the advertised ceiling locally and in
the generated remote program. Crossing that limit terminates the invocation
without `stream_end`; it is not converted into a content-free gap because the
producer has not found a safe next record boundary.

UTF-8 decoding, JSON object validation, and the 512-level nesting limit are
incremental. Invalid UTF-8/JSON produces `invalid_json`; a byte-budget or depth
ceiling produces `record_processing_budget_exceeded`. Both are content-free.

The remote receiver bounds each line, performs incremental structural and depth
validation before `json.loads`, normalizes parser recursion failures, and only
then validates the exact frame schema and decodes bounded base64 payloads.
Remote diagnostics retain only the final content-free message, capped at 512
UTF-8 bytes.

## Exact Frame Schemas

The remote transport uses a closed field schema. Missing fields, unknown fields,
wrong types, duplicate JSON object fields, duplicate metadata/terminal frames,
unsupported reasons, and request-binding mismatches are fatal. The field sets
below are exact before CLI decoration. `cmd_session_shards` adds exactly `host`
and `rollout` to each JSON object printed to stdout for descriptor and record
streams.

`holdout-receipt` is deliberately separate from those stream frames. It emits
exactly one JSON object and does not receive `host`/`rollout` decoration because
host is already an authenticated binding and no rollout is read.

All transport frames contain these binding fields:

```text
kind schema mode source_token request_binding
```

### Stream Metadata

`stream_meta` contains the binding fields plus exactly:

```text
request_rollout request_source_token request_resume_cursor
source_bytes byte_start byte_end record_start
shard_bytes max_shards record_processing_budget_bytes
fixed_memory_envelope_bytes hard_record_processing_ceiling_bytes
hard_record_scan_ceiling_bytes record_fragment_bytes
json_nesting_depth_limit max_remote_frame_chars
protocol_features
```

It is first and unique. Its request fields must exactly match the invocation.

### Descriptor Frames

A descriptor has the binding fields plus exactly this base set:

```text
status byte_start byte_end record_start record_end record_count
page_shard_index resume_cursor
```

A normal `status=ready` descriptor has no additional fields. A ready descriptor
for one record larger than `shard_bytes` adds exactly:

```text
oversized_record record_transport record_fragment_bytes
record_processing_budget_bytes
```

`oversized_record` is `true` and `record_transport` is `base64_fragments`.
The receiver rejects a normal ready range larger than `shard_bytes`. A larger
range is accepted only when all oversized fields are present and consistent,
the descriptor owns exactly one record, and its byte count does not exceed the
declared record-processing budget.

A `status=gap` descriptor adds exactly:

```text
gap_reason byte_count
```

The closed gap reasons are `invalid_json` and
`record_processing_budget_exceeded`. A processing-budget gap additionally has
exactly:

```text
record_processing_budget_bytes hard_record_processing_ceiling_bytes
processing_ceiling_kind processing_ceiling_limit processing_ceiling_observed
```

`processing_ceiling_kind` is `record_bytes` or `json_nesting_depth`.

The descriptor `stream_end` contains the binding fields plus exactly:

```text
complete reason emitted_shards
byte_start byte_end record_start record_end
next_byte_start next_record_start next_resume_cursor
accounted_byte_count accounted_record_count
```

The only valid state/reason pairs are:

- `complete=true`, `reason=eof`, with all continuation fields null.
- `complete=false`, `reason=max_shards`, with authenticated continuation
  coordinates and exactly `max_shards` emitted descriptors.

### Record And Gap Frames

A normal `record` frame has the binding fields plus exactly:

```text
byte_start byte_end byte_count record_start record_end delimiter_bytes
record_encoding record_b64 record_commitment
```

`record_encoding` is `base64`. Decoded bytes must match `byte_count`, the byte
range, delimiter count, and `record_commitment`.

Each `record_fragment` has the binding fields plus exactly:

```text
byte_start byte_end byte_count record_start record_end delimiter_bytes
record_byte_start record_byte_end record_byte_count
fragment_index fragment_count record_encoding fragment_b64
fragment_commitment record_commitment
```

Fragments must be contiguous, stable across the sequence, individually
committed, and collectively match the whole-record commitment.

A record-mode `gap` has the binding fields plus exactly:

```text
byte_start byte_end byte_count record_start record_end delimiter_bytes reason
```

Its closed reasons and optional processing-ceiling fields are identical to a
gap descriptor. A gap never contains `record_b64`, `fragment_b64`, `record`,
`payload`, `raw`, or `text`.

The record `stream_end` has the binding fields plus exactly:

```text
complete reason
emitted_records emitted_gaps emitted_fragments
emitted_record_bytes emitted_gap_bytes emitted_fragment_bytes
byte_start byte_end record_start record_end conservation_proof
```

It is valid only with `complete=true` and `reason=range_complete`. The nested
`conservation_proof` has exactly:

```text
schema source_token request_binding
byte_start byte_end byte_count accounted_byte_count
record_start record_end record_count accounted_record_count
accounting_commitment
```

The counters, coordinates, commitments, and proof must account for every byte
and record in the exact requested range.

### Controlled Holdout Terminal Receipt

The exact field set is:

```text
kind schema terminal qualification_mode receipt_type reason
host window_start window_end source_kind source_lease_ref
content_free source_observed transport_attempted backfill_required
identity_key_id holdout_ref authentication_tag
```

The fixed terminal state is:

```text
kind=transport_receipt
schema=session-shards-shadow-holdout-v1
terminal=true
qualification_mode=shadow
receipt_type=controlled_missing_host_holdout
reason=shadow_qualification_controlled_missing_host
content_free=true
source_observed=false
transport_attempted=false
backfill_required=true
```

`shadow_qualification_controlled_missing_host` is the only safe reason in this
receipt schema. It is not and must never be translated to `no_activity`,
`invalid_json`, `record_processing_budget_exceeded`, timeout, unreachable,
authentication failure, or an ordinary processing/coverage gap. The receipt
contains no rollout path, source token, byte/record coordinate, record,
fragment, payload, raw text, or inferred activity.

Production mode cannot emit this schema. Omitting either explicit shadow flag,
using `local`, naming a rollout, supplying byte/token coordinates, selecting a
non-Daily window, or using an unsafe identity terminates without a receipt.

## Remote Completion

Descriptor and record execution use the existing fixed SSH argv and a
self-contained stdlib Python program. The remote helper starts no SSH process,
shell pipeline, agent, or child process. Holdout receipt creation is local and
must not probe DNS, start SSH, or inspect a Codex root.

The local side uses `Popen` and reads one bounded line at a time. Internal begin
and end markers are not exposed as JSON frames. Data frames may stream after
validation, but `stream_end` is withheld until all of these hold:

1. Exactly one begin marker was received.
2. Exactly one request-bound terminal frame was received.
3. The end marker followed the terminal frame.
4. SSH and remote Python exited successfully.
5. No malformed, oversized, duplicate, missing-fragment, or trailing frame was
   observed.

A dropped or failed SSH channel therefore cannot expose a visible
`complete=true` terminal frame.

The v2 shadow runner executes each status-authenticated `session-shards`
capture and its matching `accept-source` action under the same per-host mutex.
Before capture, it verifies the lease's `sha256:<digest>` transport program
commitment against the installed helper and executes an owner-only run-local
snapshot of exactly those verified bytes, so path replacement cannot change the
program after validation.
Capture stdout is written to a fresh owner-only temporary file inside the
invocation directory, published atomically to the authenticated stream path,
and removed after acceptance. The capture sandbox permits the exact installed
transport helper to read Codex history and use SSH, but denies writes outside
the invocation directory. The runner enforces a 90-second wall-clock limit and
a lease-derived output limit capped at 512 MiB while capture is running. A
capture failure, timeout, output-limit breach, retained descendant, command
mismatch, or atomic-publication failure blocks acceptance.
Coordinator status and action subprocesses run in supervised process groups
with 30-second and 300-second limits respectively. Timeout, oversized status
output, or a retained descendant terminates the complete process group and
blocks the invocation without retaining the host mutex indefinitely.

## Recovery And Retention

- **Stale token or cursor:** discard the invocation. Restart descriptors at byte
  zero and never mix generations.
- **Pagination:** retain only a fully terminated page. Resume from all three
  continuation values without a prefix rescan. Completion still requires the
  final stable EOF page.
- **Missing terminal/end marker, SSH failure, or timeout:** discard the current
  invocation. Retry only the same exact page/range within the existing lease;
  otherwise record an explicit transport gap.
- **Source changed:** discard the invocation and restart at byte zero.
- **Invalid JSON or processing ceiling:** preserve the exact content-free gap.
  Do not print, log, infer, or recover its source content through another
  transport.
- **Hard record-scan ceiling:** discard the incomplete invocation and report
  the source blocked. Do not scan onward for a delimiter, fabricate a resume
  boundary, or switch to another transport.
- **Misaligned range:** correct the caller from descriptor boundaries. The
  helper never widens a range.
- **Unsafe path:** fail closed. Do not repair it with a shell or weaker read.
- **Holdout replay or binding mismatch:** reject the receipt. Do not generate a
  replacement lease ref, identity, window, or host binding to make it pass.
- **Holdout identity unavailable:** block the controlled scenario. Do not
  downgrade the receipt to an unauthenticated gap or transport error.

For a later Daily backfill, the coordinator must start a distinct backfill run
with the partial run's exact run ref as its backfill lineage. Its source
contract must select only the held-out host, preserve the same window and
source kind, and carry the authenticated `holdout_ref` as the terminal receipt
being replaced. The accepted real `session-shards` transport must match all of
those bindings. The authenticated partial coverage receipt and its sole gap
must both name that exact `holdout_ref`; all other canonical hosts must be
covered, and a second gap is fatal. The coordinator atomically marks the
holdout replaced only after complete or genuine no-activity accounting for that
real source; another holdout cannot satisfy backfill. Reuse across another partial run,
host, window, source kind, identity/configuration root, or an already replaced
receipt is rejected.

Descriptor output is transient catalog material. Record and fragment frames are
raw source evidence and may flow only to the bounded supervisor intake. Never
place them in normal terminal logs, prompts, retained reports, or committed
artifacts.
