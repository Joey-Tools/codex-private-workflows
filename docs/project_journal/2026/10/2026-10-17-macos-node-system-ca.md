---
id: 20261017-macos-node-system-ca
title: Explicit macOS Node System CA Setup
status: completed
created: 2026-10-17
updated: 2026-10-17
branch: wip/macos-node-system-ca
pr: https://github.com/Joey-Tools/codex-private-workflows/pull/231
supersedes: []
superseded_by:
---

# Explicit macOS Node System CA Setup

## Summary

- Package the private-owned `codex-node-system-ca` helper for explicit
  installation, status inspection, and removal on each GUI Mac.
- Keep ordinary private synchronization limited to distribution; enabling a
  persistent host setting requires an explicit helper invocation.
- Use the current account's home and UID without packaging certificate data,
  host identities, or account-specific absolute paths.

## Behavior

- The fixed Aqua LaunchAgent sets `NODE_USE_SYSTEM_CA=1` at login, and explicit
  installation applies the value to the current GUI launchd context.
- Supporting subsequent Node processes use the system trust store with TLS
  verification enabled. Existing application environments remain unchanged.
- Equivalent manually installed plists are accepted; unrelated files and
  symlinks are rejected. Repeated operations remain explicit and inspectable.
- Removal disables and unloads the managed job and removes its matching
  plist. It only clears the environment variable while its value is `1`.
- Dry-run reports the plan without filesystem or launchd mutations. Failed
  activation may retain a matching plist for a diagnosed retry.

## Validation

- Focused helper tests use temporary homes and mocked launchctl calls.
- Private package tests verify manifest ownership and executable mode.
- Local macOS validation runs the focused helper tests. The existing private
  package test module carries the helper lifecycle cases, so CI and release
  tests include them without changing the canonical CI distribution profile.
- The original live connection diagnosis established that enabling system CA
  trust let ChatGPT's remote-control WebSocket connect through the enterprise
  TLS inspection chain. Repository tests do not restart the app or mutate the
  live host setting.

## Limitations

- Requires a live Aqua GUI login and a Python 3.9-or-newer interpreter.
- The setting applies to subsequent processes in the user's GUI context,
  rather than only ChatGPT. Login-item ordering is not guaranteed.
- Private-overlay removal does not undo an independently activated host
  setting; invoke helper removal first when retiring it.
- Filesystem and launchd changes are not one atomic transaction. Reported
  activation failures must be diagnosed before retrying.

## Next Steps

- None within the packaged helper implementation.
