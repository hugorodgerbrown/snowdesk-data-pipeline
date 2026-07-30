"""
tests/bin/test_sw_version_cli.py — Subprocess tests for bin/sw-version
(SNOW-517): exit codes for the bare report, ``--write``, and ``--check``
modes.

Each test builds a throwaway "mini repo" under ``tmp_path`` — a copy of
``bin/sw-version`` and ``apps/core/sw_shell.py`` plus a minimal shell tree in
the same relative layout ``apps.core.sw_shell.SHELL_SOURCES`` expects — and
invokes the CLI with that directory as ``cwd``. ``bin/sw-version`` derives
its own ``REPO_ROOT`` from ``Path(__file__).resolve().parent.parent``, so
running the copy from ``tmp_path`` makes it operate entirely against the
throwaway tree. This keeps the tests hermetic under pytest-xdist's default
``-n auto`` (parallel, not grouped) distribution — subprocess tests that
mutated the real repo's ``static/js/`` tree raced with each other across
workers, since ``bin/sw-version`` recomputes its shell-source glob fresh
on every invocation.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_SW_JS_TEMPLATE = "const CACHE_VERSION = 'snowdesk-shell-v{version}';\n"


def _build_isolated_repo(root: Path, *, version: int = 1) -> Path:
    """
    Build a throwaway repo under `root` and return the path to its
    `bin/sw-version` copy.

    Copies the real `bin/sw-version` and `apps/core/sw_shell.py` verbatim (so
    the test always exercises the actual tool code, not a reimplementation)
    alongside a minimal shell tree in the layout `SHELL_SOURCES` expects.
    """
    (root / "bin").mkdir(parents=True, exist_ok=True)
    sw_version_copy = root / "bin" / "sw-version"
    shutil.copyfile(REPO_ROOT / "bin" / "sw-version", sw_version_copy)
    sw_version_copy.chmod(0o755)

    (root / "apps" / "core").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        REPO_ROOT / "apps" / "core" / "sw_shell.py",
        root / "apps" / "core" / "sw_shell.py",
    )

    js_dir = root / "static" / "js"
    js_dir.mkdir(parents=True, exist_ok=True)
    (js_dir / "sw.js").write_text(
        _SW_JS_TEMPLATE.format(version=version), encoding="utf-8"
    )
    (js_dir / "other.js").write_text("console.log('shell');\n", encoding="utf-8")

    css_dir = root / "src" / "css"
    css_dir.mkdir(parents=True, exist_ok=True)
    (css_dir / "main.css").write_text("body { color: red; }\n", encoding="utf-8")

    templates_dir = root / "apps" / "public" / "templates" / "public"
    partials_dir = templates_dir / "partials"
    partials_dir.mkdir(parents=True, exist_ok=True)
    (templates_dir / "base.html").write_text("<html></html>\n", encoding="utf-8")
    (templates_dir / "home.html").write_text("<div>home</div>\n", encoding="utf-8")
    (partials_dir / "_map_embed.html").write_text("<div>map</div>\n", encoding="utf-8")

    (root / "static" / "offline.html").write_text(
        "<div>offline</div>\n", encoding="utf-8"
    )

    return sw_version_copy


def _run(
    sw_version_path: Path, root: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    """Run the isolated `bin/sw-version` copy with `args`, capturing output."""
    return subprocess.run(  # noqa: S603 — fixed argv list, no shell, no untrusted input
        [sys.executable, str(sw_version_path), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_exits_zero_when_freshly_written(tmp_path: Path) -> None:
    """--write then --check: a freshly-written shell reports in sync."""
    sw_version_path = _build_isolated_repo(tmp_path)
    write_result = _run(sw_version_path, tmp_path, "--write")
    assert write_result.returncode == 0, write_result.stdout + write_result.stderr

    check_result = _run(sw_version_path, tmp_path, "--check")
    assert check_result.returncode == 0, check_result.stdout + check_result.stderr
    assert "in sync" in check_result.stdout.lower()


def test_check_exits_nonzero_when_never_written(tmp_path: Path) -> None:
    """A brand-new shell tree with no committed hash is reported as stale."""
    sw_version_path = _build_isolated_repo(tmp_path)
    result = _run(sw_version_path, tmp_path, "--check")
    assert result.returncode == 1
    assert "bump" in (result.stdout + result.stderr).lower()


def test_no_args_reports_version_and_sync_status(tmp_path: Path) -> None:
    """The bare invocation reports the committed version and sync status."""
    sw_version_path = _build_isolated_repo(tmp_path)
    _run(sw_version_path, tmp_path, "--write")

    result = _run(sw_version_path, tmp_path)
    assert result.returncode == 0
    assert "snowdesk-shell-v" in result.stdout
    assert "in sync" in result.stdout.lower()


def test_write_bumps_version_and_is_idempotent(tmp_path: Path) -> None:
    """--write bumps v1 -> v2 once, then reports a no-op on the next run."""
    sw_version_path = _build_isolated_repo(tmp_path, version=1)

    first = _run(sw_version_path, tmp_path, "--write")
    assert first.returncode == 0
    sw_js_text = (tmp_path / "static" / "js" / "sw.js").read_text(encoding="utf-8")
    assert "snowdesk-shell-v2" in sw_js_text

    second = _run(sw_version_path, tmp_path, "--write")
    assert second.returncode == 0
    assert "no-op" in second.stdout.lower()
    # The version must not have advanced further on the idempotent run.
    assert (tmp_path / "static" / "js" / "sw.js").read_text(
        encoding="utf-8"
    ) == sw_js_text


def test_check_fails_when_a_shell_source_changes_after_write(tmp_path: Path) -> None:
    """Editing a shell source after --write trips --check without a bump."""
    sw_version_path = _build_isolated_repo(tmp_path)
    _run(sw_version_path, tmp_path, "--write")

    (tmp_path / "static" / "js" / "other.js").write_text(
        "console.log('changed');\n", encoding="utf-8"
    )

    result = _run(sw_version_path, tmp_path, "--check")
    assert result.returncode == 1
    assert "bump" in (result.stdout + result.stderr).lower()
