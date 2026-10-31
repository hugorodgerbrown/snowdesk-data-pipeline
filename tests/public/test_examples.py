"""
tests/public/test_examples.py — Tests for the example URL views.

Covers ``/examples/random/``, ``/examples/category/<danger_level>/``,
and the deprecated ``/random/`` redirect. Both example views render
the bulletin page inline using the canonical view's core (SNOW-99) so
the rendered output is byte-for-byte identical to a real bulletin.
The ``<link rel="canonical">`` resolves to either the live no-date
form-2 URL (``examples_random``, evergreen) or the historical dated
form-3 URL (``examples_category``, pinned to a specific bulletin) —
never the ``/examples/...`` URL itself.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.bulletins.models import Bulletin
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
)


def _wrap(properties: dict) -> dict:
    """Wrap a CAAML properties dict in a GeoJSON Feature envelope."""
    return {"type": "Feature", "geometry": None, "properties": properties}


@pytest.fixture(autouse=True)
def _clear_cache() -> Generator[None, None, None]:
    """Clear the cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def region() -> MicroRegion:
    """Return a Region with a human-readable name."""
    return MicroRegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


def _make_bulletin_with_region(
    region: MicroRegion,
    danger_level: str,
    issued_at: datetime,
    *,
    rated: bool = True,
) -> Bulletin:
    """Create a bulletin with a specific danger level linked to a region.

    Args:
        region: The region the bulletin covers.
        danger_level: The CAAML ``mainValue`` of its one rating.
        issued_at: Its issue, validity start and validity end.
        rated: Also create the region-day's RegionDayRating with this
            bulletin as its source, which is how ``examples_category``
            finds candidates (SNOW-998). Off for a second bulletin on the
            same region-day, which the unique (region, date) leaves unrated.

    Returns:
        The bulletin.

    """
    bulletin = BulletinFactory.create(
        raw_data=_wrap(
            {
                "dangerRatings": [{"mainValue": danger_level}],
                "avalancheProblems": [],
            }
        ),
        issued_at=issued_at,
        valid_from=issued_at,
        valid_to=issued_at,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    if rated:
        RegionDayRatingFactory.create(
            region=region,
            date=issued_at.date(),
            min_rating=danger_level,
            max_rating=danger_level,
            source_bulletin=bulletin,
        )
    return bulletin


@pytest.mark.django_db
class TestDeprecatedRandomRedirect:
    """Tests for the deprecated ``/random/`` URL."""

    def test_redirects_permanently_to_examples_random(self, client: Client) -> None:
        """``/random/`` should 301 to ``/examples/random/``."""
        url = reverse("public:random")
        response = client.get(url)
        assert response.status_code == 301
        assert response["Location"] == "/examples/random/"


@pytest.mark.django_db
class TestExamplesRandom:
    """Tests for the ``/examples/random/`` view."""

    def test_renders_bulletin_inline(self, client: Client, region: MicroRegion) -> None:
        """With bulletins available, renders the bulletin page at the same URL."""
        _make_bulletin_with_region(
            region, "moderate", datetime(2025, 3, 15, 8, 0, tzinfo=UTC)
        )
        url = reverse("public:examples_random")
        response = client.get(url)
        assert response.status_code == 200
        assert b"Valais" in response.content

    def test_redirects_to_home_when_no_bulletins(self, client: Client) -> None:
        """When no bulletins exist, redirects to the homepage."""
        url = reverse("public:examples_random")
        response = client.get(url)
        assert response.status_code == 302
        assert response["Location"] == "/"

    def test_canonical_url_points_at_today_form2(
        self, client: Client, region: MicroRegion
    ) -> None:
        """``/examples/random/`` is evergreen ⇒ canonical = no-date form 2."""
        _make_bulletin_with_region(
            region, "moderate", datetime(2025, 3, 15, 8, 0, tzinfo=UTC)
        )
        url = reverse("public:examples_random")
        response = client.get(url)

        assert response.status_code == 200
        canonical = response.context["canonical_url"]
        # Canonical points at the live no-date URL of the picked region —
        # not the dated form-3 URL, and not at /examples/random/.
        assert canonical.endswith("/ch-4115/valais/")
        assert "/examples/" not in canonical
        assert b'<link rel="canonical"' in response.content


@pytest.mark.django_db
class TestExamplesCategory:
    """Tests for the ``/examples/category/<danger_level>/`` view."""

    @pytest.mark.parametrize(
        "slug,caaml_key",
        [
            ("low", "low"),
            ("moderate", "moderate"),
            ("considerable", "considerable"),
            ("high", "high"),
            ("very-high", "very_high"),
        ],
    )
    def test_renders_inline_for_each_danger_level(
        self, client: Client, region: MicroRegion, slug: str, caaml_key: str
    ) -> None:
        """Each valid danger level slug renders the matching bulletin inline."""
        _make_bulletin_with_region(
            region, caaml_key, datetime(2025, 3, 15, 8, 0, tzinfo=UTC)
        )
        url = reverse("public:examples_category", kwargs={"danger_level": slug})
        response = client.get(url)
        assert response.status_code == 200
        # The rendered page is the real bulletin template, with the
        # picked region's name visible.
        assert b"Valais" in response.content

    def test_unknown_danger_level_returns_404(self, client: Client) -> None:
        """An unrecognised danger level slug returns 404."""
        url = reverse(
            "public:examples_category",
            kwargs={"danger_level": "extreme"},
        )
        response = client.get(url)
        assert response.status_code == 404

    def test_no_matching_bulletins_returns_404(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Returns 404 when no bulletins match the requested danger level."""
        _make_bulletin_with_region(
            region, "low", datetime(2025, 3, 15, 8, 0, tzinfo=UTC)
        )
        url = reverse("public:examples_category", kwargs={"danger_level": "high"})
        response = client.get(url)
        assert response.status_code == 404

    def test_canonical_url_points_at_real_bulletin(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The canonical URL points at the form-3 URL of the matched bulletin."""
        _make_bulletin_with_region(
            region, "considerable", datetime(2025, 3, 15, 8, 0, tzinfo=UTC)
        )
        url = reverse(
            "public:examples_category",
            kwargs={"danger_level": "considerable"},
        )
        response = client.get(url)

        assert response.status_code == 200
        canonical = response.context["canonical_url"]
        # Canonical points at /<region_id>/<slug>/<bulletin-date>/, never
        # at /examples/category/...
        assert "/ch-4115/valais/2025-03-15/" in canonical
        assert "/examples/" not in canonical

    def test_pinned_bulletin_actually_renders(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The matched bulletin is pinned via ``requested_issue_id``."""
        # Two bulletins on the same date with different danger levels.
        # ``examples_category`` should pick the one matching the URL slug
        # and pass its bulletin_id as requested_issue_id so the rendered
        # body actually shows the requested danger level.
        target = _make_bulletin_with_region(
            region, "high", datetime(2025, 3, 15, 8, 0, tzinfo=UTC)
        )
        # Decoy: a different danger level on the same day.
        _make_bulletin_with_region(
            region, "low", datetime(2025, 3, 15, 9, 0, tzinfo=UTC), rated=False
        )
        url = reverse(
            "public:examples_category",
            kwargs={"danger_level": "high"},
        )
        response = client.get(url)

        assert response.status_code == 200
        # The view's rendered bulletin context object is the matched one.
        assert response.context["bulletin"].pk == target.pk


@pytest.mark.django_db
class TestExamplesCategoryPerRegionVolume:
    """SNOW-998: the pool survives SLF publishing ~149 bulletins per issue."""

    def test_an_older_level_is_found_behind_a_full_per_region_issue(
        self, client: Client, region: MicroRegion
    ) -> None:
        """149 newer considerable bulletins do not crowd out a moderate one.

        "The newest 200 bulletins" held a moderate bulletin only while it
        was among them; one de-aggregated issue is most of that pool. The
        pool now starts from the level, so the moderate one is still found.
        """
        older = _make_bulletin_with_region(
            region, "moderate", datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
        )
        newer = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
        for _ in range(149):
            _make_bulletin_with_region(
                MicroRegionFactory.create(), "considerable", newer
            )
        # Unrated newer rows bring the newer total to 200, which is the
        # whole of the old pool: the old query would never reach `older`.
        for _ in range(51):
            _make_bulletin_with_region(
                MicroRegionFactory.create(), "considerable", newer, rated=False
            )

        url = reverse("public:examples_category", kwargs={"danger_level": "moderate"})
        response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"].pk == older.pk

    def test_a_level_only_in_the_split_counts(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A region-day whose lower rating is the level is a candidate too."""
        bulletin = BulletinFactory.create(
            raw_data=_wrap(
                {
                    "dangerRatings": [
                        {"mainValue": "considerable"},
                        {"mainValue": "moderate"},
                    ],
                    "avalancheProblems": [],
                }
            ),
            issued_at=datetime(2026, 9, 10, 9, 0, tzinfo=UTC),
            valid_from=datetime(2026, 9, 10, 9, 0, tzinfo=UTC),
            valid_to=datetime(2026, 9, 10, 15, 0, tzinfo=UTC),
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        RegionDayRatingFactory.create(
            region=region,
            date=bulletin.issued_at.date(),
            min_rating="moderate",
            max_rating="considerable",
            source_bulletin=bulletin,
        )

        url = reverse("public:examples_category", kwargs={"danger_level": "moderate"})
        response = client.get(url)

        assert response.status_code == 200
        assert response.context["bulletin"].pk == bulletin.pk
