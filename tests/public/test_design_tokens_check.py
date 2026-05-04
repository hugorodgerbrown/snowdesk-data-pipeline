"""
tests/public/test_design_tokens_check.py — Tests for the design-token sync check.

The check (public/checks.py) keeps FOUNDATION_CATEGORIES in lockstep with
the @theme {} and .dark {} blocks in src/css/main.css. These tests cover
the parser internals (so a future formatting change in main.css doesn't
silently break the check) and a behavioural integration test against a
synthetic CSS file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings

from public import checks
from public.design_tokens import FoundationCategory, IconToken, Token


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
    fake_css_dir: Path, monkeypatch
) -> None:
    """Happy path: identical values → no errors."""
    _write_css(fake_css_dir, "--color-x: #fff;", "--color-x: #000;")
    monkeypatch.setattr(
        "public.checks.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", "#000")),
        raising=False,
    )
    monkeypatch.setattr("public.design_tokens.FOUNDATION_CATEGORIES", (), raising=False)
    # ``check_design_tokens_match_css`` re-imports from ``public.design_tokens``
    # at call time, so patch that module instead.
    monkeypatch.setattr(
        "public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", "#000")),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == []


def test_check_flags_missing_token_in_theme(fake_css_dir: Path, monkeypatch) -> None:
    """Token in registry but missing from @theme → E002."""
    _write_css(fake_css_dir, "--other: 1;", "")
    monkeypatch.setattr(
        "public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--missing", "M", "#fff", None)),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "public.design_tokens.E002"


def test_check_flags_light_value_drift(fake_css_dir: Path, monkeypatch) -> None:
    """Token light value differs → E003."""
    _write_css(fake_css_dir, "--color-x: #fff;", "")
    monkeypatch.setattr(
        "public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#000", None)),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "public.design_tokens.E003"


def test_check_flags_unexpected_dark_override(fake_css_dir: Path, monkeypatch) -> None:
    """Token marked theme-invariant but appears in .dark {} → E004."""
    _write_css(fake_css_dir, "--color-x: #fff;", "--color-x: #000;")
    monkeypatch.setattr(
        "public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", None)),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "public.design_tokens.E004"


def test_check_flags_missing_dark_override(fake_css_dir: Path, monkeypatch) -> None:
    """Token has dark value in registry but no .dark {} entry → E005."""
    _write_css(fake_css_dir, "--color-x: #fff;", "")
    monkeypatch.setattr(
        "public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", "#000")),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "public.design_tokens.E005"


def test_check_flags_dark_value_drift(fake_css_dir: Path, monkeypatch) -> None:
    """Token dark value differs → E006."""
    _write_css(fake_css_dir, "--color-x: #fff;", "--color-x: #111;")
    monkeypatch.setattr(
        "public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(Token("--color-x", "X", "#fff", "#000")),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "public.design_tokens.E006"


def test_check_flags_missing_css_file(tmp_path: Path, monkeypatch) -> None:
    """Missing src/css/main.css → E001 with a helpful path."""
    monkeypatch.setattr("public.design_tokens.FOUNDATION_CATEGORIES", (), raising=False)
    with override_settings(BASE_DIR=str(tmp_path)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "public.design_tokens.E001"


def test_check_skips_icon_tokens(fake_css_dir: Path, monkeypatch) -> None:
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
        "public.design_tokens.FOUNDATION_CATEGORIES", (category,), raising=False
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == []


def test_check_normalises_whitespace_in_values(fake_css_dir: Path, monkeypatch) -> None:
    """Cosmetic whitespace differences don't trigger drift errors."""
    _write_css(
        fake_css_dir,
        "--font-sans: 'DM Sans',  system-ui,  sans-serif;",
        "",
    )
    monkeypatch.setattr(
        "public.design_tokens.FOUNDATION_CATEGORIES",
        _patched_categories(
            Token("--font-sans", "Sans", "'DM Sans', system-ui, sans-serif", None)
        ),
        raising=False,
    )
    with override_settings(BASE_DIR=str(fake_css_dir)):
        errors = checks.check_design_tokens_match_css(app_configs=None)
    assert errors == []
