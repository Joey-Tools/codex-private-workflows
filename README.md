# Codex Private Workflows

这个仓库承载 private Codex overlay release。它需要保持 private，不应公开。

个人同步运行时和发布校验工具支持 Python 3.9 及以上版本。

## Scope

- private `AGENTS.md`
- private reviewer agent config
- private personal skills and private variants of personal skills
- Apple Notes Work Report overlay
- private automation `automation.toml` references
- private automation workspace routing, including Daily Skill Friction's
  `Joey-Tools/codex-workspace` canonical repo mirror wrapper and its active-plus-
  archived rollout corpus contract
- private session retrospective automation routing for cross-host, redacted
  retrospective history capture

Public base release is published by `Joey-Tools/codex-toolbox`. This private overlay
installs into `~/.codex/personal-sync/overlays/private/current` and manages only
private-owned symlinks.

## Test

The synced review helper requires Python 3.10 or later. CI exercises its full
test suite on both Ubuntu and macOS at that minimum runtime, while the private
overlay packaging and sync tests run on the Linux matrix leg.

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

Both release publishing paths require an `IMMUTABLE_RELEASES_READ_TOKEN`
Actions secret. Configure this long-lived secret with a fine-grained personal
access token that has repository **Administration (read)** permission for this
private repository. A workflow that instead generates a short-lived GitHub App
installation access token must grant the app the same permission and export the
generated token as `IMMUTABLE_RELEASES_READ_TOKEN`; do not store an expiring
installation token as the long-lived secret. The publisher uses this token only
for immutable-release capability checks; ordinary Release reads and all Release
mutations continue to use the workflow `GITHUB_TOKEN`. The secret is not required
when the publisher only reuses an already complete immutable Release.

The package builder defaults to the private overlay manifest in this repository:

```bash
python3 scripts/build_personal_codex_package.py \
  --sha <40-hex-sha> \
  --output-dir dist
```

Release validation compares removal history with the most recent complete
GitHub Release rather than the immediately preceding commit. Strict release
validation also batch-loads every authenticated complete Release manifest and
rejects target hierarchy or transaction-capacity failures for clients that skip
one or more intermediate Releases. Strict release
builds bind the requested package SHA to `HEAD`, require packaged files to match
the committed Git index, and reject untracked content, symlinked source
ancestors, submodule `gitlink` content, and nested Git repositories.

`Scheduled Private Overlay Sync Release` is a low-frequency fallback that runs every
eight hours and can also be manually dispatched. It syncs explicit public Joey-Tools
sources into this private aggregate, preserves private Joey/Cisco transforms, and
opens or updates a sync PR when the source sync creates a repository diff. Merging
that PR publishes the private overlay release through the normal `master` push
release workflow. If a run detects sync changes, it does not attempt to repair an
incomplete release from the pre-sync SHA after mutating the checkout; release repair
is reserved for runs whose sync working tree remains unchanged. Immediately before
building, the workflow rechecks both `HEAD` and the complete Git working-tree state.

The sync PR step requires a `PRIVATE_OVERLAY_SYNC_PR_TOKEN` secret with repository
contents, pull-request, and issues write access. The workflow uses that token for
branch pushes, PR creation, and the `codex-automation` PR label so the resulting
PR `pull_request` validation workflows are not suppressed as `GITHUB_TOKEN`-triggered
events.

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

For the secure review-skill rule, source sync intentionally retains one randomized
`0700` public-only preparation tree under the system temporary directory and prints
its absolute path. This avoids unsafe pathname deletion under same-UID races; private
catalog bytes are created only in the repository-side recovery scope and never enter
the retained external tree. Normal system-temporary-directory lifecycle handles the
retained copy.

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
second, and then runs the overlay verifier. The shared ownership ledger adopts
matching legacy links only during first-use bootstrap; after the ledger exists,
an otherwise untracked matching symlink remains unowned unless the current
transaction creates or replaces it.
