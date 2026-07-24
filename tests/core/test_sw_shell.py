"""
tests/core/test_sw_shell.py — Tests for the service-worker shell version
tracker (SNOW-517): CACHE_VERSION parsing, next_version() incrementing,
the shell content hash, and the "bump owed" comparison — including the
chicken-and-egg guard that bumping the version alone must not re-trigger
"bump owed".

Builds a throwaway shell tree under ``tmp_path`` and monkeypatches
``core.sw_shell``'s module-level path constants to point at it, so tests
never touch the real repo's ``static/js/sw.js`` / ``sw-shell.hash``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import sw_shell

_SW_JS_TEMPLATE = "const CACHE_VERSION = 'snowdesk-shell-v{version}';\n"


def _write_shell_tree(root: Path, *, version: int = 37) -> None:
    """Populate `root` with a minimal shell tree matching SHELL_SOURCES' shape."""
    js_dir = root / "static" / "js"
    js_dir.mkdir(parents=True, exist_ok=True)
    (js_dir / "sw.js").write_text(
        _SW_JS_TEMPLATE.format(version=version), encoding="utf-8"
    )
    (js_dir / "other.js").write_text("console.log('shell');\n", encoding="utf-8")

    css_dir = root / "src" / "css"
    css_dir.mkdir(parents=True, exist_ok=True)
    (css_dir / "main.css").write_text("body { color: red; }\n", encoding="utf-8")

    templates_dir = root / "public" / "templates" / "public"
    partials_dir = templates_dir / "partials"
    partials_dir.mkdir(parents=True, exist_ok=True)
    (templates_dir / "base.html").write_text("<html></html>\n", encoding="utf-8")
    (templates_dir / "home.html").write_text("<div>home</div>\n", encoding="utf-8")
    (partials_dir / "_map_embed.html").write_text("<div>map</div>\n", encoding="utf-8")

    static_dir = root / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "offline.html").write_text("<div>offline</div>\n", encoding="utf-8")


@pytest.fixture()
def shell_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a temp shell tree and repoint core.sw_shell's globals at it."""
    _write_shell_tree(tmp_path)
    monkeypatch.setattr(sw_shell, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sw_shell, "SW_JS_PATH", tmp_path / "static" / "js" / "sw.js")
    monkeypatch.setattr(
        sw_shell, "HASH_FILE_PATH", tmp_path / "static" / "js" / "sw-shell.hash"
    )
    monkeypatch.setattr(sw_shell, "SHELL_SOURCES", sw_shell._default_shell_sources())
    return tmp_path


class TestReadCacheVersion:
    """Tests for read_cache_version()."""

    def test_parses_current_version(self, shell_tree: Path) -> None:
        """The version is parsed from the CACHE_VERSION assignment."""
        assert sw_shell.read_cache_version() == "snowdesk-shell-v37"

    def test_raises_when_assignment_missing(self, shell_tree: Path) -> None:
        """A sw.js with no recognisable CACHE_VERSION assignment raises."""
        (shell_tree / "static" / "js" / "sw.js").write_text(
            "// no version here\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="CACHE_VERSION"):
            sw_shell.read_cache_version()


class TestNextVersion:
    """Tests for next_version()."""

    @pytest.mark.parametrize(
        ("current", "expected"),
        [
            ("snowdesk-shell-v37", "snowdesk-shell-v38"),
            ("snowdesk-shell-v1", "snowdesk-shell-v2"),
            ("snowdesk-shell-v9", "snowdesk-shell-v10"),
        ],
    )
    def test_increments_numeric_suffix(self, current: str, expected: str) -> None:
        """The trailing -vNN suffix increments by one; the prefix is preserved."""
        assert sw_shell.next_version(current) == expected

    def test_raises_for_unparseable_scheme(self) -> None:
        """A string with no -vNN suffix raises rather than guessing."""
        with pytest.raises(ValueError, match="cannot parse version scheme"):
            sw_shell.next_version("not-a-version")


class TestBumpOwed:
    """Tests for compute_shell_hash() / bump_owed() — the core contract."""

    def test_unchanged_shell_is_not_owed(self, shell_tree: Path) -> None:
        """A hash committed against the current tree reports no bump owed."""
        sw_shell.write_committed_hash(sw_shell.compute_shell_hash())
        assert sw_shell.bump_owed() is False

    def test_mutated_shell_source_is_owed(self, shell_tree: Path) -> None:
        """Editing any shell source after committing the hash flips bump_owed()."""
        sw_shell.write_committed_hash(sw_shell.compute_shell_hash())
        (shell_tree / "static" / "js" / "other.js").write_text(
            "console.log('changed');\n", encoding="utf-8"
        )
        assert sw_shell.bump_owed() is True

    def test_mutated_template_is_owed(self, shell_tree: Path) -> None:
        """A shell template edit also flips bump_owed() — not just JS/CSS."""
        sw_shell.write_committed_hash(sw_shell.compute_shell_hash())
        (shell_tree / "public" / "templates" / "public" / "home.html").write_text(
            "<div>changed</div>\n", encoding="utf-8"
        )
        assert sw_shell.bump_owed() is True

    def test_missing_hash_file_is_owed(self, shell_tree: Path) -> None:
        """No committed hash file at all counts as a bump owed."""
        assert sw_shell.read_committed_hash() == ""
        assert sw_shell.bump_owed() is True

    def test_version_bump_alone_does_not_flip_bump_owed(self, shell_tree: Path) -> None:
        """The chicken-and-egg guard.

        Bumping CACHE_VERSION rewrites sw.js's bytes, but must not, on its
        own, make bump_owed() true again — the version line is normalised
        out of the hash before it's computed.
        """
        sw_shell.write_committed_hash(sw_shell.compute_shell_hash())
        assert sw_shell.bump_owed() is False

        sw_shell.write_cache_version("snowdesk-shell-v38")
        assert sw_shell.read_cache_version() == "snowdesk-shell-v38"
        assert sw_shell.bump_owed() is False

    def test_compute_shell_hash_is_deterministic(self, shell_tree: Path) -> None:
        """Repeated calls against an unchanged tree return the same digest."""
        assert sw_shell.compute_shell_hash() == sw_shell.compute_shell_hash()


class TestCommittedHash:
    """Tests for read_committed_hash() / write_committed_hash() round-trip."""

    def test_round_trips(self, shell_tree: Path) -> None:
        """Whatever is written is read back verbatim (whitespace-stripped)."""
        sw_shell.write_committed_hash("deadbeef")
        assert sw_shell.read_committed_hash() == "deadbeef"

    def test_missing_file_returns_empty_string(self, shell_tree: Path) -> None:
        """No hash file yet reads back as an empty string, not an error."""
        assert sw_shell.read_committed_hash() == ""


class TestWriteCacheVersion:
    """Tests for write_cache_version()."""

    def test_rewrites_only_the_version_line(self, shell_tree: Path) -> None:
        """Only the CACHE_VERSION assignment changes — nothing else in sw.js."""
        sw_js_path = shell_tree / "static" / "js" / "sw.js"
        original = sw_js_path.read_text(encoding="utf-8")
        sw_shell.write_cache_version("snowdesk-shell-v99")
        rewritten = sw_js_path.read_text(encoding="utf-8")
        assert "snowdesk-shell-v99" in rewritten
        assert original.replace("v37", "v99") == rewritten


class TestDefaultShellSources:
    """Tests for _default_shell_sources()'s live static/js/*.js glob."""

    def test_includes_every_js_file_in_static_js(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A newly added JS file is picked up without editing this module."""
        _write_shell_tree(tmp_path)
        new_module = tmp_path / "static" / "js" / "new_module.js"
        new_module.write_text("// new\n", encoding="utf-8")
        monkeypatch.setattr(sw_shell, "REPO_ROOT", tmp_path)

        sources = sw_shell._default_shell_sources()

        assert new_module in sources
