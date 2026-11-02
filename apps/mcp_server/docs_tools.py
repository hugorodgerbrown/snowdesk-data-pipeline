"""
apps/mcp_server/docs_tools.py — the docs tools both MCP servers carry (SNOW-1393).

``search_docs``, ``read_doc`` and ``list_docs``, built by :func:`docs_tools`
over one corpus from ``apps.mcp_server.docs_corpus``: the public server
gets them over the rider-facing corpus, the staff ops server over the full
one. Same names, same arguments — only what they can see differs.

:class:`ResourceCatalogue` lists the same docs as MCP resources beside a
server's fixed ``ui://`` views, building the corpus only when a client
first asks, so importing the views never reads the docs from disk.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any

from apps.mcp_server.docs_corpus import Corpus, Doc
from apps.mcp_server.tools import ToolError, ToolSpec

#: Characters ``read_doc`` returns before it cuts a doc short. A whole doc
#: up to this size reads in one call; past it, the caller is told to read
#: by section.
MAX_READ_CHARS = 40_000
_DEFAULT_LIMIT = 8
_MAX_LIMIT = 25


def _kinds(corpus: Corpus) -> list[str]:
    """Return the kinds of doc a corpus holds, sorted."""
    return sorted({doc.kind for doc in corpus.docs.values()})


def _optional_kind(arguments: dict[str, Any], corpus: Corpus) -> str | None:
    """Return the ``kind`` argument, checked against the corpus's kinds.

    Args:
        arguments: The raw tool arguments.
        corpus: The corpus being read.

    Returns:
        The kind, or ``None`` when none was given.

    Raises:
        ToolError: The kind is not one the corpus holds.

    """
    kind = arguments.get("kind")
    if kind is None or kind == "":
        return None
    kinds = _kinds(corpus)
    if kind not in kinds:
        raise ToolError(f"'kind' must be one of: {', '.join(kinds)}.")
    return str(kind)


def search_docs(corpus: Corpus, arguments: dict[str, Any]) -> dict[str, Any]:
    """Rank the corpus's sections against a question.

    Args:
        corpus: The corpus to search.
        arguments: ``query`` (required), ``limit`` and ``kind``.

    Returns:
        ``hits`` — doc id, title, section, snippet and score — and a
        ``summary`` line.

    Raises:
        ToolError: A missing query, or a bad limit or kind.

    """
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolError("'query' is required and must be a non-empty string.")
    limit = arguments.get("limit", _DEFAULT_LIMIT)
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= _MAX_LIMIT
    ):
        raise ToolError(f"'limit' must be an integer from 1 to {_MAX_LIMIT}.")
    kind = _optional_kind(arguments, corpus)
    hits = corpus.search(query, limit=limit, kind=kind)
    rows = [
        {
            "doc": hit.doc.id,
            "title": hit.doc.title,
            "section": hit.section.heading,
            "kind": hit.doc.kind,
            "status": hit.doc.status,
            "snippet": hit.snippet,
            "score": hit.score,
        }
        for hit in hits
    ]
    if not rows:
        summary = f"No docs match {query!r}. Try other words, or list_docs."
    else:
        # The text content is all some clients read, so it carries every
        # hit's id, section and snippet, not only a count.
        lines = [
            f"{len(rows)} sections match {query!r}. Read one with read_doc "
            "(doc, and section for just that part):"
        ]
        lines += [
            f"- {row['doc']} § {row['section']} ({row['kind']}): {row['snippet']}"
            for row in rows
        ]
        summary = "\n".join(lines)
    return {"query": query, "hits": rows, "summary": summary}


def _find_section(doc: Doc, wanted: str) -> int | None:
    """Return the index of the section ``wanted`` names, or ``None``.

    An exact heading (any case) wins; otherwise a heading containing the
    words, when exactly one does.
    """
    target = wanted.strip().lower()
    headings = [section.heading.lower() for section in doc.sections]
    if target in headings:
        return headings.index(target)
    partial = [i for i, heading in enumerate(headings) if target in heading]
    return partial[0] if len(partial) == 1 else None


def read_doc(corpus: Corpus, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return one doc, or one section of it, as Markdown.

    Args:
        corpus: The corpus to read from.
        arguments: ``doc`` (an id from search or list), ``section`` and
            ``offset`` — the character to start from, for the rest of a
            text cut at :data:`MAX_READ_CHARS`.

    Returns:
        The doc's metadata, its section headings, the ``text``,
        ``truncated`` and, when cut, ``next_offset``. ``summary`` is the
        text itself, so a client that reads only the text content reads
        the doc.

    Raises:
        ToolError: An unknown doc or section, or a bad offset.

    """
    reference = arguments.get("doc")
    if not isinstance(reference, str) or not reference.strip():
        raise ToolError("'doc' is required: an id from search_docs or list_docs.")
    doc = corpus.resolve(reference)
    if doc is None:
        raise ToolError(
            f"No doc {reference!r}. Find its id with search_docs or list_docs."
        )
    headings = [section.heading for section in doc.sections]
    wanted = arguments.get("section")
    if isinstance(wanted, str) and wanted.strip():
        index = _find_section(doc, wanted)
        if index is None:
            raise ToolError(
                f"{doc.id} has no section {wanted!r}. "
                f"Its sections: {'; '.join(headings)}."
            )
        section = doc.sections[index]
        text = f"# {doc.title}\n\n## {section.heading}\n\n{section.text}"
    else:
        text = doc.text()
    offset = arguments.get("offset", 0)
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or not 0 <= offset < max(len(text), 1)
    ):
        raise ToolError(f"'offset' must be an integer from 0 to {len(text) - 1}.")
    end = offset + MAX_READ_CHARS
    truncated = end < len(text)
    page = text[offset:end]
    result: dict[str, Any] = {
        **doc.listing(),
        "sections": headings,
        "truncated": truncated,
    }
    if truncated:
        # A single section can outrun the limit (CLAUDE.md's routing
        # table does), so "read by section" alone cannot reach the rest:
        # the offset always can.
        result["next_offset"] = end
        page += (
            f"\n\n[Cut short at character {end} of {len(text)}. Call read_doc "
            f"again with the same doc and section and offset={end} for the rest.]"
        )
    return {**result, "text": page, "summary": page}


def list_docs(corpus: Corpus, arguments: dict[str, Any]) -> dict[str, Any]:
    """List the corpus's docs with their descriptions.

    Args:
        corpus: The corpus to list.
        arguments: ``kind``, to list one kind only.

    Returns:
        ``docs`` and the ``kinds`` the corpus holds, with a ``summary``.

    """
    kind = _optional_kind(arguments, corpus)
    docs = [
        doc.listing() for doc in corpus.docs.values() if not kind or doc.kind == kind
    ]
    kinds = _kinds(corpus)
    return {
        "docs": docs,
        "kinds": kinds,
        "summary": "\n".join(
            [f"{len(docs)} docs ({', '.join(kinds)}):"]
            + [f"- {doc['id']}: {doc['title']}. {doc['description']}" for doc in docs]
        ),
    }


def _bound(
    function: Callable[[Corpus, dict[str, Any]], dict[str, Any]],
    corpus: Callable[[], Corpus],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Adapt a docs tool to the ``handler(arguments)`` shape ToolSpec calls."""

    def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        """Call the tool over the corpus, building it on first use."""
        return function(corpus(), arguments)

    return handler


def docs_tools(corpus: Callable[[], Corpus], audience: str) -> dict[str, ToolSpec]:
    """Build the three docs tools over one corpus.

    Args:
        corpus: Returns the corpus (cached by its module).
        audience: A few words naming what the corpus covers, for the
            tool descriptions.

    Returns:
        The tools, keyed by name, ready to merge into a catalogue.

    """
    kind = {
        "type": "string",
        "description": "Only docs of this kind (list_docs names the kinds).",
    }
    return {
        "search_docs": ToolSpec(
            name="search_docs",
            description=(
                f"Search Snowdesk's documentation — {audience} — for how the "
                "app works. Returns the best-matching sections with a "
                "snippet each. Start here for any 'how does Snowdesk…', "
                "'what does … mean' or 'why does …' question, then "
                "read_doc the hits worth reading in full."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Plain words."},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_LIMIT,
                        "default": _DEFAULT_LIMIT,
                    },
                    "kind": kind,
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=_bound(search_docs, corpus),
        ),
        "read_doc": ToolSpec(
            name="read_doc",
            description=(
                "Read one Snowdesk doc as Markdown: the whole doc, or one "
                "section when 'section' names a heading. 'doc' is an id "
                "from search_docs or list_docs. A long reply is cut short "
                "and names the offset to call again with."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "doc": {"type": "string"},
                    "section": {
                        "type": "string",
                        "description": "A section heading, or words from one.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "Character to start from — the next_offset of a "
                            "reply that was cut short."
                        ),
                    },
                },
                "required": ["doc"],
                "additionalProperties": False,
            },
            handler=_bound(read_doc, corpus),
        ),
        "list_docs": ToolSpec(
            name="list_docs",
            description=(
                f"List every Snowdesk doc available here — {audience} — "
                "with its one-line description, to browse rather than search."
            ),
            input_schema={
                "type": "object",
                "properties": {"kind": kind},
                "additionalProperties": False,
            },
            handler=_bound(list_docs, corpus),
        ),
    }


class ResourceCatalogue(Mapping[str, Any]):
    """A server's fixed resources plus its corpus's docs, keyed by URI.

    The protocol module reads ``resources`` as a mapping; this one builds
    the corpus on first read rather than at import.
    """

    def __init__(self, fixed: Mapping[str, Any], corpus: Callable[[], Corpus]) -> None:
        """Hold the fixed resources and the corpus to add."""
        self._fixed = fixed
        self._corpus = corpus

    def _all(self) -> Mapping[str, Any]:
        """Return every resource, the fixed ones first."""
        return {**self._fixed, **self._corpus().resources}

    def __getitem__(self, uri: str) -> Any:
        """Return the resource at ``uri``."""
        return self._all()[uri]

    def __iter__(self) -> Iterator[str]:
        """Iterate the URIs."""
        return iter(self._all())

    def __len__(self) -> int:
        """Count the resources."""
        return len(self._all())
