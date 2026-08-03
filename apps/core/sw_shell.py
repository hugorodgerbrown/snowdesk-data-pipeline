"""
apps/core/sw_shell.py — SW shell cache-version derivation (SNOW-517, SNOW-590).

The service worker names its shell cache with a ``CACHE_VERSION`` string. A
returning client keeps serving the old shell until that name changes, so the
value must change whenever a shell asset changes — SNOW-457 shipped exactly
that regression when it did not.

SNOW-517 solved this with a hand-bumped ``snowdesk-shell-vNN`` constant in
``static/js/sw.js``, policed by a committed hash file, a ``bin/sw-version``
CLI and a CI guard. That worked, but the bump was a hand edit to a single
line in the one file every shell change touches, which made it a guaranteed
conflict between concurrent branches — it collided three times in one
afternoon on 2026-08-02, and one of those collisions silently skipped every
``pull_request`` workflow because GitHub cannot build a merge commit for a
conflicting PR.

SNOW-590 removes the constant from source control instead. The version is
now **derived** from the shell content hash and injected at serve time by
``apps.public.views.serve_sw``. Nothing compares it ordinally — it is used
purely as a cache *name* (``caches.open(CACHE_VERSION)`` and
``name !== CACHE_VERSION`` in the activate sweep) — so the monotonic ``vNN``
scheme was never load-bearing. A change to any shell source changes the
hash, which changes the cache name, which is the whole requirement.

There is consequently nothing to bump, nothing to keep in sync, and no
conflict surface: ``bin/sw-version``, ``static/js/sw-shell.hash`` and the
``sw-version`` tox env are all gone.

Failing safe
------------
The one new risk is a silent substitution failure: if the placeholder line
in ``sw.js`` were edited into an unrecognised shape, every client would keep
whatever literal shipped and the shell would freeze — the SNOW-457 failure
mode again. ``inject_cache_version()`` therefore **raises** rather than
returning the body unchanged, and ``apps.core.checks`` turns the same
condition into a Django system check so it fails in ``tox -e django-checks``
(a required CI job) long before it can reach production.

The hash is byte-exact and therefore line-ending-sensitive: it reads each
shell source as raw bytes, so a checkout that rewrites LF to CRLF (Windows
with ``git autocrlf=true`` and no ``.gitattributes`` pinning these files to
LF) would compute a digest that diverges from one computed on macOS/Linux.
Under SNOW-590 that no longer causes a mismatch against a committed value —
it would just mean Windows clients get their own cache name — so this is
now a curiosity rather than a hazard.
"""

from __future__ import annotations

import hashlib
import re
from functools import cache
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent

SW_JS_PATH: Path = REPO_ROOT / "static" / "js" / "sw.js"

# The cache name is a fixed prefix plus a slice of the shell digest. Twelve
# hex chars is ~48 bits — far beyond any collision concern for a value whose
# only job is to differ from the previous one.
_VERSION_PREFIX: str = "snowdesk-shell-"
_HASH_SLICE: int = 12

# Matches the whole assignment statement regardless of its value, so the
# committed placeholder and any previously-shipped literal are both
# rewritable at serve time.
_CACHE_VERSION_LINE_RE: re.Pattern[str] = re.compile(r"const CACHE_VERSION = '[^']*';")


def _shell_template_paths() -> tuple[Path, ...]:
    """
    Return the shell templates that participate in the offline shell.

    These are the templates whose bytes ship inside the service worker's
    cached shell (``base.html`` is extended by every page; ``home.html``
    and ``_map_embed.html`` render the map shell; ``offline.html`` is the
    precached offline fallback) — not every template in the project.
    """
    return (
        REPO_ROOT / "apps" / "public" / "templates" / "public" / "base.html",
        REPO_ROOT / "apps" / "public" / "templates" / "public" / "home.html",
        REPO_ROOT
        / "apps"
        / "public"
        / "templates"
        / "public"
        / "partials"
        / "_map_embed.html",
        REPO_ROOT / "static" / "offline.html",
    )


def _default_shell_sources() -> tuple[Path, ...]:
    """
    Return every shell source path to hash.

    Includes every ``static/js/*.js`` file (globbed live, so a new script
    is picked up automatically without editing this module), the committed
    Tailwind source ``src/css/main.css`` (``static/css/output.css`` is a
    gitignored build artefact and must NOT be hashed), and the shell
    templates from ``_shell_template_paths()``.

    ``static/js/sw.js`` is included like any other script. Under SNOW-590
    its ``CACHE_VERSION`` line is a fixed placeholder that serve-time
    substitution never writes back, so hashing it raises no circularity —
    the chicken-and-egg normalisation SNOW-517 needed is gone with it.
    """
    js_sources = sorted((REPO_ROOT / "static" / "js").glob("*.js"))
    css_source = (REPO_ROOT / "src" / "css" / "main.css",)
    return tuple(js_sources) + css_source + _shell_template_paths()


# Computed once at import time from the current tree. Tests that need to
# exercise a mutated shell monkeypatch this (and, where relevant,
# ``SW_JS_PATH``) rather than mutating real repo files.
SHELL_SOURCES: tuple[Path, ...] = _default_shell_sources()


def _repo_relative(path: Path) -> str:
    """
    Return the POSIX repo-relative form of a path.

    Falls back to the plain POSIX form unchanged if ``path`` sits outside
    ``REPO_ROOT`` (as a test fixture's temporary file would).
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def compute_shell_hash() -> str:
    """
    Return the sha256 hex digest over every path in ``SHELL_SOURCES``.

    Hashes the sorted (repo-relative-path, file-bytes) pairs so the result
    is independent of filesystem iteration order.
    """
    entries: list[tuple[str, bytes]] = []
    for path in SHELL_SOURCES:
        entries.append((_repo_relative(path), path.read_bytes()))
    entries.sort(key=lambda entry: entry[0])

    digest = hashlib.sha256()
    for rel_path, raw in entries:
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw)
        digest.update(b"\0")
    return digest.hexdigest()


def cache_version() -> str:
    """
    Return the derived shell cache name, e.g. ``"snowdesk-shell-a1b2c3d4e5f6"``.

    Not cached here: ``cached_cache_version()`` is the request-path entry
    point. Call this one when a fresh read of the tree is wanted (tests,
    the staff debug page).
    """
    return f"{_VERSION_PREFIX}{compute_shell_hash()[:_HASH_SLICE]}"


@cache
def cached_cache_version() -> str:
    """
    Return ``cache_version()``, computed once per process.

    ``/sw.js`` is served with ``Cache-Control: no-cache``, so browsers
    revalidate it on every page load; hashing ~15 files per request would
    be pure waste. The shell cannot change under a running process in
    production (the tree is immutable between deploys).

    In development the tree *does* change under the process, and the
    autoreloader only restarts on ``.py`` edits — not on the ``.js`` /
    ``.css`` / template edits that matter here. ``serve_sw`` therefore
    calls ``cache_version()`` directly when ``settings.DEBUG`` is on, and
    only uses this cached form in production.
    """
    return cache_version()


def inject_cache_version(body: str, version: str | None = None) -> str:
    """
    Return ``body`` with its ``CACHE_VERSION`` assignment set to ``version``.

    Args:
        body: The raw ``sw.js`` source.
        version: The cache name to inject. Defaults to ``cache_version()``.

    Returns:
        The body with the assignment rewritten.

    Raises:
        ValueError: if no ``CACHE_VERSION`` assignment is present. This is
            deliberately loud: returning the body unchanged would ship a
            frozen cache name and reintroduce the SNOW-457 stale-shell
            regression silently. ``apps.core.checks`` catches the same
            condition at ``manage.py check`` time.

    """
    if version is None:
        version = cache_version()
    new_body, count = _CACHE_VERSION_LINE_RE.subn(
        f"const CACHE_VERSION = '{version}';", body, count=1
    )
    if count == 0:
        raise ValueError(
            "No `const CACHE_VERSION = '...';` assignment found in the service "
            "worker source. Serving it unmodified would freeze every client's "
            "shell cache name — see apps/core/sw_shell.py."
        )
    return new_body
