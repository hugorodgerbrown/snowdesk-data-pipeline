"""
tests/offline/conftest.py — fixtures for the weekly offline-maps assurance
suite.

This directory is deliberately NOT ``tests/e2e/``. That suite is a capped,
per-PR smoke alarm (``bin/e2e-lint``, ``docs/client-side-tests.md``) and
these tests are the opposite of every rule it enforces: they are long, they
download several hundred real tiles from a live origin, and they take
minutes rather than seconds. Putting them there would either blow the cap
or force the assertions to shrink until they proved nothing. They run on a
weekly cron instead — ``tox -e offline``.

The three things that make this suite different from every other browser
test in the repo:

1. **The network boundary is real.** Every context is built on
   ``RecordingProxy`` (``tests/offline/proxy.py``), so the suite can both
   see and control traffic a service worker generates — which
   ``page.route`` never sees and ``page.context.set_offline`` does not
   reliably stop.

2. **Going offline is done through the product.** The suite presses
   ``#nav-offline-mode``, the switch a user presses, and then asserts at
   the proxy that nothing left the device. A test that reaches for a
   Playwright API instead is testing Playwright.

3. **The subject is drawn from a seed** (``tests/offline/fuzz.py``), so the
   suite walks the region/basemap/zoom space over the weeks instead of
   re-proving one tile set.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from PIL import Image
from playwright.sync_api import Browser, Page
from pytest_django.live_server_helper import LiveServer

from tests.factories import AccountFactory
from tests.offline.fuzz import OfflineSubject, default_seed, draw_subject
from tests.offline.proxy import NetworkRecorder, RecordingProxy
from tests.seeding import seed_test_dataset

logger = logging.getLogger(__name__)

# Session backend used for magic-link / passkey logins (apps/accounts/backends.py).
_TOKEN_BACKEND = "apps.accounts.backends.TokenBackend"

# The PWA IndexedDB, deleted in teardown so one test's downloads cannot be
# mistaken for the next one's. Duplicated from tests/e2e/conftest.py rather
# than imported, for the same reason that file gives: neither conftest
# should depend on the other's internals.
_PWA_DB_NAME = "snowdesk-pwa-v1"

# How long the SW gets to install, activate and claim the page. Generous
# compared with tests/e2e (5s): every request in this suite crosses the
# proxy, and on a cold CI runner the first load also pulls a real basemap
# style over TLS.
_SW_CONTROL_TIMEOUT_MS = 20_000


@pytest.fixture(scope="session")
def offline_seed() -> str:
    """Return this run's seed, and log it once.

    Logged rather than printed so it lands in the CI job output and the
    pytest report header without a ``-s``.
    """
    seed = default_seed()
    logger.warning("offline-assurance seed: %s", seed)
    return seed


@pytest.fixture()
def _load_offline_dataset(django_db_blocker: Any) -> None:
    """Seed the navigable dataset, function-scoped, after the flush.

    Same constraint as ``tests/e2e/conftest.py``: ``live_server`` pulls in
    ``transactional_db``, which flushes at the start of every test, so a
    session-scoped load would be washed away before the test body ran.
    """
    with django_db_blocker.unblock():
        seed_test_dataset()
    cache.clear()


@pytest.fixture()
def subject(
    offline_seed: str, django_db_blocker: Any, _load_offline_dataset: None
) -> OfflineSubject:
    """Draw this run's region, basemap and zooms from the seed.

    Reads the candidate regions out of the seeded database rather than the
    fixture file, so the draw can only ever name a region the running app
    actually has.
    """
    from apps.regions.models import MicroRegion

    with django_db_blocker.unblock():
        candidates = []
        for region in MicroRegion.objects.exclude(
            basemap_download__isnull=True
        ).select_related("centroid_location"):
            blob = region.basemap_download
            centre = region.centre_point()
            if not blob or not blob.get("count") or centre is None:
                continue
            candidates.append(
                (region.region_id, region.name, int(blob["count"]), *centre)
            )
    drawn = draw_subject(candidates, sorted(settings.BASEMAP_STYLES), seed=offline_seed)
    logger.warning("offline-assurance subject: %s", drawn.to_string())
    return drawn


@pytest.fixture()
def network() -> Iterator[RecordingProxy]:
    """Start the recording proxy for the duration of one test."""
    with RecordingProxy() as proxy:
        yield proxy


@dataclass
class OfflineMapPage:
    """A signed-in map page whose every byte crosses the recording proxy.

    Args:
        page: The Playwright page.
        live_server_url: Origin of the live server.
        network: The proxy's recorder — the switch for the simulated
            network condition, and the log every traffic assertion in
            this suite reads.
        subject: This run's fuzzed draw.
        page_errors: Named JavaScript errors collected for the life of the
            page; asserted empty in teardown, so a silently broken script
            cannot pass as a successful offline render.
        console_errors: Console error/warning lines, with their source
            location. Not asserted on — they are the CONTEXT attached to
            every other failure in this suite, which is what turns "the map
            was blank" into a line number.

    """

    page: Page
    live_server_url: str
    network: NetworkRecorder
    subject: OfflineSubject
    page_errors: list[str]
    console_errors: list[str]

    # -- Going offline the way a user does ----------------------------------

    def switch_offline_mode(self, on: bool) -> None:
        """Press the account menu's "Offline mode" switch and wait for it.

        This is the product's own control (``#nav-offline-mode``,
        ``includes/nav.html``), not a Playwright API — the distinction is
        the point of the suite. The wait is on the header connectivity
        symbol, which ``pwa_offline.js`` repaints only after the service
        worker has acknowledged the mode change, so returning from here
        means the WORKER is in the new mode rather than merely that a
        checkbox was ticked.

        Args:
            on: Whether offline mode should end up switched on.

        """
        self.open_account_menu()
        row = self.page.locator("[data-network-toggle]")
        row.wait_for(state="visible", timeout=10_000)
        if self.page.locator("#nav-offline-mode").is_checked() != on:
            # Two labels point at this input: the row's text label and the
            # switch's own wrapper (includes/_switch.html). The switch is
            # the LAST of them, and it is the thing a user presses — its
            # track and thumb are pointer-events-none, so the click has to
            # land on the label itself (SNOW-645).
            row.locator('label[for="nav-offline-mode"]').last.click()
        expected = "offline" if on else "online"
        self.page.wait_for_selector(
            f'[data-network-indicator][data-network-state="{expected}"]',
            timeout=10_000,
        )
        self.close_account_menu()

    def discard_passive_basemap_cache(self) -> list[str]:
        """Delete every basemap cache that is not a pinned download bucket.

        Snowdesk caches basemap tiles twice, for two different reasons.
        ``snowdesk-basemap-v1`` is a passive stale-while-revalidate cache
        holding whatever the user happened to look at; the
        ``snowdesk-basemap-pinned-*`` buckets hold what they deliberately
        downloaded. Only the second is a promise the product makes.

        The suite's coverage-edge assertions are about the pinned bucket,
        and they cannot be made at all while the passive cache is warm:
        merely opening the map caches a country-level view, and MapLibre
        renders a scaled-up parent tile wherever an exact tile is missing,
        so a viewport hundreds of kilometres outside any downloaded area
        still draws — from the passive cache, at a zoom nothing was stored
        at. That is correct behaviour and a nice touch for the user; it is
        simply not the thing under test, and leaving it in place means
        "where does the stored map stop" has no observable answer.

        So the passive cache is discarded and the pinned buckets are left
        exactly as the download left them. What remains on disk afterwards
        is precisely what the user asked for, which is what the coverage
        assertions are entitled to assume.

        Returns:
            The names of the caches deleted, for the failure message.

        """
        deleted: list[str] = self.page.evaluate(
            """async () => {
                const names = await caches.keys();
                const passive = names.filter(
                  (name) =>
                    name.startsWith('snowdesk-basemap-') &&
                    !name.startsWith('snowdesk-basemap-pinned-'),
                );
                await Promise.all(passive.map((name) => caches.delete(name)));
                return passive;
            }"""
        )
        return deleted

    def go_offline(self) -> None:
        """Switch on Offline mode AND physically remove the network.

        Both, deliberately, and the order matters.

        The switch is what a user presses, so it is what the suite drives.
        But a render test whose only guarantee is the switch is measuring
        the switch: if the switch leaks — and
        ``test_offline_toggle_is_watertight.py`` exists because it does —
        then a map that "rendered offline" may simply have fetched what it
        needed over a connection the user asked it not to use, and the test
        passes while proving nothing.

        So the proxy is cut too. After this call the network is not merely
        unwanted, it is gone: anything that draws came off the disk. That
        makes every render assertion in this suite unambiguous, and leaves
        the watertightness of the switch to the one test whose subject it
        is.

        ``reject`` rather than ``blackhole`` is the default condition
        because it is the common one — a radio that is off — and because a
        hang would add each read path's full timeout budget to every probe.
        ``blackhole`` has its own test.
        """
        self.switch_offline_mode(True)
        self.network.set_mode("reject")

    def open_account_menu(self) -> None:
        """Open the account menu, which is a native ``<details>``."""
        menu = self.page.locator("details[data-subscriber-menu]")
        if not menu.evaluate("(el) => el.open"):
            menu.locator("summary").click()
        self.page.wait_for_function(
            "() => document.querySelector('details[data-subscriber-menu]')?.open === true",
            timeout=5_000,
        )

    def close_account_menu(self) -> None:
        """Close the account menu if it is open."""
        menu = self.page.locator("details[data-subscriber-menu]")
        if menu.evaluate("(el) => el.open"):
            menu.locator("summary").click()

    # -- Camera ---------------------------------------------------------------

    def jump_to(self, longitude: float, latitude: float, zoom: float) -> None:
        """Move the camera and wait for MapLibre to settle.

        ``jumpTo`` rather than ``flyTo``: an animated move fires a stream of
        tile requests along the flight path, which would pollute the
        proxy's log with traffic no user gesture asked for and make a
        "nothing left the device" assertion meaningless.

        Args:
            longitude: Target longitude.
            latitude: Target latitude.
            zoom: Target zoom.

        """
        self.page.evaluate(
            """([lng, lat, zoom]) => {
                window.snowdeskMap.jumpTo({ center: [lng, lat], zoom });
            }""",
            [longitude, latitude, zoom],
        )
        self.wait_for_map_idle()

    def wait_for_map_idle(self, timeout: int = 20_000) -> None:
        """Wait until MapLibre reports it has finished what it can.

        ``idle`` is the only event that means "no further rendering is
        expected without new input". Offline it still fires — tiles that
        cannot be fetched resolve as errored rather than staying pending,
        which is precisely the behaviour the bounded read paths in
        ``sw.js`` exist to guarantee, so a hang here is itself a finding.
        """
        self.page.wait_for_function(
            """() => {
                const map = window.snowdeskMap;
                if (!map) return false;
                return map.loaded() && map.areTilesLoaded();
            }""",
            timeout=timeout,
        )

    # -- Driving the product's own controls ----------------------------------

    def choose_basemap(self, key: str) -> None:
        """Pick a basemap from the map's own picker.

        Coverage is per basemap — an area stored under OpenFreeMap is not
        coverage for swisstopo — so which one is active when the download
        runs is part of what the fuzzer varies, and it has to be chosen the
        way a user chooses it rather than written straight into
        localStorage.

        Args:
            key: A ``BASEMAP_STYLES`` key, e.g. ``"swisstopo_winter"``.

        """
        self.page.click("#basemap-toggle")
        self.page.wait_for_selector("#basemap-menu", state="visible")
        self.page.click(f'#basemap-menu button[data-basemap-key="{key}"]')
        # `aria-checked`, not the localStorage key. Picking the basemap
        # that is ALREADY active is a deliberate no-op in
        # map_basemap_picker.js — it closes the menu and writes nothing —
        # so a wait on the storage key hangs for exactly the draw where
        # the fuzzer happens to pick the default. `aria-checked` is true
        # in both cases, which is what "this basemap is the active one"
        # actually means.
        self.page.wait_for_selector(
            f'#basemap-menu button[data-basemap-key="{key}"][aria-checked="true"]',
            state="attached",
            timeout=20_000,
        )
        # The picker swaps the whole MapLibre style. Wait for the new one
        # to settle before returning: a download started mid-swap would
        # fetch against the outgoing style's sources.
        self.wait_for_map_idle()

    def select_region(self, name: str) -> None:
        """Find a region through the map's search and select it.

        Search rather than a synthetic click on the canvas: a click needs
        the region's pixel position, which changes with every camera move,
        and search is what a user actually does to reach a named place.

        Args:
            name: The region's display name.

        """
        self.page.wait_for_function(
            "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()",
            timeout=30_000,
        )
        self.page.click("#search-toggle")
        self.page.wait_for_selector("#search-input", state="visible")
        self.page.fill("#search-input", name)
        result = self.page.locator("#search-results .search-result").first
        result.wait_for(state="visible", timeout=10_000)
        result.click()
        # The roundel only exists once a region is selected, so its
        # appearance is the signal that the selection landed.
        self.page.wait_for_selector(
            "#map-download-control", state="visible", timeout=15_000
        )

    def download_selected_region(self, timeout_ms: int = 240_000) -> None:
        """Press the download roundel and wait for it to report ``done``.

        ``done`` is not a stored flag: ``map_region_download.js`` recomputes
        it as a live cache read of BOTH halves — every tile, and every
        render dependency (``a-downloaded-area-is-verified-by-what-it-renders.md``).
        So waiting for it is waiting for the area to be genuinely
        renderable, which is the precondition every offline assertion in
        this suite then rests on.

        Args:
            timeout_ms: How long the download may take. Generous by
                default: this is several hundred real tiles from a live
                origin, and a slow origin is not a test failure.

        Raises:
            AssertionError: If the roundel ends in ``error`` rather than
                ``done``.

        """
        roundel = self.page.locator("#map-download-control")
        roundel.click()
        self.page.wait_for_function(
            """() => {
                const state = document.getElementById('map-download-control')
                    ?.dataset.downloadState;
                return state === 'done' || state === 'error'
                    || state === 'incomplete';
            }""",
            timeout=timeout_ms,
        )
        state = roundel.get_attribute("data-download-state")
        assert state == "done", (
            f"The download of {self.subject.region_id} "
            f"({self.subject.region_name}, {self.subject.tile_count} tiles) "
            f"finished in state {state!r} rather than 'done'. "
            "'incomplete' means the tiles arrived but a render dependency "
            "— the style, a TileJSON or the sprite — did not, so the area "
            "cannot draw offline however complete its tile set looks."
        )

    # -- What actually drew ---------------------------------------------------

    def basemap_ink(self) -> float:
        """Return how much of the map canvas is drawn basemap, 0.0–1.0.

        The suite's central measurement, and the reason it is a measurement
        rather than a screenshot comparison: the question
        ``a-downloaded-area-is-verified-by-what-it-renders.md`` poses is
        "did the basemap draw", and every fuzzed subject draws something
        different. A golden image would have to be regenerated for each of
        149 regions and would fail on a font-rendering change; a coverage
        ratio holds across all of them.

        MapLibre is constructed without ``preserveDrawingBuffer``, so
        ``canvas.toDataURL()`` reads back blank — the drawing buffer is
        gone by the time script runs. Playwright's element screenshot goes
        through the compositor instead and captures what is genuinely on
        screen, which is also the thing a user would be looking at.

        "Ink" is the fraction of sampled pixels that differ from the
        style's background colour. An area with stored tiles has roads,
        contours and labels over that background and scores well above
        zero; a viewport outside coverage is the background alone and
        scores at zero.

        Returns:
            Fraction of sampled pixels that are not the background colour.

        """
        with self._overlays_hidden():
            shot = self.page.locator("#map canvas").screenshot(type="png")
        image = Image.open(io.BytesIO(shot)).convert("RGB")
        # Downsample before counting: a 1280x720 canvas is a million
        # pixels, the answer needs two significant figures, and the sample
        # must be cheap enough to take at every probe point.
        image = image.resize((160, 90))
        pixels = list(image.getdata())
        # The modal colour IS the background: with the overlays hidden, an
        # uncovered viewport is the style's background layer and nothing
        # else, and deriving the colour beats hard-coding a hex that
        # differs per basemap and per theme.
        background: tuple[int, int, int] = max(set(pixels), key=pixels.count)

        def differs(pixel: tuple[int, int, int]) -> bool:
            # A tolerance, not equality — the compositor's colour
            # management shifts flat fills by a unit or two, and counting
            # that as ink would score an empty viewport as fully covered.
            return sum(abs(a - b) for a, b in zip(pixel, background)) > 12

        return sum(1 for pixel in pixels if differs(pixel)) / len(pixels)

    @contextmanager
    def _overlays_hidden(self) -> Iterator[None]:
        """Hide Snowdesk's own map layers for the duration of the block.

        Without this the measurement is not about the basemap at all. The
        danger choropleth, the region outlines and the downloaded-tiles
        squares are drawn onto the SAME canvas, they cover most of
        Switzerland, and they keep painting perfectly well when the
        basemap does not — which is the behaviour the product wants and
        the reason a naive pixel count reported ~10% "coverage" over
        ground with no stored tiles at all.

        The split is structural rather than a list of layer ids: every
        Snowdesk overlay is fed by a ``geojson`` source, and every basemap
        layer by the style's own ``vector``/``raster`` sources. So a new
        overlay added later is excluded automatically, and no id here can
        go stale. The style's ``background`` layer has no source at all and
        deliberately stays visible — it is what "blank" is supposed to look
        like.
        """
        hidden: list[str] = self.page.evaluate(
            """() => {
                const map = window.snowdeskMap;
                const style = map.getStyle();
                const overlaySources = new Set(
                  Object.entries(style.sources)
                    .filter(([, source]) => source.type === 'geojson')
                    .map(([id]) => id),
                );
                const ids = style.layers
                  .filter((layer) => overlaySources.has(layer.source))
                  .map((layer) => layer.id)
                  .filter((id) => map.getLayoutProperty(id, 'visibility') !== 'none');
                for (const id of ids) map.setLayoutProperty(id, 'visibility', 'none');
                return ids;
            }"""
        )
        try:
            self.wait_for_map_idle()
            yield
        finally:
            self.page.evaluate(
                """(ids) => {
                    const map = window.snowdeskMap;
                    for (const id of ids) {
                      if (map.getLayer(id)) {
                        map.setLayoutProperty(id, 'visibility', 'visible');
                      }
                    }
                }""",
                hidden,
            )

    def diagnostics(self) -> str:
        """Return the console context to attach to a failing assertion.

        Every failure in this suite is "the map did not look right", and on
        its own that is not actionable. The console is where the reason is
        — a CSP refusal, a 504 from the worker, a source that never
        loaded — so it is attached to the failure rather than left in a
        log nobody opens.
        """
        rejections = self.page.evaluate("() => window.__offlineRejections || []")
        lines = [
            *(f"console  {line}" for line in self.console_errors[-15:]),
            *(f"rejected {line}" for line in rejections[-5:]),
            *(f"maplibre {line}" for line in self.tile_errors()[-10:]),
        ]
        return "\n  ".join(lines) if lines else "(console clean)"

    def tile_errors(self) -> list[str]:
        """Return basemap tile errors MapLibre has reported since page load.

        Collected by an init script (see the fixture below) rather than
        polled, because ``map.on('error')`` fires and is gone. Offline,
        outside coverage, these are EXPECTED — a blank basemap is the
        correct behaviour, and MapLibre says so by erroring on each missing
        tile. They are a fault only when they appear INSIDE coverage.
        """
        errors: list[str] = self.page.evaluate("() => window.__offlineTileErrors || []")
        return errors


def _session_login(context: Any, live_server_url: str, user: User) -> None:
    """Add a valid Django session cookie for ``user`` to ``context``.

    Builds the session with the test ``Client.force_login()`` rather than
    driving the magic-link email through the browser: this suite is about
    the offline map, and a sign-in journey it does not assert on would just
    be more surface to flake. Same mechanism as ``tests/e2e/conftest.py``.

    Args:
        context: The Playwright browser context.
        live_server_url: Origin the cookie is scoped to.
        user: The ``auth.User`` to authenticate as.

    """
    client = Client()
    client.force_login(user, backend=_TOKEN_BACKEND)
    cookie = client.cookies[settings.SESSION_COOKIE_NAME]
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": cookie.value,
                "url": live_server_url,
            }
        ]
    )


@pytest.fixture()
def offline_map_page(
    browser: Browser,
    browser_context_args: dict[str, Any],
    live_server: LiveServer,
    django_db_blocker: Any,
    network: RecordingProxy,
    subject: OfflineSubject,
) -> Iterator[OfflineMapPage]:
    """A signed-in, SW-controlled map page behind the recording proxy.

    The context is built by hand rather than taken from pytest-playwright's
    ``page`` fixture because ``proxy`` is a context-creation argument and
    cannot be applied afterwards — the same constraint that made
    ``no_script_page`` build its own in ``tests/e2e/conftest.py``.

    Two loads, not one, for the reason ``tests/e2e/conftest.py``'s
    ``pwa_page`` documents at length: the navigation that REGISTERS the
    worker was itself never intercepted by it, so the shell is not cached
    until a second, worker-controlled load happens. Every offline
    assertion in this suite depends on that having happened.

    Yields:
        The assembled page.

    """
    with django_db_blocker.unblock():
        account = AccountFactory.create()

    context = browser.new_context(
        **{**browser_context_args, "proxy": network.playwright_proxy()}
    )
    page_errors: list[str] = []
    page = context.new_page()
    console_errors: list[str] = []

    def _record_page_error(err: Any) -> None:
        """Record a page error, unless it is an anonymous rejection.

        An unhandled rejection carrying a non-Error value reaches
        Playwright with no name, no message and no stack — it stringifies
        to "undefined" and names neither what broke nor where. Under a
        deliberately severed network the app's own fetch paths (htmx's
        sendError among them) produce these by design, so failing on one
        would make every test in this suite permanently red for a reason
        no one can act on, which is how a suite gets switched off.

        What this guard is actually for is the NAMED error — the
        template-tag-in-a-JS-comment parse failure, the undefined symbol,
        the script that died halfway through an offline render and made it
        look like it worked. Those all carry a message, and those stay
        fatal. The anonymous ones are still visible, in the console log
        attached to every failure.
        """
        # Anonymous means exactly that: no name and no stack. The message
        # is not part of the test — a rejection carrying `undefined` has
        # the message "undefined", which looks like content and is not.
        if not err.name and not err.stack:
            return
        page_errors.append(f"{err.name}: {err.message}\n{err.stack}".strip())

    page.on("pageerror", _record_page_error)
    page.on(
        "console",
        lambda message: (
            console_errors.append(
                f"{message.type}: {message.text} "
                f"({message.location.get('url')}:{message.location.get('lineNumber')})"
            )
            if message.type in {"error", "warning"}
            else None
        ),
    )

    # The first-run intro overlays the map controls this suite clicks; a
    # returning visitor has long since dismissed it (tests/e2e/conftest.py's
    # _suppress_home_intro makes the same choice, for the same reason).
    page.add_init_script(
        "try { localStorage.setItem('snowdesk.home.intro', 'dismissed'); } catch (_) {}"
    )
    # An unhandled rejection carrying a non-Error value reaches Playwright
    # as a `pageerror` that stringifies to nothing at all — no name, no
    # message, no stack — which names neither what broke nor where. Catch
    # it in the page, where the reason is still intact.
    page.add_init_script(
        """(() => {
            window.__offlineRejections = [];
            addEventListener('unhandledrejection', (event) => {
              const reason = event.reason;
              window.__offlineRejections.push(
                reason && reason.stack
                  ? String(reason.stack)
                  : 'unhandled rejection: ' + String(reason),
              );
            });
          })();"""
    )
    # Collect MapLibre's tile errors as they happen. `map.on('error')` is
    # fire-and-forget, so there is nothing to poll for afterwards unless
    # something is listening from the start.
    page.add_init_script(
        """(() => {
            window.__offlineTileErrors = [];
            const attach = () => {
              if (!window.snowdeskMap) return false;
              window.snowdeskMap.on('error', (event) => {
                const url = event?.sourceId || event?.error?.url || '';
                window.__offlineTileErrors.push(String(event?.error?.message || event) + ' ' + url);
              });
              return true;
            };
            if (!attach()) {
              const timer = setInterval(() => { if (attach()) clearInterval(timer); }, 100);
              setTimeout(() => clearInterval(timer), 30000);
            }
          })();"""
    )
    _session_login(context, live_server.url, account.user)

    page.goto(live_server.url + "/")
    page.wait_for_load_state("load")
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=_SW_CONTROL_TIMEOUT_MS,
    )
    page.reload()
    page.wait_for_load_state("load")
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=_SW_CONTROL_TIMEOUT_MS,
    )

    surface = OfflineMapPage(
        page=page,
        live_server_url=live_server.url,
        network=network.recorder,
        subject=subject,
        page_errors=page_errors,
        console_errors=console_errors,
    )
    try:
        yield surface
    finally:
        # Leave the app online before teardown: a context closed under a
        # forced offline mode persists that mode in IndexedDB, and the
        # deletion below can then race the worker still holding it.
        try:
            surface.switch_offline_mode(False)
        except Exception:  # pragma: no cover - teardown is best-effort
            logger.debug("could not restore online mode during teardown")
        context.close()
    assert page_errors == [], (
        "JavaScript errors were raised during the test. An offline render "
        "that only 'worked' because a script died halfway is not a passing "
        "offline render:\n  " + "\n  ".join(page_errors)
    )
