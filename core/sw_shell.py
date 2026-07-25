"""
core/sw_shell.py — SW shell cache-version tracker (SNOW-517).

``static/js/sw.js`` declares a hand-bumped ``CACHE_VERSION`` constant
(``'snowdesk-shell-vNN'``) that must be incremented whenever a shell asset
changes, or returning users get a stale shell (SNOW-457 shipped exactly
that regression). Nothing previously enforced the bump. This module is the
pure-Python core reused by both ``bin/sw-version`` (CLI) and
``public.debug_views.sw_version`` (staff page) — no Django imports, so it
is importable standalone by the bin script and under Django by the view
alike (composition over duplication, matching ``bin/ds-lint`` /
``bin/docs-lint``).

Approach: keep the legible monotonic ``snowdesk-shell-vNN`` scheme, but
commit a content hash of the shell sources (``static/js/sw-shell.hash``)
next to it. ``bump_owed()`` recomputes the live hash and compares it to the
committed one to decide whether a bump is owed.

Chicken-and-egg guard
----------------------
``static/js/sw.js`` is itself one of the hashed shell sources, and bumping
the version rewrites it. Without care, bumping the version would change
the hash of ``sw.js``, which would make ``bump_owed()`` true again
immediately after a bump. ``compute_shell_hash()`` avoids this by
normalising the ``CACHE_VERSION = '...'`` line out of ``sw.js`` before
hashing it (replacing the value with a fixed placeholder), so a version
bump alone never changes the computed hash. ``static/js/sw-shell.hash``
itself is deliberately excluded from ``SHELL_SOURCES``.

The hash is byte-exact and therefore line-ending-sensitive: it reads each
shell source as raw bytes, so a checkout that rewrites LF to CRLF (Windows
with ``git autocrlf=true`` and no ``.gitattributes`` pinning these files to
LF) would compute a digest that diverges from one committed on macOS/Linux.
The project runs on macOS + Render (Linux) today, so this is latent, not
current — noted here so it is understood before it bites a new contributor.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

SW_JS_PATH: Path = REPO_ROOT / "static" / "js" / "sw.js"
HASH_FILE_PATH: Path = REPO_ROOT / "static" / "js" / "sw-shell.hash"

# Matches e.g. "const CACHE_VERSION = 'snowdesk-shell-v37';" and captures
# the numeric suffix.
_CACHE_VERSION_RE: re.Pattern[str] = re.compile(
    r"const CACHE_VERSION = 'snowdesk-shell-v(?P<num>\d+)';"
)
# Matches the whole assignment statement, regardless of its value — used to
# both normalise the line out for hashing and to rewrite it on --write.
_CACHE_VERSION_LINE_RE: re.Pattern[str] = re.compile(r"const CACHE_VERSION = '[^']*';")
_CACHE_VERSION_PLACEHOLDER: str = "const CACHE_VERSION = '__CACHE_VERSION__';"

# Version scheme: a literal prefix ending in "-v" plus a numeric suffix.
_VERSION_SCHEME_RE: re.Pattern[str] = re.compile(r"^(?P<prefix>.*-v)(?P<num>\d+)$")


def _shell_template_paths() -> tuple[Path, ...]:
    """
    Return the shell templates that participate in the offline shell.

    These are the templates whose bytes ship inside the service worker's
    cached shell (``base.html`` is extended by every page; ``home.html``
    and ``_map_embed.html`` render the map shell; ``offline.html`` is the
    precached offline fallback) — not every template in the project.
    """
    return (
        REPO_ROOT / "public" / "templates" / "public" / "base.html",
        REPO_ROOT / "public" / "templates" / "public" / "home.html",
        REPO_ROOT / "public" / "templates" / "public" / "partials" / "_map_embed.html",
        REPO_ROOT / "static" / "offline.html",
    )


def _default_shell_sources() -> tuple[Path, ...]:
    """
    Return every shell source path to hash.

    Includes every ``static/js/*.js`` file (globbed live, so a new script
    is picked up automatically without editing this module), the committed
    Tailwind source ``src/css/main.css`` (``static/css/output.css`` is a
    gitignored build artefact and must NOT be hashed), and the shell
    templates from ``_shell_template_paths()``. ``static/js/sw-shell.hash``
    is never part of this list — hashing the hash file would be circular.
    """
    js_sources = sorted((REPO_ROOT / "static" / "js").glob("*.js"))
    css_source = (REPO_ROOT / "src" / "css" / "main.css",)
    return tuple(js_sources) + css_source + _shell_template_paths()


# Computed once at import time from the current tree. Tests that need to
# exercise a mutated shell monkeypatch this (and, where relevant,
# ``SW_JS_PATH`` / ``HASH_FILE_PATH``) rather than mutating real repo files.
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


def _normalise_cache_version_line(raw: bytes) -> bytes:
    """
    Strip the live ``CACHE_VERSION`` value out of raw ``sw.js`` bytes.

    Replaces the ``CACHE_VERSION = '...'`` assignment with a fixed
    placeholder so its value never affects a computed hash — see the
    module docstring's "Chicken-and-egg guard" section.
    """
    text = raw.decode("utf-8")
    normalised = _CACHE_VERSION_LINE_RE.sub(_CACHE_VERSION_PLACEHOLDER, text, count=1)
    return normalised.encode("utf-8")


def read_cache_version() -> str:
    """
    Return the current committed ``CACHE_VERSION``.

    Parsed from ``static/js/sw.js``, e.g. ``"snowdesk-shell-v37"``.

    Raises:
        ValueError: if the ``CACHE_VERSION`` assignment can't be found —
            a sign ``sw.js`` was hand-edited into an unrecognised shape.

    """
    text = SW_JS_PATH.read_text(encoding="utf-8")
    match = _CACHE_VERSION_RE.search(text)
    if match is None:
        raise ValueError(f"CACHE_VERSION assignment not found in {SW_JS_PATH}")
    return f"snowdesk-shell-v{match.group('num')}"


def next_version(current: str) -> str:
    """
    Return the next version in the ``snowdesk-shell-vNN`` scheme.

    Args:
        current: The current version string, e.g. ``"snowdesk-shell-v37"``.

    Returns:
        The incremented version string, e.g. ``"snowdesk-shell-v38"``.

    Raises:
        ValueError: if ``current`` doesn't match the ``<prefix>-v<N>``
            scheme.

    """
    match = _VERSION_SCHEME_RE.match(current)
    if match is None:
        raise ValueError(f"cannot parse version scheme: {current!r}")
    incremented = int(match.group("num")) + 1
    return f"{match.group('prefix')}{incremented}"


def compute_shell_hash() -> str:
    """
    Return the sha256 hex digest over every path in ``SHELL_SOURCES``.

    Hashes the sorted (repo-relative-path, file-bytes) pairs so the result
    is independent of filesystem iteration order. ``static/js/sw.js`` has
    its ``CACHE_VERSION`` line normalised out first (see
    ``_normalise_cache_version_line``) so bumping the version alone never
    changes the digest.
    """
    entries: list[tuple[str, bytes]] = []
    for path in SHELL_SOURCES:
        raw = path.read_bytes()
        if path == SW_JS_PATH:
            raw = _normalise_cache_version_line(raw)
        entries.append((_repo_relative(path), raw))
    entries.sort(key=lambda entry: entry[0])

    digest = hashlib.sha256()
    for rel_path, raw in entries:
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw)
        digest.update(b"\0")
    return digest.hexdigest()


def read_committed_hash() -> str:
    """
    Return the committed shell hash.

    Read from ``static/js/sw-shell.hash``, or ``""`` if the file doesn't
    exist yet (first-run — everything is "owed").
    """
    if not HASH_FILE_PATH.exists():
        return ""
    return HASH_FILE_PATH.read_text(encoding="utf-8").strip()


def bump_owed() -> bool:
    """
    Return whether a ``CACHE_VERSION`` bump is owed.

    True when the live shell hash differs from the committed one — i.e. a
    shell source changed since the last ``CACHE_VERSION`` bump.
    """
    return compute_shell_hash() != read_committed_hash()


def write_cache_version(new_version: str) -> None:
    """
    Rewrite the ``CACHE_VERSION`` assignment in ``static/js/sw.js``.

    Args:
        new_version: The full new version string, e.g.
            ``"snowdesk-shell-v38"``.

    """
    text = SW_JS_PATH.read_text(encoding="utf-8")
    new_text = _CACHE_VERSION_LINE_RE.sub(
        f"const CACHE_VERSION = '{new_version}';", text, count=1
    )
    SW_JS_PATH.write_text(new_text, encoding="utf-8")


def write_committed_hash(hash_hex: str) -> None:
    """
    Write ``hash_hex`` to ``static/js/sw-shell.hash``.

    Overwrites any existing content.
    """
    HASH_FILE_PATH.write_text(f"{hash_hex}\n", encoding="utf-8")
