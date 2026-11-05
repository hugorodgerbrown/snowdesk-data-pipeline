"""
apps/map/views.py — The map page (``/map/``) and its context builders.

``map_page`` renders the interactive map: the season ribbon, the basemap
picker, the panels (favourites, routes, downloads, community reports,
weather, the terrain filter) and the ``?edit=`` editor modes. Each
``_*_context`` builder hands one affordance its URLs and eligibility; only
``map_page`` calls them.

Moved out of ``apps/public/views.py`` by SNOW-1428
(docs/decisions/public-splits-by-surface.md). The URL name stays
``public:map``: ``apps/public/urls.py`` routes this view.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any
from urllib.parse import urlencode

import waffle
from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils.dates import MONTHS, WEEKDAYS_ABBR
from django.utils.functional import Promise
from django.utils.translation import (
    gettext_lazy as _,
)

from apps.accounts.alert_views import alert_list_url
from apps.accounts.models import user_is_verified
from apps.bulletins.models import (
    RegionDayRating,
)
from apps.map import resort_guide
from apps.map.problem_terrain import problem_terrain_key
from apps.observations.models import FieldObservation
from apps.public.map_shared import map_modes_enabled, trip_url_templates
from apps.public.season_calendar import (
    SeasonRibbon,
    build_season_ribbon,
)
from apps.regions.models import MicroRegion
from apps.routes.models import Route, RouteShare
from apps.routes.services.shares import pending_tokens

logger = logging.getLogger(__name__)


def _edit_locations_context() -> dict[str, str]:
    """Return the URL context the location editor's panel is wired with.

    Three of the five endpoints take an identifier, so they are reversed
    with a placeholder the JS swaps at runtime — the same trick
    ``edit_save_url_template`` above and ``static/js/map.js``'s
    region-summary URL use. SNOW-798: save and link take the location's
    short id (``__SHORTID__`` is eleven characters with a non-digit, so
    the ``short_id`` converter accepts it verbatim); unlink takes the
    link's uuid, reversed with the nil uuid and swapped for ``__UUID__``,
    the pattern ``_favourites_context`` established.

    Returns:
        The five URLs the panel's data attributes carry.

    """
    nil_uuid = uuid.UUID(int=0)
    unlink_template = reverse("api:edit_location_unlink", args=[nil_uuid]).replace(
        str(nil_uuid), "__UUID__"
    )
    return {
        "edit_locations_queue_url": reverse("api:edit_locations_queue"),
        "edit_location_create_url": reverse("api:edit_location_create"),
        "edit_location_save_url_template": reverse(
            "api:edit_location_save", args=["__SHORTID__"]
        ),
        "edit_location_link_url_template": reverse(
            "api:edit_location_link", args=["__SHORTID__"]
        ),
        "edit_location_unlink_url_template": unlink_template,
    }


# The estates the in-map editors can curate. ``?edit=`` names one of these
# or the page is the ordinary map — an unrecognised value is not an error,
# it is simply not an editor, so the URL stays safe to bookmark and safe to
# share (SNOW-755).
_EDIT_TARGETS: frozenset[str] = frozenset({"resorts", "locations"})


def _fake_location_allowed(request: HttpRequest) -> bool:
    """Whether this request may spoof its own position with ``?loc=``.

    A testing aid, gated the same way ``?edit=`` is (SNOW-724): superusers,
    plus local development. The drop zone and the field-report flow both
    ask the device where it is, and neither can be exercised from a desk in
    London against a map of the Alps — so ``?loc=46.0961,7.2286`` (or a
    named preset) stands in for a real fix. See
    ``static/js/map_geolocate.js``.

    Gated rather than shipped open because the same fix reaches the field
    report: a spoofed position on a community observation is bad data other
    people act on, which is a different thing from a spoofed position on
    your own map.

    Costs no query: ``request.user`` is already resolved for the several
    other eligibility flags on this page, and ``is_superuser`` is a column
    on the row that resolved it.

    Args:
        request: The incoming HTTP request.

    Returns:
        True when the client may honour ``?loc=``.

    """
    return bool(settings.DEBUG or request.user.is_superuser)


def _edit_target(request: HttpRequest) -> str:
    """Return the estate ``?edit=`` selects for this request, or "".

    Both halves have to agree: the querystring names a known estate **and**
    the request user is a superuser (SNOW-724). For everyone else the
    parameter is silently ignored rather than refused, because the whole
    point is that an editor URL pasted into a chat renders the normal map.

    Args:
        request: The incoming HTTP request.

    Returns:
        ``"resorts"``, ``"locations"``, or ``""`` for the normal map.

    """
    target = request.GET.get("edit", "")
    if target in _EDIT_TARGETS and request.user.is_superuser:
        return target
    return ""


def map_page(request: HttpRequest) -> HttpResponse:
    """
    Render the interactive map at ``/map/``.

    SNOW-314: the full-frame map. The map lived at ``/`` from SNOW-344 until the
    marketing homepage took the root; ``/`` now 301s here when it carries
    map query parameters.

    Edit mode: when ``?edit=`` names an editable estate **and** the request
    user is a superuser, the page renders that estate's edit panel —
    ``resorts`` for the resort-coordinate editor (SNOW-74 / SNOW-86;
    superuser check since SNOW-724), ``locations`` for the curated
    location estate (SNOW-755). For everyone else the query string is
    silently ignored.

    CH-4115 (Martigny / Verbier) is pre-selected so the readout chip and
    breadcrumb are correct on first paint (SNOW-342); the scrubber paints that
    region's ratings into the track. The template carries a hidden one-time
    offer of the map tour (``#map-tour-offer``, SNOW-1117); it replaced the
    ``#home-intro`` welcome panel now that the homepage says what Snowdesk
    is.

    Context:
      ``ribbon``              — default-region (CH-4115) SeasonRibbon or None.
      ``default_region_id``   — str: "CH-4115".
      ``default_region_name`` — str: the pre-selected region's display name.
      ``default_region_slug`` — str: the pre-selected region's bulletin slug.
      ``default_subregion_name`` — str: L2 sub-region name for the breadcrumb.
      ``default_major_name``  — str: L1 major-region name for the breadcrumb.
      ``is_offseason``        — True when no region has a rated row for
                                today (SNOW-1431): data, not a date.
      ``edit_target``         — "resorts" when resort-edit mode is active,
                                else "". The empty string is the normal map.
      ``edit_queue_url``      — URL for the edit queue API (resorts only).
      ``edit_save_url_template`` — Save URL with ``__SLUG__`` placeholder (resorts).
      ``edit_create_url``     — URL for the resort-create API (resorts only).
      ``edit_resorts_geojson_url`` — URL for the resorts GeoJSON endpoint (resorts).
      ``edit_locations_*``     — the five location-editor URLs, present only
                                when ``edit_target == "locations"``
                                (see ``_edit_locations_context``).
      ``weather_geojson_url`` — URL for the map's Weather overlay feed
        (SNOW-761). Ungated: the feed is public.
      ``community_reports_geojson_url`` — URL for the community-reports
                                GeoJSON endpoint (SNOW-419).
      ``terrain_filter_eligible`` — True when
                                ``settings.TERRAIN_CLASS_TILE_URL`` is
                                configured (SNOW-978). Also gates the slope
                                layer, the filter's slope mode (SNOW-1073).
      ``terrain_class_tile_url`` — XYZ template for the terrain filter's
                                class tiles, or "" while ineligible.
      ``example_route_url``   — ``routes:example`` while an example route is
                                set, else "" (SNOW-1230): the welcome card
                                offers it only when there is one.
      ``resort_guide_popular`` — the "Find a resort" guide's popular
                                resorts (SNOW-1231, ``resort_guide``).
      ``resort_guide_date``   — the day that guide moves the map to on a
                                day with no bulletin, or None otherwise.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered map page.

    """
    today = datetime.date.today()
    base_ctx = _base_map_context(today, _shown_date(request))
    ribbon = _build_default_ribbon(today)
    # Name + slug + L2/L1 parents of the pre-selected default region (CH-4115)
    # for the readout chip, its "view bulletin" link, and its breadcrumb.
    (
        default_region_name,
        default_region_slug,
        default_subregion_name,
        default_major_name,
    ) = _default_region_label()

    # SNOW-1431: "off season" is data, not a date — no region has a rated
    # row for today. It moves the resort walkthrough to a day that has one.
    is_offseason = not RegionDayRating.objects.rated().filter(date=today).exists()

    # SNOW-344 (merged from map_view): edit mode when the querystring and
    # the request user's superuser bit both agree. Silently ignored for
    # everyone else so the URL is safe to bookmark.
    #
    # SNOW-755: ``edit_target`` names WHICH estate is being edited rather
    # than carrying a boolean that only ever meant "resorts" — there is now
    # a second editor (``?edit=locations``) alongside it, and a flag whose
    # name no longer says what it gates is how the two get confused.
    edit_target = _edit_target(request)
    edit_context: dict[str, Any] = {"edit_target": edit_target}
    if edit_target == "resorts":
        # The save URL carries a ``__SLUG__`` placeholder the JS swaps for
        # the selected resort's slug (SNOW-798) — same trick as the
        # region_summary URL in static/js/map.js. The slug converter
        # accepts the placeholder as-is, so no string-replace is needed.
        save_url_template = reverse("api:edit_resort_save", args=["__SLUG__"])
        edit_context.update(
            {
                "edit_queue_url": reverse("api:edit_resorts_queue"),
                "edit_save_url_template": save_url_template,
                "edit_create_url": reverse("api:edit_resort_create"),
                "edit_resorts_geojson_url": reverse("api:resorts_geojson"),
            }
        )
    elif edit_target == "locations":
        edit_context.update(_edit_locations_context())

    report_ctx = _report_context(request)
    fake_location_ctx = {
        "fake_location_allowed": _fake_location_allowed(request),
        # SNOW-1077: the zoom control is public; only its camera-zoom line
        # is DEBUG. The continuous camera zoom to two decimals is a
        # developer's instrument for work on the tile bands (how close the
        # camera is to the next whole tile zoom), and says nothing a reader
        # of the map wants.
        "zoom_camera_visible": bool(settings.DEBUG),
    }
    favourites_ctx = _favourites_context(request)
    routes_ctx = _routes_context(request)
    downloads_ctx = _downloads_context(request)
    community_reports_ctx = _community_reports_context(request)
    weather_ctx = _weather_overlay_context()
    terrain_filter_ctx = _terrain_filter_context()
    trips_ctx = trip_url_templates()
    # SNOW-1230/1231: one query answers both the welcome card's example
    # link (is one set?) and the resort guide's no-bulletin day (its day).
    example = RouteShare.objects.example().only("example_date").first()

    return render(
        request,
        "public/map.html",
        {
            **base_ctx,
            **edit_context,
            **fake_location_ctx,
            **report_ctx,
            **favourites_ctx,
            **routes_ctx,
            **downloads_ctx,
            **community_reports_ctx,
            **weather_ctx,
            **terrain_filter_ctx,
            **trips_ctx,
            "ribbon": ribbon,
            "default_region_id": _DEFAULT_RIBBON_REGION_ID,
            "default_region_name": default_region_name,
            "default_region_slug": default_region_slug,
            "default_subregion_name": default_subregion_name,
            "default_major_name": default_major_name,
            "is_offseason": is_offseason,
            # SNOW-1230: the welcome card's "Explore an example route", and
            # the "?" menu's way back to it, render only while an example
            # is set (set_example_route). One indexed EXISTS, on this page
            # alone — not in _routes_context, which the homepage runs too.
            "example_route_url": reverse("routes:example") if example else "",
            # SNOW-1231: the "Find a resort" walkthrough's popular resorts
            # and, on a day with no bulletin, the day it moves the map to.
            "resort_guide_popular": resort_guide.popular_resorts(),
            "resort_guide_date": resort_guide.guide_date(
                is_offseason, example.example_date if example else None
            ),
            # SNOW-1333: the date row's bell lists the account's alerts, and
            # is not drawn at all without them to list.
            "alert_list_url": alert_list_url(request),
            # SNOW-1394: the Dated / Terrain toggle, behind its flag.
            "map_modes_enabled": map_modes_enabled(request),
        },
    )


# User-facing labels for the basemap layer picker (SNOW-58). Keyed by the
# same key as ``settings.BASEMAP_STYLES``; ``gettext_lazy`` so a future
# i18n pass picks them up. Presentation, not config — lives here rather
# than in settings so the picker UI stays close to the view that renders it.
#
# Every label names the organisation publishing the map. SNOW-831 renamed
# the first entry from "Standard", which named nothing: it said only that
# it was the default, which the picker already shows by selecting it.
_BASEMAP_LABELS: dict[str, Promise] = {
    "openfreemap_liberty": _("OpenFreeMap"),
    # National mapping-agency basemaps. Each covers only its own country
    # (blank elsewhere), so labels carry the ISO country suffix to set the
    # expectation. ``swisstopo_light`` stays in BASEMAP_STYLES as a BASEMAP=
    # env override but is intentionally omitted here so it no longer appears
    # in the picker.
    "swisstopo_winter": _("Swisstopo (CH)"),
    "ign_plan": _("IGN (FR)"),
    "basemap_at": _("basemap.at (AT)"),
}


def _basemaps_for_picker() -> list[dict[str, Any]]:
    """Build the ordered ``{key, label, url, countries}`` catalogue for the picker.

    Order follows ``_BASEMAP_LABELS`` (the user-facing intent), not the
    iteration order of ``settings.BASEMAP_STYLES`` — the labels dict is
    where the picker's display order is curated. Any key in settings
    that has no label here is dropped from the picker (still usable as
    a ``BASEMAP=`` env override on the deployed default).

    SNOW-891: ``countries`` is the space-separated coverage from
    ``settings.BASEMAP_COUNTRIES``, rendered onto the row as
    ``data-basemap-countries`` for ``static/js/map.js`` to scope the EAWS
    boundary outlines by. It has to travel through the DOM rather than a
    shared JS constant because ``map_layer_sync_status.js`` loads before the
    map bundle and cannot read one — the same constraint that put
    ``data-country-codes`` on the country rows.
    """
    return [
        {
            "key": key,
            "label": label,
            "url": settings.BASEMAP_STYLES[key],
            "countries": " ".join(settings.BASEMAP_COUNTRIES[key]),
        }
        for key, label in _BASEMAP_LABELS.items()
        if key in settings.BASEMAP_STYLES
    ]


# SNOW-314: The default region whose season ribbon is server-rendered at
# first paint on /. CH-4115 (Martigny / Verbier) is the canonical preview
# region — it has the richest test-data coverage and represents a central
# Swiss backcountry area that most users recognise.
_DEFAULT_RIBBON_REGION_ID = "CH-4115"


def _shown_date(request: HttpRequest) -> datetime.date | None:
    """
    Return the day the map is being asked to show, from ``?d=``.

    SNOW-660 made ``?d=`` the single home of the chosen day, written and read
    by the client. This is a READ, not a second writer: the server needs it
    only to decide whether the scrubber is on screen (SNOW-794), so
    that first paint is already correct instead of correcting itself a frame
    later.

    Anything unparseable is treated as no choice at all rather than raising —
    the map itself already ignores a malformed ``?d=``, and the two must not
    disagree about a querystring a visitor can type by hand.

    Args:
        request: The incoming HTTP request.

    Returns:
        The parsed date, or ``None`` when the parameter is absent or invalid.

    """
    raw = request.GET.get("d")
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def _base_map_context(
    today: datetime.date,
    shown_date: datetime.date | None = None,
) -> dict[str, Any]:
    """
    Build the shared map context for the timeline scrubber and ribbon.

    Shared between ``home()`` (the canonical map page) and any other view
    that embeds the full map surface.

    The scrubber's track follows the data (SNOW-1431,
    docs/decisions/date-windows-follow-the-data.md): it starts at the first
    rated day of the latest bulletin run
    (``RegionDayRatingQuerySet.latest_run``) and ends at the later of that
    run's last rated day and today. Today is therefore always on the track,
    in August as in January, and an empty DB gives ``[today, today]``. The
    context keeps the ``season_start`` / ``season_end`` names the templates
    and ``data-season-*`` attributes have always used.

    SNOW-794 made the calendar the scrubber's reveal: the calendar is "pick
    a date, any date", and a day it offers before the run started has no
    position on the track. ``scrubber_on_track`` says whether the shown day
    has one; when it has not, the scrubber is hidden rather than clamped to
    a day the URL does not name. Computed server-side so first paint is
    already right rather than correcting itself.

    ``today_pct`` is clamped to [0, 100] so the thumb always sits inside the
    track.

    Args:
        today: Current date — passed in so callers can freeze time in tests.
        shown_date: The day the map is displaying (a validated ``?d=``), or
            ``None`` when the visitor has chosen none — in which case today
            stands in, since that is the day the scrubber would rest on.

    Returns:
        A dict with ``basemaps``, ``default_basemap_key``,
        ``map_default_overlays`` / ``map_default_boundary`` /
        ``map_default_opacity_step`` (SNOW-872's configurable opening view),
        ``season_start``, ``season_end`` (the track's bounds), ``today``,
        ``today_pct``, ``scrubber_on_track``, ``display_date``, and
        ``data_end`` (the latest run's last rated day, or ``None`` when no
        row is rated).

    """
    run = RegionDayRating.objects.latest_run()
    data_end = run[1] if run is not None else None
    season_start = run[0] if run is not None else today
    season_end = max(data_end, today) if data_end is not None else today
    span = (season_end - season_start).days
    elapsed = max(0, min((today - season_start).days, span))
    today_pct = round(elapsed / span * 100, 2) if span else 100.0
    effective_date = shown_date or today
    scrubber_on_track = season_start <= effective_date <= season_end
    return {
        "basemaps": _basemaps_for_picker(),
        "default_basemap_key": settings.BASEMAP,
        # Origin for map.html's preconnect hint. The map's style JSON and
        # vector tiles are the first cross-origin bytes the page needs, and
        # nothing references that host until map.js has parsed and run — so
        # without this the DNS, TCP and TLS round trips start late and land
        # squarely on the critical path for a mobile connection.
        "basemap_origin": settings.BASEMAP_ORIGIN,
        # SNOW-872: the map's opening view, resolved from the environment the
        # same way the basemap key above is. Rendered as data- attributes on
        # #map and read by static/js/map_state.js's ``mapDefaults()``, which
        # is the one owner of these values on the client. They are DEFAULTS: a
        # device with a stored preference under the matching
        # ``snowdesk.map.overlay.*`` key never reaches them.
        "map_default_overlays": settings.MAP_DEFAULT_OVERLAYS,
        "map_default_boundary": settings.MAP_DEFAULT_BOUNDARY,
        "map_default_opacity_step": settings.MAP_DEFAULT_OPACITY_STEP,
        "season_start": season_start,
        "season_end": season_end,
        "today": today,
        "today_pct": today_pct,
        "scrubber_on_track": scrubber_on_track,
        # The day map.js's boot leg fetches ratings for — ``readDisplayDate()``,
        # the ``?d=`` the visitor asked for or else today. map.html preloads
        # that exact URL so the fetch is answered from the preload rather than
        # queued behind map.js. It used to prefetch ``season_end``, a day
        # SNOW-660/793 stopped booting on: a wasted request for a date
        # nothing paints, plus the real one after it.
        "display_date": effective_date,
        "data_end": data_end,
        # SNOW-792: the scrubber's calendar popup builds its month grid in
        # JavaScript, so every word in it has to be handed over from here —
        # ``makemessages`` never scans JS, and a month name written as a JS
        # literal would ship as English to every locale (docs/i18n.md).
        # Django's own lazy-translated tables, in the order the grid needs
        # them: months January-first, weekdays Monday-first.
        "calendar_month_names": [MONTHS[i] for i in range(1, 13)],
        "calendar_weekday_abbrs": [WEEKDAYS_ABBR[i] for i in range(7)],
    }


def _build_default_ribbon(
    today: datetime.date,
) -> SeasonRibbon | None:
    """
    Build the season ribbon for the default region (CH-4115) for first-paint.

    Returns ``None`` gracefully when the region does not exist in the DB
    (e.g. a freshly migrated empty database in development or CI), so the
    ribbon template renders nothing rather than raising a 500.

    Args:
        today: Current date (passed through to ``build_season_ribbon``).

    Returns:
        A :class:`~apps.public.season_calendar.SeasonRibbon` or ``None``.

    """
    try:
        region = MicroRegion.objects.get_by_natural_key(_DEFAULT_RIBBON_REGION_ID)
    except MicroRegion.DoesNotExist:
        return None
    return build_season_ribbon(region, today)


def _default_region_label() -> tuple[str, str, str, str]:
    """
    Return the name, slug, and L2/L1 parent names of the default ribbon region.

    Used to seed the persistent region-readout chip on the homepage, where
    CH-4115 is pre-selected: the name labels the chip, the slug builds its
    "view bulletin" link, and the sub-region (L2) / major (L1) names seed the
    breadcrumb so it is correct on first paint — before any region-selected
    event fires. One query for all four (the parents are select_related).
    Returns ``("", "", "", "")`` when the region is absent (empty DB) so the
    readout simply stays hidden.

    Returns:
        A ``(name, name_slug, subregion_name, major_name)`` tuple, or four empty
        strings if the region does not exist.

    """
    try:
        region = MicroRegion.objects.select_related(
            "subregion__major"
        ).get_by_natural_key(_DEFAULT_RIBBON_REGION_ID)
    except MicroRegion.DoesNotExist:
        return "", "", "", ""
    sub = region.subregion
    subregion_name = sub.name_en if sub.name_en and sub.name_en != sub.prefix else ""
    major_name = sub.major.name_en or sub.major.name_native
    return region.name, region.name_slug, subregion_name, major_name


def _report_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the field-report affordance.

    ``report_eligible`` is True only when the user is authenticated **and** has
    a verified ``Account`` — eligible users get the report flow.  This mirrors
    the server-side gate in ``apps/observations/views.py`` exactly (both call
    ``apps.accounts.models.user_is_verified``) so the client affordance can never
    invite a request the server will 403 (SNOW-477).  ``report_unverified`` is
    True for an authenticated-but-unverified user — the sheet shows them a
    "verify your email" prompt rather than the anonymous sign-in CTA or a
    silent stall.  Anonymous users are neither eligible nor unverified and get
    the sign-in CTA.

    Args:
        request: The current HTTP request.

    Returns:
        Dict with ``report_eligible``, ``report_unverified``,
        ``report_form_url``, ``report_submit_url``, ``report_list_url``,
        ``report_signin_url``.

    """
    report_eligible = user_is_verified(request.user)
    report_unverified = request.user.is_authenticated and not report_eligible
    return {
        "report_eligible": report_eligible,
        "report_unverified": report_unverified,
        "report_form_url": reverse("observations:report_form"),
        "report_submit_url": reverse("observations:report_submit"),
        # SNOW-658: the roundel opens a panel listing the user's own reports
        # before it offers to file another, so the panel needs the list
        # endpoint.
        # The rows come back with map-focus labels; since SNOW-803 the sheet
        # is the endpoint's only surface, so there is no variant to ask for.
        "report_list_url": reverse("observations:list"),
        "report_signin_url": reverse("accounts:sign_in"),
    }


def _favourites_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the saved-pin favourites affordance.

    ``favourites_eligible`` requires the user to be authenticated —
    favourites.js and map.js branch on this to show the real add/rename/
    delete flow versus an anonymous sign-in CTA.

    The dict also includes the HTMX endpoint URLs, a sign-in URL for the
    anonymous CTA, and two ``__UUID__``-templated URLs (mirroring
    ``edit_save_url_template`` in ``home()``) that favourites.js
    string-replaces at runtime to build the rename/delete requests for a
    pin selected on the map — there is no server-side "fetch one
    favourite" endpoint, so the client reconstructs the same rename/delete
    markup ``favourites/partials/_favourite.html`` renders after create.

    Args:
        request: The current HTTP request.

    Returns:
        Dict with ``favourites_eligible``, ``favourites_geojson_url``,
        ``favourite_create_url``, ``favourite_list_url``,
        ``favourite_rename_url_template``,
        ``favourite_delete_url_template``, and ``favourites_signin_url``.

    """
    favourites_eligible = request.user.is_authenticated
    # __UUID__ placeholder, mirroring the __SLUG__ trick used above for
    # edit_save_url_template — reverse with a dummy uuid, then string-
    # replace at runtime with the uuid of the pin actually selected.
    dummy_uuid = uuid.UUID(int=0)
    return {
        "favourites_eligible": favourites_eligible,
        "favourites_geojson_url": reverse("favourites:geojson"),
        "favourite_create_url": reverse("favourites:create"),
        # SNOW-658: the roundel opens a panel listing the user's own pins
        # before it offers to add one, so the panel needs the list endpoint.
        # The sheet's lean row template is the endpoint's only shape since
        # SNOW-803 removed the account page that hosted the fuller one.
        "favourite_list_url": reverse("favourites:list"),
        "favourite_rename_url_template": reverse(
            "favourites:rename", args=[dummy_uuid]
        ).replace(str(dummy_uuid), "__UUID__"),
        "favourite_delete_url_template": reverse(
            "favourites:delete", args=[dummy_uuid]
        ).replace(str(dummy_uuid), "__UUID__"),
        "favourites_signin_url": reverse("accounts:sign_in"),
    }


def _routes_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the map's saved-routes panel (SNOW-686).

    ``routes_eligible`` was authentication alone until SNOW-764, mirroring
    ``favourites_eligible``. routes.js branches on it to show the real list
    and upload control versus an anonymous sign-in CTA
    (tests/js/test_routes_panel_anonymous.js covers that branch), and the
    panel itself is now in the DOM for every visitor.

    SNOW-764 ADDS A SECOND WAY TO BE ELIGIBLE: an anonymous visitor whose
    SESSION holds a pending share token. They were sent a route and have
    followed the link, so the panel and the map layer both have something
    of theirs to draw — and ``routes:list`` / ``routes:geojson`` answer for
    exactly that request (see ``apps.routes.views``'s header). Without this
    the deep link would land on a map whose routes endpoint the page never
    even asks for.

    THE WAFFLE FLAG IS DELIBERATELY NOT READ HERE. This helper runs on the
    homepage, and ``docs/feature-flags.md`` records what a flag read costs
    there — SNOW-749 measured it at three extra queries on the site's
    most-requested page and deleted the flag rather than pay them. The
    check below is a session-dictionary lookup on an object the auth
    middleware has already loaded: no query, flagged or not. The flag is
    read by the endpoints this eligibility merely POINTS AT, which is
    where it has to hold anyway — an eligible-looking panel whose list
    endpoint 403s is a worse failure than nothing, and cannot happen,
    because a token only reaches a session through the flagged redirect.

    No ``__UUID__``-templated delete URL, unlike ``_favourites_context``. A
    route row's Remove is a plain HTMX form rendered server-side into the
    row itself (routes/partials/_route_row_actions.html), so nothing
    client-side ever has to build that URL; only rename does, because its
    commit is a fetch from an inline editor. SNOW-1018's rail is the one
    exception, and ``route_delete_url_template`` exists for it alone: the
    rail is filled by JS for whichever route is open, so its Delete is a
    fetch too.

    Args:
        request: The current HTTP request.

    SNOW-687 adds ``routes_geojson_url`` — the map layer's own data
    endpoint, rendered onto ``#map`` as ``data-routes-url`` for
    static/js/map.js to fetch. A ``reverse()``, not a query, so the
    homepage's query count is unmoved.

    Returns:
        Dict with ``routes_eligible``, ``routes_upload_eligible``,
        ``route_create_url``, ``route_list_url``,
        ``route_rename_url_template``, ``route_share_url_template``,
        ``route_claim_url_template``, ``route_delete_url_template``,
        ``route_name_max_length``, ``routes_geojson_url``,
        ``routes_signin_url``, ``example_upload_signin_url`` and
        ``route_share_dismiss_url_template``.

    """
    # __UUID__ placeholder, mirroring _favourites_context — reverse with a
    # dummy uuid, then string-replace at runtime with the uuid of the row
    # actually being renamed.
    dummy_uuid = uuid.UUID(int=0)
    return {
        "routes_eligible": (
            request.user.is_authenticated or bool(pending_tokens(request.session))
        ),
        # SNOW-764: the question ``routes_eligible`` used to answer on its
        # own. Uploading needs an account; reading a route somebody sent
        # you does not. Kept as a separate value rather than re-derived in
        # JS because "may upload" and "has routes to show" are now
        # genuinely different facts, and collapsing them is what would put
        # an Add-a-route CTA in front of a signed-out recipient.
        "routes_upload_eligible": request.user.is_authenticated,
        "route_create_url": reverse("routes:create"),
        # One row shape since SNOW-803 — the shared
        # includes/_ugc_panel_row.html row the sheet has always listed.
        "route_list_url": reverse("routes:list"),
        "route_rename_url_template": reverse(
            "routes:rename", args=[dummy_uuid]
        ).replace(str(dummy_uuid), "__UUID__"),
        # SNOW-764: the owner's Share button and the recipient's Save. Both
        # are templated the same way the rename above is, on the same rule
        # — static/js must not know how this project spells its URLs — but
        # on different placeholders, because they are addressed by
        # different things. Share names a route the caller OWNS, so it
        # takes a uuid; Save names a share the caller does not own, so it
        # takes the token and never a uuid (see apps.routes.views).
        #
        # ``__TOKEN__`` survives ``<str:token>``'s converter, which accepts
        # any non-empty run without a slash — the same reason
        # _downloads_context spells its placeholder ``AREAID``.
        "route_share_url_template": reverse(
            "routes:share_create", args=[dummy_uuid]
        ).replace(str(dummy_uuid), "__UUID__"),
        "route_claim_url_template": reverse("routes:share_claim", args=["__TOKEN__"]),
        # SNOW-1018: rail one's Delete. The routes ROW needs no delete
        # template (its Remove is a server-rendered HTMX form, as the
        # docstring says), but the rail below the map is filled by JS for
        # whichever route is open, so its Delete is a fetch built from
        # this — the rename template's own reasoning.
        # SNOW-1018: the rail's rename input is capped at the column's own
        # length, which routes:rename enforces with a 400 — read off the
        # field rather than restated, so the two cannot drift.
        "route_name_max_length": Route._meta.get_field("name").max_length,
        "route_delete_url_template": reverse(
            "routes:delete", args=[dummy_uuid]
        ).replace(str(dummy_uuid), "__UUID__"),
        # SNOW-687: the map layer's data. Emitted only for an eligible user
        # (see the template) — the endpoint 403s for anyone else, and there
        # is nothing to draw.
        "routes_geojson_url": reverse("routes:geojson"),
        "routes_signin_url": reverse("accounts:sign_in"),
        # SNOW-1244: the example walkthrough's finish card. One page for new
        # and returning visitors, and the link comes back to the routes
        # panel, which is where an upload starts.
        "example_upload_signin_url": "{}?{}".format(
            reverse("accounts:sign_in"),
            urlencode(
                {"next": f"{reverse('public:map')}?panel=routes", "intent": "upload"}
            ),
        ),
        # SNOW-1230: where the example walkthrough drops the example from
        # the session when it is exited or finished. Token-templated as the
        # claim URL above is; a reverse, so no query.
        "route_share_dismiss_url_template": reverse(
            "routes:share_dismiss", args=["__TOKEN__"]
        ),
        # SNOW-1256: drawing a route on the map, behind the route_drawing
        # flag. Short-circuited for an anonymous request, so a signed-out
        # visitor pays no waffle query for it (the rule
        # tests/public/test_debug_log_panel.py pins). Drawing is meant to be
        # free, so rolling it out to signed-out visitors means removing this
        # guard deliberately — and the endpoints themselves already read the
        # flag for anyone.
        "route_drawing_enabled": (
            request.user.is_authenticated
            and waffle.flag_is_active(request, "route_drawing")
        ),
        "route_draw_preview_url": reverse("routes:draw_preview"),
        "route_create_drawn_url": reverse("routes:create_drawn"),
        # Where an anonymous drawer's Save sends them. The draft waits on
        # the device (route_draw.js), and the link comes back to the routes
        # panel, whose "Draw on the map" picks the draft up again.
        "route_draw_signin_url": "{}?{}".format(
            reverse("accounts:sign_in"),
            urlencode({"next": f"{reverse('public:map')}?panel=routes"}),
        ),
    }


def _downloads_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the offline-downloads affordances (SNOW-749).

    ``downloads_eligible`` is authentication, mirroring ``routes_eligible``
    and ``favourites_eligible``. It gates *starting* a download, not reading
    one: static/js/map_region_download.js and
    static/js/map_downloads_manager.js paint a sign-in state on their two
    start controls when it is false, and the controls stay visible either
    way — a hidden control reads as a bug, not as a gate. Nothing here gates
    the manage sheet, which must keep listing an already-downloaded area for
    a signed-out user, because working with no signal is the entire point of
    having downloaded it.

    ``__AREA_ID__`` templating, mirroring ``_favourites_context``'s
    ``__UUID__`` trick: reverse with a placeholder, then string-replace at
    runtime with the id of the area actually being acted on. The placeholder
    literal has to survive ``path("<str:area_id>/")``'s converter, so it is
    ``AREAID`` rather than a bracketed token — ``str`` accepts any non-empty
    run without a slash, and a bare alphanumeric run is the safest thing to
    round-trip through ``reverse()``.

    Areas are addressed by their client-minted ``area_id`` rather than by a
    server uuid because the client always has the area id to hand — it names
    the device's own Cache Storage bucket — while a uuid would have to be
    learnt from a response the mutation queue may have discarded long before
    the write replays.

    **The gate ships unconditionally — there is no rollout flag.** SNOW-749
    carried a ``download_sync`` waffle flag through review and dropped it
    before merge, on cost: the read took the homepage from 5 queries to 8,
    and waffle reads through Django's ``default`` cache, which in production
    is ``DatabaseCache`` against the ``django_cache`` table — so a warm
    cache traded three model queries for a cache-table one rather than for
    none. Three queries on the site's most-requested page, permanently, to
    hold a kill switch open on a gate nobody intended to close, was the
    wrong trade. What was given up is the admin toggle; reverting the
    feature is a deploy, as it is for every other capability here.

    That is why this helper reads no flag and costs no query: every value
    below is a ``reverse()``, and the homepage's count is unmoved.

    Args:
        request: The current HTTP request.

    Returns:
        Dict with ``downloads_eligible``, ``download_sync_url``,
        ``download_areas_url``, ``download_rename_url_template``,
        ``download_forget_url_template`` and ``downloads_signin_url``.

    """
    # See the docstring: a plain alphanumeric placeholder, because the URL
    # pattern's <str:area_id> converter would not match a bracketed one.
    placeholder = "AREAID"
    return {
        "downloads_eligible": request.user.is_authenticated,
        "download_sync_url": reverse("downloads:sync"),
        "download_areas_url": reverse("downloads:areas"),
        "download_rename_url_template": reverse(
            "downloads:rename", args=[placeholder]
        ).replace(placeholder, "__AREA_ID__"),
        "download_forget_url_template": reverse(
            "downloads:forget", args=[placeholder]
        ).replace(placeholder, "__AREA_ID__"),
        "downloads_signin_url": reverse("accounts:sign_in"),
    }


def _community_reports_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the community-reports map overlay.

    Unlike favourites, there is no per-user eligibility split: the overlay
    shows anonymised, publicly-shared data, so every request sees the
    toggle.

    Args:
        request: The current HTTP request.

    Returns:
        Dict with ``community_reports_geojson_url`` and
        ``observation_families`` — the ``(value, label)`` pairs the map
        legend's "On the ground" key lists, most serious first (SNOW-1290).

    """
    return {
        "community_reports_geojson_url": reverse("api:community_reports_geojson"),
        "observation_families": FieldObservation.OBSERVATION_FAMILY.choices,
    }


def _weather_overlay_context() -> dict[str, Any]:
    """Build the template context dict for the map's Weather overlay.

    Like community reports and unlike favourites, there is no eligibility
    split: the feed is filtered server-side by ``Location.objects.public()``
    and carries no per-user data, so every request sees the toggle and the
    URL.

    Returns:
        Dict with ``weather_geojson_url``.

    """
    return {"weather_geojson_url": reverse("api:weather_geojson")}


def _terrain_filter_context() -> dict[str, Any]:
    """Build the template context dict for the map's terrain filter (SNOW-978).

    Eligibility is ``settings.TERRAIN_CLASS_TILE_URL`` being configured —
    it defaults to empty, so the filter is off until an operator sets it.
    Since SNOW-1073 the map's slope layer is the filter's slope mode, so
    the same setting gates it; swisstopo's raster and its
    ``_slope_context`` are gone. An ineligible page carries neither the
    menu row nor the tile template: a template in the DOM would be an
    invitation to install a layer the environment has withdrawn.

    SNOW-1194's problem-terrain layer shares the filter's gate and tiles;
    its key in the map legend lists the whole scale, because every region
    is painted at once.

    Returns:
        Dict with ``terrain_filter_eligible``, ``terrain_class_tile_url``
        (empty string while ineligible) and ``bulletin_terrain_key``.

    """
    eligible = bool(settings.TERRAIN_CLASS_TILE_URL)
    return {
        "terrain_filter_eligible": eligible,
        "terrain_class_tile_url": settings.TERRAIN_CLASS_TILE_URL if eligible else "",
        "bulletin_terrain_key": problem_terrain_key(None),
    }
