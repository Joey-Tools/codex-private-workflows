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
pull-request test suite on both Ubuntu and macOS at that minimum runtime, while
the private overlay packaging and sync tests run on the Linux matrix leg. macOS
runners are reserved for gates that need real Darwin, Xcode, Seatbelt, or
Keychain behavior. Bounded, short-lived status and release-specific pull-request
jobs use `ubuntu-slim`; longer validation, publishing, and scheduled sync jobs
remain on `ubuntu-latest`.

```bash
python3 -B -c 'import pathlib, sys; [compile(pathlib.Path(path).read_bytes(), path, "exec") for path in sys.argv[1:]]' \
  scripts/codex_personal_sync.py \
  scripts/build_personal_codex_package.py \
  scripts/private_overlay_release.py \
  scripts/sync_private_overlay_sources.py \
  tests/test_private_overlay_package.py \
  tests/test_private_overlay_sync.py

PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests
```

## Release

On pull requests, `Private Overlay Release` keeps the required
`Build private overlay release` check focused on release-specific validation:
sync-manifest change validation against the pull-request base, package build
and verification, and the source-only Python-tree guard. CI owns the full
helper syntax, private test, and canonical review suites for pull requests, so
the release workflow does not repeat them or scan complete Release history.

On `master` pushes and eligible manual dispatches, `Private Overlay Release`
still runs the complete validation set before publishing a GitHub release. New
pull-request runs cancel superseded release validation for the same ref, while
push, manual, and scheduled release work remains non-cancelling through the
shared concurrency group. Release assets keep the same sync format used by the
public base channel:

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

Default-branch and manual release validation compares removal history with the
most recent complete GitHub Release rather than the immediately preceding
commit. That strict validation also batch-loads every authenticated complete
Release manifest and rejects target hierarchy or transaction-capacity failures
for clients that skip one or more intermediate Releases. Strict release
builds bind the requested package SHA to `HEAD`, require packaged files to match
the committed Git index, and reject untracked content, symlinked source
ancestors, submodule `gitlink` content, and nested Git repositories.

`Scheduled Private Overlay Sync Release` is a low-frequency fallback that runs every
eight hours and can also be manually dispatched. It syncs explicit public Joey-Tools
sources into this private aggregate, preserves private Joey/Cisco transforms, and
persists the exact five-source commit/tree inventory in
`private-overlay-source-lock.json` before it opens or updates a sync PR. The public
toolbox source is always checked out at the exact immutable base release SHA declared
by the private manifest and release verifier; advancing that base is an explicit
reviewed policy change. The other four source repositories are refreshed from their
default branches and frozen into the candidate lock. Every checkout is full,
detached, non-promisor, alternate-free, clean, object-complete, and verified against
the candidate lock both before and after generation. Merging
that PR publishes the private overlay release through the normal `master` push
release workflow. If a run detects sync changes, it does not attempt to repair an
incomplete release from the pre-sync SHA after mutating the checkout; release repair
is reserved for runs whose sync working tree remains unchanged. Immediately before
building, the workflow rechecks both `HEAD` and the complete Git working-tree state.
The canonical review skill's `tests/fixtures/ci/private.yml` materializes the live
private CI workflow byte-for-byte; scheduled sync tracks and stages `.github`, and
the scheduled and release full-suite jobs run on Python 3.13.
The generated PR records the exact review-workflow SHA and tree rather than treating
a moving default branch as executable control-plane input. The current generated PR
workflow declares only `contents: read` and contains no `secrets.*` references.

The sync PR step requires a `PRIVATE_OVERLAY_SYNC_PR_TOKEN` secret with repository
contents, pull-request, and issues write access. Because sync can update
`.github/workflows/ci.yml`, a fine-grained PAT or GitHub App token must also grant
`Workflows: write`; a classic PAT must include the `workflow` scope. The workflow
uses that token for branch pushes, PR creation, and the `codex-automation` PR label
so the resulting PR `pull_request` validation workflows are not suppressed as
`GITHUB_TOKEN`-triggered events.

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
