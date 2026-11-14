"""
tests/bulletins/services/test_alert_wording.py — The words of an alert.

Each test builds A1 states directly and checks the title, body and link a
reader would see.
"""

from __future__ import annotations

import datetime

import pytest

from apps.bulletins.services.alert_wording import (
    PlaceAlert,
    Release,
    alert_message,
    aspect_ranges,
    cold_copy,
    elevation_phrase,
    headline,
    new_snow,
    provider_name,
    release_line,
    trend,
)
from apps.bulletins.services.bulletin_change import (
    BulletinState,
    ProblemState,
    summarise_change,
)

TODAY = datetime.date(2026, 1, 16)  # a Friday
TOMORROW = TODAY + datetime.timedelta(days=1)

WIND = ProblemState("wind_slab", ("N", "NE", "E"), lower=2400, rating="moderate")
WET = ProblemState("wet_snow", ("S",))


def state(
    rating: str = "considerable",
    subdivision: str | None = None,
    problems: tuple[ProblemState, ...] = (),
) -> BulletinState:
    """Return a bulletin state."""
    return BulletinState(rating=rating, subdivision=subdivision, problems=problems)


def place(
    previous: BulletinState | None,
    current: BulletinState,
    label: str = "Verbier",
    kind: str = "new",
    day: datetime.date = TOMORROW,
    region_id: str = "CH-4115",
    releases: tuple[Release, ...] = (),
) -> PlaceAlert:
    """Return one place that fired."""
    return PlaceAlert(
        region_id=region_id,
        label=label,
        target_date=day,
        kind=kind,  # type: ignore[arg-type]
        change=summarise_change(previous, current),
        releases=releases,
    )


class TestPhrases:
    """The small phrases."""

    @pytest.mark.parametrize(
        ("aspects", "expected"),
        [
            (("N", "NE", "E"), "N–E"),
            (("NW", "N", "NE"), "NW–NE"),
            (("N", "S"), "N, S"),
            (("N", "NE", "E", "SE", "S", "SW", "W", "NW"), "all aspects"),
            ((), ""),
            (("S",), "S"),
        ],
    )
    def test_aspect_ranges(self, aspects: tuple[str, ...], expected: str) -> None:
        """Aspects read as clockwise ranges."""
        assert aspect_ranges(aspects) == expected

    def test_elevation_phrases(self) -> None:
        """Each band shape has its words."""
        assert elevation_phrase(WIND) == "above 2,400 m"
        assert elevation_phrase(ProblemState("x", upper=1800)) == "below 1,800 m"
        assert (
            elevation_phrase(ProblemState("x", lower=1800, upper=2400))
            == "between 1,800 and 2,400 m"
        )
        assert elevation_phrase(ProblemState("x", treeline="upper")) == (
            "below the treeline"
        )
        assert elevation_phrase(ProblemState("x", treeline="lower")) == (
            "above the treeline"
        )
        assert elevation_phrase(ProblemState("x")) == ""

    def test_headline(self) -> None:
        """The sub-rating shows as a figure, neutral as the bare digit."""
        assert headline(state("considerable", "+")) == "Considerable (3+)"
        assert headline(state("considerable", "-")) == "Considerable (3−)"
        assert headline(state("considerable", "=")) == "Considerable (3)"
        assert headline(state("")) == "No rating"


def at(hour: int, minute: int = 0, day: datetime.date = TODAY) -> datetime.datetime:
    """Return a UTC moment that is ``hour:minute`` in Zurich in January."""
    return datetime.datetime(
        day.year, day.month, day.day, hour - 1, minute, tzinfo=datetime.UTC
    )


SLF_17 = Release("SLF", at(17))


class TestColdMessage:
    """Every alert reads the same: a bulletin is out (Hugo, 2026-11-12)."""

    def test_one_place(self) -> None:
        """The title is fixed; the body names the provider and the time."""
        msg = alert_message(
            [place(state("moderate"), state("considerable"), releases=(SLF_17,))],
            TODAY,
        )
        assert msg.title == "New avalanche bulletin"
        assert msg.body == "Bulletin released at 17:00 by SLF."

    def test_says_nothing_about_the_level(self) -> None:
        """No level, place or problem reaches the message."""
        msg = alert_message(
            [place(state("low"), state("high", problems=(WIND,)), releases=(SLF_17,))],
            TODAY,
        )
        assert "High" not in msg.body and "Verbier" not in msg.body
        assert "Verbier" not in msg.title

    def test_reissue_reads_the_same_with_its_own_time(self) -> None:
        """An update is told apart only by its release time."""
        msg = alert_message(
            [
                place(
                    state("moderate"),
                    state("considerable"),
                    kind="update",
                    day=TODAY,
                    releases=(Release("SLF", at(8, 12)),),
                )
            ],
            TODAY,
        )
        assert msg.title == "New avalanche bulletin"
        assert msg.body == "Bulletin released at 08:12 by SLF."

    def test_many_places_one_issue(self) -> None:
        """Places sharing one issue name it once; the count is never said."""
        places = [
            place(state("moderate"), state("moderate"), label=name, releases=(SLF_17,))
            for name in ("Andermatt", "Montana, Anzère", "Verbier")
        ]
        msg = alert_message(places, TODAY)
        assert msg.body == "Bulletin released at 17:00 by SLF."
        assert "places" not in msg.title

    def test_one_provider_several_times_drops_the_time(self) -> None:
        """Different release times are not named."""
        msg = alert_message(
            [
                place(None, state(), label="A", releases=(SLF_17,)),
                place(None, state(), label="B", releases=(Release("SLF", at(8)),)),
            ],
            TODAY,
        )
        assert msg.body == "Bulletins released by SLF."

    def test_several_providers_at_several_times(self) -> None:
        """Several providers are named in a fixed order, with no time."""
        msg = alert_message(
            [
                place(
                    None, state(), label="A", releases=(Release("METEOFRANCE", at(16)),)
                ),
                place(None, state(), label="B", releases=(SLF_17,)),
                place(
                    None, state(), label="C", releases=(Release("ALBINA", at(17, 5)),)
                ),
            ],
            TODAY,
        )
        assert msg.body == "Bulletins released by SLF, ALBINA and Météo-France."

    def test_several_providers_at_one_time(self) -> None:
        """A shared time is still named."""
        assert (
            release_line([SLF_17, Release("ALBINA", at(17))])
            == "Bulletins released at 17:00 by SLF and ALBINA."
        )

    def test_unknown_provider(self) -> None:
        """No provider known: the body still says a bulletin is out."""
        assert release_line([Release("", at(17))]) == "New bulletin."
        assert release_line([]) == "New bulletin."

    def test_cold_copy_is_the_title_and_release_line(self) -> None:
        """cold_copy gives the fixed title and who released it (SNOW-1496)."""
        assert cold_copy([SLF_17]) == (
            "New avalanche bulletin",
            "Bulletin released at 17:00 by SLF.",
        )
        assert cold_copy([SLF_17, Release("ALBINA", at(17, 5))]) == (
            "New avalanche bulletin",
            "Bulletins released by SLF and ALBINA.",
        )

    def test_every_alert_opens_the_landing_page(self) -> None:
        """Every alert, one place or several days, opens /today/ (SNOW-1497)."""
        one = alert_message([place(None, state(), releases=(SLF_17,))], TODAY)
        assert one.url == "/today/"
        mixed = alert_message(
            [place(None, state(), day=TODAY), place(None, state(), day=TOMORROW)],
            TODAY,
        )
        assert mixed.url == "/today/"

    def test_as_push(self) -> None:
        """The push payload is the title, body and link."""
        msg = alert_message([place(None, state(), releases=(SLF_17,))], TODAY)
        assert msg.as_push() == {
            "title": "New avalanche bulletin",
            "body": "Bulletin released at 17:00 by SLF.",
            "url": "/today/",
        }

    def test_empty_raises(self) -> None:
        """No places is a programming error."""
        with pytest.raises(ValueError, match="at least one place"):
            alert_message([], TODAY)


class TestPagePhrases:
    """The phrases the landing page uses beside each place."""

    @pytest.mark.parametrize(
        ("before", "after", "expected"),
        [
            (state("moderate"), state("considerable"), "up from Moderate"),
            (state("considerable"), state("moderate"), "down from Considerable"),
            (state("moderate", "-"), state("moderate"), "up from 2−"),
            (state("moderate", "+"), state("moderate", "-"), "down from 2+"),
            (state("moderate"), state("moderate"), "no change"),
        ],
    )
    def test_trend(
        self, before: BulletinState, after: BulletinState, expected: str
    ) -> None:
        """The level's move reads in a few words."""
        assert trend(summarise_change(before, after)) == expected

    def test_trend_with_nothing_to_compare(self) -> None:
        """No day before: nothing is said."""
        assert trend(summarise_change(None, state())) == ""

    @pytest.mark.parametrize("cm", [None, 0.0, 4.9])
    def test_dusting_is_not_news(self, cm: float | None) -> None:
        """Below five centimetres, no new snow line."""
        assert new_snow(cm) == ""

    def test_new_snow(self) -> None:
        """Five centimetres and more are named, rounded."""
        assert new_snow(19.6) == "20 cm new snow forecast."

    def test_provider_name(self) -> None:
        """Météo-France is written as it writes itself; unknowns pass through."""
        assert provider_name("METEOFRANCE") == "Météo-France"
        assert provider_name("XYZ") == "XYZ"
