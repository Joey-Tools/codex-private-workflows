# Supplemental Shell Evidence

Use these sources only as bounded, read-only supplements for work-report audits.
They are most useful when Joey did meaningful local terminal work that may not have a clean Codex transcript.

## Preferred Order

1. `~/.codex/shell_snapshots/`
- Best for quickly confirming a Codex shell's cwd, environment shape, and which thread/session owned the shell.
- Bound by thread id or the target date before reading.
- Do not dump raw snapshots into the user answer. They can contain secrets in exported environment variables.

2. `~/.zsh_history` or `~/.bash_history`
- Best for recovering the cleanest local command lines.
- Prefer `~/.zsh_history` on Joey's current macOS setup.
- Parse the bounded date window first; do not grep the whole file blindly.

3. `~/.dotfiles/.iterm_input_logs/`
- Use the date-prefixed text logs as corroborating terminal evidence.
- These logs are better for host, cwd, tab, and interactive context than for exact command reconstruction.
- Prefer the text logs here over `~/Library/Application Support/iTerm2/*.sqlite` for this workflow.

## Known Pitfalls And Fixes

### Pitfall: raw shell snapshots leak too much

- Problem:
  - snapshots often include full exported environments and can expose tokens or unrelated machine state.
- Fix:
  - inspect them locally only;
  - quote or summarize only safe command/path/cwd evidence in the report reasoning.

### Pitfall: local Codex evidence looks dense but still misses manual terminal work

- Problem:
  - Joey may keep long-lived shells, resume remote sessions, or run local commands outside the most obvious Codex transcript.
- Fix:
  - after remote-host preflight, always do a bounded local shell pass for daily report drafts before declaring the day fully covered.

### Pitfall: broad `rg` over iTerm logs explodes into ANSI noise

- Problem:
  - `~/.dotfiles/.iterm_input_logs/` contains large text captures with escape sequences, backspaces, and very large long-running `tmux` logs.
- Fix:
  - bound by day prefix first, for example `20260325*.log`;
  - search for stable repo/host/command tokens before opening files;
  - prefer smaller logs before huge `tmux` logs when a quick confirmation is enough;
  - strip ANSI/control sequences before extracting lines for manual inspection.

### Pitfall: iTerm logs are not the cleanest command source

- Problem:
  - edited input, backspaces, and prompt redraws make exact commands harder to recover than from shell history.
- Fix:
  - treat shell history as the primary command source;
  - use iTerm logs mainly to confirm host, cwd, terminal context, or that a command really happened in a given repo/window.

### Pitfall: iTerm SQLite looks tempting but is the wrong source here

- Problem:
  - `~/Library/Application Support/iTerm2/chatdb.sqlite` and saved-state SQLite files are not the simplest command-history source for this workflow.
- Fix:
  - stay on `~/.dotfiles/.iterm_input_logs/` text logs unless Joey explicitly asks for a deeper iTerm-internal investigation.

## Bounded Recipes

Parse the target day from `~/.zsh_history`:

```bash
python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import re

start = int(datetime(2026, 3, 25, 0, 0, 0, tzinfo=timezone.utc).timestamp())
end = int(datetime(2026, 3, 26, 0, 0, 0, tzinfo=timezone.utc).timestamp())
pat = re.compile(r'^: (\d+):\d+;(.*)$')

for line in Path.home().joinpath('.zsh_history').open('r', errors='replace'):
    m = pat.match(line.rstrip('\n'))
    if not m:
        continue
    ts = int(m.group(1))
    if start <= ts < end:
        print(ts, m.group(2))
PY
```

Search iTerm text logs for the target day with stable keywords:

```bash
rg -n -S "HDR-streaming|GitHub-agent|Codex-maintenance|miku-bot-dev|swift run HDRColorSourceApp" \
  ~/.dotfiles/.iterm_input_logs/20260325*.log
```

Strip ANSI/control sequences before manually inspecting a matched log:

```bash
python3 - <<'PY'
from pathlib import Path
import re

ansi = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[PX^_].*?\x1b\\|[@-_])', re.S)
path = Path.home() / '.dotfiles/.iterm_input_logs/20260325_110401.Default.w17t0p0.150E6ED1-3662-43AA-8AC2-4F85763854F1.1123.1377899486.log'
text = ansi.sub('', path.read_text(errors='replace')).replace('\r', '\n')
for line in text.split('\n')[:200]:
    if line.strip():
        print(line)
PY
```
