---
name: in-project-venv
description: The uv virtualenv lives at .venv/ inside the repo so the pre-commit mypy/djangofmt hooks work from GUI git clients
status: current
last-reviewed: 2026-06-19
---

# Virtualenv lives at .venv/ inside the repo

**Decision.** The project virtualenv is `.venv/` in the repo root. This is
[uv](https://docs.astral.sh/uv/)'s default location, so no extra
configuration is needed — `uv sync` / `uv run` create and use `.venv/`
automatically.

**Why.** The pre-commit hooks in `.pre-commit-config.yaml` invoke
`.venv/bin/mypy` and `.venv/bin/djangofmt` by repo-relative path. GUI git
clients (SublimeMerge, Tower, Fork, …) launch git with a minimal environment
that doesn't inherit the user's shell PATH, so any hook that relies on
PATH-resolved tools fails there. A repo-relative interpreter path works
identically from the CLI and from every GUI client.

**Consequences.** Don't relocate the venv (e.g. via `UV_PROJECT_ENVIRONMENT`)
without also updating the hook entries. Anything else that needs the project
interpreter from a hook should use the same `.venv/...` repo-relative form.
