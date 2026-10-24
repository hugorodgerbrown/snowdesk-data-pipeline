---
name: dependency-audits-run-on-a-schedule-not-on-every-pr
description: Why pip-audit and npm audit run in dependency-audit.yml (daily, on dependency or VERSION diffs) and not on every PR
status: current
last-reviewed: 2026-10-24
---

# Dependency audits run on a schedule, not on every PR

**Decision.** The two dependency audits (`Security audit`, the runtime
pip-audit gate, and `Dependency audit (dev + npm)`) live in
`.github/workflows/dependency-audit.yml` (SNOW-1209). They run daily at 06:00
UTC, on manual dispatch, and on any PR or push whose diff touches `uv.lock`,
`pyproject.toml`, `package.json`, `package-lock.json`, `tox.ini`, `VERSION`
or the workflow itself. Any other PR runs the workflow, but its audit jobs
skip. A failed scheduled or dispatched run opens one GitHub issue labelled
`dependency-audit` (or comments on the open one), and the next green run
closes it. SAST and gitleaks stay per-PR in `security-audit.yml`.

**Why.** An advisory is published against a package, not a PR. On
2026-10-23 two npm advisories (zod, body-parser) landed under Lighthouse CI,
and every open PR went red for a change none of them made (SNOW-1208). The
PR that changes a dependency is the one that should be audited, and the
daily run is what finds a new advisory in a lockfile nobody touched. SAST
and the secret scan are different: they check the PR's own code, so a
failure there belongs to that PR.

A job-level skip reports a skipped check run, which satisfies a required
context exactly as the docs-only skip does (SNOW-481). So `Security audit`
stays required on both rulesets, and no ruleset edit was needed. `VERSION`
is in the trigger list so that a release PR and its merge push audit the
tree being shipped: the "Release branch" ruleset still reads a fresh runtime
audit, never a skip.

The issue exists because a red scheduled run nobody reads is how the
gitleaks cron stayed red for four weeks (SNOW-599).

**Consequences.**

- An advisory published between releases blocks nothing until the next
  release PR or dependency change. The daily issue is what surfaces it, so
  treat an open `dependency-audit` issue as blocking the next release.
- Do not rename the `Security audit` job. Its name is the ruleset's
  required context.
- A new dependency manifest (another lockfile) has to be added to the
  `changes` filter, or a PR changing it will not be audited.
