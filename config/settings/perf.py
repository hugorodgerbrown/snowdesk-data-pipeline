"""
config/settings/perf.py — Local-perf settings for Lighthouse audits.

Runs the app through the same WhiteNoise + ManifestStaticFilesStorage
stack used in production so local Lighthouse runs see the real cache
headers, hashed filenames, and pre-compressed (.br / .gz) responses.
This is NOT a general-purpose dev mode — it requires ``collectstatic``
to be run before the server starts, and runserver will not auto-reload
on static-file changes.

Typical workflow::

    poetry run python manage.py collectstatic --noinput
    DJANGO_SETTINGS_MODULE=config.settings.perf \
        poetry run python manage.py runserver --noreload 8765

The Lighthouse CI ``startServerCommand`` uses this module so ``npm run
lh`` matches production-representative performance.
"""

from .development import *  # noqa: F401, F403

DEBUG = False

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

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
