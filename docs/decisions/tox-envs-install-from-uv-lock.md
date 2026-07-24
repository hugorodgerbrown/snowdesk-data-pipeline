---
name: tox-envs-install-from-uv-lock
description: tox envs install from uv.lock via tox-uv, not bare unpinned deps lists; SAST rulesets are a deliberate live-fetched exception
status: current
last-reviewed: 2026-07-24
---

# tox envs install from `uv.lock`, not their own `deps` lists

**Decision.** Every Python tox env (`test`, `django-checks`, `mypy`, `fmt`,
`lint`, `djangofmt`, `sast`, `e2e`) uses [tox-uv](https://github.com/tox-dev/tox-uv)'s
`uv-venv-lock-runner` instead of tox's default resolver. Each env declares
`dependency_groups = <group>` (mapping to a purpose-scoped group in
`pyproject.toml`'s `[dependency-groups]`) and syncs with `--frozen`, so it
installs exactly what `uv.lock` already pins — the same versions `uv sync`
installs for local dev, `bin/init-worktree`, pre-commit's `.venv/bin/*`, and
Render's `build.sh` (`uv sync --no-dev --frozen`). This supersedes the
former convention (recorded in `CLAUDE.md` until this ticket) that "tox envs
declare their own deps, independent of the uv-managed venv" — that
independence was the bug, not a feature.

**Why.** Before this change, every tox env carried a bare, unpinned `deps =
[...]` list (`Django`, `shapely`, `webauthn`, …) that pip-resolved
latest-from-PyPI on every run — a second, uncontrolled dependency universe
that only agreed with `uv.lock` by luck. SNOW-506 hit this directly: a fresh
`ruff` release turned `main` red with no code change in the repo. Collapsing
tox's dependency resolution into `uv.lock` means a toolchain upgrade is
always a reviewable `uv lock --upgrade` diff (or a Dependabot PR), never a
silent CI failure with nothing to `git diff`.

**Consequences.**

- `[project.dependencies]` (Django, shapely, …) is installed into every env
  automatically — bare runtime package names are no longer listed in
  `tox.ini`; only the group of *tooling* packages the env needs
  (`dependency_groups = test|type|lint|sast|e2e`) is declared.
- `uv_sync_flags = --frozen` is load-bearing: it makes a stale/out-of-sync
  `uv.lock` a hard CI failure instead of a silent re-resolve.
  `--no-default-groups` does not need to be listed explicitly — tox-uv
  defaults it to `True` whenever `dependency_groups` is set, so each env
  stays minimal (`mypy` never pulls in Playwright).
- `runner`, `uv_sync_flags`, and `dependency_groups` are set **per env**, not
  globally in `[testenv]` — `audit` (`skip_install`, runs `uv export`
  directly against the default runner) and `ds-lint`/`docs-lint`/`js`
  (no third-party Python deps) are intentionally left off the lock runner.
- CI installs `tox-uv` alongside `tox` (`pip install tox tox-uv`) in every
  workflow that runs a tox env — `tox-uv` bundles its own `uv`, so no
  separate `astral-sh/setup-uv` step is needed there.
- A new `uv` Dependabot ecosystem (`.github/dependabot.yml`) opens PRs when
  `uv.lock`'s toolchain pins go stale, mirroring the existing
  `github-actions` ecosystem.
- `ruff` stays the one tool pinned **exactly** (not a range) in the `lint`
  group, because it is also consumed by `.pre-commit-config.yaml`'s
  `ruff-pre-commit` `rev:`, and the two must stay eyeball-equal — see
  `CODING_STANDARDS.md` §6.5.

## Deliberate exception: SAST rulesets stay live-fetched

The `semgrep` **package** in the `sast` group is pinned by `uv.lock` like
everything else. Its **rulesets** are not: `[testenv:sast]` keeps
`--config=p/django --config=p/python --config=p/security-audit`, fetched
live from the Semgrep registry on every run, rather than being vendored or
pinned. This is a conscious trade-off, not an oversight: for a security
scanner, a freshly-published rule is a newly-detected vulnerability class,
so live rules are a feature — the alternative (pinning them) would trade
reproducibility for staleness, silently missing new vulnerability classes
until someone remembers to bump the pin. The residual risk — a new upstream
rule turning `sast` red with no local `uv.lock` diff to explain it — is
accepted in exchange for that freshness; the existing `--exclude-rule` list
already absorbs the known false positives idiomatic Django code triggers.
Do not "finish the job" by vendoring or pinning these rulesets; if a new
default rule proves too noisy, add it to the exclude list with a comment,
the same way the existing ones were handled.
