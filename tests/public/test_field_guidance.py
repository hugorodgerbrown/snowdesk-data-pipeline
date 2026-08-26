"""
tests/public/test_field_guidance.py — Field guidance: the loader and the render.

SNOW-673. The guidance was authored, loaded and attached to every problem card
for the project's whole life while no template read it — the exact shape of
bug the fidelity guard exists to catch, on Snowdesk's own content rather than a
provider's. These tests cover both halves so it cannot silently stop rendering
again: `apps/public/guidance.py` (which had no test file at all), and the block
`public/_rating_block.html` renders from it.

The render assertions go through `render_to_string` on the partial, following
the pattern in test_components_partials.py — the behaviour under test is the
template's contract with the card dict, and a full bulletin fixture would test
the view instead. One test does render a real bulletin page, to prove the
guidance survives the whole path rather than only the partial in isolation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import Client

from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.public._component_fixtures import _ElevationBounds, _make_rating_card
from apps.public.guidance import load_field_guidance
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
)

# Every EAWS problem type the YAML carries a note for.
EXPECTED_TYPES = {
    "new_snow",
    "wind_slab",
    "persistent_weak_layers",
    "wet_snow",
    "gliding_snow",
    "no_distinct_avalanche_problem",
}

GUIDANCE_TESTID = 'data-testid="field-guidance"'
PANEL_TESTID = 'data-testid="field-guidance-panel"'
ATTRIBUTION = "SLF Avalanche Bulletin Interpretation Guide"
GUIDE_URL = (
    "https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/"
    "about-the-avalanche-bulletin/interpretation-guide/"
)


def _render_model_with_wind_slab() -> dict[str, Any]:
    """A minimal current-version render model with one wind-slab problem.

    Wind slab is chosen because it has an entry in field_guidance.yaml, so the
    page is guaranteed to exercise the block rather than skip it. Kept local
    and minimal rather than importing the private helpers in
    test_bulletin_page.py.
    """
    return {
        "version": RENDER_MODEL_VERSION,
        "source": "SLF",
        "danger": {
            "key": "moderate",
            "number": "2",
            "subdivision": None,
            "ratings": [],
        },
        "danger_patterns": [],
        "traits": [
            {
                "category": "dry",
                "time_period": "all_day",
                "title": "Dry avalanches",
                "geography": {"source": "problems"},
                "problems": [
                    {
                        "problem_type": "wind_slab",
                        "comment_html": "<p>Wind slabs have formed on lee aspects.</p>",
                        "aspects": ["N", "NE", "E"],
                        "elevation": {
                            "lower": 2200,
                            "upper": None,
                            "treeline": False,
                        },
                        "time_period": "all_day",
                        "core_zone_text": None,
                        "danger_rating_value": "moderate",
                    }
                ],
                "prose": None,
                "danger_level": 2,
            }
        ],
        "metadata": {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": "2026-03-15T15:00:00+00:00",
            "unscheduled": False,
            "lang": "en",
        },
        "prose": {
            "snowpack_structure": "<p>The snowpack is generally stable.</p>",
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
            "avalanche_activity": {"highlights": "", "comment": ""},
            "tendency_lead": None,
        },
    }


def _notes(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    """Build the card's field_guidance list from (label, text) pairs."""
    return [{"label": label, "text": text} for label, text in pairs]


def _render(**card_kwargs: Any) -> str:
    """Render `_rating_block.html` for one card, defaults filled in."""
    card = _make_rating_card(
        category="dry",
        danger_level=3,
        danger_level_key="considerable",
        problem_type=card_kwargs.pop("problem_type", "wind_slab"),
        time_period="all_day",
        aspects=["N", "NE"],
        elevation=_ElevationBounds(
            lower="2200", upper="", display="above 2200m", bound_type="lower"
        ),
        label=card_kwargs.pop("label", "Wind slab"),
        time_period_label="",
        core_zone_text="",
        **card_kwargs,
    )
    return render_to_string("public/_rating_block.html", {"card": card})


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------


class TestLoadFieldGuidance:
    """Tests for apps.public.guidance.load_field_guidance."""

    def test_loads_every_problem_type(self) -> None:
        """All six EAWS problem types the YAML covers are returned."""
        assert set(load_field_guidance()) == EXPECTED_TYPES

    def test_values_are_stripped_non_empty_text(self) -> None:
        """Each note is a non-empty string with no leading/trailing whitespace."""
        for problem_type, text in load_field_guidance().items():
            assert text, f"{problem_type} has empty guidance"
            assert text == text.strip(), f"{problem_type} is not stripped"

    def test_markup_stays_inside_the_sanitiser_allowlist(self) -> None:
        """Any tag used in the YAML survives `snowdesk_html` rather than escaping.

        `wet_snow` carried a `<b>` until SNOW-673. Bleach's allowlist does not
        include it, so the reader saw a literal "<b>" on the page. This asserts
        the class of bug rather than that one instance: whatever markup the
        notes use has to be markup the filter keeps.
        """
        from apps.public.templatetags.snowdesk_html import snowdesk_html

        for problem_type, text in load_field_guidance().items():
            assert "&lt;" not in snowdesk_html(text), (
                f"{problem_type} uses a tag snowdesk_html escapes — it would "
                f"reach the reader as literal characters"
            )


# ---------------------------------------------------------------------------
# The rendered block
# ---------------------------------------------------------------------------


class TestFieldGuidanceBlock:
    """Tests for the guidance block in public/_rating_block.html."""

    def test_renders_the_note_for_a_type_that_has_one(self) -> None:
        """The guidance text appears on the card."""
        guidance = load_field_guidance()["persistent_weak_layers"]
        html = _render(
            problem_type="persistent_weak_layers",
            field_guidance=_notes(("Persistent weak layers", guidance)),
        )
        assert GUIDANCE_TESTID in html
        assert "Persistent weak layers are very challenging to recognise" in html

    def test_renders_the_separating_rule(self) -> None:
        """The provenance boundary is drawn, not merely implied by position.

        The rule is the whole point of the treatment — it is what tells the
        reader that everything above it is the provider's and everything below
        is Snowdesk's. Asserting only on the text would let the block ship
        looking like a continuation of the provider's prose.
        """
        html = _render(
            field_guidance=_notes(
                ("Wind slab", "Look for fresh snow deposits on lee slopes.")
            )
        )
        block = html[html.index(GUIDANCE_TESTID) :]
        assert "border-t border-border" in block

    def test_credits_the_slf_guide(self) -> None:
        """The attribution names the source, so the note is not read as ours."""
        html = _render(field_guidance=_notes(("Wind slab", "Some guidance.")))
        assert ATTRIBUTION in html
        assert "Source:" in html

    def test_the_credit_links_the_guide(self) -> None:
        """The source is reachable, not just named.

        Links the guide's landing page rather than the PDF: SLF treats only
        the current online version as binding, so a PDF URL would pin an
        edition and go stale. ``rel="noopener"`` is asserted because the
        link opens in a new tab.
        """
        html = _render(field_guidance=_notes(("Wind slab", "Some guidance.")))
        block = html[html.index(GUIDANCE_TESTID) :]
        assert GUIDE_URL in block
        assert 'target="_blank"' in block
        assert 'rel="noopener"' in block

    def test_notes_sit_in_a_panel_closed_by_default(self) -> None:
        """The block is a <details> that ships shut.

        Closed-by-default is the whole point of the panel — every note
        expanded made a busy level-3 day ~31% longer. Asserting the absence
        of ``open`` is what distinguishes this from a panel that merely
        *can* collapse.
        """
        html = _render(field_guidance=_notes(("Wind slab", "Some guidance.")))
        block = html[html.index(GUIDANCE_TESTID) :]
        panel = block[block.index(PANEL_TESTID) :]
        assert "<details" in block
        assert "open" not in panel[: panel.index(">")]

    def test_panel_is_titled_field_notes(self) -> None:
        """The summary carries the panel's name, not the old eyebrow copy."""
        html = _render(field_guidance=_notes(("Wind slab", "Some guidance.")))
        block = html[html.index(GUIDANCE_TESTID) :]
        assert "Field notes" in block
        assert "In the field" not in block

    def test_the_rule_sits_outside_the_panel(self) -> None:
        """The provenance boundary is drawn while the panel is still shut.

        If the rule moved inside the <details>, a closed card would show
        Snowdesk's panel title with nothing separating it from the
        provider's prose above.
        """
        html = _render(field_guidance=_notes(("Wind slab", "Some guidance.")))
        block = html[html.index(GUIDANCE_TESTID) :]
        assert block.index("border-t border-border") < block.index("<details")

    def test_the_note_text_is_present_while_closed(self) -> None:
        """Collapsed is not omitted — the text ships in the HTML.

        A <details> keeps its body in the document, so the guidance stays
        available to find-in-page, to assistive tech and to crawlers. This
        is what makes collapsing acceptable for content the fidelity guard
        exists to protect.
        """
        html = _render(
            field_guidance=_notes(("Wind slab", "Look for fresh snow deposits."))
        )
        assert "Look for fresh snow deposits." in html

    def test_absent_type_renders_neither_text_nor_rule(self) -> None:
        """A problem type with no entry renders nothing — not an empty rule.

        `cornices` is a real EAWS type with no note in the YAML, so this is the
        live state rather than a hypothetical.
        """
        html = _render(problem_type="cornices", field_guidance=[])
        assert GUIDANCE_TESTID not in html
        assert ATTRIBUTION not in html

    def test_guidance_follows_the_provider_prose(self) -> None:
        """Snowdesk's note sits below the provider's text, never above it.

        Ordering is the fidelity rule made visible: the provider's words come
        first, and the addition is an addition.
        """
        html = _render(
            comment_html="<p>Fresh drifted snow lies on lee slopes.</p>",
            field_guidance=_notes(
                ("Wind slab", "Look for fresh snow deposits on lee slopes.")
            ),
        )
        assert html.index("Fresh drifted snow") < html.index(GUIDANCE_TESTID)

    def test_one_note_carries_no_label_of_its_own(self) -> None:
        """A single-problem card doesn't repeat its own heading above the note."""
        html = _render(
            label="Wind slab",
            field_guidance=_notes(("Wind slab", "Look for fresh snow deposits.")),
        )
        block = html[html.index(GUIDANCE_TESTID) :]
        assert block.count("Wind slab") == 0

    def test_a_composite_card_labels_each_note(self) -> None:
        """A trait carrying two problem types shows both notes, each named.

        The card's own label merges the two ("Wind slab + New snow"), so an
        unlabelled pair of paragraphs would leave the reader unable to tell
        which note belongs to which problem — and showing only the first
        would drop guidance the bulletin's problems earned.
        """
        html = _render(
            label="Wind slab + New snow",
            field_guidance=_notes(
                ("Wind slab", "Look for fresh snow deposits on lee slopes."),
                ("New snow", "Easy to spot - the snow is fresh."),
            ),
        )
        block = html[html.index(GUIDANCE_TESTID) :]
        assert "Look for fresh snow deposits" in block
        assert "Easy to spot" in block
        assert "Wind slab" in block
        assert "New snow" in block

    def test_strong_survives_but_script_does_not(self) -> None:
        """The note goes through the same sanitiser as the provider prose."""
        html = _render(
            field_guidance=_notes(
                ("Wet snow", "Plan <strong>early returns</strong>.<script>x()</script>")
            )
        )
        assert "<strong>early returns</strong>" in html
        assert "<script>" not in html


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
class TestFieldGuidanceOnTheBulletinPage:
    """The guidance reaches a real rendered bulletin page."""

    @pytest.fixture()
    def region(self) -> MicroRegion:
        """A micro-region. The factory auto-creates its SubRegion parent."""
        return MicroRegionFactory.create(
            region_id="CH-4115", name="Valais", slug="valais"
        )

    def test_bulletin_page_renders_guidance(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A wind-slab bulletin shows the note, the rule and the credit.

        The view has always put `field_guidance` on the card; until SNOW-673
        nothing read it. This is the assertion that would have failed for the
        whole of that time.
        """
        day = date(2026, 3, 15)
        valid_from = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
        bulletin = BulletinFactory.create(
            issued_at=valid_from - timedelta(minutes=30),
            valid_from=valid_from,
            valid_to=valid_from + timedelta(hours=9),
            render_model=_render_model_with_wind_slab(),
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time=region.name,
        )

        response = client.get(
            f"/{region.region_id.lower()}/{region.slug}/{day.isoformat()}/",
            follow=True,
        )
        assert response.status_code == 200
        content = response.content.decode()

        assert GUIDANCE_TESTID in content
        assert ATTRIBUTION in content
        assert "Look for fresh snow deposits on lee slopes" in content
        # The provider's own prose is still there, and still first — the note
        # is an addition, never a replacement.
        assert "Wind slabs have formed on lee aspects" in content
        assert content.index("Wind slabs have formed") < content.index(GUIDANCE_TESTID)
