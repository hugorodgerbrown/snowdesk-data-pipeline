"""
tests/core/services/test_waffle_manifest_call_sites.py — Manifest ↔ call-site guard.

Every other waffle test in this tree builds its own manifest under
``tmp_path``, so nothing checked that the flag names the **shipped**
manifest declares are the flag names the **shipped** code asks for. That
gap has a distinctive failure mode: waffle answers ``False`` for a name it
cannot find (``WAFFLE_FLAG_DEFAULT``), so a mismatch never raises — the
gated feature simply goes dark, on every environment, silently.

It has happened twice. SNOW-685 shipped a ``routes`` call site whose flag
was seeded by a migration and absent from the manifest, and the feature was
invisible on staging until the manifest entry landed. SNOW-724 removed four
flags and left one call site behind — a ``routes_visible`` context
processor that arrived from a concurrent merge — which would have dropped
the Routes entry from every signed-in user's nav with nothing failing.

Both directions are a defect, so both are asserted:

- **A name in the code but not in the manifest** is the dark-feature bug
  above: the row never exists, so the gate is permanently closed.
- **A name in the manifest but in no call site** is a flag row created and
  reconciled on every deploy that gates nothing — either a leftover from an
  incomplete removal, or a rename that only landed on one side.

Only *literal* flag names are visible to this guard. A call site that
builds its name dynamically (``flag_is_active(request, name)``) is
invisible here, and would need the check to run the code rather than read
it — there are none today, and the third test below fails loudly if the
sweep ever stops finding the call sites we know exist.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from apps.core.management.commands.sync_waffle_flags import DEFAULT_MANIFEST_PATH
from apps.core.services.waffle_sync import load_manifest

# Directories swept for call sites — everything this project ships that can
# ask waffle a question. ``tests/`` is deliberately excluded: an
# ``override_flag`` in a test names a flag the test controls, and a fixture
# manifest built under ``tmp_path`` legitimately names flags that do not
# exist in the shipped one.
_SWEPT_DIRS: tuple[str, ...] = ("apps", "config", "templates")

# Suffixes worth reading. Flag checks live in Python and in templates; the
# project deliberately mounts no ``wafflejs`` endpoint, so JavaScript never
# names a flag (docs/feature-flags.md, "Why no wafflejs endpoint?").
_SWEPT_SUFFIXES: frozenset[str] = frozenset({".py", ".html"})

# ``waffle.flag_is_active(request, "name")`` — the first argument is the
# request (any expression without a comma or bracket in it), the second the
# literal name this guard is after.
_PYTHON_CALL = re.compile(r"""flag_is_active\(\s*[^,()]+,\s*["']([A-Za-z0-9_]+)["']""")

# ``{% flag "name" %}`` — waffle's template tag. No call sites use it today
# (every gate resolves in a view and reaches the template as context), but a
# future one must not slip past this check.
_TEMPLATE_TAG = re.compile(r"""{%\s*flag\s+["']([A-Za-z0-9_]+)["']""")


def _collect_call_sites() -> dict[str, list[str]]:
    """Return every literal flag name the shipped code asks waffle about.

    Returns:
        Mapping of flag name -> the ``path:line`` locations naming it,
        sorted, with paths relative to the repository root so a failure
        message is readable and clickable.

    """
    root = Path(settings.BASE_DIR)
    found: dict[str, list[str]] = {}
    for directory in _SWEPT_DIRS:
        for path in sorted((root / directory).rglob("*")):
            if path.suffix not in _SWEPT_SUFFIXES or not path.is_file():
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                for pattern in (_PYTHON_CALL, _TEMPLATE_TAG):
                    for name in pattern.findall(line):
                        location = f"{path.relative_to(root)}:{number}"
                        found.setdefault(name, []).append(location)
    return found


def _manifest_names() -> set[str]:
    """Return the flag names declared in the shipped manifest.

    Reads the same path ``sync_waffle_flags`` defaults to, through the same
    loader, so moving or reshaping the manifest cannot desynchronise this
    guard from the command it is guarding.

    Returns:
        The set of ``name`` values in ``apps/core/fixtures/waffle_flags.json``.

    """
    return {spec.name for spec in load_manifest(DEFAULT_MANIFEST_PATH)}


def test_every_flag_the_code_asks_for_is_in_the_manifest() -> None:
    """A call site naming a flag the manifest omits gates a dark feature.

    ``WAFFLE_FLAG_DEFAULT = False`` means the lookup answers ``False``
    rather than raising, so this assertion is the only thing standing
    between a typo (or a half-finished removal) and a feature that is
    silently off everywhere it is deployed.
    """
    call_sites = _collect_call_sites()
    orphaned = {
        name: locations
        for name, locations in call_sites.items()
        if name not in _manifest_names()
    }
    assert not orphaned, (
        "These flag names are asked for in code but are not in "
        f"{DEFAULT_MANIFEST_PATH.name}, so they always evaluate False and the "
        "code they gate is dark:\n"
        + "\n".join(
            f"  {name}: {', '.join(locations)}"
            for name, locations in sorted(orphaned.items())
        )
    )


def test_every_manifest_flag_has_a_call_site() -> None:
    """A manifest entry nothing reads is a row deployed to gate nothing.

    ``sync_waffle_flags`` creates it on every deploy and the admin offers
    targeting controls for it, both of which imply a gate that no longer
    exists — the reverse half of the same drift.
    """
    call_sites = _collect_call_sites()
    unused = sorted(_manifest_names() - set(call_sites))
    assert not unused, (
        f"These flags are declared in {DEFAULT_MANIFEST_PATH.name} but no "
        "call site reads them, so they gate nothing: " + ", ".join(unused)
    )


def test_the_sweep_finds_known_call_sites() -> None:
    """Guard the guard: a regex that matches nothing would pass both tests.

    Both assertions above are satisfied by an empty sweep, so this pins the
    sweep against a call site that really exists. ``sync_log`` is the
    project's only remaining flag (SNOW-724) and is read by both
    ``apps/public/views.py`` and ``apps/accounts/views.py``.
    """
    call_sites = _collect_call_sites()
    assert "sync_log" in call_sites, (
        "The call-site sweep found no reference to sync_log — the patterns "
        "have drifted from how flags are actually checked, and both "
        "assertions in this module are passing vacuously."
    )
    assert len(call_sites["sync_log"]) >= 2, call_sites["sync_log"]
