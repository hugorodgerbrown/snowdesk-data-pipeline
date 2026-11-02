"""
tests/mcp_server/test_docs_search_quality.py — how well search_docs answers real questions (SNOW-1393).

Scores the BM25 ranking against the thirty questions in
``docs_eval_questions``. Measured on 2026-11-02: the right doc is first
for 18/30 and in the top five for 27/30 (MRR 0.72). The three misses are
word-choice gaps ("save a place" for a favourite, "rating" for danger),
the case embeddings would be added for, and the yardstick they must beat.

The floors sit a little under the measurement so a doc edit that shifts
one question does not fail the build, while a ranking change that loses
several does. Raise them when the ranking improves.
"""

from __future__ import annotations

import pytest

from apps.mcp_server import docs_corpus
from apps.mcp_server.docs_corpus import Corpus
from tests.mcp_server.docs_eval_questions import QUESTIONS, Question

#: The share of questions whose answer must be in the top five hits.
RECALL_AT_5_FLOOR = 0.85
#: The mean reciprocal rank the ranking must hold.
MRR_FLOOR = 0.6


def _corpus(question: Question) -> Corpus:
    """Return the corpus a question is asked of."""
    if question.corpus == "public":
        return docs_corpus.public_corpus()
    return docs_corpus.ops_corpus()


def _rank(question: Question) -> int | None:
    """Return the 1-based rank of the first answering doc in the top ten."""
    hits = _corpus(question).search(question.text, limit=10)
    for rank, hit in enumerate(hits, start=1):
        if hit.doc.id in question.answers:
            return rank
    return None


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q.text[:40])
def test_every_answer_is_in_its_corpus(question: Question) -> None:
    """An answer naming a doc the corpus lacks is a broken question."""
    corpus = _corpus(question)
    assert question.answers <= set(corpus.docs)


def test_the_ranking_holds_its_floors() -> None:
    """Recall at five and MRR stay at or above what was measured."""
    ranks = [_rank(question) for question in QUESTIONS]
    recall_at_5 = sum(1 for rank in ranks if rank and rank <= 5) / len(ranks)
    mrr = sum(1 / rank for rank in ranks if rank) / len(ranks)
    missed = [
        q.text for q, rank in zip(QUESTIONS, ranks, strict=True) if not rank or rank > 5
    ]
    assert recall_at_5 >= RECALL_AT_5_FLOOR, f"missed: {missed}"
    assert mrr >= MRR_FLOOR
