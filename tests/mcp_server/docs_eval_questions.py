"""
tests/mcp_server/docs_eval_questions.py — questions the docs search must answer (SNOW-1393).

Thirty questions put the way a person would ask them, each with the docs
that answer it. ``test_docs_search_quality.py`` scores ``search_docs``
against them, both as a floor the build holds and as the yardstick any
change to ranking (embeddings, a hybrid) has to beat.

Writing rules: phrase each question as someone would ask it, not by
copying a doc's title; list every doc that genuinely answers it, so a
search is not marked wrong for finding a second good answer.
"""

from __future__ import annotations

from typing import NamedTuple


class Question(NamedTuple):
    """One question, the corpus it is asked of, and the docs that answer it."""

    corpus: str
    text: str
    answers: frozenset[str]


def _q(corpus: str, text: str, *answers: str) -> Question:
    """Build a question with its answer set."""
    return Question(corpus, text, frozenset(answers))


QUESTIONS: tuple[Question, ...] = (
    # --- Riders, on the public server --------------------------------------
    _q(
        "public",
        "what does considerable danger mean",
        "docs/bulletin-guide",
        "help/bulletins",
    ),
    _q(
        "public",
        "how do I get maps on my phone for when there's no signal",
        "help/downloads",
        "help/install",
    ),
    _q(
        "public",
        "can I put my own GPX track on the map",
        "help/routes",
        "docs/explainers/from-gpx-to-line",
    ),
    _q("public", "what is a wind slab", "docs/bulletin-guide", "help/problems"),
    _q(
        "public",
        "how steep is too steep, how do I see slope angles",
        "help/slope",
        "help/terrain-filter",
    ),
    _q(
        "public",
        "can I see what the danger was last week",
        "help/timeline",
        "help/calendar",
    ),
    _q("public", "how do I save a place I go to often", "help/favourites"),
    _q("public", "where do I report what I saw on the snow today", "help/observations"),
    _q(
        "public",
        "why does the line on my route change colour",
        "docs/explainers/anatomy-of-a-route",
        "docs/explainers/reading-the-track-scale",
    ),
    _q("public", "how do I install the app on my home screen", "help/install"),
    # --- Staff, on the ops server -----------------------------------------
    _q(
        "ops",
        "why does tonight's bulletin show on tomorrow's map",
        "docs/decisions/a-bulletin-belongs-to-the-day-it-forecasts",
        "docs/decisions/bulletin-target-date-vs-default-issue-selection",
    ),
    _q(
        "ops",
        "how do I connect Claude to the MCP server, what auth does it need",
        "docs/oauth",
        "docs/mcp-server",
    ),
    _q(
        "ops",
        "why don't resorts show an avalanche rating",
        "docs/decisions/a-resort-has-no-avalanche-danger",
    ),
    _q(
        "ops",
        "how does staging get production's data",
        "docs/decisions/data-reaches-other-environments-through-a-feed",
        "docs/runbooks/refresh-staging-from-production",
        "docs/data-feed",
    ),
    _q(
        "ops",
        "what happens to downloaded content when the service worker updates",
        "docs/decisions/an-update-carries-your-downloaded-content-across",
        "docs/decisions/service-worker-updates-apply-silently",
    ),
    _q(
        "ops",
        "why are the map panels light when the phone is in dark mode",
        "docs/decisions/the-map-surface-stays-light",
    ),
    _q(
        "ops",
        "how often is the weather fetched and from where",
        "docs/weather-surfaces",
        "docs/decisions/weather-is-one-immutable-location-row",
        "docs/management-commands",
    ),
    _q(
        "ops",
        "why can't I use a django signal to send the email",
        "docs/decisions/no-signals-for-side-effects",
    ),
    _q(
        "ops",
        "how many playwright tests are we allowed",
        "docs/client-side-tests",
        "CLAUDE",
    ),
    _q(
        "ops",
        "why is a route's elevation not from the GPS",
        "docs/decisions/route-heights-come-from-the-terrain-model",
    ),
    _q(
        "ops",
        "how do I run a management command from the ops MCP",
        "docs/decisions/a-remote-command-runs-on-the-task-worker",
        "docs/mcp-server",
    ),
    _q(
        "ops",
        "what should a new management command do by default",
        "docs/decisions/dry-run-default-commands",
        "docs/management-commands",
        "CLAUDE",
    ),
    _q(
        "ops",
        "how does a Météo-France bulletin get its id",
        "docs/decisions/meteofrance-bulletin-identity",
        "docs/meteofrance-mapping",
    ),
    _q(
        "ops",
        "who can see the exact address of a field report",
        "docs/decisions/an-address-is-owner-only-on-observations",
    ),
    _q("ops", "how do push notifications get their keys", "docs/push-notifications"),
    _q("ops", "how do I release to production", "docs/deployment"),
    _q(
        "ops",
        "why does the map open on today and not the last day with a rating",
        "docs/decisions/map-defaults-to-today-not-the-last-rated-day",
    ),
    _q(
        "ops",
        "what does a trip page show about the other people who saved it",
        "docs/decisions/a-trip-is-one-object-with-a-roster",
        "CLAUDE",
    ),
    _q(
        "ops",
        "how long is a what3words address kept",
        "docs/decisions/what3words-addresses-are-stored-indefinitely",
        "docs/decisions/what3words-cache-expires-at-thirty-days",
    ),
    _q(
        "ops",
        "why is the regions colour fading when I zoom in",
        "docs/decisions/the-region-colour-gives-way-by-zoom",
    ),
)
