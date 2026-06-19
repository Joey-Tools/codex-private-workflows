# Verified Hosts

Verified on 2026-03-10.
`codex-hoteng-srv-01` was added and verified on 2026-06-19.
Re-check these assumptions if a host starts returning stale or missing evidence.

## Local Machine

- Primary evidence root: `~/.codex`
- Use for local GUI state, Apple Notes, local repo worktrees, and the current desktop session.

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
  - treat it as a separate default evidence host

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

Current dedicated helper path for those repeated remote Codex reads:

```bash
python3 "$HOME/.codex/skills/remote-host-context/scripts/remote_codex_probe.py" preflight --host local --host miku-bot-dev --host hoteng-srv-01 --host codex-hoteng-srv-01
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
If a rollout is too large to copy cleanly, prefer `rollout-summary` to skim bounded assistant/tool-output evidence on the remote host before considering any heavier fallback.
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

`rollout-summary` still scans the validated rollout on the remote host, but it only emits a small structured skim instead of copying the whole JSONL payload back to the local machine.
If a host has an explicitly verified `archived_sessions/rollout-*.jsonl` path, use `fetch-rollout` directly for that one file instead of turning `session-meta` into a broad archived-session search.
