"""
notes_config.py

Builds the `notes` list to pass into xbrl_tagger.tag_filing() for the
"Notes to Consolidated Financial Statements" section of sec-10-k.html /
sec-10q.html.

--------------------------------------------------------------------------
DYNAMIC NOTE SELECTION (no hardcoded note numbers)
--------------------------------------------------------------------------
Which notes exist, in what order, and under what number is no longer
fixed in this file. It's read from note_list_10k.toml / note_list_10q.toml
via notes_registry.get_selected_notes() — the SAME function notes_agent.py
uses to generate the narrative — so the heading text this module searches
for always matches what was actually written to the document. Re-ordering
or adding/removing notes in the toml file requires no change here at all;
this file only needs updating when a genuinely NEW note title needs
XBRL-tagging metadata (see notes_registry.NOTE_TAGGING_METADATA).

heading_pattern / end_pattern are built from each SelectedNote's number and
title, e.g. note number 3 titled "Segment Information" produces a pattern
that matches (among other equivalent shapes):
    <p>Note 3 – Segment Information</p>
    <p style="...">Note 3 – Segment Information</p>
    <p style="..."><strong>Note 3 – Segment Information</strong></p>
For the last selected note, end_pattern falls back to
notes_registry.DEFAULT_END_OF_NOTES_PATTERN[report_type] (or an explicit
notes_end_pattern override passed in), since there is no next note heading
to bound against.

The "." in "Note 3 . Segment Information" is a regex wildcard, not a
literal period — it matches whatever single character the docx→HTML
round-trip produces for the "–" (en dash) between the number and the
title. In practice this is now always a literal "-" (hyphen): tag_filing()
substitutes every en dash for a hyphen as part of its EFM 5.02.01.01
Unicode sanitization pass, which runs BEFORE tag_notes_section() ever sees
the HTML. The wildcard is kept anyway as a defensive catch-all in case
that substitution list ever changes.

--------------------------------------------------------------------------
STYLE TOLERANCE (heading/end patterns now used against STYLED body HTML)
--------------------------------------------------------------------------
docx_service.py's create_10k_edgar_html() now builds the ENTIRE filing
body (not just the intro/cover) with _docx_body_to_html_parts(
preserve_style=True) — see that function's docstring. Every paragraph
picks up a real inline style="..." attribute, and any run that was bold
in the source docx gets wrapped in <strong>...</strong>. Both
_heading_pattern() below and notes_registry.DEFAULT_END_OF_NOTES_PATTERN
are written to tolerate both of these additions:
  - the opening tag's `[^>]*` already absorbs any attributes, styled or
    not (this needed no change);
  - an optional `(?:<strong>)?` / `(?:</strong>)?` pair now wraps the
    matched title text, in case whatever produced the note heading
    paragraph (the TipTap round-trip via HtmlToDocx — see
    notes_config.py's original tag-agnostic-heading comment below)
    applied bold directly to that run instead of a Heading style.
This tolerance is additive — it still matches the exact same plain,
unstyled markup the regexes matched before this change, so nothing here
depends on preserve_style actually being True.

--------------------------------------------------------------------------
GRANULAR TAGGING STRATEGY (unchanged from the original design)
--------------------------------------------------------------------------
Segment Information, Income Taxes, Earnings Per Share, Short-term and
Long-term Debt, and Leases' granular facts are all anchored on FIXED,
deterministically-built sentences from notes_agent.py
(_build_revenue_detail_sentence, _build_tax_detail_sentence,
_build_eps_detail_sentence, _build_debt_detail_sentence, and the lease
table + operating-cash-flow sentence) — NOT on assumed free-form LLM
phrasing. An earlier version anchored directly on prompted wording ("was
$X million", "representing X% of") that worked for a while, then silently
broke when the model reworded the sentence. Since notes_agent.py now
guarantees the exact wording for these specific facts via token
substitution, the granular patterns (kept in notes_registry.py, keyed by
title) can safely assume that exact wording. These granular patterns need
NO style tolerance: the narrative sentences they anchor on are plain body
text, never bold, so they're unaffected by the preserve_style=True switch
either way.

Numeric patterns use a proper thousands-grouping capture group
(\\d{1,3}(?:,\\d{3})*) rather than a greedy [\\d,]+ — the greedy version
will happily consume a trailing sentence comma (e.g. matching "7,009,009,"
including the comma before "representing"), which is invalid input for
the ixt:num-dot-decimal transform.

Usage:
    from app.agents.notes_config import get_notes_config
    tag_filing(
        ...,
        notes=get_notes_config(fiscal_year, period_start, period_end, report_type),
    )
"""

import logging

from app.agents.notes_registry import (
    get_selected_notes,
    get_tagging_info,
    DEFAULT_END_OF_NOTES_PATTERN,
)

logger = logging.getLogger(__name__)


def _heading_pattern(number: int, title: str) -> str:
    # Tag-agnostic on purpose: the docx→HTML round-trip does not reliably
    # produce the same wrapping tag for every note heading — some come out
    # as <p>...</p>, others as <h1>...</h6> (depending on whatever style the
    # narrative picked up upstream, e.g. in the TipTap editor round-trip
    # before create_10k_item082()/create_10q_item011() ever see it).
    # Matching ANY opening/closing tag pair around the heading text — rather
    # than assuming <p> specifically — means a note tagged as a heading
    # level instead of a plain paragraph (or vice versa) still gets found.
    # This is deliberately loose about whether the opening and closing tag
    # NAMES match each other: the heading text itself ("Note N – Title") is
    # specific enough that a false match on unrelated content is not a
    # realistic risk here.
    #
    # `[^>]*` on the opening tag already absorbs any inline style="..."
    # attribute preserve_style=True adds, with no change needed. The
    # optional `(?:<strong>)?...(?:</strong>)?` pair additionally tolerates
    # the heading text being wrapped in bold — the same style-tolerance
    # convention used throughout xbrl_tagger.py's tag_cover_page() and
    # tag_auditor_report_block() regexes.
    return (
        rf"<[a-zA-Z0-9]+[^>]*>(?:<strong>)?\s*Note {number} . {title}"
        rf"(?:</strong>)?\s*</[a-zA-Z0-9]+>"
    )


def get_notes_config(
    fiscal_year: int,
    period_start: str,
    period_end: str,
    report_type: str = "10-K",
    notes_end_pattern: str = None,
    quarter: int = None,
) -> list:
    """
    Parameters
    ----------
    fiscal_year  : the filing's fiscal year as an int (e.g. 2025). Not
                   used directly (notes don't tag by year text), but kept
                   in the signature since it's the natural thing callers
                   have on hand (derived from period_end).
    period_start / period_end : ISO date strings ("YYYY-MM-DD") for the
                   filing's own full fiscal year (10-K) or the relevant
                   period (10-Q). Used as the duration context for every
                   note's textblock fact (and for the granular facts
                   nested inside several notes).
    report_type  : "10-K" or "10-Q" — selects which toml file
                   (note_list_10k.toml, or one of the three quarterly
                   note_list_10q-q{quarter}.toml files) drives note
                   selection, and the default end-of-notes boundary.
    quarter      : 1, 2, or 3 — REQUIRED when report_type="10-Q", since
                   each quarter now has its own toml file
                   (note_list_10q-q1.toml / -q2.toml / -q3.toml) rather
                   than one shared note_list_10q.toml. Ignored for "10-K".
    notes_end_pattern : optional override for the boundary that ends the
                   LAST selected note's textblock (there's no "next note
                   heading" to bound against for it). Defaults to
                   notes_registry.DEFAULT_END_OF_NOTES_PATTERN[report_type].
                   The 10-Q default has not been verified against a real
                   generated sec-10q.html — pass this explicitly if it
                   doesn't match.
    """
    selected = get_selected_notes(report_type, quarter)
    if not selected:
        logger.warning(
            f"[notes_config] No notes selected for {report_type} "
            "(check note_list_10k.toml / note_list_10q.toml); returning an empty notes config."
        )
        return []

    if notes_end_pattern is None:
        notes_end_pattern = DEFAULT_END_OF_NOTES_PATTERN.get(report_type)
        if notes_end_pattern is None:
            raise ValueError(
                f"No default end-of-notes pattern for report_type {report_type!r}; "
                "pass notes_end_pattern explicitly."
            )

    headings = [_heading_pattern(note.number, note.title) for note in selected]

    config = []
    for i, note in enumerate(selected):
        tagging = get_tagging_info(note.title)
        if not tagging.textblock_concept:
            # Unknown title with no NOTE_TAGGING_METADATA entry — skip
            # tagging this note (it still appears in the document, just
            # untagged). Doesn't affect neighboring notes' boundaries,
            # since each note's own heading text is always present in the
            # HTML regardless of whether it ends up in this tagging list.
            continue

        end_pattern = headings[i + 1] if i + 1 < len(headings) else notes_end_pattern

        entry = {
            "heading_pattern": headings[i],
            "end_pattern": end_pattern,
            "textblock_concept": tagging.textblock_concept,
            "period_start": period_start,
            "period_end": period_end,
        }
        if tagging.granular:
            entry["granular"] = tagging.granular
        config.append(entry)

    return config

