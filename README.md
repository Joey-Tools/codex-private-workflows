# Codex Private Workflows

这个仓库承载 private Codex overlay release。它需要保持 private，不应公开。

## Scope

- private `AGENTS.md`
- private reviewer agent config
- private personal skills and private variants of personal skills
- Apple Notes Work Report overlay
- private automation `automation.toml` references

Public base release should be installed first. This overlay installs into
`~/.codex/personal-sync/overlays/private/current` and manages only
private-owned symlinks.

## Test

```bash
python3 -m unittest discover -s tests
```

## Package

```bash
python3 scripts/build_personal_codex_package.py           --manifest personal_codex/private-sync-manifest.json           --sha <40-hex-sha>           --output-dir dist
```
