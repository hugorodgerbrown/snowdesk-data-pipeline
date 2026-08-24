"""
tests/sentinels/test_fidelity_lint.py — tests for the fidelity guard itself.

A guard nobody tests is a guard that quietly stops guarding. These cover
the machinery in ``tests/sentinels/fidelity.py`` and the three structural
checks ``bin/fidelity-lint`` runs on top of it: an unclassified path
fails and names itself, an exclusion with a blank reason fails, and a row
for a path no sentinel carries fails as stale.

Nothing here touches the database or renders a page — that is
``test_fidelity.py``'s job. These are pure-Python tests of the same
module the dependency-free lint imports.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests.sentinels import fidelity
from tests.sentinels.fidelity import (
    EXCLUDED,
    RENDERED,
    Excluded,
    Rendered,
    RenderedPage,
    aspects,
    boolean_marker,
    danger_pattern,
    flatten,
    literal,
    mapped,
    prose,
    provider_of,
    resolve,
    snake_label,
    split_key,
    timestamp,
    visible_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LINT_SCRIPT = REPO_ROOT / "bin" / "fidelity-lint"


def _load_lint() -> Any:
    """Import ``bin/fidelity-lint`` as a module (it has no ``.py`` suffix)."""
    spec = importlib.util.spec_from_loader(
        "fidelity_lint",
        importlib.machinery.SourceFileLoader("fidelity_lint", str(LINT_SCRIPT)),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _page(text: str = "", html: str = "") -> RenderedPage:
    """Build a RenderedPage carrying the given text and markup."""
    return RenderedPage(
        sentinel_id="slf/A-single-level",
        text=text.casefold(),
        html=html,
        render_model={},
    )


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


class TestFlatten:
    """``flatten`` turns a payload into dotted paths naming fields."""

    def test_list_indices_collapse_to_one_path(self) -> None:
        """Two problems' aspects are one path, not two."""
        result = flatten(
            {"avalancheProblems": [{"aspects": ["N", "E"]}, {"aspects": ["S"]}]}
        )
        assert set(result) == {"avalancheProblems[].aspects[]"}
        assert result["avalancheProblems[].aspects[]"] == ["N", "E", "S"]

    def test_nested_dicts_dot_together(self) -> None:
        """Nested keys join with dots."""
        assert set(flatten({"validTime": {"startTime": "x"}})) == {
            "validTime.startTime"
        }

    def test_empty_containers_yield_no_path(self) -> None:
        """An empty dict or list carries nothing for a page to show.

        A path with no value is not a decision anybody can make, so it
        must not land on the table as an unclassified failure.
        """
        assert flatten({"elevation": {}, "regions": [], "lang": "en"}) == {
            "lang": ["en"]
        }

    def test_nulls_are_kept_as_values(self) -> None:
        """A null leaf still names a path — the field exists, it is just empty."""
        assert flatten({"nextUpdate": None}) == {"nextUpdate": [None]}


# ---------------------------------------------------------------------------
# visible_text
# ---------------------------------------------------------------------------


class TestVisibleText:
    """``visible_text`` is what stops probes reading the debug payload."""

    def test_script_bodies_are_stripped(self) -> None:
        """A value visible only inside a script is not visible text.

        This is the whole guard's load-bearing assumption: the bulletin
        page embeds the raw CAAML payload in a script block under DEBUG.
        """
        html = '<p>shown</p><script type="application/json">{"secret": 1}</script>'
        assert "secret" not in visible_text(html)
        assert "shown" in visible_text(html)

    def test_style_bodies_are_stripped(self) -> None:
        """CSS is not something a reader reads."""
        assert "colour" not in visible_text("<style>.x{colour:red}</style><p>hi</p>")

    def test_entities_are_unescaped_and_whitespace_collapsed(self) -> None:
        """A reader sees "a & b", not "a &amp;  b"."""
        assert visible_text("<p>a &amp;\n\n  b</p>") == "a & b"


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


class TestProbes:
    """Each probe finds what it claims to, and misses what it should."""

    def test_literal_matches_the_value_verbatim(self) -> None:
        assert literal()(["Moderate"], _page("danger: moderate today"))
        assert not literal()(["Moderate"], _page("danger: low today"))

    def test_literal_ignores_nulls(self) -> None:
        """A null value gives a page nothing to show, so it cannot fail."""
        assert literal()([None], _page(""))

    def test_snake_label_matches_the_humanised_form(self) -> None:
        assert snake_label()(
            ["persistent_weak_layers"], _page("Persistent weak layers")
        )

    def test_snake_label_rejects_the_raw_form(self) -> None:
        """A page showing "persistent_weak_layers" is a bug, not coverage."""
        assert not snake_label()(
            ["persistent_weak_layers"], _page("persistent_weak_layers")
        )

    def test_prose_matches_the_body_past_its_heading(self) -> None:
        """Prose is split into panel title and body, so the whole never appears."""
        value = "<h1>Snowpack</h1><p>The old snow is well bonded in most places.</p>"
        assert prose()([value], _page("snowpack the old snow is well bonded in most"))

    def test_prose_fails_when_the_body_is_absent(self) -> None:
        value = "<h1>Snowpack</h1><p>The old snow is well bonded in most places.</p>"
        assert not prose()([value], _page("snowpack"))

    def test_mapped_requires_the_mapped_label(self) -> None:
        assert mapped({2: "medium"})([2], _page("size medium"))
        assert not mapped({2: "medium"})([2], _page("size 2"))

    def test_mapped_fails_on_a_value_it_has_never_seen(self) -> None:
        """An unmapped value means nobody has checked how it renders."""
        assert not mapped({2: "medium"})([9], _page("size medium"))

    def test_aspects_matches_a_comma_delimited_run(self) -> None:
        assert aspects()(["N", "NE"], _page("n, ne, e above 2200m"))

    def test_aspects_rejects_a_bare_substring(self) -> None:
        """A bare compass point must be a token, not any substring.

        Otherwise "N" matches half the words on the page.
        """
        assert not aspects()(["N"], _page("north-facing slopes are dangerous"))

    def test_aspects_accepts_the_all_aspects_collapse(self) -> None:
        assert aspects()(["N", "NE", "E"], _page("all aspects above 2400m"))

    def test_danger_pattern_expects_the_gm_form(self) -> None:
        assert danger_pattern()(["DP1"], _page("gm.1 gm.2"))
        assert not danger_pattern()(["DP3"], _page("gm.1 gm.2"))

    def test_timestamp_matches_the_rendered_format(self) -> None:
        assert timestamp()(["2025-11-28T18:06:01Z"], _page("issued 28 nov 18:06 utc"))

    def test_timestamp_rejects_the_raw_iso_string(self) -> None:
        """The strip reformats every timestamp; the source form never appears."""
        assert not timestamp()(
            ["2025-11-28T18:06:01Z"], _page("issued 2025-11-28t18:06:01z")
        )

    def test_boolean_marker_requires_the_marker_when_true(self) -> None:
        assert boolean_marker("m")([True], _page(html='<div data-testid="m">'))
        assert not boolean_marker("m")([True], _page(html="<div>"))

    def test_boolean_marker_forbids_the_marker_when_false(self) -> None:
        """A page rendering the marker unconditionally is also wrong."""
        assert boolean_marker("m")([False], _page(html="<div>"))
        assert not boolean_marker("m")([False], _page(html='<div data-testid="m">'))


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestResolution:
    """Provider-scoped rows win over bare ones."""

    def test_bare_path_applies_to_every_provider(self) -> None:
        for provider in fidelity.PROVIDERS:
            assert isinstance(resolve("bulletinID", provider), Excluded)

    def test_provider_scope_overrides_the_bare_row(self) -> None:
        """The same field is prose from ALBINA and a level name from MF."""
        assert isinstance(resolve("tendency[].highlights", "albina"), Rendered)
        assert isinstance(resolve("tendency[].highlights", "meteofrance"), Excluded)

    def test_unclassified_path_resolves_to_none(self) -> None:
        assert resolve("someProviderAddedThis", "slf") is None

    def test_split_key_separates_a_real_scope(self) -> None:
        assert split_key("meteofrance:tendency[].highlights") == (
            "meteofrance",
            "tendency[].highlights",
        )

    def test_split_key_leaves_a_bare_path_alone(self) -> None:
        """A colon inside a path is not a provider scope."""
        assert split_key("bulletinID") == (None, "bulletinID")


# ---------------------------------------------------------------------------
# The table's own invariants
# ---------------------------------------------------------------------------


class TestTableInvariants:
    """Properties the committed table must hold, independent of the lint."""

    def test_no_path_is_both_rendered_and_excluded(self) -> None:
        """A field either reaches a reader or does not. Not both."""
        assert not set(RENDERED) & set(EXCLUDED)

    def test_every_exclusion_has_a_reason(self) -> None:
        blank = [key for key, entry in EXCLUDED.items() if not entry.reason.strip()]
        assert not blank, f"exclusions with no reason: {blank}"

    def test_every_rendered_row_names_its_surface(self) -> None:
        """A failure that cannot say what broke is a failure nobody acts on."""
        blank = [key for key, entry in RENDERED.items() if not entry.surface.strip()]
        assert not blank, f"rendered rows with no surface: {blank}"

    def test_every_duplicate_of_names_a_real_path(self) -> None:
        """A pointer to a path that does not exist is worse than none."""
        known = {split_key(key)[1] for key in set(RENDERED) | set(EXCLUDED)}
        dangling = {
            key: entry.duplicate_of
            for key, entry in EXCLUDED.items()
            if entry.duplicate_of and entry.duplicate_of not in known
        }
        assert not dangling, f"duplicate_of pointing nowhere: {dangling}"

    def test_provider_of_reads_the_sentinel_directory(self) -> None:
        assert provider_of("albina/A-single-level") == "albina"


# ---------------------------------------------------------------------------
# bin/fidelity-lint
# ---------------------------------------------------------------------------


class TestLintChecks:
    """The three structural checks, each provoked deliberately."""

    def test_committed_table_is_clean(self) -> None:
        """The state this branch leaves the repo in passes its own guard."""
        assert _load_lint().check() == []

    def test_unclassified_path_fails_and_names_itself(self) -> None:
        """A provider adding a field breaks the build until somebody decides."""
        lint = _load_lint()
        with patch.object(
            lint,
            "sentinel_paths",
            return_value={"slf/A-single-level": {"providerAddedThis"}},
        ):
            violations = lint.check()
        assert any("providerAddedThis" in v for v in violations)
        assert any("unclassified" in v for v in violations)

    def test_exclusion_with_a_blank_reason_fails(self) -> None:
        """The reason is the whole point; a blank one is an oversight."""
        lint = _load_lint()
        with patch.dict(lint.EXCLUDED, {"lang": Excluded("   ")}, clear=False):
            violations = lint.check()
        assert any("exclusion with no reason" in v and "lang" in v for v in violations)

    def test_stale_row_fails(self) -> None:
        """A row for a path no sentinel carries any more is dead weight."""
        lint = _load_lint()
        with patch.dict(
            lint.EXCLUDED, {"fieldThatWasRemoved": Excluded("gone upstream")}
        ):
            violations = lint.check()
        assert any("stale row" in v and "fieldThatWasRemoved" in v for v in violations)

    def test_stale_provider_scoped_row_fails(self) -> None:
        """A scope naming a provider whose sentinels lack the path is stale."""
        lint = _load_lint()
        with patch.dict(lint.EXCLUDED, {"slf:customData.MF.massif": Excluded("nope")}):
            violations = lint.check()
        assert any("stale provider-scoped row" in v for v in violations)


class TestLintCli:
    """The CLI contract: exit codes and the audit view.

    Run in-process rather than through ``subprocess`` — the script is the
    module these tests already import, so spawning a interpreter would
    only add a second way for the same code to be wrong.
    """

    def test_bare_invocation_exits_zero_on_the_committed_table(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lint = _load_lint()
        with patch.object(sys, "argv", ["fidelity-lint"]):
            assert lint.main() == 0
        assert "all classified" in capsys.readouterr().out

    def test_violations_exit_non_zero_and_are_printed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """CI needs the exit code; a human needs the path named."""
        lint = _load_lint()
        with (
            patch.object(
                lint,
                "sentinel_paths",
                return_value={"slf/A-single-level": {"providerAddedThis"}},
            ),
            patch.object(sys, "argv", ["fidelity-lint"]),
        ):
            assert lint.main() == 1
        assert "providerAddedThis" in capsys.readouterr().out

    def test_show_exclusions_prints_every_reason(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The audit view is what makes the omissions reviewable cold."""
        lint = _load_lint()
        with patch.object(sys, "argv", ["fidelity-lint", "--show-exclusions"]):
            assert lint.main() == 0
        out = capsys.readouterr().out
        for key, entry in EXCLUDED.items():
            path = split_key(key)[1]
            assert path in out, f"{path} missing from --show-exclusions"
            assert entry.reason.split()[0] in out

    def test_lint_needs_no_django(self) -> None:
        """The check runs in the dependency-free lint matrix, so prove it.

        ``fidelity.py`` importing anything from ``apps`` or ``django``
        would work locally and fail in CI, where the lint envs install
        nothing. Asserting on the module's source keeps that honest.
        """
        source = Path(fidelity.__file__).read_text(encoding="utf-8")
        for forbidden in ("import django", "from django", "from apps", "import apps"):
            assert forbidden not in source, (
                f"tests/sentinels/fidelity.py must stay import-free of Django "
                f"and the app tree — found {forbidden!r}"
            )


@pytest.mark.parametrize("sentinel_id", sorted(fidelity.sentinel_paths()))
def test_every_sentinel_is_fully_classified(sentinel_id: str) -> None:
    """Per-sentinel view of the same check, so a failure names the file.

    Args:
        sentinel_id: e.g. ``"slf/A-single-level"``.

    """
    provider = provider_of(sentinel_id)
    unclassified = sorted(
        path
        for path in fidelity.sentinel_paths()[sentinel_id]
        if resolve(path, provider) is None
    )
    assert not unclassified, (
        f"{sentinel_id} carries {len(unclassified)} unclassified CAAML path(s): "
        f"{unclassified}"
    )
