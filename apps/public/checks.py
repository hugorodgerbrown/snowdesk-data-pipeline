"""
apps/public/checks.py — Django system checks for the public app.

Currently hosts a single check that keeps ``apps/public/design_tokens.py`` and
``src/css/main.css`` from drifting apart. The Python registry is the
component-library's source of truth for *what to render*, but the CSS file
is the source of truth for *what those tokens actually resolve to* at
runtime. If the two ever disagree, the design-system page would be lying
about the live values; this check fails fast at ``manage.py check`` time
rather than letting the lie ship.

On VALUES the check stays one-directional, and deliberately so: the CSS
is what the browser reads, so it is the source of truth for what a token
resolves to, and the registry is what has to follow.

On EXISTENCE it runs both ways (SNOW-969). It used to be one-directional
there too — the CSS was free to declare a colour the library never listed
— which let ``--color-route-line``, ``--color-accent`` and 29 others be
added to ``@theme``, mirrored into a MapLibre paint literal, drawn and
shipped while staying invisible on the page that claims to be the
complete account of the design system. Nothing else in the build noticed,
and the page a reviewer reads before picking the next colour was reading
short. So every ``--color-*`` in ``@theme {}`` must now be either
registered in ``FOUNDATION_CATEGORIES`` or listed in
``TOKEN_EXEMPTIONS`` with a reason.

An exemption is how a token opts out of being BROWSABLE without opting
out of being CHECKED: an exempted name is still value-checked if it is
registered later, and the exemption itself must carry a reason — same
contract as ``bin/ds-lint``'s per-line allow comments, where a blank
reason fails the build and a vague one fails review.

The reverse check covers ``--color-*`` only. The other namespaces
(``--text-*``, ``--z-*``, ``--shadow-*``, ``--container-*``) hold
mechanical values a reviewer does not browse for a decision, and pulling
them in would turn every spacing tweak into a registry edit; a colour is
the thing that gets chosen, mirrored into JavaScript and then lost.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import Error, Tags, register

if TYPE_CHECKING:  # pragma: no cover — import cycle at runtime, not at type time
    from apps.public.design_tokens import TokenExemption

CSS_PATH = Path("src") / "css" / "main.css"

# E001 — CSS file missing.
# E002 — token in registry not declared in @theme {}.
# E003 — token light-value mismatch between registry and @theme {}.
# E004 — token marked theme-invariant in registry but declared in .dark {}.
# E005 — token has dark value in registry but missing from .dark {}.
# E006 — token dark-value mismatch between registry and .dark {}.
# E007 — --color-* declared in @theme {} but neither registered nor exempted.
# E008 — exemption declared with a blank reason.
CHECK_ID_PREFIX = "apps.public.design_tokens"

# The reverse check's scope — see the module docstring for why it is
# colours only.
COLOUR_PREFIX = "--color-"


@register(Tags.compatibility)
def check_design_tokens_match_css(app_configs: Any, **kwargs: Any) -> list[Error]:
    """Verify every token in ``FOUNDATION_CATEGORIES`` matches ``main.css``.

    Errors include the offending token name and both the registry value
    and the CSS value, so the fix is mechanical (copy/paste either side).
    """
    from apps.public.design_tokens import (
        FOUNDATION_CATEGORIES,
        TOKEN_EXEMPTIONS,
        Token,
    )

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
    registered: set[str] = set()
    for category in FOUNDATION_CATEGORIES:
        # IconToken entries don't map to CSS custom properties — they're
        # static-asset paths, validated by Django's collectstatic, not here.
        for token in category.tokens:
            if not isinstance(token, Token):
                continue
            registered.add(token.name)
            errors.extend(_diff_token(token, category.slug, light_tokens, dark_tokens))

    errors.extend(_unregistered_colours(light_tokens, registered, TOKEN_EXEMPTIONS))
    return errors


def _unregistered_colours(
    light_tokens: dict[str, str],
    registered: set[str],
    exemptions: "tuple[TokenExemption, ...]",
) -> list[Error]:
    """Return an error per ``--color-*`` in the CSS that the registry omits.

    Args:
        light_tokens: Every declaration parsed out of ``@theme {}``.
        registered: Names carried by ``FOUNDATION_CATEGORIES``.
        exemptions: ``TokenExemption`` entries, each a name or a
            ``*``-suffixed prefix plus the reason it is not browsable.

    """
    errors: list[Error] = [
        Error(
            f"Token exemption {exemption.pattern!r} carries no reason",
            hint=(
                "Every entry in TOKEN_EXEMPTIONS says why the token is not "
                "browsable at /_components/ — a reviewer has to be able to "
                "judge whether the omission still holds."
            ),
            id=f"{CHECK_ID_PREFIX}.E008",
        )
        for exemption in exemptions
        if not exemption.reason.strip()
    ]

    for name in sorted(light_tokens):
        if not name.startswith(COLOUR_PREFIX) or name in registered:
            continue
        if any(exemption.matches(name) for exemption in exemptions):
            continue
        errors.append(
            Error(
                f"{name}: declared in @theme {{}} in {CSS_PATH} but neither "
                "registered in FOUNDATION_CATEGORIES nor exempted",
                hint=(
                    "Add it to a category in design_tokens.py so it shows at "
                    "/_components/, or add a TokenExemption with the reason "
                    "it should not."
                ),
                id=f"{CHECK_ID_PREFIX}.E007",
            )
        )
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
