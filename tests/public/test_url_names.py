"""
tests/public/test_url_names.py — Snapshot of every ``public:`` and ``api:`` URL.

The apps/public split (SNOW-1409, docs/decisions/public-splits-by-surface.md)
moves views into new apps but keeps every URL name, path and route order.
This test walks the URL resolver and compares each ``public:`` / ``api:``
route — in resolver order — with the checked-in snapshot
``snapshots/url_names.txt``: its name, its full path pattern and the dotted
path of the view it resolves to.

A move changes only the dotted view paths. Anything else in the diff — a
renamed route, a changed path, a reordered pattern — is a regression. To
regenerate after a deliberate change, run with ``UPDATE_URL_SNAPSHOT=1`` and
review the diff.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from django.urls import URLPattern, URLResolver, get_resolver

SNAPSHOT = Path(__file__).parent / "snapshots" / "url_names.txt"
NAMESPACES = frozenset({"public", "api"})


def _view_path(callback: Callable[..., Any]) -> str:
    """Return the dotted path of a route's view.

    Args:
        callback: The resolved callback. A class-based view's ``as_view()``
            result carries the class as ``view_class``; a feed is an
            instance, so its class names it.

    Returns:
        ``module.qualname`` of the view function or class.

    """
    target: Any = getattr(callback, "view_class", inspect.unwrap(callback))
    if not hasattr(target, "__qualname__"):
        target = type(target)
    return f"{target.__module__}.{target.__qualname__}"


def _walk(
    patterns: list[URLPattern | URLResolver], prefix: str, namespace: str | None
) -> Iterator[str]:
    """Yield one snapshot line per named route, in resolver order.

    Args:
        patterns: The URL patterns at this level.
        prefix: The path pattern of the enclosing includes.
        namespace: The innermost enclosing namespace, if any.

    Yields:
        ``<namespace>:<name>  <path pattern>  <view dotted path>`` for each
        named route in a tracked namespace.

    """
    for entry in patterns:
        route = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            yield from _walk(entry.url_patterns, route, entry.namespace or namespace)
        elif namespace in NAMESPACES and entry.name:
            yield f"{namespace}:{entry.name}  {route}  {_view_path(entry.callback)}"


def _current() -> list[str]:
    """Return the snapshot lines for the URL configuration under test.

    Returns:
        One line per tracked route, in resolver order.

    """
    return list(_walk(get_resolver().url_patterns, "", None))


def test_public_and_api_routes_match_the_snapshot() -> None:
    """Names, paths, views and order are unchanged by the split."""
    current = _current()
    if os.environ.get("UPDATE_URL_SNAPSHOT"):
        SNAPSHOT.write_text("\n".join(current) + "\n")
    assert current == SNAPSHOT.read_text().splitlines()


def test_the_snapshot_covers_both_namespaces() -> None:
    """A walker that found nothing would pass the comparison vacuously."""
    namespaces = {line.split(":", 1)[0] for line in _current()}
    assert namespaces == NAMESPACES
