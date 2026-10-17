---
id: 20261017-aqua-domain-probe-output-gdp001
title: Bounded Aqua Domain Probe Output
status: completed
created: 2026-10-17
updated: 2026-10-17
branch: codex/daily-skill-friction-20261017-codex-private-workflows-standalone-gui-probe
pr:
supersedes: []
superseded_by:
---

# Bounded Aqua Domain Probe Output

## Summary

- Give the Aqua domain probe a dedicated 1 MiB output budget. A normal GUI
  domain includes all services and endpoints; its output can exceed the old
  8 KiB budget even when the requested GUI domain is healthy.
- Preserve the five-second timeout, successful exit requirement, overflow
  rejection, and existing subprocess cleanup guarantees.

## Evidence

- On a second GUI laptop, `launchctl print gui/501` succeeded with 115390
  output bytes. The old probe exceeded its budget and failed during process
  cleanup before activation state was published.
- Regression coverage exercises a large healthy GUI domain and rejection of
  failed, timed-out, and over-budget probes.
- The complete controller suite passed on Python 3.9.6 and Python 3.13.0:
  241 tests per runtime, with one platform-specific skip in each run.
- The candidate passed the same laptop's read-only live probe with all
  115390 output bytes, a zero exit status, and no overflow or timeout.
- The local Python 3.13.0 installation emitted existing unavailable-BLAKE2
  startup diagnostics; the complete selected suite still passed.
