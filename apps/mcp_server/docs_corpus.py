"""
apps/mcp_server/docs_corpus.py — the documentation the MCP servers can read (SNOW-1393).

Lets an LLM answer "how does Snowdesk work?" without the codebase. Two
corpora, one engine, split by who is asking:

* :func:`public_corpus` — what a rider is already shown: the /help/ topics,
  rendered from their own templates (the two flag-gated topics are left
  out, as the page leaves them out for a reader without the flag), the
  bulletin guide (``docs/bulletin-guide.md``) and the explainers under
  ``docs/explainers/``, which are written for readers with no repo access.
  Served at ``POST /api/mcp/``.
* :func:`ops_corpus` — the public corpus plus everything written for the
  people building the app: every ``docs/**/*.md`` outside the dated
  artefact folders, the root ``CLAUDE.md`` and each app's ``CLAUDE.md``.
  Served at the staff-only ``POST /api/ops/mcp/``.

Both are read from the files the running build ships, once per process, so
the answer can never drift from the deployed docs. A doc is split into
sections at its ``#``/``##``/``###`` headings (``<h1>``–``<h3>`` for HTML)
and the sections are ranked with BM25 — no embeddings, no index on disk:
the corpus is a few megabytes and a search is a pure-Python loop.
"""

from __future__ import annotations

import functools
import logging
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation

logger = logging.getLogger(__name__)

#: ``docs/`` subfolders holding dated artefacts rather than living
#: documentation — the same four ``bin/docs-lint`` exempts (CLAUDE.md).
DATED_FOLDERS = frozenset({"code-reviews", "page-audits", "qa", "research"})

#: Help topics gated on a waffle flag. The help page renders them only for a
#: reader with the flag, so the public corpus — which has no reader — never
#: carries them (``apps.public.views.help_page``).
GATED_HELP_TOPICS = frozenset({"what3words", "sync-log"})

#: URI scheme of a doc listed as an MCP resource.
RESOURCE_SCHEME = "docs://"

#: MIME type of a doc's text in ``resources/read``.
RESOURCE_MIME_TYPE = "text/markdown"

_BM25_K1 = 1.2
_BM25_B = 0.75
#: A heading's words count this many times over the body's: a section
#: titled "Authentication" is about authentication.
_HEADING_WEIGHT = 3
_SNIPPET_CHARS = 280

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOPWORDS = frozenset(
    "a an and are as at be by does do for from how i in is it its of on or "
    "that the this to was what when where which who why will with".split()
)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HELP_TITLE_RE = re.compile(r'{%\s*translate\s+"([^"]+)"\s+as\s+(t_\w+)\s*%}')
_HELP_PANEL_RE = re.compile(
    r"{%\s*include\s+\"includes/_collapsible_panel\.html\"[^%]*?"
    r"title=(t_\w+)[^%]*?anchor_id=\"help-topic-([\w-]+)\"[^%]*?"
    r"body_template=\"([^\"]+)\""
)


@dataclass(frozen=True)
class Section:
    """One headed stretch of a doc — the unit search ranks."""

    heading: str
    text: str


@dataclass(frozen=True)
class Doc:
    """One document: its identity, its metadata and its sections."""

    id: str
    title: str
    description: str
    kind: str
    status: str
    sections: tuple[Section, ...]
    url: str = ""

    @property
    def uri(self) -> str:
        """The doc's MCP resource URI."""
        return f"{RESOURCE_SCHEME}{self.id}"

    def text(self) -> str:
        """Return the whole doc as Markdown, a heading per section."""
        parts = [f"# {self.title}"]
        for section in self.sections:
            if section.heading and section.heading != self.title:
                parts.append(f"## {section.heading}")
            if section.text:
                parts.append(section.text)
        return "\n\n".join(parts)

    def listing(self) -> dict[str, Any]:
        """Return the ``list_docs`` / ``resources/list`` metadata."""
        entry = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "status": self.status,
        }
        if self.url:
            entry["url"] = self.url
        return entry


@dataclass(frozen=True)
class DocResource:
    """A doc as an MCP resource, in the shape ``protocol`` reads."""

    doc: Doc

    def listing(self) -> dict[str, Any]:
        """Return the ``resources/list`` entry."""
        return {
            "uri": self.doc.uri,
            "name": self.doc.title,
            "description": self.doc.description,
            "mimeType": RESOURCE_MIME_TYPE,
        }

    def contents(self) -> dict[str, Any]:
        """Return the ``resources/read`` content item."""
        return {
            "uri": self.doc.uri,
            "mimeType": RESOURCE_MIME_TYPE,
            "text": self.doc.text(),
        }


@dataclass(frozen=True)
class Hit:
    """One ranked search result: a section of a doc."""

    doc: Doc
    section: Section
    score: float
    snippet: str


@dataclass
class Corpus:
    """A set of docs and the BM25 index over their sections."""

    name: str
    docs: dict[str, Doc]
    _postings: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    _units: list[tuple[Doc, Section]] = field(default_factory=list)
    _lengths: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Index every section of every doc."""
        for doc in self.docs.values():
            for section in doc.sections:
                counts = Counter(tokenise(section.text))
                for token in tokenise(f"{doc.title} {section.heading}"):
                    counts[token] += _HEADING_WEIGHT
                unit = len(self._units)
                self._units.append((doc, section))
                self._lengths.append(sum(counts.values()))
                for token, count in counts.items():
                    self._postings.setdefault(token, []).append((unit, count))

    def __str__(self) -> str:
        """Name the corpus and its size, for the log line."""
        return f"{self.name} ({len(self.docs)} docs)"

    def search(self, query: str, limit: int = 8, kind: str | None = None) -> list[Hit]:
        """Rank sections against ``query`` with BM25.

        Args:
            query: Free text.
            limit: The most hits to return.
            kind: Only docs of this kind, when given.

        Returns:
            Up to ``limit`` hits, best first, at most two per doc so one
            long doc cannot fill the page.

        """
        terms = list(dict.fromkeys(tokenise(query)))
        scores = self._scores(terms)
        hits: list[Hit] = []
        per_doc: Counter[str] = Counter()
        for unit, score in sorted(scores.items(), key=lambda item: -item[1]):
            doc, section = self._units[unit]
            if kind and doc.kind != kind:
                continue
            if per_doc[doc.id] >= 2:
                continue
            per_doc[doc.id] += 1
            hits.append(
                Hit(doc, section, round(score, 3), snippet(section.text, terms))
            )
            if len(hits) >= limit:
                break
        return hits

    def _scores(self, terms: list[str]) -> dict[int, float]:
        """Return each matching section's BM25 score, keyed by unit index.

        Args:
            terms: The query's distinct terms.

        Returns:
            Scores for the sections holding at least one term.

        """
        if not terms or not self._units:
            return {}
        average = sum(self._lengths) / len(self._lengths)
        total = len(self._units)
        scores: dict[int, float] = {}
        for term in terms:
            postings = self._postings.get(term, [])
            idf = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            for unit, count in postings:
                norm = _BM25_K1 * (
                    1 - _BM25_B + _BM25_B * self._lengths[unit] / average
                )
                gain = idf * count * (_BM25_K1 + 1) / (count + norm)
                scores[unit] = scores.get(unit, 0.0) + gain
        return scores

    def resolve(self, reference: str) -> Doc | None:
        """Find a doc by its id, its path, its URI or a unique slug.

        Args:
            reference: ``docs/mcp-server``, ``docs/mcp-server.md``,
                ``docs://docs/mcp-server`` or ``mcp-server``.

        Returns:
            The doc, or ``None`` when nothing (or more than one doc)
            matches.

        """
        ref = reference.strip().removeprefix(RESOURCE_SCHEME).strip("/")
        ref = re.sub(r"\.(md|html)$", "", ref)
        if ref in self.docs:
            return self.docs[ref]
        matches = [
            doc for key, doc in self.docs.items() if key.rsplit("/", 1)[-1] == ref
        ]
        return matches[0] if len(matches) == 1 else None

    @functools.cached_property
    def resources(self) -> dict[str, DocResource]:
        """Every doc as an MCP resource, keyed by URI."""
        return {doc.uri: DocResource(doc) for doc in self.docs.values()}


def tokenise(text: str) -> list[str]:
    """Lower-case, accent-fold and split ``text`` into search terms.

    A trailing plural ``s`` is dropped so "rules" finds "rule"; stopwords
    are dropped so "how does the map work" ranks on "map" and "work".

    Args:
        text: Any text.

    Returns:
        The terms, in order, duplicates kept.

    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    terms = []
    for token in _TOKEN_RE.findall(folded.lower()):
        if token in _STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        terms.append(token)
    return terms


def snippet(text: str, terms: Iterable[str]) -> str:
    """Return the stretch of ``text`` that holds the most query terms.

    Args:
        text: A section's text.
        terms: The query's terms, already tokenised.

    Returns:
        About :data:`_SNIPPET_CHARS` characters, ellipsised where cut.

    """
    wanted = set(terms)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return ""
    best = max(paragraphs, key=lambda p: len(wanted & set(tokenise(p))))
    flat = " ".join(best.split())
    if len(flat) <= _SNIPPET_CHARS:
        return flat
    lowered = flat.lower()
    starts = [lowered.find(term) for term in wanted if lowered.find(term) >= 0]
    start = max(0, min(starts) - 60) if starts else 0
    cut = flat[start : start + _SNIPPET_CHARS]
    return ("…" if start else "") + cut + "…"


# ---------------------------------------------------------------------------
# Readers — one per source shape
# ---------------------------------------------------------------------------


def _frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown file's YAML frontmatter from its body.

    Args:
        raw: The file's text.

    Returns:
        The frontmatter (empty when there is none, or it does not parse)
        and the body.

    """
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---", 4)
    if end < 0:
        return {}, raw
    try:
        meta = yaml.safe_load(raw[4:end]) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), raw[end + 4 :].lstrip("\n")


def markdown_sections(body: str) -> tuple[str, tuple[Section, ...]]:
    """Split Markdown at its first three heading levels.

    Headings inside fenced code are text, not headings. The first ``#``
    heading is the doc's title; text before any heading is a section
    headed by that title.

    Args:
        body: Markdown with the frontmatter already removed.

    Returns:
        The title (empty when the doc has no ``#`` heading) and the
        sections, empty ones dropped.

    """
    title = ""
    sections: list[Section] = []
    heading = ""
    lines: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING_RE.match(line)
        if match is None:
            lines.append(line)
            continue
        if len(match.group(1)) == 1 and not title:
            title = match.group(2)
        sections.append(Section(heading or title, "\n".join(lines).strip()))
        heading, lines = match.group(2), []
    sections.append(Section(heading or title, "\n".join(lines).strip()))
    return title, tuple(s for s in sections if s.text)


class _TextExtractor(HTMLParser):
    """Reduce an HTML page to its title, description and headed sections.

    Drops what a reader does not read as prose: scripts, styles, SVG
    (the explainers' baked figures) and ``<template>`` blocks.
    """

    _SKIP = frozenset({"script", "style", "svg", "template", "noscript"})
    _BLOCKS = frozenset(
        {
            "p",
            "li",
            "div",
            "section",
            "tr",
            "br",
            "figcaption",
            "blockquote",
            "dd",
            "dt",
        }
    )
    #: Inline boxes laid out side by side ("<span>Label</span><span>…") that
    #: would run their words together without a space between them.
    _SPACED = frozenset({"span", "td", "th"})

    def __init__(self) -> None:
        """Start with an empty page."""
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.sections: list[Section] = []
        self._skip_depth = 0
        self._heading: str | None = None
        self._current_heading = ""
        self._buffer: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track skipped regions, headings and block breaks."""
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and dict(attrs).get("name") == "description":
            self.description = dict(attrs).get("content") or ""
        elif tag in {"h1", "h2", "h3"} and not self._skip_depth:
            self._flush()
            self._heading = ""
        elif tag in self._BLOCKS:
            self._buffer.append("\n\n")
        elif tag in self._SPACED:
            self._buffer.append(" ")
        elif tag in {"h4", "h5", "h6"}:
            self._buffer.append("\n\n**")

    def handle_endtag(self, tag: str) -> None:
        """Close skipped regions and headings."""
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in {"h1", "h2", "h3"} and self._heading is not None:
            heading = " ".join(self._heading.split())
            if tag == "h1" and not self.title:
                self.title = heading
            self._current_heading = heading
            self._heading = None
        elif tag in {"h4", "h5", "h6"}:
            self._buffer.append("**\n\n")

    def handle_data(self, data: str) -> None:
        """Keep prose that is not inside a skipped region."""
        if self._in_title:
            self.title = self.title or " ".join(data.split())
        elif self._skip_depth:
            return
        elif self._heading is not None:
            self._heading += data
        else:
            self._buffer.append(data)

    def _flush(self) -> None:
        """Close the section in progress."""
        text = re.sub(r"[ \t]+", " ", "".join(self._buffer))
        text = "\n\n".join(p.strip() for p in re.split(r"\n\s*\n", text) if p.strip())
        if text:
            self.sections.append(Section(self._current_heading or self.title, text))
        self._buffer = []

    def close(self) -> None:
        """Flush the last section."""
        super().close()
        self._flush()


def html_text(html: str) -> _TextExtractor:
    """Parse ``html`` into its title, description and sections.

    Args:
        html: A page or fragment.

    Returns:
        The closed extractor, read for ``title``, ``description`` and
        ``sections``.

    """
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor


def read_markdown(path: Path, root: Path, kind: str) -> Doc:
    """Read one Markdown doc.

    Args:
        path: The file.
        root: The repo root its id is relative to.
        kind: The doc's kind.

    Returns:
        The doc, titled from its first ``#`` heading (its path when it has
        none) and described by its frontmatter ``description``.

    """
    meta, body = _frontmatter(path.read_text(encoding="utf-8"))
    title, sections = markdown_sections(body)
    doc_id = path.relative_to(root).with_suffix("").as_posix()
    return Doc(
        id=doc_id,
        title=title or doc_id,
        description=str(meta.get("description") or ""),
        kind=kind,
        status=str(meta.get("status") or "current"),
        sections=sections,
    )


def read_explainer(path: Path, root: Path) -> Doc:
    """Read one self-contained explainer page as text.

    Args:
        path: The ``.html`` file.
        root: The repo root its id is relative to.

    Returns:
        The doc, its figures and scripts dropped.

    """
    page = html_text(path.read_text(encoding="utf-8"))
    doc_id = path.relative_to(root).with_suffix("").as_posix()
    return Doc(
        id=doc_id,
        title=page.title or doc_id,
        description=page.description,
        kind="explainer",
        status="current",
        sections=tuple(page.sections),
    )


def help_topics(template_dir: Path) -> Iterator[Doc]:
    """Read the /help/ page's topics, in the page's order.

    The topic list and each topic's title come from ``public/help.html``
    itself, so a topic added to the page is in the corpus without an edit
    here. Each body is rendered from its own template, in English.

    Args:
        template_dir: The directory holding ``public/help.html``.

    Yields:
        One doc per ungated topic, id ``help/<slug>``.

    """
    page = (template_dir / "public" / "help.html").read_text(encoding="utf-8")
    titles = {var: text for text, var in _HELP_TITLE_RE.findall(page)}
    with translation.override("en"):
        for title_var, anchor, template in _HELP_PANEL_RE.findall(page):
            if anchor in GATED_HELP_TOPICS:
                continue
            body = html_text(render_to_string(template, {}))
            title = titles.get(title_var, anchor)
            yield Doc(
                id=f"help/{anchor}",
                title=title,
                description=f"Snowdesk help: {title}.",
                kind="help",
                status="current",
                sections=(Section(title, "\n\n".join(s.text for s in body.sections)),),
                url=f"/help/#help-topic-{anchor}",
            )


# ---------------------------------------------------------------------------
# The two corpora
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Return the directory the running build was deployed from."""
    return Path(settings.BASE_DIR)


def _public_docs(root: Path) -> list[Doc]:
    """Build the rider-facing docs: help, the bulletin guide, the explainers."""
    docs = list(help_topics(root / "apps" / "public" / "templates"))
    docs.append(read_markdown(root / "docs" / "bulletin-guide.md", root, "guide"))
    docs.extend(
        read_explainer(path, root)
        for path in sorted((root / "docs" / "explainers").glob("*.html"))
    )
    return docs


def _kind_of(path: Path, root: Path) -> str:
    """Name the kind of an ops Markdown file from where it lives."""
    parts = path.relative_to(root).parts
    if path.name == "CLAUDE.md":
        return "claude-md"
    if len(parts) > 2 and parts[1] in {"decisions", "runbooks"}:
        return parts[1].rstrip("s")
    return "doc"


def _ops_paths(root: Path) -> Iterator[Path]:
    """Every Markdown file the ops corpus holds, in a stable order."""
    yield root / "CLAUDE.md"
    yield from sorted((root / "apps").glob("*/CLAUDE.md"))
    for path in sorted((root / "docs").rglob("*.md")):
        if path.relative_to(root / "docs").parts[0] in DATED_FOLDERS:
            continue
        yield path


@functools.cache
def public_corpus() -> Corpus:
    """Return the rider-facing corpus, built once per process."""
    docs = _public_docs(_repo_root())
    corpus = Corpus("public", {doc.id: doc for doc in docs})
    logger.info("mcp_server: indexed %s", corpus)
    return corpus


@functools.cache
def ops_corpus() -> Corpus:
    """Return the staff corpus — the public one plus the builders' docs."""
    root = _repo_root()
    docs = {doc.id: doc for doc in public_corpus().docs.values()}
    for path in _ops_paths(root):
        if not path.is_file():
            continue
        doc = read_markdown(path, root, _kind_of(path, root))
        docs.setdefault(doc.id, doc)
    corpus = Corpus("ops", docs)
    logger.info("mcp_server: indexed %s", corpus)
    return corpus
