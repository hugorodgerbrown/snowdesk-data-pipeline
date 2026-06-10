---
name: in-project-venv
description: The Poetry virtualenv is pinned to .venv/ inside the repo so the pre-commit mypy hook works from GUI git clients
status: current
last-reviewed: 2026-06-10
---

# Virtualenv lives at .venv/ inside the repo

**Decision.** The Poetry virtualenv is pinned to `.venv/` in the repo root
via `poetry.toml` (`virtualenvs.in-project = true`).

**Why.** The pre-commit mypy hook in `.pre-commit-config.yaml` invokes
`.venv/bin/mypy` by repo-relative path. GUI git clients (SublimeMerge,
Tower, Fork, …) launch git with a minimal environment that doesn't inherit
the user's shell PATH, so any hook that relies on PATH-resolved tools fails
there. A repo-relative interpreter path works identically from the CLI and
from every GUI client.

**Consequences.** Don't relocate the venv without also updating the mypy
hook entry. Anything else that needs the project interpreter from a hook
should use the same `.venv/...` repo-relative form.
