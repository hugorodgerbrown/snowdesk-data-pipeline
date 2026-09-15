"""
tests/public/test_design_tokens_check.py — Tests for the design-token sync check.

The check (apps/public/checks.py) keeps FOUNDATION_CATEGORIES in lockstep with
the @theme {} and .dark {} blocks in src/css/main.css. These tests cover
the parser internals (so a future formatting change in main.css doesn't
silently break the check) and a behavioural integration test against a
synthetic CSS file.

Since SNOW-969 that lockstep runs both ways on EXISTENCE: a --color-* in
@theme must be registered or exempted (E007), and an exemption must carry
a reason (E008). The synthetic-registry tests below cover both, and one
test at the bottom runs the real registry against the real main.css —
the only assertion here that fails when someone adds a colour and forgets
the library.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings

from apps.public import checks
from apps.public.design_tokens import (
    FoundationCategory,
    IconToken,
    Token,
    TokenExemption,
)


def test_strip_comments_removes_block_comments() -> None:
    """Block comments are removed; everything else is preserved."""
    assert checks._strip_comments("a /* drop */ b") == "a  b"
    assert checks._strip_comments("/* multi\nline */x") == "x"


def test_extract_block_walks_braces() -> None:
    """The block extractor returns the body of the matching ``{...}``."""
    css = "@theme { --a: 1; } .other { x: y; }"
    assert checks._extract_block(css, "@theme").strip() == "--a: 1;"
    assert checks._extract_block(css, ".other").strip() == "x: y;"


def test_extract_block_returns_empty_when_missing() -> None:
    """No block → empty string (not an error)."""
    assert checks._extract_block("nothing here", "@theme") == ""


def test_extract_tokens_parses_declarations() -> None:
    """Each ``--name: value;`` declaration becomes a dict entry."""
    block = "--color-bg: #fff; --font-sans: 'DM Sans', sans-serif;"
    tokens = checks._extract_tokens(block)
    assert tokens["--color-bg"] == "#fff"
    assert tokens["--font-sans"] == "'DM Sans', sans-serif"


def test_normalise_collapses_internal_whitespace() -> None:
    """Cosmetic whitespace differences shouldn't show up as drift."""
    assert checks._normalise("a  b   c") == "a b c"
    assert checks._normalise(" a b ") == "a b"


@pytest.fixture()
def fake_css_dir(tmp_path: Path) -> Path:
    """Materialise a fake project directory with a src/css/main.css inside."""
    css_dir = tmp_path / "src" / "css"
    css_dir.mkdir(parents=True)
    return tmp_path


def _write_css(base_dir: Path, theme: str, dark: str) -> None:
    """Write a synthetic main.css to ``base_dir``."""
    (base_dir / "src" / "css" / "main.css").write_text(
        f"@theme {{\n{theme}\n}}\n\n.dark {{\n{dark}\n}}\n",
        encoding="utf-8",
    )


def _patched_categories(*tokens: Token) -> tuple[FoundationCategory, ...]:
    """Build a single-category registry with the given tokens."""
    return (
        FoundationCategory(
            slug="t",
            label="Test",
            description="Test",
            kind="swatches",
            tokens=tokens,
        ),
    )


def test_check_passes_when_registry_matches_css(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: identical values → no errors."""
    _write_css(fake_css_dir, "--color-x: #fff;", "--color-x: #000;")
    monkeypatch.setattr(
        "apps.public.checks.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", "#000")),
        raising=False,
    )
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES", (), raising=False
    )
    # ``check_design_tokens_match_css`` re-imports from ``apps.public.design_tokens``
    # at call time, so patch that module instead.
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", "#000")),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == []


def test_check_flags_missing_token_in_theme(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token in registry but missing from @theme → E002."""
    _write_css(fake_css_dir, "--other: 1;", "")
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--missing", "M", "#fff", None)),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "apps.public.design_tokens.E002"


def test_check_flags_light_value_drift(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token light value differs → E003."""
    _write_css(fake_css_dir, "--color-x: #fff;", "")
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#000", None)),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "apps.public.design_tokens.E003"


def test_check_flags_unexpected_dark_override(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token marked theme-invariant but appears in .dark {} → E004."""
    _write_css(fake_css_dir, "--color-x: #fff;", "--color-x: #000;")
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", None)),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "apps.public.design_tokens.E004"


def test_check_flags_missing_dark_override(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token has dark value in registry but no .dark {} entry → E005."""
    _write_css(fake_css_dir, "--color-x: #fff;", "")
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", "#000")),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "apps.public.design_tokens.E005"


def test_check_flags_dark_value_drift(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token dark value differs → E006."""
    _write_css(fake_css_dir, "--color-x: #fff;", "--color-x: #111;")
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", "#000")),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "apps.public.design_tokens.E006"


def test_check_flags_missing_css_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing src/css/main.css → E001 with a helpful path."""
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES", (), raising=False
    )
    with override_settings(BASE_DIR=str(tmp_path)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "apps.public.design_tokens.E001"


def test_check_skips_icon_tokens(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IconToken entries are static assets, not CSS — the check ignores them.

    A category mixing Token and IconToken should validate the Token side
    and pass even when the IconToken's ``name`` is not present in @theme.
    """
    _write_css(fake_css_dir, "--color-x: #fff;", "")
    category = FoundationCategory(
        slug="icons",
        label="Icons",
        description="d",
        kind="icons",
        tokens=(
            Token("--color-x", "X", "#fff", None),
            IconToken("favicon", "Default", "favicon.svg", "Favicon"),
        ),
    )
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES", (category,), raising=False
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == []


def test_check_normalises_whitespace_in_values(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cosmetic whitespace differences don't trigger drift errors."""
    _write_css(
        fake_css_dir,
        "--font-sans: 'DM Sans',  system-ui,  sans-serif;",
        "",
    )
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(
            Token("--font-sans", "Sans", "'DM Sans', system-ui, sans-serif", None)
        ),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == []


# ── Reverse check: @theme → registry (SNOW-969) ───────────────────────────────


def _patch_registry(
    monkeypatch: pytest.MonkeyPatch,
    tokens: tuple[Token, ...] = (),
    exemptions: tuple[TokenExemption, ...] = (),
) -> None:
    """Point the check at a synthetic registry and exemption list.

    Args:
        monkeypatch: pytest's patcher.
        tokens: Tokens the synthetic single category carries.
        exemptions: Exemptions the check should read instead of the real set.

    """
    monkeypatch.setattr(
        "apps.public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(*tokens),
        raising=False,
    )
    monkeypatch.setattr(
        "apps.public.design_tokens.TOKEN_EXEMPTIONS", exemptions, raising=False
    )


def test_exemption_matches_an_exact_name() -> None:
    """A pattern with no trailing ``*`` matches that one name and no other."""
    exemption = TokenExemption("--color-admin-bg", "admin chrome")
    assert exemption.matches("--color-admin-bg")
    assert not exemption.matches("--color-admin-bg-hover")
    assert not exemption.matches("--color-admin")


def test_exemption_matches_a_family_by_prefix() -> None:
    """A trailing ``*`` covers every name beginning with the prefix."""
    exemption = TokenExemption("--color-basemap-*", "one per BASEMAP_STYLES key")
    assert exemption.matches("--color-basemap-ign-plan")
    assert exemption.matches("--color-basemap-anything-added-tomorrow")
    assert not exemption.matches("--color-base")


def test_check_flags_colour_declared_in_theme_but_not_registered(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A --color-* in @theme that nothing lists → E007.

    This is the case SNOW-969 exists for: a colour added to the CSS,
    used on the map, and invisible at /_components/.
    """
    _write_css(fake_css_dir, "--color-route-line: #c026d3;", "")
    _patch_registry(monkeypatch)
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "apps.public.design_tokens.E007"
    assert "--color-route-line" in errors[0].msg


def test_check_passes_when_the_unregistered_colour_is_exempted(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exemption naming the token silences E007."""
    _write_css(fake_css_dir, "--color-admin-bg: #f9fafb;", "")
    _patch_registry(
        monkeypatch,
        exemptions=(
            TokenExemption("--color-admin-bg", "Django admin chrome, not public"),
        ),
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == []


def test_check_passes_when_a_family_is_exempted_by_prefix(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One prefix exemption covers a family that grows a member."""
    _write_css(
        fake_css_dir,
        "--color-basemap-ign-plan: #9333ea; --color-basemap-new-one: #123456;",
        "",
    )
    _patch_registry(
        monkeypatch,
        exemptions=(
            TokenExemption("--color-basemap-*", "one per settings.BASEMAP_STYLES key"),
        ),
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == []


def test_check_rejects_an_exemption_with_no_reason(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank (or whitespace-only) reason → E008, even though it silences E007.

    The reason is the whole contract — ``bin/ds-lint``'s per-line allows
    work the same way. A vague reason is a review problem; an absent one
    is a build failure.
    """
    _write_css(fake_css_dir, "--color-admin-bg: #f9fafb;", "")
    _patch_registry(
        monkeypatch, exemptions=(TokenExemption("--color-admin-bg", "   "),)
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert [error.id for error in errors] == ["apps.public.design_tokens.E008"]


def test_reverse_check_ignores_non_colour_namespaces(
    fake_css_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only --color-* is reverse-checked; sizes and z-indices are not.

    A spacing token is a mechanical value nobody browses the library to
    choose, so requiring a registry edit for one would tax every tweak
    without protecting anything.
    """
    _write_css(
        fake_css_dir,
        "--text-sheet-title: 22px; --z-toast: 50; --shadow-glass: 0 2px 14px #000;",
        "",
    )
    _patch_registry(monkeypatch)
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == []


def test_the_real_registry_accounts_for_every_colour_in_main_css() -> None:
    """The live registry + exemptions cover every --color-* in main.css.

    The other tests here prove the check works; this one is the check
    running for real, and is what fails when a colour is added to
    @theme and left out of the library.
    """
    errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == [], [error.msg for error in errors]
