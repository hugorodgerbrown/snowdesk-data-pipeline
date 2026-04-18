"""
config/settings/development.py — Development-environment overrides.

Enables DEBUG, uses SQLite by default, and relaxes security settings that
would be inappropriate in local development.
"""

from decouple import config

from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",")

INTERNAL_IPS = ["127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# Show all SQL queries in the console during development
LOGGING["loggers"]["django.db.backends"] = {  # type: ignore[index]  # noqa: F405
    "handlers": ["console"],
    "level": "DEBUG",
    "propagate": False,
}

# Disable rate limiting in development and tests so that rapid local requests
# (including the full test suite) are never throttled.
RATELIMIT_ENABLE = False
