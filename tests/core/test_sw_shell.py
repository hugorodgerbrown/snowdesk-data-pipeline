"""
tests/core/test_sw_shell.py — Tests for the service-worker shell cache-version
derivation (SNOW-517, SNOW-590): the shell content hash, the derived cache
name, and the serve-time ``CACHE_VERSION`` substitution.

The behaviour that matters is that *any* change to *any* shell source
produces a different cache name, and that a body which cannot be
substituted raises rather than silently shipping a frozen name.

Builds a throwaway shell tree under ``tmp_path`` and monkeypatches
``apps.core.sw_shell``'s module-level path constants to point at it, so tests
never touch the real repo's ``static/js/sw.js``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.core import sw_shell

_SW_JS_TEMPLATE = "const CACHE_VERSION = 'snowdesk-shell-UNSUBSTITUTED';\n"


def _write_shell_tree(root: Path) -> None:
    """Populate `root` with a minimal shell tree matching SHELL_SOURCES' shape."""
    js_dir = root / "static" / "js"
    js_dir.mkdir(parents=True, exist_ok=True)
    (js_dir / "sw.js").write_text(_SW_JS_TEMPLATE, encoding="utf-8")
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

    static_dir = root / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "offline.html").write_text("<div>offline</div>\n", encoding="utf-8")


@pytest.fixture()
def shell_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a temp shell tree and repoint apps.core.sw_shell's globals at it."""
    _write_shell_tree(tmp_path)
    monkeypatch.setattr(sw_shell, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sw_shell, "SW_JS_PATH", tmp_path / "static" / "js" / "sw.js")
    monkeypatch.setattr(sw_shell, "SHELL_SOURCES", sw_shell._default_shell_sources())
    return tmp_path


class TestComputeShellHash:
    """Tests for compute_shell_hash()'s sensitivity and stability."""

    def test_is_stable_across_calls(self, shell_tree: Path) -> None:
        """An unchanged tree hashes to the same digest every time."""
        assert sw_shell.compute_shell_hash() == sw_shell.compute_shell_hash()

    @pytest.mark.parametrize(
        "relative_path",
        [
            "static/js/other.js",
            "src/css/main.css",
            "apps/public/templates/public/base.html",
            "apps/public/templates/public/home.html",
            "apps/public/templates/public/partials/_map_embed.html",
            "static/offline.html",
        ],
    )
    def test_changes_when_any_shell_source_changes(
        self, shell_tree: Path, relative_path: str
    ) -> None:
        """Editing any hashed shell source changes the digest.

        This is the whole contract: the cache name is derived from this
        digest, so a source the hash ignores is a source whose change never
        reaches a returning client.
        """
        before = sw_shell.compute_shell_hash()

        target = shell_tree / relative_path
        target.write_text(
            target.read_text(encoding="utf-8") + "/* changed */\n", encoding="utf-8"
        )

        assert sw_shell.compute_shell_hash() != before

    def test_changes_when_sw_js_itself_changes(self, shell_tree: Path) -> None:
        """Hash sw.js like any other source.

        SNOW-590 dropped the chicken-and-egg normalisation along with the
        hand-bumped constant, so an edit to sw.js now changes the digest.
        """
        before = sw_shell.compute_shell_hash()

        sw_js = shell_tree / "static" / "js" / "sw.js"
        sw_js.write_text(
            sw_js.read_text(encoding="utf-8") + "// changed\n", encoding="utf-8"
        )

        assert sw_shell.compute_shell_hash() != before

    def test_ignores_untracked_build_artefacts(self, shell_tree: Path) -> None:
        """static/css/output.css is a gitignored build artefact and must not be hashed."""
        before = sw_shell.compute_shell_hash()

        output_css = shell_tree / "static" / "css" / "output.css"
        output_css.parent.mkdir(parents=True, exist_ok=True)
        output_css.write_text("body{color:blue}\n", encoding="utf-8")

        assert sw_shell.compute_shell_hash() == before


class TestCacheVersion:
    """Tests for the derived cache name."""

    def test_uses_the_shell_prefix_and_a_hash_slice(self, shell_tree: Path) -> None:
        """The name is the fixed prefix plus a slice of the digest."""
        version = sw_shell.cache_version()

        assert version.startswith("snowdesk-shell-")
        suffix = version.removeprefix("snowdesk-shell-")
        assert suffix == sw_shell.compute_shell_hash()[: sw_shell._HASH_SLICE]
        assert len(suffix) == sw_shell._HASH_SLICE

    def test_changes_when_the_shell_changes(self, shell_tree: Path) -> None:
        """A shell edit yields a different cache name — the point of the whole change."""
        before = sw_shell.cache_version()

        (shell_tree / "src" / "css" / "main.css").write_text(
            "body { color: green; }\n", encoding="utf-8"
        )

        assert sw_shell.cache_version() != before


class TestInjectCacheVersion:
    """Tests for the serve-time CACHE_VERSION substitution."""

    def test_replaces_the_placeholder(self) -> None:
        """The committed placeholder is rewritten with the supplied version."""
        body = "const CACHE_VERSION = 'snowdesk-shell-UNSUBSTITUTED';\n"

        result = sw_shell.inject_cache_version(body, version="snowdesk-shell-abc123")

        assert result == "const CACHE_VERSION = 'snowdesk-shell-abc123';\n"
        assert "UNSUBSTITUTED" not in result

    def test_replaces_any_previously_shipped_literal(self) -> None:
        """A stale hand-bumped value is rewritten too, not just the placeholder."""
        body = "const CACHE_VERSION = 'snowdesk-shell-v108';\n"

        result = sw_shell.inject_cache_version(body, version="snowdesk-shell-abc123")

        assert result == "const CACHE_VERSION = 'snowdesk-shell-abc123';\n"

    def test_replaces_only_the_first_assignment(self) -> None:
        """Substitution is bounded — a later mention in a comment is untouched."""
        body = (
            "const CACHE_VERSION = 'snowdesk-shell-UNSUBSTITUTED';\n"
            "// see const CACHE_VERSION = 'x'; above\n"
        )

        result = sw_shell.inject_cache_version(body, version="v")

        assert result.count("const CACHE_VERSION = 'v';") == 1
        assert "const CACHE_VERSION = 'x';" in result

    def test_defaults_to_the_derived_version(self, shell_tree: Path) -> None:
        """Called without an explicit version, it injects cache_version()."""
        body = "const CACHE_VERSION = 'placeholder';\n"

        result = sw_shell.inject_cache_version(body)

        assert sw_shell.cache_version() in result

    def test_raises_when_no_assignment_is_present(self) -> None:
        """A body with nothing to substitute raises rather than passing through.

        Returning it unchanged would pin every client to one frozen cache
        name and stop shell updates reaching anyone — the SNOW-457
        regression, but silent.
        """
        with pytest.raises(ValueError, match="No `const CACHE_VERSION"):
            sw_shell.inject_cache_version("// no assignment here\n")

    def test_raises_on_a_double_quoted_assignment(self) -> None:
        """The single-quoted form is the contract; a reformat must fail loudly."""
        with pytest.raises(ValueError):
            sw_shell.inject_cache_version('const CACHE_VERSION = "single-only";\n')


class TestRealShellSource:
    """Guards against the real repo drifting out of the substitutable shape."""

    def test_committed_sw_js_is_substitutable(self) -> None:
        """The real static/js/sw.js can be substituted.

        Belt-and-braces alongside
        ``apps.core.checks.check_sw_cache_version_substitutable`` — this one
        fails in the unit suite, which runs on every PR.
        """
        source = sw_shell.SW_JS_PATH.read_text(encoding="utf-8")

        result = sw_shell.inject_cache_version(source, version="snowdesk-shell-probe")

        assert "const CACHE_VERSION = 'snowdesk-shell-probe';" in result

    def test_committed_sw_js_ships_the_placeholder(self) -> None:
        """The committed value is the inert placeholder, not a real-looking name.

        A plausible-looking literal in source would make a substitution
        failure indistinguishable from correct operation in devtools.
        """
        source = sw_shell.SW_JS_PATH.read_text(encoding="utf-8")

        assert "const CACHE_VERSION = 'snowdesk-shell-UNSUBSTITUTED';" in source


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


class TestRepoRelative:
    """Tests for _repo_relative()'s outside-REPO_ROOT fallback."""

    def test_falls_back_to_posix_form_outside_repo_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A path outside REPO_ROOT returns its own POSIX form, not an error."""
        monkeypatch.setattr(sw_shell, "REPO_ROOT", tmp_path / "elsewhere")
        outside_path = tmp_path / "not_under_repo_root.js"

        assert sw_shell._repo_relative(outside_path) == outside_path.as_posix()
