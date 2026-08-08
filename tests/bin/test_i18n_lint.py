"""
tests/bin/test_i18n_lint.py — the hardcoded-JS-string guard from SNOW-620.

Fixture-driven, asserted through the exit code, because the exit code is
what CI reads. Mirrors tests/bin/test_lint_guards_js.py (SNOW-619).

The guard exists because roughly forty user-facing strings accumulated in
eight JS modules where `makemessages` cannot see them, and nothing in the
toolchain would ever have said so. The cases below are split between "it
catches the thing" and — more important for a heuristic check — "it stays
quiet about the enormous volume of non-prose strings these modules assign",
since a guard that cries wolf gets suppressed everywhere and stops working.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
I18N_LINT = REPO_ROOT / "bin" / "i18n-lint"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the linter over `args` and capture its result."""
    return subprocess.run(  # noqa: S603
        [sys.executable, str(I18N_LINT), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


class TestFlagsHardcodedProse:
    """Strings that reach the user and cannot reach the catalogue."""

    @pytest.mark.parametrize(
        "line",
        [
            "el.textContent = 'Update available';",
            'el.textContent = "Update available";',
            "el.innerHTML = 'Sign in to save a favourite.';",
            "el.title = 'Play season timelapse';",
            "el.placeholder = 'Unnamed pin';",
            "el.ariaLabel = 'Hide map controls';",
            "el.setAttribute('aria-label', 'Show map controls');",
            "el.textContent += 'No syncs yet.';",
        ],
        ids=lambda v: v[:28],
    )
    def test_flags_a_literal_reaching_a_user_visible_sink(
        self, tmp_path: Path, line: str
    ) -> None:
        """Every sink form the codebase actually uses is covered."""
        js = tmp_path / "offender.js"
        js.write_text(line + "\n")

        result = _run(str(js))

        assert result.returncode == 1
        assert "hardcoded-string" in result.stdout

    def test_flags_a_template_literal_with_prose_between_the_holes(
        self, tmp_path: Path
    ) -> None:
        """`${set} / ${total} set` has a translatable word in it."""
        js = tmp_path / "offender.js"
        js.write_text("el.textContent = `${set} / ${total} set`;\n")

        assert _run(str(js)).returncode == 1

    def test_names_the_offending_string_in_the_message(self, tmp_path: Path) -> None:
        """The reader has to be told which string, not just which line."""
        js = tmp_path / "offender.js"
        js.write_text("el.textContent = 'Update available';\n")

        assert "Update available" in _run(str(js)).stdout


class TestFlagsVariableIndirection:
    """SNOW-645 review: one hop of variable indirection into a sink.

    ``SINK_RE`` alone is blind to a value arriving through a variable —
    which is exactly how seven of ``map_region_download.js``'s eight
    roundel labels shipped untranslated for as long as this check has
    existed (a `const text = {…}[state]` map, read by both
    ``setAttribute('aria-label', text)`` and ``btn.title = text``). This
    class is the regression test against that pattern reappearing anywhere
    in the tree.
    """

    def test_flags_the_exact_map_region_download_shape(self, tmp_path: Path) -> None:
        """A state-keyed label object, read by two sinks in a row."""
        js = tmp_path / "offender.js"
        js.write_text(
            "function setState(state) {\n"
            "  const text = {\n"
            "    idle: 'Download the basemap for this region',\n"
            "    done: 'The basemap for this region is downloaded',\n"
            "  }[state];\n"
            "  btn.setAttribute('aria-label', text);\n"
            "  btn.title = text;\n"
            "}\n"
        )

        result = _run(str(js))

        assert result.returncode == 1
        assert "Download the basemap for this region" in result.stdout
        assert "The basemap for this region is downloaded" in result.stdout

    def test_flags_a_ternary_assigned_to_a_variable_before_the_sink(
        self, tmp_path: Path
    ) -> None:
        """The simplest shape: one literal, one ternary branch, one hop."""
        js = tmp_path / "offender.js"
        js.write_text(
            "const label = saving ? 'Saving…' : 'Save';\nel.textContent = label;\n"
        )

        assert _run(str(js)).returncode == 1

    def test_reports_a_traced_literal_only_once_across_two_sinks(
        self, tmp_path: Path
    ) -> None:
        """One declaration read by two sinks is one thing to fix, not two."""
        js = tmp_path / "offender.js"
        js.write_text(
            "const text = 'Update available';\n"
            "el.setAttribute('aria-label', text);\n"
            "el.title = text;\n"
        )

        result = _run(str(js))

        assert result.returncode == 1
        # Two SINKS read the one declaration; count the SUMMARY LINE, not
        # occurrences of the string itself — it legitimately appears twice
        # within a single finding (the snippet, and the "→ …" explanation
        # quoting `finding.value`).
        assert "1 hardcoded user-facing string(s)." in result.stdout

    def test_does_not_flag_a_bare_reassignment_still_out_of_that_variable(
        self, tmp_path: Path
    ) -> None:
        """A declaration with nothing to trace back stays quiet, as today."""
        js = tmp_path / "clean.js"
        js.write_text("const label = computeLabel();\nel.textContent = label;\n")

        assert _run(str(js)).returncode == 0


class TestStaysQuiet:
    """The non-prose strings these modules assign constantly."""

    @pytest.mark.parametrize(
        "line",
        [
            # Machine tokens: ids, dataset keys, class names, states.
            "el.textContent = '';",
            "el.textContent = '×';",
            "el.textContent = 'no_rating';",
            "el.textContent = 'region-tooltip-date';",
            "el.textContent = 'CACHE_VERSION';",
            "el.setAttribute('aria-label', '');",
            # Pure data interpolation — nothing for a translator to do.
            "el.textContent = `${record.digit || ''}${record.subdivision || ''}`;",
            "el.textContent = `[${stamp}] ${msg}`;",
            # A bare placeholder is substituted into, not translated.
            "el.textContent = '%(used)s';",
            # Not a user-visible sink.
            "el.className = 'Available offline';",
            "el.dataset.state = 'Playing now';",
            "fetch('/api/ratings/');",
        ],
        ids=lambda v: v[:30],
    )
    def test_does_not_flag(self, tmp_path: Path, line: str) -> None:
        """A false positive is a demand to suppress code that was fine."""
        js = tmp_path / "clean.js"
        js.write_text(line + "\n")

        assert _run(str(js)).returncode == 0, line

    def test_ignores_prose_in_a_header_comment(self, tmp_path: Path) -> None:
        """These files carry long headers quoting the very strings they set."""
        js = tmp_path / "prose.js"
        js.write_text(
            "/*\n * Sets el.textContent = 'Update available' on install.\n */\n"
            "// el.textContent = 'Reload';\n"
        )

        assert _run(str(js)).returncode == 0

    def test_accepts_a_string_read_from_the_strings_template(
        self, tmp_path: Path
    ) -> None:
        """The shape this whole ticket converts everything to."""
        js = tmp_path / "clean.js"
        js.write_text(
            "const S = self.pwaStrings.read('t', { reload: 'Reload' });\n"
            "el.textContent = S.reload;\n"
        )

        assert _run(str(js)).returncode == 0

    def test_accepts_a_ternary_of_two_map_strings_lookups_through_a_variable(
        self, tmp_path: Path
    ) -> None:
        """The `custom-control-idle`/`-done` shape — already translated."""
        js = tmp_path / "clean.js"
        js.write_text(
            "const text = done\n"
            "  ? MAP_STRINGS['custom-control-done']\n"
            "  : MAP_STRINGS['custom-control-idle'];\n"
            "btn.setAttribute('aria-label', text);\n"
        )

        assert _run(str(js)).returncode == 0

    def test_ignores_a_separator_literal_passed_to_a_function_call_in_the_declaration(
        self, tmp_path: Path
    ) -> None:
        """A `.join(' &middot; ')` separator sits in a CALL ARGUMENT, not a
        value position (after `=`/`?`/`:`) — map.js's own attribution line
        builds exactly this shape, and it is decoration, not a message.
        """
        js = tmp_path / "clean.js"
        js.write_text(
            "const html = Array.from(seen).join(' &middot; ');\nel.innerHTML = html;\n"
        )

        assert _run(str(js)).returncode == 0

    def test_ignores_a_function_parameter_indirection(self, tmp_path: Path) -> None:
        """A parameter never matches DECL_RE/REASSIGN_RE (both anchored at
        line-start) — this needs real dataflow to trace, which is exactly
        the class of case this check declines to chase (see its own
        module docstring).
        """
        js = tmp_path / "clean.js"
        js.write_text("function toast(message) {\n  body.textContent = message;\n}\n")

        assert _run(str(js)).returncode == 0


class TestEscapeHatch:
    """`// i18n-allow: <reason>`."""

    def test_a_preceding_allow_comment_suppresses(self, tmp_path: Path) -> None:
        """The documented form."""
        js = tmp_path / "allowed.js"
        js.write_text(
            "// i18n-allow: staff-only diagnostic page\n"
            "el.textContent = 'unsupported in this browser';\n"
        )

        assert _run(str(js)).returncode == 0

    def test_a_trailing_allow_comment_suppresses(self, tmp_path: Path) -> None:
        """Short reasons read better on the line itself."""
        js = tmp_path / "allowed.js"
        js.write_text("el.textContent = 'Debug mode'; // i18n-allow: staff only\n")

        assert _run(str(js)).returncode == 0

    def test_an_allow_above_a_wrapped_reason_still_suppresses(
        self, tmp_path: Path
    ) -> None:
        """A reason worth writing rarely fits on one line.

        Both real suppressions in the tree wrap, so scanning back only one
        line would have made the hatch unusable at its documented length.
        """
        js = tmp_path / "allowed.js"
        js.write_text(
            "// i18n-allow: the resort editor is staff-only (edit_mode,\n"
            "// superuser gate) and never rendered for a member of the public.\n"
            "el.textContent = 'Debug mode';\n"
        )

        assert _run(str(js)).returncode == 0

    def test_an_allow_does_not_leak_past_the_comment_block(
        self, tmp_path: Path
    ) -> None:
        """A suppression covers its own statement, not the rest of the file."""
        js = tmp_path / "leaky.js"
        js.write_text(
            "// i18n-allow: staff only\n"
            "el.textContent = 'Debug mode';\n"
            "other.textContent = 'Sign in to save a favourite.';\n"
        )

        assert _run(str(js)).returncode == 1

    def test_an_allow_without_a_reason_does_not_suppress(self, tmp_path: Path) -> None:
        """The reason is the point — a bare marker is not an argument."""
        js = tmp_path / "reasonless.js"
        js.write_text("// i18n-allow:\nel.textContent = 'Update available';\n")

        assert _run(str(js)).returncode == 1

    def test_show_allows_prints_every_suppression_and_its_reason(
        self, tmp_path: Path
    ) -> None:
        """Suppressions must be auditable in bulk, not one file at a time."""
        js = tmp_path / "allowed.js"
        js.write_text(
            "// i18n-allow: staff-only diagnostic page\nel.textContent = 'Debug';\n"
        )

        result = _run("--show-allows", str(js))

        assert "staff-only diagnostic page" in result.stdout


class TestShippedTree:
    """The assertion that keeps the tree converted."""

    def test_the_shipped_tree_is_clean(self) -> None:
        """Every first-party module passes today.

        SNOW-620 converted eight of them; the two that remain are staff-only
        surfaces carrying an explicit `i18n-allow`.
        """
        result = _run()

        assert result.returncode == 0, result.stdout

    def test_only_the_two_staff_only_files_are_suppressed(self) -> None:
        """A hatch is cheap to add — this is what keeps it from spreading."""
        result = _run("--show-allows")
        suppressed = {
            line.split(":")[0].strip()
            for line in result.stdout.splitlines()
            if line.startswith("  static/js/")
        }

        assert suppressed == {
            "static/js/map_edit_resorts.js",
            "static/js/push_demo.js",
        }
