"""
config/wsgi.py — WSGI entry point for production deployment.

Exposes the Django application as a WSGI callable named `application`.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_wsgi_application()
