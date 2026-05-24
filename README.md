# Codex Private Workflows

这个仓库承载 private Codex overlay release。它需要保持 private，不应公开。

## Scope

- private `AGENTS.md`
- private reviewer agent config
- private personal skills and private variants of personal skills
- Apple Notes Work Report overlay
- private automation `automation.toml` references
- private automation workspace routing, including Daily Skill Friction's
  `Joey-Tools/codex-workspace` canonical repo mirror wrapper
- private session retrospective automation routing for cross-host, redacted
  retrospective history capture

Public base release is published by `Joey-Tools/codex-toolbox`. This private overlay
installs into `~/.codex/personal-sync/overlays/private/current` and manages only
private-owned symlinks.

## Test

```bash
python3 -m py_compile \
  scripts/codex_personal_sync.py \
  scripts/build_personal_codex_package.py \
  scripts/private_overlay_release.py \
  scripts/sync_private_overlay_sources.py \
  tests/test_private_overlay_package.py \
  tests/test_private_overlay_sync.py

python3 -m unittest discover -s tests
```

## Release

`Private Overlay Release` runs on pull requests for validation and on `master` pushes
or manual dispatch to publish a GitHub release. Release assets keep the same sync
format used by the public base channel:

- `personal-codex-<full-sha>.tar.gz`
- `personal-codex-<full-sha>.sha256`

The package builder defaults to the private overlay manifest in this repository:

```bash
python3 scripts/build_personal_codex_package.py \
  --sha <40-hex-sha> \
  --output-dir dist
```

`Scheduled Private Overlay Sync Release` is a low-frequency fallback that runs every
eight hours and can also be manually dispatched. It syncs explicit public Joey-Tools
sources into this private aggregate, preserves private Joey/Cisco transforms, and
opens or updates a sync PR when the source sync creates a repository diff. Merging
that PR publishes the private overlay release through the normal `master` push
release workflow.

The sync PR step requires a `PRIVATE_OVERLAY_SYNC_PR_TOKEN` secret with repository
contents and pull-request write access. The workflow uses that token for both branch
pushes and PR creation so the resulting PR `pull_request` validation workflows are
not suppressed as `GITHUB_TOKEN`-triggered events.

After merging a Joey-Tools source-repo PR that should flow into the private overlay,
trigger the sync manually so the release is not delayed until the fallback window:

```bash
gh workflow run scheduled-sync-release.yml \
  --repo Joey-Tools/codex-private-workflows \
  -f force=true
```

Scheduled fallback runs skip when a non-scheduled complete release was published
in the previous eight hours. Ordinary manual runs also observe the eight-hour cooldown.
Post-merge dispatches should use `force=true` so consecutive source PR merges are
not suppressed by cooldown.

The private manifest declares the public base release repo through `base_release.repo`.
Private machines should bootstrap the public runner from a `Joey-Tools/codex-toolbox`
release that includes `install-private`, then switch the scheduler to the private
aggregate entrypoint:

```bash
"$HOME/.codex/bin/codex-personal-sync" install-private \
  --repo Joey-Tools/codex-private-workflows \
  --home "$HOME/.codex" \
  --dry-run

"$HOME/.codex/bin/codex-personal-sync" install-scheduler \
  --mode private \
  --repo Joey-Tools/codex-private-workflows \
  --base-repo Joey-Tools/codex-toolbox \
  --home "$HOME/.codex" \
  --interval-minutes 60
```

`install-private` downloads the private overlay release, reads its `base_release`
configuration, installs the public base release first, installs the private overlay
second, and then runs the overlay verifier.
