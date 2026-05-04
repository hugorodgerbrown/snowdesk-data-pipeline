"""
public/checks.py — Django system checks for the public app.

Currently hosts a single check that keeps ``public/design_tokens.py`` and
``src/css/main.css`` from drifting apart. The Python registry is the
component-library's source of truth for *what to render*, but the CSS file
is the source of truth for *what those tokens actually resolve to* at
runtime. If the two ever disagree, the design-system page would be lying
about the live values; this check fails fast at ``manage.py check`` time
rather than letting the lie ship.

The check is one-directional: every token in the registry must exist in
the CSS with a matching value. The CSS is allowed to declare tokens that
are not surfaced in the library (admin chrome, callouts, chip overlays,
etc.) — those don't trigger errors here.
"""

import re
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.checks import Error, Tags, register

CSS_PATH = Path("src") / "css" / "main.css"

# E001 — CSS file missing.
# E002 — token in registry not declared in @theme {}.
# E003 — token light-value mismatch between registry and @theme {}.
# E004 — token marked theme-invariant in registry but declared in .dark {}.
# E005 — token has dark value in registry but missing from .dark {}.
# E006 — token dark-value mismatch between registry and .dark {}.
CHECK_ID_PREFIX = "public.design_tokens"


@register(Tags.compatibility)
def check_design_tokens_match_css(app_configs: Any, **kwargs: Any) -> list[Error]:
    """Verify every token in ``FOUNDATION_CATEGORIES`` matches ``main.css``.

    Errors include the offending token name and both the registry value
    and the CSS value, so the fix is mechanical (copy/paste either side).
    """
    from public.design_tokens import FOUNDATION_CATEGORIES, Token

    css_file = Path(settings.BASE_DIR) / CSS_PATH
    if not css_file.exists():
        return [
            Error(
                f"Design-token CSS file not found at {css_file}",
                hint=(
                    "The design_tokens registry sync check expects "
                    f"{CSS_PATH} to exist relative to BASE_DIR."
                ),
                id=f"{CHECK_ID_PREFIX}.E001",
            )
        ]

    raw = _strip_comments(css_file.read_text(encoding="utf-8"))
    light_tokens = _extract_tokens(_extract_block(raw, "@theme"))
    dark_tokens = _extract_tokens(_extract_block(raw, ".dark"))

    errors: list[Error] = []
    for category in FOUNDATION_CATEGORIES:
        # IconToken entries don't map to CSS custom properties — they're
        # static-asset paths, validated by Django's collectstatic, not here.
        for token in category.tokens:
            if not isinstance(token, Token):
                continue
            errors.extend(_diff_token(token, category.slug, light_tokens, dark_tokens))
    return errors


def _diff_token(
    token: Any,
    category_slug: str,
    light_tokens: dict[str, str],
    dark_tokens: dict[str, str],
) -> list[Error]:
    """Return any drift errors between one registry ``token`` and the CSS."""
    errors: list[Error] = []
    label = f"[{category_slug}] {token.name}"

    actual_light = light_tokens.get(token.name)
    if actual_light is None:
        errors.append(
            Error(
                f"{label}: declared in design_tokens.py but missing from "
                f"@theme {{}} in {CSS_PATH}",
                hint=(
                    "Either add the token to @theme in main.css, or remove "
                    "it from FOUNDATION_CATEGORIES."
                ),
                id=f"{CHECK_ID_PREFIX}.E002",
            )
        )
    elif _normalise(actual_light) != _normalise(token.light):
        errors.append(
            Error(
                f"{label}: light-value drift — "
                f"registry={token.light!r} css={actual_light!r}",
                hint="Update design_tokens.py to match the CSS, or vice versa.",
                id=f"{CHECK_ID_PREFIX}.E003",
            )
        )

    actual_dark = dark_tokens.get(token.name)
    if token.dark is None:
        if actual_dark is not None:
            errors.append(
                Error(
                    f"{label}: marked theme-invariant in registry "
                    f"(dark=None) but declared in .dark {{}} as "
                    f"{actual_dark!r}",
                    hint=(
                        "Set the token's dark value in design_tokens.py, or "
                        "remove the .dark override in main.css."
                    ),
                    id=f"{CHECK_ID_PREFIX}.E004",
                )
            )
    else:
        if actual_dark is None:
            errors.append(
                Error(
                    f"{label}: declares dark={token.dark!r} but no "
                    f".dark {{}} override exists in {CSS_PATH}",
                    hint=(
                        "Add the override in main.css, or set dark=None in "
                        "design_tokens.py to mark the token theme-invariant."
                    ),
                    id=f"{CHECK_ID_PREFIX}.E005",
                )
            )
        elif _normalise(actual_dark) != _normalise(token.dark):
            errors.append(
                Error(
                    f"{label}: dark-value drift — "
                    f"registry={token.dark!r} css={actual_dark!r}",
                    hint="Update design_tokens.py to match the CSS, or vice versa.",
                    id=f"{CHECK_ID_PREFIX}.E006",
                )
            )
    return errors


def _strip_comments(css: str) -> str:
    """Remove ``/* ... */`` blocks so commented-out tokens don't get parsed."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _extract_block(css: str, selector: str) -> str:
    """Return the body of the first ``{ ... }`` group following ``selector``.

    Walks braces so nested at-rules inside the block (none today, but cheap
    insurance) don't truncate the match early.
    """
    pattern = re.escape(selector) + r"\s*\{"
    match = re.search(pattern, css)
    if not match:
        return ""
    start = match.end()
    depth = 1
    i = start
    while i < len(css) and depth > 0:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i]
        i += 1
    return css[start:]


_DECLARATION_RE = re.compile(r"(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);")


def _extract_tokens(block: str) -> dict[str, str]:
    """Parse ``--name: value;`` declarations into a dict, last-wins."""
    return {m.group(1): m.group(2).strip() for m in _DECLARATION_RE.finditer(block)}


def _normalise(value: str) -> str:
    """Collapse internal whitespace so cosmetic CSS spacing isn't a diff."""
    return re.sub(r"\s+", " ", value).strip()
