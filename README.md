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

Public base release is published by `Joey-Tools/codex-toolbox`. This private overlay
installs into `~/.codex/personal-sync/overlays/private/current` and manages only
private-owned symlinks.

## Role-aware macOS sync

The private overlay packages `personal_codex/private-sync-hosts.json` as the
explicit macOS host-role inventory. It is reference data inside the immutable
private release, not a mutable stable configuration link. The installed
`bin/codex-private-macos-sync` helper is private-owned and remains inert until
explicit activation commits a valid role receipt. Only during activation may
an unlisted Aqua host be selected as `gui-standalone`.

That immutable private release also packages exactly one
`private-overlay-source-lock.json` and one `generated-sync-source-lock.json` as
`reference_only` attestations. Before any canonical child starts, the
controller requires the installed private manifest's `base_release` to equal
the unique `codex-toolbox` pin in the private source lock, requires that lock's
generated-provenance receipt digest to bind the exact generated source-lock
bytes, and accepts exactly one generated engine at
`scripts/codex_personal_sync.py` with mode `0755` and its declared SHA-256. The
result selects and hashes the physical runner in the pinned public release;
the canonical subprocess is not trusted to attest itself through a live
symlink.

The supported macOS roles are deliberately closed:

- `gui-controller` runs the hardened Aqua scheduler through the activation-aware
  private wrapper, completes the local public/private sync first, and then fans
  out only to its explicitly assigned `headless-managed` targets.
- `headless-managed` does not install an Aqua scheduler. Its designated
  controller owns synchronization.
- `gui-standalone` runs that same activation-aware private wrapper locally and
  never fans out to another host.

An explicit inventory entry and its valid activation receipt always take
precedence over runtime GUI observations. An unlisted host is never inferred to
be a controller or managed target. During explicit activation, an unlisted
macOS host with an available Aqua domain defaults to `gui-standalone`; without
an Aqua domain, activation does not install a scheduler and reports
`role-activation-required` with guidance to use the role activation mechanism.
Activation is deny-first: a durable `in-flight` fence is published before any
Aqua scheduler subprocess starts, and every receipt consumer rejects
authorization while that fence exists or is unsafe. Receipt publication
completes under the fence; removing the fence is the authorization commit
point. A subprocess failure with proven cleanup moves the fence to `retryable`,
so another explicit `activate` call can roll forward. Cleanup that cannot prove
the owned process group terminal remains blocking instead of authorizing or
automatically retrying the activation.

Activation performs a bounded stable audit of every target-state filename and
payload before and after publishing its sentinel and again after scheduler
commit proof. A malformed, changing, or blocking orphan target state prevents a
retired alias or role from being rebound; a safely `retryable` orphan remains
non-blocking.

One mode-`0600` host-mutation state and one execution lock serialize every
mutation-capable host path: activation, controller and standalone scheduled
runs, manual target sync, and remote apply. A blocking fence from any one path
blocks all the others. Under the shared lock, a safe `retryable` fence is rebound
to the exact next operation, host, controller, scope, and generation. Each
controller-target pair additionally retains its own mode-`0600` state and lock.
The controller and remote endpoint publish host operation `in-flight` before
their local `run-scheduled` child; the controller additionally publishes target
`in-flight` before SSH. These states survive scheduler cycles, so a failed or
uncertain attempt is retried only after complete local cleanup, even when GitHub
has no new release. A confirmed pair with no pending failure is skipped.
Successful acknowledgement requires the remote `run-scheduled` command,
strict public and private status, overlay verification, and exact equality of
both active release SHAs and both release-tree SHA-256 digests.

Every launched child is started in a separate process group under bounded
supervision. On normal completion and after any catchable signal, timeout,
output overflow, or post-spawn failure, the supervisor attempts TERM/KILL as
needed, bounded pipe drain, process-group absence proof, and one final
direct-child reap. A complete cleanup receipt proves group absence, pipe EOF,
and child reap before the state lock unwinds and is the only basis for a
retryable state. If any proof remains unavailable, cleanup is reported
inconclusive and the durable in-flight or quarantine fence remains blocking
after the lock unwinds; the leader may deliberately remain unreaped rather than
release its PGID identity unsafely. Mutation-capable installer, local-sync, and
SSH paths record the durable in-flight fence before spawn.
`--force` does not bypass a blocking operation or target state. Read-only
identity, status, and notification children use the same bounded supervisor but
do not create durable mutation fences.

Every canonical-runner `Popen` uses the physical runner and interpreter from a
descriptor-backed binding. The controller binds each complete absolute
ancestor chain component-relative from `/`, plus the attestation files, runner,
and interpreter, then brackets process start and return with the caller-state
callback, when present, and runner revalidation sandwich. The protected
properties are object identity, exact content hash, and owner/mode/link/ACL
access policy. Timestamps and directory child-entry churn are benign when those
properties remain stable. Unsafe or replaced ancestors fail closed before
execution. This point-in-time pathname-to-`Popen` contract excludes replacement
by a differently privileged UID through the validated chain, but it does not
claim to defeat root, a malicious same-UID process, or any process holding a
writable descriptor that was authorized before the binding. Such a descriptor
can mutate the same runner or interpreter inode after the final pre-exec check,
even if its mode or ACL is later tightened. The controller does not enumerate
or revoke system-wide file descriptors.

`run-scheduled` deliberately executes through the old, already validated
physical release binding because that child may replace both current releases.
After the child, the controller first revalidates the held old objects, then
builds a new full binding from the new private `current` release and its pinned
public base. Release identities, status, overlay verification, fanout, and
remote acknowledgement cannot begin until that new binding succeeds.

On Darwin, canonical commands use the fixed Command Line Tools entry
`/Library/Developer/CommandLineTools/usr/bin/python3`. The controller validates
its symlink chain and the resolved executable layout as a Python 3.9-or-newer
launcher, binds the resolved payload, and invokes that payload directly with
`-I -B -S`. The complete root-owned Command Line Tools Python framework and
standard library remain an external deployment trust root: the wrapper does
not attest their transitive runtime closure, publisher provenance, or code
signature. A missing, unsafe, malformed, or older launcher fails closed.

Immediately before every
mutation-capable `Popen`, the supervisor revalidates the exact named host,
activation, and target fences that authorize the spawn, including object
identity, payload, access policy, and their retained directory/lock bindings.
After both output pipes reach EOF, the managed-signal latch remains active while
the direct child is observed without reaping; a late catchable signal cancels
that wait and enters the same bounded cleanup path instead of waiting for the
ordinary timeout.

Fanout starts one hardened SSH invocation per target with
`ConnectionAttempts=4`, `BatchMode=yes`, strict host-key checking, forwarding
disabled, and no TTY. Exit `255` or another uncertain result remains pending;
the helper does not immediately start a second SSH process. This workstream
does not add GitHub retries. Best-effort native notifications are limited to
the `healthy -> pending` and `pending -> healthy` transitions. Ordinary
notification command and transport errors do not change sync success, but an
unproven notification process cleanup is propagated as an operational failure
after its bounded cleanup attempt. No notification-specific mutation fence is
claimed.

The remote endpoint reserves exit `75` for cleanup uncertainty or a preexisting
remote operation fence. The controller treats that exit as quarantine even if
the accompanying receipt is missing or malformed; an exact receipt can add
scope evidence but can never downgrade the quarantine. A later ordinary or
forced run therefore cannot start another BL sync behind an uncertain remote
process. Both the target fence and the shared host-mutation fence remain
quarantined, and controller fanout stops before starting any remaining target.

Cleanup-inconclusive and legacy ambiguous in-flight fences are intentionally
not cleared automatically. Recovery requires an operator to first prove the
old process cannot still be running, normally after a reboot, and then repair
the protected state before explicitly retrying. Abrupt `SIGKILL`, interpreter
crash, and host power loss are outside the catchable-signal cleanup guarantee.

The safe macOS migration order is:

1. Publish and install the release that provides the required capability.
2. Activate the local `gui-controller` Aqua scheduler.
3. On BL, uninstall its existing scheduler and require that operation to
   succeed. Do not continue while a legacy or active local scheduler remains.
4. Activate the BL host's `headless-managed` receipt. Activation independently
   proves scheduler absence and fails closed if the old job is still present or
   its state is uncertain.
5. On the controller, run `codex-private-macos-sync sync-target --target-id
   BL-mac-mini-m4-hoteng --force --strict` for the first verified fanout.
6. Still on the controller, require `codex-private-macos-sync status --strict`
   to succeed.

The strict role health gate consumes the canonical macOS scheduler report. Both
GUI roles must have the hardened Aqua job loaded with the private wrapper and
may have only the exact canonical runner-drift compatibility finding; the
standalone role remains local-only and has no fanout targets. A managed headless
host must have no local scheduler. Missing, disabled, legacy, duplicated, or
differently routed jobs fail the gate.

The same status command reads operation state without changing it. After it
acquires and validates a stable shared-lock snapshot, a safe host-mutation
`retryable` state is healthy, while `in-flight`, cleanup-inconclusive, and
activation or target blockers remain readable but degraded and make `--strict`
exit `2`. A missing, busy, replaced, or unsafe snapshot lock, or malformed or
unsafe state, is an operational error and exits `1`.

On Darwin, the helper validates the inventory ancestry, `current` pointer,
inventory file, controller state directory, state files, publication temporary
files, and all three lock kinds through bound file descriptors. The protected
access property is exact expected-UID ownership with no non-owner extended-ACL
`ALLOW` entry. No ACL, deny-only ACLs, and owner-only `ALLOW` entries remain
valid. Each semantic ACL decision is bracketed by coherent metadata samples on
the same descriptor; device/inode/type, owner/group/mode, and the single-link
property for regular files must remain safe. One benign property-stable sample
churn may be retried once, while repeated drift, an unsafe link count, a changed
protected property, or an unverifiable ACL fails closed. Safe ACL ordering or
entry churn is not treated as content mutation.

State publication on Darwin requires `F_FULLFSYNC` for the validated temporary
regular file before rename and for the containing state directory after rename,
then revalidates that the published name still identifies the authorized inode
and payload. There is no weaker `fsync` fallback on Darwin. A successful call
proves that the helper requested the platform's full-sync operation and the
kernel/storage stack reported success; it is not an absolute guarantee that
particular hardware physically committed the bytes across every power-loss
scenario.

Linux scheduler and installation behavior is unchanged. Credential storage and
additional credential hardening are deferred; the helper relies on the
existing host-local `gh` and SSH authentication configuration.

## Test

The synced review helper requires Python 3.10 or later. CI exercises its full
pull-request test suite on both Ubuntu and macOS at that minimum runtime, while
the private overlay packaging and sync tests run on the Linux matrix leg. macOS
runners are reserved for gates that need real Darwin, Xcode, Seatbelt, or
Keychain behavior. Bounded, short-lived status jobs use `ubuntu-slim`; release
validation, publishing, and scheduled sync jobs remain on `ubuntu-latest`.

```bash
# Use compile() so the extensionless helper is checked without creating bytecode.
python3 -B -c 'import pathlib, sys; [compile(pathlib.Path(path).read_bytes(), path, "exec") for path in sys.argv[1:]]' \
  scripts/codex_personal_sync.py \
  scripts/build_personal_codex_package.py \
  scripts/private_overlay_release.py \
  scripts/sync_private_overlay_sources.py \
  personal_codex/bin/codex-private-macos-sync \
  tests/test_private_overlay_package.py \
  tests/test_private_macos_sync_controller.py \
  tests/test_private_overlay_sync.py

PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests
```

## Release

On pull requests, `Private Overlay Release` makes the required
`Build private overlay release` check depend on two controller prerequisites:
the complete controller suite on Ubuntu with Python 3.9 and the same suite on
macOS with Python 3.13. The required job explicitly rejects either prerequisite
unless it succeeded, then performs release-specific validation: complete
sync-manifest Release-history validation, package build and verification, and
the source-only Python-tree guard. CI still owns the broader private and
canonical review suites, so the release workflow repeats only the controller
compatibility/platform coverage needed by this required check. Release
validation retains its `ubuntu-latest` runner and 30-minute budget because
cross-version manifest safety requires inspecting complete Release history.

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

Release validation compares removal history with the most recent complete
GitHub Release rather than the immediately preceding commit. That strict
validation also batch-loads every authenticated complete Release manifest and
rejects target hierarchy or transaction-capacity failures for clients that skip
one or more intermediate Releases. Strict release
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
release that includes `install-private`, then install the private overlay:

```bash
"$HOME/.codex/bin/codex-personal-sync" install-private \
  --repo Joey-Tools/codex-private-workflows \
  --home "$HOME/.codex" \
  --dry-run

"$HOME/.codex/bin/codex-personal-sync" install-private \
  --repo Joey-Tools/codex-private-workflows \
  --home "$HOME/.codex"
```

`install-private` downloads the private overlay release, reads its `base_release`
configuration, installs the public base release first, installs the private overlay
second, and then runs the overlay verifier. The shared ownership ledger adopts
matching legacy links only during first-use bootstrap; after the ledger exists,
an otherwise untracked matching symlink remains unowned unless the current
transaction creates or replaces it.

On macOS, role activation is the scheduler entrypoint. Do not call the canonical
`install-scheduler` command directly, because that would bypass the controller
runner and its target fanout. Activate the applicable role after the private
overlay is installed:

```bash
# Run on the controller: activate its explicit GUI role from the inventory.
"$HOME/.codex/bin/codex-private-macos-sync" activate \
  --host-id HOTENG-M-NCQ2

# Run on BL: remove the existing local scheduler before headless activation.
"$HOME/.codex/bin/codex-personal-sync" uninstall-scheduler \
  --home "$HOME/.codex" \
  --platform macos

# Still on BL: activate its explicit headless role. This installs no Aqua
# scheduler and fails closed unless scheduler absence can be proved.
"$HOME/.codex/bin/codex-private-macos-sync" activate \
  --host-id BL-mac-mini-m4-hoteng

# Run on an unlisted GUI Mac: activate implicit standalone mode.
"$HOME/.codex/bin/codex-private-macos-sync" activate
```

For the first controller-to-BL migration, return to the controller and require
both the forced target sync and the following health check to succeed:

```bash
"$HOME/.codex/bin/codex-private-macos-sync" sync-target \
  --target-id BL-mac-mini-m4-hoteng \
  --force \
  --strict

"$HOME/.codex/bin/codex-private-macos-sync" status --strict
```

Do not reverse the BL uninstall and activation steps. Headless activation is
the final scheduler-absence gate; an installed, active, legacy, or uncertain
local scheduler prevents receipt publication and therefore prevents fanout.

Direct canonical `install-scheduler` remains available only for Linux and
deliberately maintained legacy deployments. A Linux private scheduler uses the
canonical runner directly:

```bash
"$HOME/.codex/bin/codex-personal-sync" install-scheduler \
  --mode private \
  --repo Joey-Tools/codex-private-workflows \
  --base-repo Joey-Tools/codex-toolbox \
  --home "$HOME/.codex" \
  --interval-minutes 60 \
  --platform linux
```
