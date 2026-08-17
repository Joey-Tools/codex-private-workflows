---
id: 20260814-macos-role-aware-private-sync
title: Role-aware Private macOS Sync
status: completed
created: 2026-08-14
updated: 2026-08-17
branch: wip/macos-role-aware-fanout-landing
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/174
supersedes: []
superseded_by:
---

# Role-aware Private macOS Sync

## Summary

- Add a private macOS role inventory and a packaged controller helper without
  changing the public canonical runner or the package schema.
- Keep GUI scheduling host-local while allowing one explicitly activated
  controller to synchronize an explicitly managed headless Mac.
- Preserve a durable retry obligation after any failed or uncertain target
  attempt, even when the next scheduler cycle finds no new GitHub release.
- Serialize every activation, controller, standalone, target-sync, and remote
  mutation through one durable host-wide process fence.
- Bind every canonical child to an independently attested physical public
  runner instead of asking the canonical subprocess or live symlink to attest
  itself.

## Current State

- The only macOS roles are `gui-controller`, `headless-managed`, and
  `gui-standalone`. Explicit inventory and a valid activation receipt take
  precedence over runtime GUI observations.
- An unlisted host is never inferred to be a controller or managed target.
  During explicit activation, an available Aqua domain permits the
  `gui-standalone` runtime default; without Aqua, no scheduler is installed and
  the helper reports `role-activation-required`.
- Activation publishes a durable `in-flight` deny marker before the scheduler
  subprocess starts. Mutation and authorization consumers reject old or new
  receipts while that marker exists; status may diagnose the receipt through a
  stable read-only snapshot but cannot authorize work. Proven subprocess
  cleanup may move it to `retryable`; cleanup uncertainty remains blocking.
  Marker removal is the authorization commit point. A bounded stable audit
  rejects malformed, changing, or blocking orphan target states before and
  after marker publication and after scheduler proof, so a retired alias or
  role cannot bypass its previous fence.
- A controller completes its local private `run-scheduled` operation before
  target fanout. Both GUI roles install the activation-aware private wrapper;
  standalone hosts run it locally with no targets, and managed headless hosts
  do not install an Aqua scheduler.
- A single host-mutation state and lock cover activation, controller and
  standalone runs, manual target sync, and remote apply. A blocking fence from
  any operation blocks every role path. A safe `retryable` state is rebound
  under that lock to the exact next operation/role/scope and a new generation.
  Per-target state and locks remain additional fanout fences.
- Strict role health consumes the canonical scheduler report: a controller
  or standalone host requires the loaded hardened Aqua job through the private
  wrapper plus only the exact expected canonical runner-drift finding; a
  managed headless host requires scheduler absence.
- Status aggregates the shared host-mutation state even when scheduler
  inspection fails. After a stable shared-lock snapshot is acquired and
  validated, `in-flight` and cleanup-inconclusive are readable degraded states
  (`--strict` exit `2`). A missing, busy, replaced, or unsafe snapshot lock, or
  malformed or unsafe operation state, is an operational error (exit `1`).
- `personal_codex/private-sync-hosts.json` is packaged as immutable reference
  data and does not create a managed stable link. The mode-`0755`
  `codex-private-macos-sync` helper is private-owned and linked through the
  private current release.
- The private release also packages `private-overlay-source-lock.json` and
  `generated-sync-source-lock.json` exactly once as `reference_only` data. The
  installed private manifest's base release must equal the private source
  lock's unique toolbox pin; its generated-provenance receipt digest must bind
  the exact generated lock; and that lock must identify one exact engine path,
  mode `0755`, and SHA-256 before the physical public-release runner is used.

## Fanout Contract

- The mode-`0600` host-mutation state and host lock serialize every host-side
  mutating operation. Each controller-target pair has an additional isolated
  mode-`0600` state and lock. `in-flight` is committed before local mutation or
  network I/O, and each whole-file publication compares the exact prior file
  snapshot before replacing it.
- A target is skipped only when the exact desired public/private pair equals
  the last acknowledged pair and no pending failure exists. A failure retains
  `pending`, so the next scheduler cycle retries even when the local release
  identities are unchanged.
- Fanout uses one hardened SSH process with `ConnectionAttempts=4`, batch mode,
  strict host-key checking, disabled forwarding, and no TTY. Exit `255` and
  other uncertain outcomes are not followed by a second immediate invocation.
- Before each mutation-capable installer, local-sync, or SSH spawn, the durable
  state is `in-flight`. For each launched child, bounded supervision attempts
  TERM/KILL as needed, pipe drain, group-absence proof, and one final leader
  reap. A complete cleanup receipt proves group absence, pipe EOF, and reap
  before the operation, role, or target lock unwinds. If any proof is
  unavailable, the durable fence is quarantined and remains blocking after the
  lock unwinds; the leader may deliberately remain unreaped. Failed quarantine
  publication remains blocked by the earlier in-flight state. The exact named
  host, activation, and target fences are revalidated in a pre-spawn callback
  immediately before every mutation-capable `Popen`; missing, replaced,
  payload-changed, or policy-changed fence objects prevent launch.
- Remote exit `75` is reserved for cleanup uncertainty or a blocking remote
  operation fence. The controller quarantines its target on that exit even when
  the remote receipt is missing or malformed, so protocol damage cannot turn an
  uncertain BL process into an ordinary retry. Both target and host-mutation
  fences remain quarantined, and no later controller target is started in that
  fanout cycle.
- Remote acknowledgement requires successful `run-scheduled`, strict public
  and private status, overlay verification, and exact equality of public and
  private SHA plus release-tree SHA-256 identities.
- Native macOS notification is best effort and occurs only on
  `healthy -> pending` and `pending -> healthy`. Ordinary notification command
  and transport errors are not promoted to sync failures. An unproven
  notification process cleanup is propagated as an operational failure after
  its bounded cleanup attempt; no notification-specific mutation fence is
  claimed.
- Managed signal cancellation remains live after stdout/stderr EOF while the
  direct child is observed without reaping. A signal arriving in that window
  cancels the status wait and enters the same bounded cleanup attempt. The
  selected signal is surfaced only after a complete cleanup receipt; otherwise
  cleanup-inconclusive remains authoritative.
- Every canonical `Popen` is issued by the private controller through a
  descriptor-backed physical-runner binding. The controller applies the full
  caller-fence, when present, and runner-revalidation sandwich before spawn and
  after return; the child does not self-attest. `run-scheduled` uses the
  validated old physical binding, revalidates the held old objects after the
  child, and then rebuilds the complete binding from the new private `current`.
  No identity query, strict status, overlay verification, fanout, or remote
  acknowledgement may start before that rebuild succeeds.

## Protected Properties

- Role activation protects receipt content and access policy: the receipt is
  bound to the selected inventory role, local account, and machine identity,
  and is accepted only as a current-user-owned mode-`0600` regular file. On
  Darwin, bound descriptors also require exact expected-UID ownership and no
  non-owner extended-ACL `ALLOW`; no ACL, deny-only, and owner-only `ALLOW`
  remain valid. Each ACL decision is bracketed by metadata samples from the
  same descriptor. Device/inode/type, owner/group/mode, and a regular file's
  single-link property must remain safe. One benign property-stable sample
  churn may retry once; repeated drift, an unsafe link count, protected-policy
  change, or unverifiable ACL fails closed. Safe ACL entry or ordering churn is
  benign.
  A mode-`0600` in-flight marker establishes a deny-first fence before scheduler
  installation; marker and receipt identity, content, and access policy are
  revalidated under the activation lock before marker removal commits access.
  Timestamp churn triggers revalidation but is not itself treated as mutation;
  missing, unreadable, policy-changed, and content-mismatched states remain
  distinct failures.
- Host mutation protects cross-role process ownership. One named fence and one
  lock span activation, controller, standalone, target-sync, and remote paths;
  a blocking generation from one role prevents every other role from spawning.
  Only an exact safe `retryable` snapshot may be rebound to the next operation.
- Fanout state protects durable content ordering rather than timestamps. Atomic
  publication records the in-flight obligation before SSH. Under the per-target
  lock, whole-file compare-and-swap binds the expected existence, payload, and
  device/inode identity before and at publication, rejecting a stale or replaced
  prior state at those boundaries. A normal or forced run cannot cross an
  in-flight or cleanup-inconclusive fence.
- Darwin state publication requests `F_FULLFSYNC` on the validated temporary
  regular file before rename and on the containing directory after rename, then
  revalidates the final name, inode, access policy, and payload. It never falls
  back to a weaker `fsync` on Darwin. This proves the full-sync requests returned
  success through the kernel/storage stack, not that every storage device gives
  an absolute physical-commit guarantee under all power-loss conditions.
- Subprocess cleanup protects process-group ownership and state-lock ordering.
  Destructive group signals are attempted while the unreaped leader still pins
  its identity. On the complete path, platform-specific membership checks,
  pipe EOF, cancellable post-EOF status observation, and the one final reap
  finish before lock unwind or managed-signal mapping. On an inconclusive path,
  no unsafe final reap is claimed and the durable blocking fence survives lock
  unwind.
- Remote success protects release content stability by binding both active
  release SHAs and both verified tree digests. A successful command without an
  exact identity match cannot clear pending state.
- SSH destination policy protects routing and access policy with an explicit
  inventory alias, strict host-key checking, forwarding disabled, and a
  validated remote role receipt.
- Canonical execution protects object identity, exact content hash, and the
  selected owner/group/mode/link/ACL access policy of every attestation file,
  the physical runner, interpreter, and complete absolute ancestor chains.
  Persistent metadata identity binds the group GID as well as device, inode,
  type, mode, and owner UID. It excludes `ctime` and directory link count, which
  may change during benign reads or child-entry churn; a regular file's
  single-link property remains protected. Those chains are bound
  component-relative from `/`. Different-UID pathname replacement through the
  validated chain fails closed at the execution boundaries.
- Production non-Darwin interpreter selection is unchanged: the internal,
  non-configurable resolver still performs the exact resolved
  `sys.executable` lookup with the same error mapping and strict binding. Linux
  controller-test setup alone patches that resolver per test to a copied
  physical executable beneath its owner-safe `.private-macos-sync.*` test root,
  setting mode `0755` and checking that the copy is regular, non-symlink, and
  single-link before canonical binding validates its executable access policy.
  Explicit tests restore the real resolver to prove both a safe actual
  executable's positive binding and an unsafe `/opt`-like ancestor's zero-child
  rejection, so the fixture cannot mask production policy drift.

## Migration Contract

1. Publish and install the capability release.
2. Activate the local host as `gui-controller` under the Aqua scheduler.
3. On BL, uninstall its existing scheduler and require the uninstall to
   succeed; do not proceed while a legacy or active local scheduler remains.
4. Activate BL as `headless-managed` and persist its receipt. This activation
   independently proves scheduler absence and fails closed on present or
   uncertain scheduler state.
5. Complete the first controller-to-BL sync and exact remote verification.

The BL uninstall must precede headless activation. Reversing those steps makes
receipt publication intentionally fail, so controller fanout cannot begin.

Linux behavior remains unchanged throughout this migration.

## Limitations

- The receipt and local state are not cryptographic authorization and do not
  defend against the same user or root deliberately rewriting host state.
- SSH exit `255` is intentionally treated as uncertain and deferred to the next
  scheduler cycle instead of claiming whether the remote command ran.
- Catchable `SIGINT`, `SIGTERM`, and `SIGHUP` enter bounded cleanup before state
  locks unwind. Only a complete cleanup receipt permits managed-signal mapping;
  cleanup uncertainty remains authoritative and blocking after lock unwind.
  Abrupt `SIGKILL`, interpreter crash, and host power loss cannot run that
  cleanup; their durable in-flight fence is deliberately not auto-cleared.
  An operator must first prove process absence, normally after reboot, before
  repairing the protected state and retrying.
- This slice does not retry GitHub operations and does not redesign existing
  SSH or `gh` credential storage. Credential hardening remains deferred.
- Native notification is an observability aid only and may be unavailable on a
  headless or noninteractive session. Ordinary notification errors are best
  effort; cleanup uncertainty remains fail-closed operational state.
- The pathname-to-`Popen` checks are point-in-time and do not exclude root, a
  malicious same-UID process, or any process holding a writable descriptor that
  was authorized before the binding. That descriptor can mutate the same runner
  or interpreter inode after the last pre-exec check even if its mode or ACL is
  later tightened. The controller neither enumerates nor revokes system-wide
  file descriptors.
- On Darwin, the fixed
  `/Library/Developer/CommandLineTools/usr/bin/python3` entry must resolve
  through the validated Command Line Tools layout to a Python 3.9-or-newer
  executable. The resolved payload is invoked directly with `-I -B -S`;
  missing, unsafe, malformed, or older launchers fail closed. The root-owned
  Command Line Tools Python framework and standard library are an external
  deployment trust root. This binding does not verify their transitive runtime
  closure, publisher provenance, or code signature.

## Evidence

- At committed head `b68403707d4d1600445bd2fca182ddbe12ac7dac`, GitHub
  Actions run `32019307069`, job `95355404842`, failed deterministically with
  23 failures, 143 errors, and 11 skipped tests because interpreter attestation
  reached the unsafe `/opt/hostedtoolcache` ancestry.
- At GitHub head `46ef8dd`, release run `32035435305`, Linux
  controller-compatibility job `95404653074`, completed 239 tests with two
  errors and 11 skips. Manual `tearDown`/`setUp` calls bypassed `addCleanup`,
  leaving `_resolved_current_python_executable` patched to a Python copy in the
  prior deleted temporary directory. Dependent Build job `95404822911` failed,
  and the required test check remained absent.
- The exact local pre-document seam snapshots were
  `personal_codex/bin/codex-private-macos-sync` SHA-256
  `89242db83cee8fb05a7e7d6df8c26202eaf7fe0532f3a21958a07699a1967763`
  and `tests/test_private_macos_sync_controller.py` SHA-256
  `563feb1d62b82829438fc7fc505bea8a073e06c50fbfbd40b67490da1fb60c40`.
- Under both Python 3.13.0 and the system Python 3.9.6, the four new GID and
  benign-churn selectors each passed 4/4, and the related fence selection each
  passed 11/11. Complete controller runs under both runtimes each completed all
  239 cases with `OK (skipped=1)`: Python 3.13.0 in 40.195 seconds and Python
  3.9.6 in 41.089 seconds. The one skip in each run is the expected Linux-only
  fixture selector on this Darwin host.
- The minimal fixture repair retains `addCleanup`, stores the resolver patch,
  and stops it idempotently in `tearDown` before temporary-directory cleanup.
  The manual lifecycle test asserts resolver restoration; production resolver
  selection and access policy are unchanged. Its exact selector passed 1/1 on
  Python 3.13.0 and 3.9.6, while the complete controller module passed all 239
  cases with one skip under each runtime. In-memory compilation, Ruff in
  no-cache mode, `git diff --check`, and repository cache checks also passed.
- Dual-runtime in-memory compilation and the pre-document `git diff --check`
  passed. Ruff's logic reported success, but that invocation mistakenly created
  a cache; the cache was moved recoverably to
  `/private/tmp/pr174-f9-gid-ruff-cache`. The later final static gate used
  Ruff's explicit no-cache mode and left the repository cache-free.
- A single `actionlint` attempt against the restored exact-b684 workflows
  became unresponsive and was terminated with return code `130`. It is not a
  passing result and is not reused as evidence. The later final static gate
  ran a fresh, bounded `actionlint` attempt successfully.
- GitHub readiness evidence for head `46ef8dd` was superseded by the fixture
  repair. Head-bound validation, review, and CI results are intentionally kept
  as external task and PR evidence rather than tracked completed-workstream
  state.

## Next Steps

- None within the tracked private implementation. Private-overlay release
  publication and the ordered host migration are downstream deployment gates.
