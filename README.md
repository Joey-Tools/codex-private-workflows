# Codex Private Workflows

这个仓库承载 private Codex overlay release。它需要保持 private，不应公开。

## Scope

- private `AGENTS.md`
- private reviewer agent config
- private personal skills and private variants of personal skills
- Apple Notes Work Report overlay
- private automation `automation.toml` references

Public base release is published by `Joey-Tools/codex-toolbox`. This private overlay
installs into `~/.codex/personal-sync/overlays/private/current` and manages only
private-owned symlinks.

## Test

```bash
python3 -m py_compile \
  scripts/codex_personal_sync.py \
  scripts/build_personal_codex_package.py \
  tests/test_private_overlay_package.py

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
