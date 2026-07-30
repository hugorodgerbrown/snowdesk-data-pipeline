# test_prose_completeness.py — Regression tests for the BRA prose extraction.
#
# The stability narrative was clipped twice over, losing 63% of all comment
# lines (42,672 of 68,237) across essentially every archived record (SNOW-559):
#
#   1. Horizontally, by ``crop_left`` at ``COLUMN_SPLIT_X = 280.0`` — the prose
#      spans the full page width, so every line was cut at ~80 characters
#      mid-word ("Ces plaques se form", "accumulations d").
#   2. Vertically, by the fixed ``BAND_STABILITY`` bottom of 360 pt — the
#      section grows with the volume of prose, and its closing heading has been
#      observed at y=355, y=398 and y=400 across four bulletins.
#
# These tests pin both bounds and the section-heading vocabulary, so a
# reintroduced fixed band or column crop fails here rather than silently
# shortening every bulletin in a future rebuild.
"""Regression tests for complete (untruncated) BRA prose extraction."""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from _pdf_extract import BAND_STABILITY, COLUMN_SPLIT_X  # noqa: E402

# The clip width in characters that 280 pt of this PDF's body text works out to.
# Lines at or beyond it in the old output were the truncated ones.
_OLD_CLIP_MIN_CHARS = 74


def _plain(comment: str) -> str:
    """Return comment HTML as plain text.

    Args:
        comment: A ``format_comment_as_html`` output string.

    Returns:
        The text with tags replaced by newlines and entities unescaped.

    """
    return html.unescape(re.sub(r"<[^>]+>", "\n", comment or ""))


def _joined(comment: str) -> str:
    """Return comment text with soft line wraps collapsed to spaces.

    The PDF lays long sentences across several visual lines, so a line ending
    without punctuation is normal.  Collapsing the wraps is what makes a
    sentence-level assertion meaningful.

    Args:
        comment: A ``format_comment_as_html`` output string.

    Returns:
        Single-spaced text.

    """
    return " ".join(
        part.strip() for part in _plain(comment).split("\n") if part.strip()
    )


class TestStabilityBandIsDerivedFromHeadings:
    """The section bounds must come from the page, not from constants."""

    def test_band_extends_past_the_old_fixed_bottom(
        self, mont_blanc_pdf_path: Path
    ) -> None:
        """MONT-BLANC's stability section runs past the old 360 pt bottom.

        This is the fixture that proves the vertical clip was real: its closing
        heading sits at ~397.9, so the old fixed band dropped two lines of
        prose.

        Args:
            mont_blanc_pdf_path: Path to the committed MONT-BLANC PDF.

        """
        import pdfplumber
        from mf_bra_to_caaml import stability_band

        with pdfplumber.open(mont_blanc_pdf_path) as pdf:
            top, bottom = stability_band(pdf.pages[0])

        assert bottom > BAND_STABILITY[1], (
            f"Expected the derived band to extend past the old fixed bottom "
            f"({BAND_STABILITY[1]}), got {bottom}"
        )
        assert top >= BAND_STABILITY[0]

    def test_band_excludes_the_closing_heading(self, mont_blanc_pdf_path: Path) -> None:
        """The "Qualité de la neige" heading must not leak into the prose.

        pdfplumber keeps any character overlapping the crop box, so a bound set
        exactly at the heading's top still includes it.

        Args:
            mont_blanc_pdf_path: Path to the committed MONT-BLANC PDF.

        """
        import pdfplumber
        from mf_bra_to_caaml import extract_snowpack_comment

        with pdfplumber.open(mont_blanc_pdf_path) as pdf:
            comment = extract_snowpack_comment(pdf.pages[0])

        assert "Qualité de la neige" not in comment
        assert "Aperçu météo" not in comment


class TestProseIsNotColumnClipped:
    """Prose must span the full page width, not stop at COLUMN_SPLIT_X."""

    def test_lines_exceed_the_old_column_clip_width(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """At least one line must be longer than the old ~80-character clip.

        Args:
            parsed_mont_blanc: The parsed MONT-BLANC envelope.

        """
        props: dict[str, Any] = parsed_mont_blanc["properties"]
        text = _plain(props["snowpackStructure"]["comment"])
        longest = max((len(line) for line in text.split("\n")), default=0)

        assert longest > _OLD_CLIP_MIN_CHARS, (
            f"Longest prose line is {longest} chars, which is within the old "
            f"{COLUMN_SPLIT_X}pt column clip — the crop has regressed"
        )

    def test_narrative_ends_on_a_complete_sentence(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """The snowpack narrative must not stop mid-sentence.

        Args:
            parsed_mont_blanc: The parsed MONT-BLANC envelope.

        """
        joined = _joined(
            parsed_mont_blanc["properties"]["snowpackStructure"]["comment"]
        )

        assert joined, "Expected a non-empty snowpack narrative"
        assert joined.rstrip()[-1] in ".!?", (
            f"Narrative ends mid-sentence: …{joined[-80:]!r}"
        )


class TestActivityHeadingVocabulary:
    """Both heading wordings, in either order, must be recognised."""

    def test_both_halves_are_extracted(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """A bulletin with both headings yields both halves.

        Args:
            parsed_mont_blanc: The parsed MONT-BLANC envelope.

        """
        mf: dict[str, Any] = parsed_mont_blanc["properties"]["customData"]["MF"]

        assert mf["spontaneous"].strip()
        assert mf["triggered"].strip()

    def test_departs_provoques_is_recognised(self) -> None:
        """Both wordings of each heading are classified correctly.

        Météo-France uses "Déclenchements" and "Départs" interchangeably for
        both halves; handling only "Déclenchements provoqués" left 143 records
        (3.1%) with an empty activity comment.
        """
        from mf_bra_to_caaml import _ACTIVITY_HEADING_RE, _is_triggered_heading

        for heading in (
            "Déclenchements provoqués :",
            "Départs provoqués :",
            "Déclenchement provoqué :",
        ):
            match = _ACTIVITY_HEADING_RE.search(heading)
            assert match is not None, f"Unmatched heading: {heading!r}"
            assert _is_triggered_heading(match.group(0)) is True

        for heading in (
            "Départs spontanés :",
            "Déclenchements spontanés :",
            "Départ spontané :",
        ):
            match = _ACTIVITY_HEADING_RE.search(heading)
            assert match is not None, f"Unmatched heading: {heading!r}"
            assert _is_triggered_heading(match.group(0)) is False

    def test_sections_are_split_in_either_order(self) -> None:
        """The halves are assigned by heading, not by position.

        ARAVIS leads with "Départs spontanés", VANOISE with "Déclenchements
        provoqués".  Splitting sequentially let ``triggered`` swallow the
        spontaneous prose whenever the order was reversed.
        """
        from mf_bra_to_caaml import extract_avalanche_activity

        class _Page:
            def __init__(self, text: str) -> None:
                self._text = text
                self.width = 594.96

            def crop(self, _bbox: tuple[float, float, float, float]) -> _Page:
                return self

            def extract_text(self) -> str:
                return self._text

            def extract_words(self) -> list[dict[str, object]]:
                return []

        triggered_first = _Page(
            "Déclenchements provoqués :\nTRIGGERED BODY\n"
            "Départs spontanés :\nSPONTANEOUS BODY\n"
        )
        spontaneous_first = _Page(
            "Départs spontanés :\nSPONTANEOUS BODY\n"
            "Déclenchements provoqués :\nTRIGGERED BODY\n"
        )

        for page in (triggered_first, spontaneous_first):
            result = extract_avalanche_activity(page)
            assert result["triggered"] == "TRIGGERED BODY"
            assert result["spontaneous"] == "SPONTANEOUS BODY"
