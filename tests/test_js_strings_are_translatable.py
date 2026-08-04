"""
tests/test_js_strings_are_translatable.py — the JS↔template string contract
(SNOW-620).

Eight modules stopped writing user-facing English as JS literals and
started reading it out of a `<template>` their surface partial renders.
That pairing is held together by nothing but a matching string key, and it
fails *silently*: rename a `data-string` in the template, or a key in the
JS fallback object, and the module quietly serves its English fallback
forever. Nothing goes red, nothing logs, and the locale is simply never
used. This module is what makes that loud.

`bin/i18n-lint` covers the other direction — a string that never got
marshalled at all.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_ROOT = REPO_ROOT / "static" / "js"
TEMPLATE_ROOTS = (REPO_ROOT / "templates", REPO_ROOT / "apps")

# `self.pwaStrings.read('<id>', { … })` — the id plus the whole fallback
# object, which is then mined for its keys.
READ_CALL_RE = re.compile(
    r"pwaStrings\.read\(\s*['\"](?P<template_id>[^'\"]+)['\"]\s*,\s*\{"
    r"(?P<body>.*?)\n\s*\}\s*\)",
    re.S,
)

# A key in that object literal: bare (`close:`) or quoted (`'no-coverage':`).
# Anchored to the start of a line so a `:` inside a string value cannot
# masquerade as one. The indent varies — map.js reads at module scope, the
# rest inside an IIFE — so it is matched loosely rather than pinned.
FALLBACK_KEY_RE = re.compile(
    r"^\s+(?:['\"](?P<quoted>[^'\"]+)['\"]|(?P<bare>[A-Za-z_$][\w$]*))\s*:",
    re.M,
)

# The helper itself, whose module docstring demonstrates a `read()` call.
# Documentation of the API is not a use of it.
HELPER = "i18n_strings.js"

TEMPLATE_BLOCK_RE = re.compile(
    r"<template\s+id=\"(?P<id>[^\"]+)\"\s*>(?P<body>.*?)</template>", re.S
)
DATA_STRING_RE = re.compile(r"data-string=\"(?P<key>[^\"]+)\"")


def _js_read_calls() -> dict[str, tuple[Path, set[str]]]:
    """Map each strings-template id to the module and keys that expect it."""
    out: dict[str, tuple[Path, set[str]]] = {}
    for path in sorted(JS_ROOT.rglob("*.js")):
        if path.name == HELPER:
            continue
        text = path.read_text(encoding="utf-8")
        for match in READ_CALL_RE.finditer(text):
            keys = {
                m.group("quoted") or m.group("bare")
                for m in FALLBACK_KEY_RE.finditer(match.group("body"))
            }
            out[match.group("template_id")] = (path, keys)
    return out


def _template_blocks() -> dict[str, tuple[Path, set[str]]]:
    """Map each rendered strings-template id to its file and its keys."""
    out: dict[str, tuple[Path, set[str]]] = {}
    for root in TEMPLATE_ROOTS:
        for path in sorted(root.rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            for match in TEMPLATE_BLOCK_RE.finditer(text):
                template_id = match.group("id")
                if not template_id.endswith("-strings-template"):
                    continue
                out[template_id] = (
                    path,
                    {
                        m.group("key")
                        for m in DATA_STRING_RE.finditer(match.group("body"))
                    },
                )
    return out


JS_CALLS = _js_read_calls()
TEMPLATES = _template_blocks()


def test_the_scan_found_the_modules_it_is_meant_to_cover() -> None:
    """Guard the regexes themselves.

    Every assertion below is driven by these two scans, so a regex that
    silently matched nothing would turn this whole module into a no-op that
    reports green. SNOW-620 converted eight modules.
    """
    assert len(JS_CALLS) >= 9, sorted(JS_CALLS)
    assert len(TEMPLATES) >= 9, sorted(TEMPLATES)


@pytest.mark.parametrize("template_id", sorted(JS_CALLS))
def test_every_module_has_a_template_rendering_its_strings(template_id: str) -> None:
    """A module reading an id nothing renders serves English forever."""
    module, _ = JS_CALLS[template_id]

    assert template_id in TEMPLATES, (
        f"{module.name} reads '{template_id}', but no template renders it — "
        f"the module will silently fall back to English in every locale."
    )


@pytest.mark.parametrize("template_id", sorted(JS_CALLS))
def test_every_key_the_module_expects_is_rendered(template_id: str) -> None:
    """A key present in JS but absent from the template is a silent fallback."""
    module, js_keys = JS_CALLS[template_id]
    if template_id not in TEMPLATES:
        pytest.skip("covered by test_every_module_has_a_template_rendering_its_strings")
    template, template_keys = TEMPLATES[template_id]

    missing = js_keys - template_keys
    assert not missing, (
        f"{module.name} expects {sorted(missing)} from '{template_id}', but "
        f"{template.name} does not render them — untranslatable in every locale."
    )


@pytest.mark.parametrize("template_id", sorted(TEMPLATES))
def test_no_template_renders_a_string_nothing_reads(template_id: str) -> None:
    """The reverse: copy translated, paid for, and never shown.

    Catches the rename done in the template but not the JS, which the
    forward check cannot see.
    """
    template, template_keys = TEMPLATES[template_id]
    if template_id not in JS_CALLS:
        pytest.fail(f"{template.name} renders '{template_id}', but no module reads it.")
    module, js_keys = JS_CALLS[template_id]

    orphaned = template_keys - js_keys
    assert not orphaned, (
        f"{template.name} renders {sorted(orphaned)} for '{template_id}', but "
        f"{module.name} never reads them."
    )


class TestMakemessages:
    """The end-to-end claim: these strings now reach the catalogue."""

    @pytest.mark.skipif(
        shutil.which("xgettext") is None,
        reason=(
            "gettext is a local-only requirement — docs/i18n.md is explicit "
            "that CI and the build container do not install it."
        ),
    )
    def test_previously_invisible_strings_reach_the_po_file(
        self, tmp_path: Path
    ) -> None:
        """Run makemessages for real and look for the strings in the output.

        The static checks above prove the JS and the templates agree with
        each other. Only this proves the premise underneath them — that a
        string in a `<template>` is a string `makemessages` can see, which
        is the entire reason for the change.
        """
        locale_dir = tmp_path / "locale"
        locale_dir.mkdir()

        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "manage.py",
                "makemessages",
                "-l",
                "de",
                "--no-location",
                "--ignore",
                "node_modules",
                "--ignore",
                ".venv",
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
            env={
                "PATH": Path(sys.executable).parent.as_posix()
                + ":"
                + os.environ.get("PATH", ""),
                "DJANGO_SETTINGS_MODULE": "config.settings.development",
                "HOME": os.environ.get("HOME", ""),
            },
        )
        po_path = REPO_ROOT / "locale" / "de" / "LC_MESSAGES" / "django.po"
        try:
            assert result.returncode == 0, result.stderr
            catalogue = po_path.read_text(encoding="utf-8")

            # One string from each of the surfaces this ticket converted.
            for msgid in (
                "Not cached — view online first",  # map_layer_sync_status.js
                "Sign in to save a favourite.",  # favourites.js
                "Play season timelapse",  # map.js
                "Something went wrong — please try again.",  # report.js
                "A newer version of Snowdesk is ready.",  # sw_register.js
                "No syncs yet.",  # sync_log.js
                "No bulletin coverage for this location.",  # favourites_offline.js
                "Show map controls",  # map_controls_collapse.js
            ):
                assert msgid in catalogue, f"{msgid!r} did not reach the catalogue"
        finally:
            # makemessages writes into the repo's own LOCALE_PATHS; the de
            # catalogue is a scratch artefact of this test, not a tracked file.
            shutil.rmtree(po_path.parent.parent, ignore_errors=True)
