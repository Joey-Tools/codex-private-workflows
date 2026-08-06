---
id: 20260806-bug-triage-network-boundary
title: Bug Triage Network Boundary
status: completed
created: 2026-08-06
updated: 2026-08-06
branch: wip/private148-security-followup
pr:
supersedes: []
superseded_by:
---

# Bug Triage Network Boundary

## Summary

- Bind authenticated Jenkins requests and redirects to the exact URL value that passed policy validation.
- Disable environment-derived proxy routing for the generated private helper.
- Build the blockable signal set without reflective lookup of unmaskable signals.

## Protected Properties

- URL policy protects the parsed scheme, host, port, and effective origin. Initial and redirected requests consume the validated `ParseResult.geturl()` value instead of the caller-supplied or unvalidated target text.
- Authorization remains limited to the exact configured private auth profiles and same-origin redirects. Cross-origin redirects are rejected before a redirected request is constructed.
- Transport routing is explicit: `ProxyHandler({})` prevents ambient proxy variables from redirecting authenticated traffic.
- Signal policy discards `SIGKILL` and `SIGSTOP` directly when present. It does not treat reflective lookup failure as permission to keep an unmaskable signal in the blocked set.
- Generated executable-content stability is admitted by a descriptor-bound manifest whose `scripts/` inventory contains only `jenkins_artifact_probe.py` and whose helper bytes match the independently reviewed SHA-256. Existing AST checks remain diagnostics and are not treated as a capability-complete security boundary.

## Validation

- The seven focused network, signal, and manifest-admission regressions pass.
- The bug-triage-focused private overlay sync matrix passes 57/57 tests.
- The complete `tests.test_private_overlay_sync` suite passes 256/256 tests.
- All three changed Python files compile from in-memory source; the generated helper's `--help` entrypoint exits successfully; `git diff --check` passes.
- The generated helper SHA-256 is `643f914a7367d799d3837645cd7dc5a0a309e57cef8e26d595de6e250a1e0ea7`, matching `PRIVATE_BUG_TRIAGE_REVIEWED_HELPER_SHA256`.

## Next Steps

- None.
