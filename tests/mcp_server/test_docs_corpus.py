"""
tests/mcp_server/test_docs_corpus.py — Tests for apps.mcp_server.docs_corpus (SNOW-1393).

The readers (Markdown sections, HTML text, the help topics), the BM25
search and the two corpora's split: the public one holds only what a
rider is already shown, the ops one everything else on top.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from django.test import override_settings

from apps.mcp_server import docs_corpus
from apps.mcp_server.docs_corpus import (
    Corpus,
    Doc,
    Section,
    html_text,
    markdown_sections,
    snippet,
    tokenise,
)


@pytest.fixture(autouse=True)
def fresh_corpora() -> Iterator[None]:
    """Rebuild the cached corpora around each test that changes BASE_DIR."""
    docs_corpus.public_corpus.cache_clear()
    docs_corpus.ops_corpus.cache_clear()
    yield
    docs_corpus.public_corpus.cache_clear()
    docs_corpus.ops_corpus.cache_clear()


def _doc(doc_id: str, *sections: tuple[str, str], kind: str = "doc") -> Doc:
    """Build a doc from (heading, text) pairs."""
    return Doc(
        id=doc_id,
        title=doc_id.title(),
        description="",
        kind=kind,
        status="current",
        sections=tuple(Section(h, t) for h, t in sections),
    )


class TestTokenise:
    """Search terms are folded, de-pluralised and stopword-free."""

    def test_folds_accents_and_case(self) -> None:
        """Météo-France finds meteo france."""
        assert tokenise("Météo-France") == ["meteo", "france"]

    def test_drops_stopwords_and_a_plural_s(self) -> None:
        """'How does the map draw routes' ranks on its nouns and verbs."""
        assert tokenise("How does the map draw routes") == ["map", "draw", "route"]

    def test_keeps_a_double_s(self) -> None:
        """'pass' is not a plural."""
        assert tokenise("pass passes") == ["pass", "passe"]


class TestMarkdownSections:
    """Markdown splits at its first three heading levels."""

    def test_splits_and_titles(self) -> None:
        """The # heading titles the doc; text before ## is its own section."""
        title, sections = markdown_sections(
            "# Doc\n\nIntro.\n\n## One\n\nFirst.\n\n### Two\n\nSecond.\n\n#### Deep\n\nStill two."
        )
        assert title == "Doc"
        assert [(s.heading, s.text) for s in sections] == [
            ("Doc", "Intro."),
            ("One", "First."),
            ("Two", "Second.\n\n#### Deep\n\nStill two."),
        ]

    def test_ignores_headings_in_code_fences(self) -> None:
        """A '# comment' in a fenced block is code."""
        _, sections = markdown_sections("# Doc\n\n```bash\n# not a heading\n```\n")
        assert len(sections) == 1
        assert "# not a heading" in sections[0].text


class TestHtmlText:
    """HTML reduces to prose sections, figures and scripts dropped."""

    def test_reads_title_description_and_sections(self) -> None:
        """h1 titles, h2 sections, svg/script dropped, side-by-side spans spaced."""
        page = html_text(
            "<html><head><title>Page</title>"
            '<meta name="description" content="About it."></head><body>'
            "<h1>Big title</h1><p>Lead.</p><script>var x = 1;</script>"
            "<h2>Part</h2><svg><text>99°</text></svg>"
            "<ul><li><span>Label</span><span>Value</span></li></ul></body></html>"
        )
        assert page.title == "Page"
        assert page.description == "About it."
        assert [(s.heading, s.text) for s in page.sections] == [
            ("Big title", "Lead."),
            ("Part", "Label Value"),
        ]


def test_an_svg_title_is_not_the_page_title() -> None:
    """A figure's <title> before the page's h1 does not title the page."""
    page = html_text(
        "<body><svg><title>Tooltip</title></svg><h1>Real</h1><p>x</p></body>"
    )
    assert page.title == "Real"


class TestSearch:
    """BM25 over sections."""

    corpus = Corpus(
        "t",
        {
            "a": _doc("a", ("Offline", "Download an area to read it offline.")),
            "b": _doc("b", ("Routes", "Upload a GPX route."), kind="help"),
            "c": _doc(
                "c",
                ("One", "offline offline"),
                ("Two", "offline"),
                ("Three", "offline"),
            ),
        },
    )

    def test_ranks_the_heading_match_first(self) -> None:
        """A section headed by the term outranks one that only mentions it."""
        hits = self.corpus.search("offline")
        assert hits[0].doc.id == "a"

    def test_caps_hits_per_doc_at_two(self) -> None:
        """One doc cannot fill the page."""
        hits = self.corpus.search("offline")
        assert sum(1 for hit in hits if hit.doc.id == "c") == 2

    def test_filters_by_kind(self) -> None:
        """kind narrows the hits."""
        assert [h.doc.id for h in self.corpus.search("gpx route", kind="help")] == ["b"]
        assert self.corpus.search("offline", kind="help") == []

    def test_empty_query_finds_nothing(self) -> None:
        """A query of stopwords has no terms."""
        assert self.corpus.search("how is the") == []

    def test_resolves_ids_paths_uris_and_unique_slugs(self) -> None:
        """Every spelling of a doc reaches it; an ambiguous slug reaches none."""
        corpus = Corpus(
            "t",
            {
                "docs/x": _doc("docs/x", ("X", "x")),
                "docs/decisions/x": _doc("docs/decisions/x", ("X", "x")),
                "docs/y": _doc("docs/y", ("Y", "y")),
            },
        )
        assert corpus.resolve("docs/y.md") is corpus.docs["docs/y"]
        assert corpus.resolve("docs://docs/y") is corpus.docs["docs/y"]
        assert corpus.resolve("y") is corpus.docs["docs/y"]
        assert corpus.resolve("x") is None
        assert corpus.resolve("nope") is None


class TestSnippet:
    """A snippet is the paragraph holding the most query terms."""

    def test_picks_the_best_paragraph(self) -> None:
        """The paragraph with both terms wins."""
        text = "Nothing here.\n\nThe map draws routes."
        assert snippet(text, ["map", "route"]) == "The map draws routes."

    def test_cuts_a_long_paragraph_round_the_first_term(self) -> None:
        """A long paragraph is cut near the term and ellipsised."""
        text = "word " * 100 + "needle " + "word " * 100
        cut = snippet(text, ["needle"])
        assert cut.startswith("…") and cut.endswith("…") and "needle" in cut

    def test_no_trailing_ellipsis_when_the_cut_reaches_the_end(self) -> None:
        """A term near the end is cut from the left only."""
        text = "word " * 100 + "needle"
        cut = snippet(text, ["needle"])
        assert cut.startswith("…") and cut.endswith("needle")

    def test_empty_text(self) -> None:
        """No text, no snippet."""
        assert snippet("", ["x"]) == ""


class TestRealCorpora:
    """The corpora built from this checkout."""

    def test_public_holds_help_the_guide_and_explainers_only(self) -> None:
        """Nothing written for builders reaches the public corpus."""
        corpus = docs_corpus.public_corpus()
        assert {doc.kind for doc in corpus.docs.values()} == {
            "help",
            "guide",
            "explainer",
        }
        assert "help/overview" in corpus.docs
        assert "docs/bulletin-guide" in corpus.docs
        assert not any(doc_id.startswith("docs/decisions/") for doc_id in corpus.docs)

    def test_public_leaves_out_the_flag_gated_help_topics(self) -> None:
        """what3words and the sync log are not shown to every reader."""
        ids = docs_corpus.public_corpus().docs
        assert "help/what3words" not in ids
        assert "help/sync-log" not in ids
        # SNOW-1394: the Dated / Terrain toggle's topic waits for its rollout.
        assert "help/map-modes" not in ids

    def test_help_topics_follow_the_page(self) -> None:
        """Every ungated topic template on the help page is in the corpus, rendered."""
        root = Path(docs_corpus.settings.BASE_DIR)
        templates = root / "apps/public/templates/public/help"
        expected = {
            path.stem.removeprefix("_topic_").replace("_", "-")
            for path in templates.glob("_topic_*.html")
        } - docs_corpus.GATED_HELP_TOPICS
        help_docs = [
            d for d in docs_corpus.public_corpus().docs.values() if d.kind == "help"
        ]
        assert {d.id.removeprefix("help/") for d in help_docs} == expected
        for doc in help_docs:
            assert doc.sections[0].text
            assert "{%" not in doc.sections[0].text
            assert doc.url.startswith("/help/#help-topic-")

    def test_ops_is_a_superset_without_the_dated_folders(self) -> None:
        """Ops adds the builders' docs and never the dated artefacts."""
        public = docs_corpus.public_corpus()
        ops = docs_corpus.ops_corpus()
        assert set(public.docs) < set(ops.docs)
        assert "AGENTS" in ops.docs
        assert "CLAUDE" in ops.docs
        assert ops.docs["AGENTS"].kind == "rulebook"
        assert "apps/bulletins/CLAUDE" in ops.docs
        assert ops.docs["docs/decisions/the-sheet-is-the-page"].kind == "decision"
        assert ops.docs["docs/runbooks/reset-live-db"].kind == "runbook"
        assert not any(
            doc_id.split("/")[1] in docs_corpus.DATED_FOLDERS
            for doc_id in ops.docs
            if doc_id.startswith("docs/") and doc_id.count("/") > 1
        )

    def test_a_historical_decision_says_so(self) -> None:
        """Status comes from frontmatter."""
        ops = docs_corpus.ops_corpus()
        assert (
            ops.docs["docs/decisions/the-bell-lists-your-alerts"].status == "historical"
        )


class TestFrontmatterFallbacks:
    """A doc with broken or missing frontmatter still reads."""

    def test_broken_yaml_and_no_title(self, tmp_path: Path) -> None:
        """Bad YAML is ignored; a doc with no # heading is titled by its path."""
        path = tmp_path / "docs" / "x.md"
        path.parent.mkdir()
        path.write_text("---\nname: [unclosed\n---\nJust text.\n", encoding="utf-8")
        doc = docs_corpus.read_markdown(path, tmp_path, "doc")
        assert doc.title == "docs/x"
        assert doc.description == ""
        assert doc.sections[0].text == "Just text."

    def test_unterminated_frontmatter_is_body(self, tmp_path: Path) -> None:
        """An opening --- with no close is read as text."""
        path = tmp_path / "x.md"
        path.write_text("---\nnot closed\n", encoding="utf-8")
        assert (
            "not closed"
            in docs_corpus.read_markdown(path, tmp_path, "doc").sections[0].text
        )

    def test_ops_skips_a_missing_root_claude_md(self, tmp_path: Path) -> None:
        """A checkout without a file the corpus names still builds."""
        (tmp_path / "apps" / "public" / "templates" / "public").mkdir(parents=True)
        (tmp_path / "apps/public/templates/public/help.html").write_text(
            "", encoding="utf-8"
        )
        (tmp_path / "docs" / "explainers").mkdir(parents=True)
        (tmp_path / "docs/bulletin-guide.md").write_text(
            "# Guide\n\nRead it.", encoding="utf-8"
        )
        (tmp_path / "docs/qa").mkdir()
        (tmp_path / "docs/qa/old.md").write_text("# Old", encoding="utf-8")
        with override_settings(BASE_DIR=tmp_path):
            ops = docs_corpus.ops_corpus()
        assert set(ops.docs) == {"docs/bulletin-guide"}
