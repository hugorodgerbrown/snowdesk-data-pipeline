"""
config/settings/perf.py — Local-perf settings for Lighthouse audits.

Runs the app through the same WhiteNoise + ManifestStaticFilesStorage
stack used in production so local Lighthouse runs see the real cache
headers, hashed filenames, and pre-compressed (.br / .gz) responses.
This is NOT a general-purpose dev mode — it requires ``collectstatic``
to be run before the server starts, and runserver will not auto-reload
on static-file changes.

Typical workflow::

    uv run python manage.py collectstatic --noinput
    DJANGO_SETTINGS_MODULE=config.settings.perf \
        uv run python manage.py runserver --noreload 8765

The Lighthouse CI ``startServerCommand`` uses this module so ``npm run
lh`` matches production-representative performance.
"""

from .development import *  # noqa: F401, F403

DEBUG = False

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# SNOW-585: this module inherits ``development.py``, where the SW dev
# shell-cache bypass defaults on. A perf run is meant to be
# production-representative, so the shell cache must behave exactly as it
# does in production — leaving the bypass on would both skew the audit
# (every shell asset re-fetched from the network) and trip
# ``core.checks.check_sw_dev_shell_bypass``, since DEBUG is off here.
SW_DEV_SHELL_BYPASS = False

# This module is the one place DEBUG is off while the site legitimately
# serves localhost, so ``core.checks.check_site_base_url`` — which aborts a
# deploy whose SITE_BASE_URL was never pointed at a real origin (SNOW-554) —
# would fire on every ``collectstatic`` / ``runserver`` in an ``npm run lh``
# run. Silenced here rather than weakened there: the check is worth nothing
# if it has to guess which localhost is deliberate.
SILENCED_SYSTEM_CHECKS = ["core.site_base_url.E001"]

MIDDLEWARE.insert(  # noqa: F405 — MIDDLEWARE imported via wildcard from base
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,  # noqa: F405
    "whitenoise.middleware.WhiteNoiseMiddleware",
)

# GZipMiddleware compresses dynamic responses (rendered HTML, JSON).
# WhiteNoise handles its own compression for static files.
MIDDLEWARE.insert(  # noqa: F405
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,  # noqa: F405
    "django.middleware.gzip.GZipMiddleware",
)

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Mirror dev: expose the X-DB-Query-Count header so Lighthouse/perf runs
# and the monitor_query_counts command can observe per-page query counts.
QUERY_COUNT_HEADER_ENABLED = True
