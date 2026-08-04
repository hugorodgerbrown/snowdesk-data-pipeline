"""
tests/bin/test_lint_guards_js.py — the two JS lint guards from SNOW-619.

`bin/ds-lint` extended to `static/js`, and the new `bin/js-globals-lint`.
Both are fixture-driven: a file that must fail and a file that must pass,
asserted through the exit code, because the exit code is what CI reads.

The guards exist because the review found things nothing could have
caught. `map_edit_resorts.js` built class strings from a hardcoded palette
that would have failed `tox -e ds-lint` inside a template, and
`map_layer_sync_status.js` read `window.MAP` for its whole life against an
assignment that has never existed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DS_LINT = REPO_ROOT / "bin" / "ds-lint"
GLOBALS_LINT = REPO_ROOT / "bin" / "js-globals-lint"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one linter over `args` and capture its result."""
    return subprocess.run(  # noqa: S603
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


class TestDsLintOnJavaScript:
    """`bin/ds-lint` applied to `static/js/*.js`."""

    def test_flags_a_raw_palette_utility_in_a_js_class_string(
        self, tmp_path: Path
    ) -> None:
        """A Tailwind palette utility built in JS fails, as it would in a template."""
        js = tmp_path / "offender.js"
        js.write_text("el.className = 'bg-red-100 text-red-800';\n")

        result = _run(DS_LINT, str(js))

        assert result.returncode == 1
        assert "raw-palette" in result.stdout

    def test_accepts_design_tokens(self, tmp_path: Path) -> None:
        """The token equivalents of the same class string pass."""
        js = tmp_path / "clean.js"
        js.write_text("el.className = 'bg-status-error-bg text-status-error-text';\n")

        assert _run(DS_LINT, str(js)).returncode == 0

    def test_a_js_comment_suppresses_the_next_line(self, tmp_path: Path) -> None:
        """`// ds-lint-allow: <reason>` is the JS form of the escape hatch."""
        js = tmp_path / "allowed.js"
        js.write_text(
            "// ds-lint-allow: third-party widget dictates this class\n"
            "el.className = 'bg-red-100';\n"
        )

        assert _run(DS_LINT, str(js)).returncode == 0

    def test_ignores_a_palette_name_mentioned_in_a_comment(
        self, tmp_path: Path
    ) -> None:
        """Prose about a class is not a class.

        These files carry long header comments naming the very utilities the
        rules forbid; without this the linter fails on its own documentation.
        """
        js = tmp_path / "prose.js"
        js.write_text(
            "/*\n * This used to use bg-red-100 before SNOW-619.\n */\n"
            "// and text-slate-400 too\n"
        )

        assert _run(DS_LINT, str(js)).returncode == 0

    def test_does_not_apply_the_hex_rule_to_js(self, tmp_path: Path) -> None:
        """A hex literal in JS passes; in a template it does not.

        MapLibre paint properties take literal colours and cannot reference
        a CSS variable, and the EAWS danger-scale palette is mandated by the
        standard — so the rule's premise ("use a token instead") does not
        hold in JavaScript. The class-string rules still do.
        """
        js = tmp_path / "paint.js"
        js.write_text("map.setPaintProperty('l', 'line-color', '#1a1a1a');\n")
        html = tmp_path / "page.html"
        html.write_text('<div style="color: #1a1a1a"></div>\n')

        assert _run(DS_LINT, str(js)).returncode == 0
        assert _run(DS_LINT, str(html)).returncode == 1

    def test_the_shipped_tree_is_clean(self) -> None:
        """Every template and first-party JS file passes today.

        SNOW-619 converted `map_edit_resorts.js` to tokens, which was the
        one file failing on day one.
        """
        assert _run(DS_LINT).returncode == 0


class TestJsGlobalsLint:
    """`bin/js-globals-lint` — reads of globals nothing assigns."""

    def test_flags_a_read_of_a_never_assigned_global(self, tmp_path: Path) -> None:
        """The `window.MAP` shape: read somewhere, assigned nowhere."""
        js = tmp_path / "reader.js"
        js.write_text("const m = window.MAP;\n")

        result = _run(GLOBALS_LINT, str(js))

        assert result.returncode == 1
        assert "unknown-global" in result.stdout
        assert "MAP" in result.stdout

    def test_accepts_a_global_another_file_assigns(self, tmp_path: Path) -> None:
        """Cross-module reads are the point of the `window.pwa*` convention."""
        (tmp_path / "publisher.js").write_text("window.pwaThing = { go() {} };\n")
        (tmp_path / "reader.js").write_text("window.pwaThing.go();\n")

        assert _run(GLOBALS_LINT, str(tmp_path)).returncode == 0

    def test_accepts_a_global_published_by_defineProperty(self, tmp_path: Path) -> None:
        """`pwa_reset.js` publishes its export this way."""
        (tmp_path / "publisher.js").write_text(
            "Object.defineProperty(window, 'pwaResetLocalData', { value: fn });\n"
        )
        (tmp_path / "reader.js").write_text("window.pwaResetLocalData(true);\n")

        assert _run(GLOBALS_LINT, str(tmp_path)).returncode == 0

    def test_accepts_a_top_level_function_declaration(self, tmp_path: Path) -> None:
        """A classic script hangs top-level `function`s off the global object.

        This is the distinction that makes the check worth having rather
        than merely noisy: `map.js` declares `let MAP` (NOT a window
        property, so `window.MAP` was a real bug) and
        `function activeBasemapTileTemplate(…)` (which IS one, so the
        same-looking read is fine).
        """
        (tmp_path / "declarer.js").write_text(
            "function activeBasemapTileTemplate(map) {\n  return null;\n}\n"
        )
        (tmp_path / "reader.js").write_text("window.activeBasemapTileTemplate(map);\n")

        assert _run(GLOBALS_LINT, str(tmp_path)).returncode == 0

    def test_still_flags_a_top_level_let(self, tmp_path: Path) -> None:
        """`let` at top level does NOT become a window property."""
        (tmp_path / "declarer.js").write_text("let MAP = null;\n")
        (tmp_path / "reader.js").write_text("const m = window.MAP;\n")

        assert _run(GLOBALS_LINT, str(tmp_path)).returncode == 1

    def test_ignores_an_assignment(self, tmp_path: Path) -> None:
        """Assigning a global defines it; only reads are checked."""
        js = tmp_path / "publisher.js"
        js.write_text("window.pwaBrandNew = 1;\n")

        assert _run(GLOBALS_LINT, str(js)).returncode == 0

    def test_ignores_a_global_named_only_in_a_comment(self, tmp_path: Path) -> None:
        """Prose naming `window.pwaFoo` is not a read of it."""
        js = tmp_path / "prose.js"
        js.write_text("// See window.pwaNeverExisted for the contract.\n")

        assert _run(GLOBALS_LINT, str(js)).returncode == 0

    def test_accepts_browser_globals(self, tmp_path: Path) -> None:
        """Platform globals are provided from outside the tree."""
        js = tmp_path / "platform.js"
        js.write_text(
            "if (window.PublicKeyCredential) {}\n"
            "window.localStorage.getItem('x');\n"
            "self.registration.showNotification('x');\n"
        )

        assert _run(GLOBALS_LINT, str(js)).returncode == 0

    def test_the_shipped_tree_is_clean(self) -> None:
        """No first-party module reads a global nothing assigns.

        Green since SNOW-607 fixed `window.MAP` — this is the assertion
        that keeps it that way.
        """
        assert _run(GLOBALS_LINT).returncode == 0
