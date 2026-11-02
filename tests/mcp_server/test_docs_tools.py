"""
tests/mcp_server/test_docs_tools.py — Tests for apps.mcp_server.docs_tools (SNOW-1393).

``search_docs``, ``read_doc`` and ``list_docs`` through the JSON-RPC
dispatcher on both servers, the ``docs://`` resources, and the split: the
public server can never read a doc written for builders.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.mcp_server import docs_tools, protocol
from apps.mcp_server.docs_corpus import Corpus, Doc, Section
from apps.mcp_server.views import OPS_SERVER


def _rpc(
    method: str,
    params: dict[str, Any],
    server: protocol.ToolServer = protocol.PUBLIC_SERVER,
) -> dict[str, Any]:
    """Dispatch one request and return its envelope."""
    response = protocol.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, server
    )
    assert response is not None
    return response


def _call(
    name: str,
    arguments: dict[str, Any],
    server: protocol.ToolServer = protocol.PUBLIC_SERVER,
) -> dict[str, Any]:
    """Call a tool and return its CallToolResult."""
    result: dict[str, Any] = _rpc(
        "tools/call", {"name": name, "arguments": arguments}, server
    )["result"]
    return result


class TestSearchDocs:
    """search_docs ranks sections and validates its arguments."""

    def test_finds_the_help_topic(self) -> None:
        """A rider's question reaches the help page's answer."""
        result = _call("search_docs", {"query": "offline downloads"})
        assert result["isError"] is False
        hits = result["structuredContent"]["hits"]
        assert hits
        assert {"doc", "title", "section", "kind", "status", "snippet", "score"} <= set(
            hits[0]
        )
        assert "help/downloads" in {hit["doc"] for hit in hits}

    def test_no_match_says_so(self) -> None:
        """A query no doc holds is a result, not an error."""
        result = _call("search_docs", {"query": "zzqqxx"})
        assert result["structuredContent"]["hits"] == []
        assert "No docs match" in result["content"][0]["text"]

    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"query": " "},
            {"query": "map", "limit": 0},
            {"query": "map", "limit": True},
        ],
    )
    def test_rejects_bad_arguments(self, arguments: dict[str, Any]) -> None:
        """A missing query or bad limit is a tool error."""
        assert _call("search_docs", arguments)["isError"] is True

    def test_rejects_an_unknown_kind(self) -> None:
        """kind must be one the corpus holds; the public one has no decisions."""
        result = _call("search_docs", {"query": "map", "kind": "decision"})
        assert result["isError"] is True
        assert "explainer" in result["content"][0]["text"]

    def test_kind_narrows_on_ops(self) -> None:
        """On ops, kind=decision returns decision records only."""
        result = _call("search_docs", {"query": "map", "kind": "decision"}, OPS_SERVER)
        hits = result["structuredContent"]["hits"]
        assert hits and {hit["kind"] for hit in hits} == {"decision"}


class TestReadDoc:
    """read_doc returns a doc or one section, and refuses what it cannot see."""

    def test_reads_a_whole_doc(self) -> None:
        """The text is the doc as Markdown, and is the text content too."""
        result = _call("read_doc", {"doc": "docs/bulletin-guide"})
        body = result["structuredContent"]
        assert body["text"].startswith("# How to Read the Avalanche Bulletin")
        assert result["content"][0]["text"] == body["text"]
        assert "The danger level" in body["sections"]
        assert body["truncated"] is False

    def test_reads_one_section_by_partial_heading(self) -> None:
        """Words from one heading pick that section."""
        body = _call("read_doc", {"doc": "bulletin-guide", "section": "danger level"})[
            "structuredContent"
        ]
        assert "## The danger level" in body["text"]
        assert "What differs by provider" not in body["text"]

    def test_unknown_section_lists_the_sections(self) -> None:
        """A heading the doc lacks is an error naming the ones it has."""
        result = _call("read_doc", {"doc": "docs/bulletin-guide", "section": "nope"})
        assert result["isError"] is True
        assert "The danger level" in result["content"][0]["text"]

    @pytest.mark.parametrize("doc", ["", "docs/nope"])
    def test_unknown_doc_is_an_error(self, doc: str) -> None:
        """No id, or one the corpus lacks, is a tool error."""
        assert _call("read_doc", {"doc": doc})["isError"] is True

    def test_public_cannot_read_a_builders_doc(self) -> None:
        """The split holds: CLAUDE.md and decisions are ops only."""
        for doc in (
            "CLAUDE",
            "docs/decisions/the-sheet-is-the-page",
            "docs/runbooks/reset-live-db",
        ):
            assert _call("read_doc", {"doc": doc})["isError"] is True
            assert _call("read_doc", {"doc": doc}, OPS_SERVER)["isError"] is False

    def test_cuts_a_long_doc_and_names_its_sections(self) -> None:
        """Past MAX_READ_CHARS the text is cut and points at the sections."""
        corpus = Corpus(
            "t",
            {
                "big": Doc(
                    id="big",
                    title="Big",
                    description="",
                    kind="doc",
                    status="current",
                    sections=(Section("Part", "x" * (docs_tools.MAX_READ_CHARS + 10)),),
                )
            },
        )
        body = docs_tools.read_doc(corpus, {"doc": "big"})
        assert body["truncated"] is True
        assert body["text"].endswith("[Cut short. Read the rest by section: Part]")

    def test_exact_heading_wins_over_partial(self) -> None:
        """An exact heading is picked even when another contains it."""
        corpus = Corpus(
            "t",
            {
                "d": Doc(
                    id="d",
                    title="D",
                    description="",
                    kind="doc",
                    status="current",
                    sections=(Section("Map layers", "a"), Section("Map", "b")),
                )
            },
        )
        assert docs_tools.read_doc(corpus, {"doc": "d", "section": "map"})[
            "text"
        ].endswith("b")


class TestListDocs:
    """list_docs lists a corpus, optionally one kind."""

    def test_lists_the_public_corpus(self) -> None:
        """Every public doc, with its kind."""
        body = _call("list_docs", {})["structuredContent"]
        assert body["kinds"] == ["explainer", "guide", "help"]
        assert any(doc["id"] == "help/overview" for doc in body["docs"])

    def test_lists_one_kind_on_ops(self) -> None:
        """kind=runbook lists only the runbooks."""
        body = _call("list_docs", {"kind": "runbook"}, OPS_SERVER)["structuredContent"]
        assert body["docs"] and {doc["kind"] for doc in body["docs"]} == {"runbook"}


class TestDocResources:
    """Each doc is a docs:// resource on its server."""

    def test_reads_a_doc_resource(self) -> None:
        """resources/read returns the doc's Markdown."""
        result = _rpc("resources/read", {"uri": "docs://help/overview"})["result"]
        content = result["contents"][0]
        assert content["mimeType"] == "text/markdown"
        assert content["text"].startswith("# What is Snowdesk")

    def test_public_resources_hold_no_builders_doc(self) -> None:
        """The public server lists no CLAUDE.md; ops lists it."""
        public = {r["uri"] for r in _rpc("resources/list", {})["result"]["resources"]}
        ops = {
            r["uri"]
            for r in _rpc("resources/list", {}, OPS_SERVER)["result"]["resources"]
        }
        assert "docs://CLAUDE" not in public
        assert "docs://CLAUDE" in ops
        assert _rpc("resources/read", {"uri": "docs://CLAUDE"})["error"]["code"] == (
            protocol.RESOURCE_NOT_FOUND
        )

    def test_catalogue_is_a_mapping(self) -> None:
        """len and iteration see the fixed resources and the docs."""
        catalogue = OPS_SERVER.resources
        assert len(catalogue) == len(list(catalogue))
