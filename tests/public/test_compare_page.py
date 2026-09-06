"""
tests/public/test_compare_page.py — Tests for the /compare page (SNOW-836).

The page is a public comparison of the avalanche apps in this category,
derived from ``docs/competitors.md`` — an internal document written from
our own side of the table. Most of what these tests pin is therefore not
"does it render" but "did the internal framing stay out of it", because
that is the failure mode with consequences: the source doc carries a
feature backlog, a scan process, and lines about where competitors
threaten us, and any of it reaching a public URL is the defect.

Covers:

  * ``GET /compare/`` returns 200 for an anonymous user, and the page
    carries its section markers.
  * Every profiled competitor is named. A comparison page that quietly
    dropped one would still render, still look complete, and be worse
    than useless to the reader it exists for.
  * The page recommends competitors by name for the cases where they are
    the better choice. This is the honesty property the page is built on
    and the one most likely to be edited away later.
  * The publisher disclosure and the "the bulletin is the authority"
    line are both present.
  * None of the internal framing survives — the backlog vocabulary, the
    scan bookkeeping, and the from-our-side phrasing are each asserted
    absent by literal string.
  * Our own entry states its gaps, and does NOT claim the two things
    ``docs/competitors.md`` still records as missing but which shipped
    (slope angle, national topo basemaps). The doc went stale on both;
    the page is checked against the code instead, and this test is what
    stops the stale claim being reintroduced from the doc.
  * The footer links to it, and ``public:compare`` resolves.

Sharing metadata is covered by ``tests/public/test_page_meta.py``, which
carries ``compare`` in its hand-maintained ``sharing_pages`` fixture.

The page itself issues no model queries — it renders authored prose plus
the static matrix in ``apps.public.competitor_matrix``. The module still
carries ``django_db`` because the *request path* touches the database
regardless of the view: the CSP middleware and the request-log capture
both do, so a page can be entirely static and still need a database to be
fetched through a client. No factories are needed.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

# Every product the page profiles in full. Hand-maintained: a product
# added to the page without a line here is a product nothing checks.
PROFILED_PRODUCTS = (
    "WhiteRisk",
    "SnowSafe",
    "AvalancheClarity",
    "Skitourenguru",
    "Yéti",
    "Whympr",
    "OpenSnow",
    "Snowdesk",
)

# Vocabulary that belongs to docs/competitors.md and must never reach the
# public page. Each is a literal from that document or its idiom.
INTERNAL_FRAMING = (
    "Feature inspiration",
    "Not yet profiled",
    "unclaimed differentiator",
    "Our answer to",
    "escalate",
    "worth copying",
    "idle infrastructure",
    "EGRESS_BLOCKED",
    "scan update",
    "routine competitor scan",
)


# The view is static, but the request path is not — see the module
# docstring. Applied at module level so a new test class cannot forget it.
pytestmark = pytest.mark.django_db


@pytest.fixture()
def client() -> Client:
    """An anonymous Django test client."""
    return Client()


@pytest.fixture()
def page(client: Client) -> str:
    """The rendered /compare/ page as a decoded HTML string."""
    response = client.get(reverse("public:compare"))
    assert response.status_code == 200
    return response.content.decode()


class TestComparePageRenders:
    """The page is reachable and structurally complete."""

    def test_returns_200_for_anonymous_user(self, client: Client) -> None:
        """The page is public — no account, no gate."""
        assert client.get(reverse("public:compare")).status_code == 200

    def test_url_resolves(self) -> None:
        """``public:compare`` reverses to the documented path."""
        assert reverse("public:compare") == "/compare/"

    @pytest.mark.parametrize(
        "testid",
        [
            "compare-heading",
            "compare-start-here",
            "compare-table",
            "compare-matrix",
            "compare-focus",
            "compare-planning",
            "compare-profiles",
            "compare-others",
            "compare-method",
            "compare-disclosure",
        ],
    )
    def test_section_is_present(self, page: str, testid: str) -> None:
        """Each section the page is built from renders."""
        assert f'data-testid="{testid}"' in page

    @pytest.mark.parametrize("product", PROFILED_PRODUCTS)
    def test_every_profiled_product_is_named(self, page: str, product: str) -> None:
        """A dropped competitor makes the page quietly incomplete."""
        assert product in page


class TestComparePageIsHonest:
    """The properties that make the page worth publishing at all.

    Each assertion here corresponds to a promise the page makes to a
    reader who knows we publish it. They are separated from the render
    tests because an edit that breaks one of these does not break the
    page — it breaks its reason to exist.
    """

    def test_discloses_that_snowdesk_publishes_it(self, page: str) -> None:
        """The conflict of interest is stated, not implied."""
        assert "published by Snowdesk, which is one of the products" in page

    def test_recommends_whiterisk_over_us_for_switzerland(self, page: str) -> None:
        """The page concedes the case where a competitor plainly wins."""
        assert "WhiteRisk is the better product and it is not close" in page

    def test_names_competitors_in_the_start_here_guide(self, page: str) -> None:
        """The decision guide routes readers away from us where it should."""
        for expected in ("SnowSafe", "Skitourenguru", "Yéti", "OpenSnow"):
            assert expected in page

    def test_states_the_bulletin_is_the_authority(self, page: str) -> None:
        """No app framing is allowed to displace the official forecast."""
        assert "The bulletin is the authority" in page

    def test_flags_the_unverified_prices(self, page: str) -> None:
        """docs/competitors.md marks these second-hand; the page must say so.

        The doc's own instruction is that OpenSnow's pricing is "worth
        re-checking before it is quoted anywhere externally". This page is
        that external quote.
        """
        assert "unconfirmed" in page
        assert "Check both yourself before" in page

    def test_does_not_claim_a_feature_the_competitor_may_simply_not_advertise(
        self, page: str
    ) -> None:
        """Absence in our notes is reported as absence of evidence.

        The planning section is where this matters: a competitor that is
        not described as sharing a plan may never have been asked.
        """
        assert "No equivalent was found in any other product checked" in page

    @pytest.mark.parametrize("phrase", INTERNAL_FRAMING)
    def test_internal_framing_does_not_leak(self, page: str, phrase: str) -> None:
        """Nothing written from our side of the table reaches the reader."""
        assert phrase not in page


class TestSnowdeskEntryMatchesTheCode:
    """Our own row is checked against this repository, not against the doc.

    ``docs/competitors.md`` recorded slope angle and national topo
    basemaps as things we lack. Both shipped — ``settings.SLOPE_TILE_URL``
    and ``settings.BASEMAP_STYLES`` — and the doc did not catch up. These
    tests exist because the doc is the page's input, so the stale claim
    has an obvious route back in.
    """

    def test_claims_the_slope_overlay_we_actually_ship(self, page: str) -> None:
        """We have it; the page says so."""
        from django.conf import settings

        assert settings.SLOPE_TILE_URL, "slope overlay is configured"
        assert "slope-angle overlay" in page

    def test_claims_the_national_basemaps_we_actually_ship(self, page: str) -> None:
        """swisstopo, IGN and basemap.at are all in BASEMAP_STYLES."""
        from django.conf import settings

        for key in ("swisstopo_winter", "ign_plan", "basemap_at"):
            assert key in settings.BASEMAP_STYLES
        assert "national topographic basemaps" in page

    def test_states_the_gaps_that_are_still_real(self, page: str) -> None:
        """Alerting, station data and per-tour scoring are genuinely absent.

        Each was re-verified when the page was written: the push endpoints
        in ``apps/accounts/push_views.py`` are all staff-only, ``apps/weather/``
        is Open-Meteo forecast only, and ``apps/routes/`` holds geometry with
        no bulletin coupling.
        """
        assert "alerting, which it does not do at all" in page
        assert "no live station network" in page
        assert "per-tour risk scoring" in page

    def test_describes_weather_as_forecasts_not_observations(self, page: str) -> None:
        """The distinction is the whole of the claim.

        "No weather" would be wrong — forecasts refresh four times a day.
        "Weather stations" would also be wrong. Only the pair is true.
        """
        assert "forecasts refreshed four times a day" in page

    def test_describes_the_shared_trip(self, page: str) -> None:
        """The companion half of the product is on the page, not just the bulletin."""
        assert "a trip is a shared object" in page


class TestComparePageIsLinked:
    """A page nothing links to is a page nobody reads."""

    def test_footer_links_to_it(self, page: str) -> None:
        """The global footer carries the link, so every page reaches it.

        Asserted on /compare/ rather than on the homepage deliberately.
        ``_site_footer.html`` is included by ``public/base.html``, so any
        page extending it proves the link the same way — and the homepage
        needs a database this module otherwise does not touch, which would
        make a footer assertion depend on bulletin fixtures.
        """
        assert reverse("public:compare") in page
        assert "Compare apps" in page


class TestFeatureMatrix:
    """The matrix is where a dishonest page would be easiest to build.

    Ticks are cheap and a grid reads as objective, so these tests pin the
    two properties that keep it honest: that we do not mark a competitor
    absent on evidence we never gathered, and that our own column is not a
    clean sweep. Both would still render perfectly if broken.
    """

    def test_every_feature_answers_every_product(self) -> None:
        """A blank cell would render as a dash and read as "no".

        ``_cells`` raises at import time on a missing answer, so this is
        really asserting that the guard is still wired to the real product
        list rather than to a stale copy of it.
        """
        from apps.public.competitor_matrix import FEATURES, PRODUCTS

        keys = {product.key for product in PRODUCTS}
        for feature in FEATURES:
            assert set(feature.cells) == keys, f"{feature.key} is incomplete"

    def test_unknown_is_used_rather_than_no(self) -> None:
        """Absence of evidence must be represented, not rounded down.

        If this ever reaches zero, someone has converted "our notes do not
        say" into "the product does not do it" — which is the one way this
        page could actively mislead a reader in our own favour.
        """
        from apps.public.competitor_matrix import FEATURES, Support

        unknowns = [
            cell
            for feature in FEATURES
            for cell in feature.cells.values()
            if cell.support is Support.UNKNOWN
        ]
        assert unknowns, "no UNKNOWN cells — absence of evidence has been rounded down"

    def test_snowdesk_does_not_sweep_the_matrix(self) -> None:
        """Our own column has real gaps, and they are marked as gaps."""
        from apps.public.competitor_matrix import FEATURES, Support

        ours = [feature.cells["snowdesk"].support for feature in FEATURES]
        assert Support.NO in ours, "our column claims everything"
        # And specifically the three verified against the code when the
        # page was written: alerts, station data, per-tour scoring.
        by_key = {feature.key: feature.cells["snowdesk"] for feature in FEATURES}
        for key in ("alerts", "stations", "tour_score"):
            assert by_key[key].support is Support.NO, f"{key} is not a gap any more"

    def test_no_competitor_column_is_all_unknown(self) -> None:
        """A column of nothing but dots is a product we should not have listed."""
        from apps.public.competitor_matrix import FEATURES, PRODUCTS, Support

        for product in PRODUCTS:
            states = {feature.cells[product.key].support for feature in FEATURES}
            assert states != {Support.UNKNOWN}, f"{product.key} is entirely unchecked"

    def test_every_state_is_explained_in_the_legend(self, page: str) -> None:
        """A reader meeting a glyph can find out what it means."""
        from apps.public.competitor_matrix import SUPPORT_LABELS

        for label in SUPPORT_LABELS.values():
            assert str(label) in page

    def test_matrix_renders_a_cell_per_product_per_feature(self, page: str) -> None:
        """The grid is complete on the page, not just in the data."""
        from apps.public.competitor_matrix import FEATURES, PRODUCTS

        assert page.count('data-support="') == len(FEATURES) * len(PRODUCTS)

    def test_glyphs_are_hidden_from_assistive_technology(self, page: str) -> None:
        """A screen reader gets the label, never the decorative mark."""
        assert 'aria-hidden="true"' in page
        assert "Not established" in page


class TestFocusSectionStaysHonest:
    """The restraint argument is the easiest section to let drift into a boast.

    It claims an advantage from ABSENCE, which no feature table can check,
    so the guard has to be that the same paragraph keeps naming what the
    absence costs. Each test here pins one half of that trade.
    """

    def test_concedes_the_matrix_favours_bigger_products(self, page: str) -> None:
        """The page admits its own table is biased towards accumulation."""
        assert "counts what a product has" in page

    def test_names_what_our_restraint_costs_the_reader(self, page: str) -> None:
        """Three concessions, each naming the competitor who does it better."""
        assert "Restraint is a trade, not a free win" in page
        assert "WhiteRisk will teach you properly and we will not" in page
        assert "a hundred thousand and we have none" in page

    def test_applies_the_no_business_model_risk_to_us_too(self, page: str) -> None:
        """We flag this about AvalancheClarity; it has to cut both ways.

        Without this the page criticises a competitor for being free with
        no visible income while presenting the same fact about ourselves
        as a virtue two sections later.
        """
        assert "That is exactly as true of us." in page

    def test_absence_rows_keep_the_grid_polarity(self) -> None:
        """A filled dot means "what the reader wants" on every row.

        The two absence-shaped rows are phrased positively for this
        reason. If either is ever relabelled to "Has ads" the dots invert
        meaning mid-table and every skim-reader misreads them.
        """
        from apps.public.competitor_matrix import FEATURES, Support

        by_key = {f.key: f for f in FEATURES}
        assert by_key["no_ads"].cells["snowsafe"].support is Support.NO
        assert by_key["no_ads"].cells["snowdesk"].support is Support.YES
        assert by_key["no_iap"].cells["whympr"].support is Support.NO
        assert by_key["no_iap"].cells["snowdesk"].support is Support.YES
