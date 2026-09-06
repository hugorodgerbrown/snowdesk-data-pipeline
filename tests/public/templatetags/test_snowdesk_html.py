"""
tests/public/templatetags/test_snowdesk_html.py — Tests for the snowdesk_html filter.

Covers sanitisation behaviour (tag allowlist, attribute stripping, disallowed
tag removal), edge cases (None/empty input), return type guarantees, and a
template-integration smoke test.  One test case uses a real SLF prose sample
from ``tests/fixtures/sample_variable_day.json`` to guard against regressions
with actual field data.

Also covers the EAWS glossary injection added by SNOW-853 — the matching
rules (longest first, once per block, synonyms, no recursion), the guarantee
that only text nodes are rewritten, and the two properties the ordering buys:
the injected markup survives because bleach ran *first*, and the provider's
own words survive character-for-character.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import bleach
import pytest
from django.template import Context, Template
from django.utils.safestring import SafeString

from apps.public.guidance import load_field_guidance
from apps.public.templatetags.snowdesk_html import (
    _ALLOWED_ATTRIBUTES,
    _ALLOWED_PROTOCOLS,
    _ALLOWED_TAGS,
    inject_glossary_terms,
    prose_body,
    prose_title,
    snowdesk_html,
    tendency_has_comment,
)
from tests.glossary_markup import strip_glossary_markup


def _popover_ids(html: str) -> list[str]:
    """
    Return every injected popover ``id`` in *html*, in document order.

    Args:
        html: Filter output, or several outputs concatenated as one page.

    Returns:
        The ids, with duplicates preserved so a caller can assert on them.

    """
    return re.findall(r'<span popover id="([^"]+)"', html)


def sanitise_only(value: str) -> str:
    """
    Run the filter's sanitisation step alone, without the glossary injection.

    Gives the fidelity tests something to compare against for real provider
    prose, where the input is not already equal to its own bleach output.

    Args:
        value: Raw provider prose HTML.

    Returns:
        The ``bleach.clean`` result the filter would inject into.

    """
    return bleach.clean(
        value,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )


# Absolute path to the test fixture used in the real-SLF test.
_SAMPLE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "sample_variable_day.json"
)


@pytest.fixture
def slf_snowpack_comment() -> str:
    """Return the real snowpackStructure.comment from the variable-day sample."""
    with _SAMPLE_PATH.open() as fh:
        data = json.load(fh)
    props = data.get("properties", data)
    comment: str = props["snowpackStructure"]["comment"]
    return comment


@pytest.fixture
def slf_weather_forecast_comment() -> str:
    """Return the real weatherForecast.comment from the variable-day sample."""
    with _SAMPLE_PATH.open() as fh:
        data = json.load(fh)
    props = data.get("properties", data)
    comment: str = props["weatherForecast"]["comment"]
    return comment


class TestSnowdeskHtmlAllowlistedTags:
    """Allowlisted tags survive sanitisation unchanged."""

    def test_allowlisted_tags_pass_through(self) -> None:
        """Structural tags in the allowlist round-trip verbatim."""
        html = "<h1>Snow</h1><p>prose</p>"
        result = snowdesk_html(html)
        assert result == html

    def test_nested_allowlisted_content(self) -> None:
        """Nested allowlisted tags round-trip verbatim."""
        html = "<ul><li><strong>a</strong></li></ul>"
        result = snowdesk_html(html)
        assert result == html

    def test_em_tag_passes_through(self) -> None:
        """The ``em`` tag is in the allowlist and must survive."""
        html = "<p><em>critical</em> terrain</p>"
        result = snowdesk_html(html)
        assert result == html

    def test_h2_tag_passes_through(self) -> None:
        """The ``h2`` tag is in the allowlist and must survive."""
        html = "<h2>Fresh snow</h2><p>-</p>"
        result = snowdesk_html(html)
        assert result == html


class TestSnowdeskHtmlDisallowedTags:
    """Disallowed tags are stripped (not escaped) from the output."""

    def test_script_tag_stripped(self) -> None:
        """
        A ``<script>`` tag wrapper is stripped; allowlisted content remains.

        bleach's ``strip=True`` removes the tag delimiters but keeps inner text
        as inert plain text — ``alert(1)`` cannot execute as JavaScript when
        rendered as a text node.  The important guarantee is that the ``<script>``
        element itself is gone so the browser never interprets the content as a
        script block.
        """
        html = "<script>alert(1)</script><p>ok</p>"
        result = snowdesk_html(html)
        assert "<script" not in result
        assert "<p>ok</p>" in result

    def test_div_wrapper_stripped(self) -> None:
        """A ``<div>`` wrapper is stripped; its text content remains."""
        html = "<div><p>text</p></div>"
        result = snowdesk_html(html)
        assert "<div" not in result
        assert "<p>text</p>" in result

    def test_anchor_tag_stripped(self) -> None:
        """``<a>`` tags are not in the allowlist and are stripped."""
        html = '<p>See <a href="https://slf.ch">SLF</a>.</p>'
        result = snowdesk_html(html)
        assert "<a" not in result
        assert "SLF" in result


class TestSnowdeskHtmlAttributeStripping:
    """All attributes are removed from allowlisted tags."""

    def test_class_and_onclick_stripped(self) -> None:
        """``class`` and ``onclick`` attributes are stripped from a ``<p>`` tag."""
        html = '<p class="foo" onclick="x()">text</p>'
        result = snowdesk_html(html)
        assert result == "<p>text</p>"

    def test_contenteditable_stripped(self) -> None:
        """``contenteditable`` is stripped — this appears in real SLF weather data."""
        html = '<h2 contenteditable="false">Fresh snow</h2>'
        result = snowdesk_html(html)
        assert result == "<h2>Fresh snow</h2>"
        assert "contenteditable" not in result


class TestSnowdeskHtmlEdgeCases:
    """Edge cases: None input, empty string, return type."""

    def test_none_input_returns_empty_safestring(self) -> None:
        """``None`` input returns an empty ``SafeString``."""
        result = snowdesk_html(None)
        assert result == ""
        assert isinstance(result, SafeString)

    def test_empty_string_returns_empty_safestring(self) -> None:
        """An empty string input returns an empty ``SafeString``."""
        result = snowdesk_html("")
        assert result == ""
        assert isinstance(result, SafeString)

    def test_return_type_is_safestring(self) -> None:
        """The return type is always ``SafeString`` so Django does not re-escape it."""
        result = snowdesk_html("<p>hello</p>")
        assert isinstance(result, SafeString)

    def test_none_is_safestring(self) -> None:
        """``None`` path also returns ``SafeString`` (not plain ``str``)."""
        result = snowdesk_html(None)
        assert isinstance(result, SafeString)


class TestSnowdeskHtmlRealSlfSample:
    """Validates the filter against real SLF prose fields from the sample fixture."""

    def test_snowpack_comment_sanitises_without_error(
        self, slf_snowpack_comment: str
    ) -> None:
        """The real snowpackStructure comment sanitises without raising."""
        result = snowdesk_html(slf_snowpack_comment)
        assert isinstance(result, SafeString)

    def test_snowpack_comment_preserves_allowlisted_tags(
        self, slf_snowpack_comment: str
    ) -> None:
        """After sanitisation the ``<h1>`` and ``<p>`` tags from SLF are still present."""
        result = snowdesk_html(slf_snowpack_comment)
        assert "<h1>" in result
        assert "<p>" in result

    def test_weather_forecast_contenteditable_stripped(
        self, slf_weather_forecast_comment: str
    ) -> None:
        """
        The real weatherForecast comment contains ``<h2 contenteditable="false">``.

        After sanitisation the attribute must be gone and the tag preserved.
        """
        result = snowdesk_html(slf_weather_forecast_comment)
        assert "contenteditable" not in result
        assert "<h2>" in result
        assert isinstance(result, SafeString)


class TestSnowdeskHtmlTemplateIntegration:
    """Template-integration test: filter registered and works inside a template."""

    def test_script_stripped_in_template_context(self) -> None:
        """
        Rendering the filter inside a template strips the ``<script>`` tag.

        The tag delimiters are removed so the browser never interprets the
        content as a script block.  bleach leaves inner text as inert plain
        text which is safe to render.
        """
        tmpl = Template("{% load snowdesk_html %}{{ val|snowdesk_html }}")
        ctx = Context({"val": "<script>x</script><p>ok</p>"})
        rendered = tmpl.render(ctx)
        assert "<script" not in rendered
        assert "<p>ok</p>" in rendered

    def test_none_in_template_context_renders_empty(self) -> None:
        """``None`` passed through the template filter renders as an empty string."""
        tmpl = Template("{% load snowdesk_html %}{{ val|snowdesk_html }}")
        ctx = Context({"val": None})
        rendered = tmpl.render(ctx)
        assert rendered == ""

    def test_allowlisted_html_not_re_escaped(self) -> None:
        """Allowlisted tags are not entity-escaped by Django's auto-escaping."""
        tmpl = Template("{% load snowdesk_html %}{{ val|snowdesk_html }}")
        ctx = Context({"val": "<p>hello</p>"})
        rendered = tmpl.render(ctx)
        # If auto-escaping hit the output the tag would be &lt;p&gt;...
        assert rendered == "<p>hello</p>"
        assert "&lt;" not in rendered


class TestGlossaryInjectionMatching:
    """Which terms get marked, and how many times (SNOW-853)."""

    def test_term_is_marked_in_prose(self) -> None:
        """A term in the ticket's motivating sentence gains a button and popover."""
        result = snowdesk_html(
            "<p>Individual avalanche prone locations are to be found in "
            "particular on steep shady slopes.</p>"
        )
        assert 'class="glossary-term">avalanche prone locations</button>' in result
        assert "Locations delineated by aspect or altitude" in result

    def test_button_and_popover_ids_agree(self) -> None:
        """The button's ``popovertarget`` names the span's ``id``."""
        result = snowdesk_html("<p>A thick crust formed overnight.</p>")
        target = re.search(r'popovertarget="([^"]+)"', result)
        assert target is not None
        assert f'<span popover id="{target.group(1)}"' in result

    def test_popover_links_back_to_the_eaws_source(self) -> None:
        """Each definition carries a link to the term's own EAWS anchor."""
        result = snowdesk_html("<p>A thick crust formed overnight.</p>")
        assert 'href="https://www.avalanches.org/glossary/#crust"' in result
        assert 'class="glossary-src">EAWS glossary</a>' in result

    def test_the_source_link_opens_in_a_new_tab(self) -> None:
        """A reader who taps the credit must not lose the bulletin page."""
        result = snowdesk_html("<p>A thick crust formed overnight.</p>")
        assert 'target="_blank" rel="noopener"' in result

    def test_the_button_announces_that_it_reveals_something(self) -> None:
        """
        ``aria-haspopup="dialog"`` tells a screen-reader user what the control does.

        ``aria-describedby`` was rejected: a closed popover is UA-styled
        ``display: none``, so the description would be read as empty.
        """
        result = snowdesk_html("<p>A thick crust formed overnight.</p>")
        assert 'aria-haspopup="dialog" class="glossary-term"' in result

    def test_longest_match_wins(self) -> None:
        """The whole "melt-freeze crust" is marked, not a bare "crust"."""
        result = snowdesk_html("<p>A melt-freeze crust has formed.</p>")
        assert 'class="glossary-term">melt-freeze crust</button>' in result
        assert "increases firmness." in result
        # The shorter term's own definition must not appear.
        assert "melt-freeze process or wind" not in result

    def test_word_boundary_prevents_substring_match(self) -> None:
        """A term inside a longer word is left alone."""
        result = snowdesk_html("<p>Encrusted rime coats the ridge.</p>")
        assert "glossary-term" not in result
        assert result == "<p>Encrusted rime coats the ridge.</p>"

    def test_matching_is_case_insensitive(self) -> None:
        """A capitalised term at the start of a sentence still matches."""
        result = snowdesk_html("<p>Weak layers persist at depth.</p>")
        assert 'class="glossary-term">Weak layers</button>' in result

    def test_only_the_first_occurrence_in_a_block_is_marked(self) -> None:
        """A term repeated inside one paragraph is marked once."""
        result = snowdesk_html(
            "<p>The weak layer is buried; that weak layer is reactive.</p>"
        )
        assert result.count('class="glossary-term"') == 1

    def test_a_synonym_does_not_re_mark_its_own_term(self) -> None:
        """A synonym of an already-marked term is skipped inside the same block."""
        result = snowdesk_html("<p>Avalanche prone locations — a danger zone.</p>")
        assert result.count('class="glossary-term"') == 1

    def test_the_seen_set_resets_at_the_next_block(self) -> None:
        """Two paragraphs each get their own marking of the same term."""
        result = snowdesk_html(
            "<p>The weak layer is buried.</p><p>That weak layer is reactive.</p>"
        )
        assert result.count('class="glossary-term"') == 2

    def test_repeat_across_blocks_gets_a_distinct_popover_id(self) -> None:
        """Two ids in one page would be invalid HTML and collide on open."""
        result = snowdesk_html(
            "<p>The weak layer is buried.</p><p>That weak layer is reactive.</p>"
        )
        ids = re.findall(r'<span popover id="([^"]+)"', result)
        assert len(ids) == 2
        assert len(set(ids)) == 2

    def test_list_items_are_block_boundaries(self) -> None:
        """Each ``<li>`` is its own block, so each marks the term once."""
        result = snowdesk_html("<ul><li>Weak layer.</li><li>Weak layer.</li></ul>")
        assert result.count('class="glossary-term"') == 2

    def test_synonym_resolves_to_the_primary_definition(self) -> None:
        """A "danger zone" shows the same text as an "avalanche prone location"."""
        primary = snowdesk_html("<p>An avalanche prone location.</p>")
        synonym = snowdesk_html("<p>A danger zone.</p>")
        definition = "Locations delineated by aspect or altitude"
        assert definition in primary
        assert definition in synonym
        assert 'id="g-avalanche-prone-location-' in synonym

    def test_definitions_are_not_themselves_marked_up(self) -> None:
        """
        No recursion: a term inside an injected definition stays plain text.

        The definition of *avalanche prone location* contains the word
        *aspect*, which is itself a glossary term. It is emitted straight to
        the output rather than fed back through the matcher, so the reader
        gets one level of explanation, not a nest of them.
        """
        result = snowdesk_html("<p>An avalanche prone location.</p>")
        assert result.count('class="glossary-term"') == 1
        assert 'class="glossary-term">aspect' not in result

    def test_a_definition_containing_markup_characters_is_escaped(self) -> None:
        """
        EAWS wording contains a bare ``>`` (see the *weak layer* entry).

        It must arrive as an entity, not as the start of a tag.
        """
        result = snowdesk_html("<p>A weak layer at depth.</p>")
        assert "Generally &gt;1mm in size" in result


class TestGlossaryPopoverIds:
    """Popover ids are unique across the whole page, not just within a block."""

    def test_id_has_the_expected_shape(self) -> None:
        """``g-<term-slug>-<n>``: the slug is readable, the ordinal is not fixed."""
        result = snowdesk_html("<p>A thick crust formed overnight.</p>")
        for popover_id in _popover_ids(result):
            assert re.fullmatch(r"g-[a-z-]+-\d+", popover_id), popover_id

    def test_every_id_in_one_call_is_unique(self) -> None:
        """Several terms in one block get several distinct ids."""
        result = snowdesk_html(
            "<p>A melt-freeze crust over the weak layer on lee slopes.</p>"
        )
        ids = _popover_ids(result)
        assert len(ids) == 3
        assert len(set(ids)) == 3

    def test_two_calls_on_identical_input_produce_disjoint_ids(self) -> None:
        """
        The regression this class exists for.

        Ids were a digest of the block's own content until review caught the
        hole: a hash is a pure function of its input, so two calls handed
        byte-identical prose produced byte-identical ids. ``id`` uniqueness
        is scoped to the page, and a page is many independent filter calls,
        so nothing derived from one block's content can supply it.
        """
        source = "<p>Watch for wind crust on lee slopes above the treeline.</p>"
        first = set(_popover_ids(snowdesk_html(source)))
        second = set(_popover_ids(snowdesk_html(source)))
        assert first
        assert first.isdisjoint(second)

    def test_two_cards_sharing_a_field_guidance_note_do_not_collide(self) -> None:
        """
        The production shape of the same bug, on Snowdesk's own prose.

        ``apps.public.guidance`` keys its note text on ``problem_type``
        alone — no bulletin-specific interpolation — so two problem cards of
        the same type on one page render byte-identical text through two
        independent filter calls. A collision there means the reader taps
        one term and reads another term's definition.
        """
        note = load_field_guidance()["persistent_weak_layers"]
        page = snowdesk_html(note) + snowdesk_html(note)
        ids = _popover_ids(page)
        assert len(ids) > 1
        assert len(set(ids)) == len(ids)

    def test_button_targets_are_all_resolvable(self) -> None:
        """Every ``popovertarget`` names an ``id`` that exists exactly once."""
        result = snowdesk_html(
            "<p>The weak layer is buried.</p><p>That weak layer is reactive.</p>"
        )
        targets = re.findall(r'popovertarget="([^"]+)"', result)
        assert targets
        for target in targets:
            assert result.count(f'<span popover id="{target}"') == 1


class TestGlossaryInjectionLeavesMarkupAlone:
    """Only text nodes are rewritten; everything else passes through verbatim."""

    def test_surrounding_markup_is_byte_identical(self) -> None:
        """The tags around a marked term are untouched."""
        result = snowdesk_html("<h2>Fresh snow</h2><ul><li><em>crust</em></li></ul>")
        assert result.startswith("<h2>Fresh snow</h2><ul><li><em>")
        assert result.endswith("</em></li></ul>")

    def test_a_term_in_a_tag_name_is_untouched(self) -> None:
        """
        ``handle_data`` is the only hook that rewrites; tags re-emit verbatim.

        Fed directly rather than through the filter because bleach strips a
        ``<crust>`` element before injection ever sees it — this asserts the
        parser's own contract, which is what keeps that true if the
        allowlist ever changes.
        """
        raw = "<crust data-crust='crust'>ridge</crust>"
        assert inject_glossary_terms(raw) == raw

    def test_entities_survive_unchanged(self) -> None:
        """``&amp;`` in and ``&amp;`` out — the parser re-emits entity refs."""
        result = snowdesk_html("<p>Wind &amp; sun formed a crust.</p>")
        assert "Wind &amp; sun formed a" in result
        assert "&amp;amp;" not in result

    def test_prose_without_any_term_is_returned_unchanged(self) -> None:
        """No match means no rewrite at all."""
        html = "<p>Conditions are broadly favourable today.</p>"
        assert snowdesk_html(html) == html


class TestGlossaryInjectionOrdering:
    """Injection runs after ``bleach.clean``, and that is load-bearing."""

    def test_injected_button_survives_because_bleach_ran_first(self) -> None:
        """
        The ``<button>`` and its attributes are not in the bleach allowlist.

        If injection ran *before* sanitisation, bleach would strip the button
        and every attribute on the span, leaving bare definition text spliced
        into the sentence. Their presence in the output is the proof of
        order.
        """
        result = snowdesk_html("<p>A thick crust formed overnight.</p>")
        assert "<button" in result
        assert "popovertarget=" in result
        assert "<span popover " in result
        assert "<a href=" in result

    def test_provider_markup_still_cannot_ship_a_button(self) -> None:
        """The narrow allowlist is unchanged — provider prose gets no button."""
        result = snowdesk_html('<p><button popovertarget="x">tap</button> me</p>')
        assert result == "<p>tap me</p>"

    def test_provider_attributes_are_still_stripped(self) -> None:
        """A provider ``id`` cannot collide with an injected popover id."""
        result = snowdesk_html('<p id="g-crust-abc123">A crust.</p>')
        assert result.startswith("<p>")


class TestGlossaryInjectionFidelity:
    """The provider's own words survive the injection character-for-character."""

    def test_stripping_the_wrappers_returns_the_provider_text(self) -> None:
        """Subtracting every injected wrapper restores the input exactly."""
        source = (
            "<h1>Snowpack structure</h1>"
            "<p>Individual avalanche prone locations are to be found on "
            "steep shady slopes. The weak layer beneath the melt-freeze "
            "crust remains reactive on all aspects.</p>"
            "<ul><li>Whumpfing was reported near the ridge.</li></ul>"
        )
        result = snowdesk_html(source)
        assert "glossary-term" in result  # the test would be vacuous otherwise
        assert strip_glossary_markup(result) == source

    def test_real_slf_prose_survives_the_injection(
        self, slf_snowpack_comment: str
    ) -> None:
        """Real provider prose round-trips once the wrappers are subtracted."""
        result = snowdesk_html(slf_snowpack_comment)
        assert "glossary-term" in result  # the test would be vacuous otherwise
        assert strip_glossary_markup(result) == sanitise_only(slf_snowpack_comment)


class TestProseTitle:
    """Extracts the leading ``<h1>`` of an SLF prose block as plain text."""

    def test_extracts_leading_h1(self) -> None:
        """The leading ``<h1>`` text is returned stripped of tags."""
        html = "<h1>Weather review for Thursday</h1><p>Overnight…</p>"
        assert prose_title(html, "Weather review") == "Weather review for Thursday"

    def test_tolerates_leading_whitespace(self) -> None:
        """Leading whitespace before ``<h1>`` does not prevent extraction."""
        html = "   \n<h1>Outlook to Sunday</h1><p>…</p>"
        assert prose_title(html, "Outlook") == "Outlook to Sunday"

    def test_tolerates_attributes_on_h1(self) -> None:
        """Attributes on the ``<h1>`` tag do not break extraction."""
        html = '<h1 class="x">Snowpack</h1><p>…</p>'
        assert prose_title(html, "fallback") == "Snowpack"

    def test_strips_inline_tags_from_title(self) -> None:
        """Inline tags inside the ``<h1>`` are stripped from the returned title."""
        html = "<h1>Weather <em>review</em></h1>"
        assert prose_title(html, "fallback") == "Weather review"

    def test_falls_back_when_no_h1(self) -> None:
        """When the prose has no leading ``<h1>``, the fallback is returned."""
        html = "<p>Just a paragraph, no heading.</p>"
        assert prose_title(html, "Snowpack") == "Snowpack"

    def test_falls_back_on_empty_h1(self) -> None:
        """An empty ``<h1>`` body falls back — a blank summary would be useless."""
        html = "<h1></h1><p>body</p>"
        assert prose_title(html, "Snowpack") == "Snowpack"

    def test_none_returns_fallback(self) -> None:
        """``None`` input returns the fallback."""
        assert prose_title(None, "Snowpack") == "Snowpack"

    def test_empty_string_returns_fallback(self) -> None:
        """Empty-string input returns the fallback."""
        assert prose_title("", "Snowpack") == "Snowpack"

    def test_only_first_h1_is_extracted(self) -> None:
        """A second ``<h1>`` later in the prose is ignored."""
        html = "<h1>First</h1><p>x</p><h1>Second</h1>"
        assert prose_title(html, "fallback") == "First"


class TestProseBody:
    """Returns the prose HTML with the leading ``<h1>`` removed."""

    def test_strips_leading_h1(self) -> None:
        """The leading ``<h1>`` is removed; the remainder is returned."""
        html = "<h1>Weather review for Thursday</h1><p>Overnight…</p>"
        assert prose_body(html) == "<p>Overnight…</p>"

    def test_preserves_subsequent_h1(self) -> None:
        """Only the first ``<h1>`` is stripped — later headings stay."""
        html = "<h1>First</h1><p>x</p><h1>Wind</h1><p>y</p>"
        assert prose_body(html) == "<p>x</p><h1>Wind</h1><p>y</p>"

    def test_leaves_body_unchanged_when_no_leading_h1(self) -> None:
        """Prose without a leading ``<h1>`` is returned unchanged."""
        html = "<p>Just a paragraph.</p>"
        assert prose_body(html) == html

    def test_none_returns_empty(self) -> None:
        """``None`` input returns an empty string."""
        assert prose_body(None) == ""

    def test_empty_string_returns_empty(self) -> None:
        """Empty-string input returns an empty string."""
        assert prose_body("") == ""

    def test_handles_whitespace_and_attributes(self) -> None:
        """Leading whitespace and attributes on ``<h1>`` don't leave debris."""
        html = '  <h1 class="x">Snowpack</h1><p>body</p>'
        assert prose_body(html) == "<p>body</p>"


class TestTendencyHasComment:
    """tendency_has_comment returns True only when a tendency entry has non-empty comment."""

    def test_returns_true_when_entry_has_comment(self) -> None:
        """A single entry with a non-empty comment returns True."""
        prose = {
            "tendency": [
                {"comment": "<p>Hazard increasing.</p>", "tendency_type": "increasing"}
            ]
        }
        assert tendency_has_comment(prose) is True

    def test_returns_false_when_all_comments_empty(self) -> None:
        """ALBINA entries with empty comment strings return False."""
        prose = {
            "tendency": [
                {"comment": "", "tendency_type": "steady"},
                {"comment": "", "tendency_type": "steady"},
            ]
        }
        assert tendency_has_comment(prose) is False

    def test_returns_false_when_all_comments_none(self) -> None:
        """Entries with None comment values return False."""
        prose = {
            "tendency": [
                {"comment": None, "tendency_type": "steady"},
            ]
        }
        assert tendency_has_comment(prose) is False

    def test_returns_true_when_one_of_several_has_comment(self) -> None:
        """When multiple entries are present and one has a comment, returns True."""
        prose = {
            "tendency": [
                {"comment": "", "tendency_type": "steady"},
                {"comment": "<p>Next day outlook.</p>", "tendency_type": "decreasing"},
            ]
        }
        assert tendency_has_comment(prose) is True

    def test_returns_false_when_tendency_list_is_empty(self) -> None:
        """An empty tendency list returns False."""
        prose: dict[str, Any] = {"tendency": []}
        assert tendency_has_comment(prose) is False

    def test_returns_false_when_tendency_key_missing(self) -> None:
        """A prose dict with no tendency key returns False."""
        prose = {"snowpack_structure": "<p>Text.</p>"}
        assert tendency_has_comment(prose) is False

    def test_returns_false_when_prose_is_none(self) -> None:
        """``None`` input returns False."""
        assert tendency_has_comment(None) is False

    def test_returns_false_when_prose_is_not_dict(self) -> None:
        """Non-dict input (e.g. a plain string) returns False."""
        assert tendency_has_comment("not a dict") is False  # type: ignore[arg-type]  # testing non-dict robustness

    def test_handles_none_entry_in_tendency_list(self) -> None:
        """A None element inside the tendency list is skipped gracefully."""
        prose = {"tendency": [None, {"comment": "<p>Text.</p>"}]}
        assert tendency_has_comment(prose) is True
