"""
notes_registry.py

Single source of truth for WHICH Notes to Financial Statements are
generated and in WHAT ORDER/NUMBER, driven entirely by the user-editable
note_list_10k.toml / note_list_10q.toml files. No note number and no note
count is hardcoded anywhere in this codebase.

Both notes_agent.py (LLM narrative generation) and notes_config.py (XBRL
tagging of the rendered HTML) import get_selected_notes() from this module
so they always agree on which notes exist, in what order, and under what
number. If they ever disagreed (e.g. one hardcoding "Note 8" while the
other computed a different count), tagging would silently attach the wrong
figures under the wrong heading — this module exists specifically to
prevent that class of bug.

--------------------------------------------------------------------------
HOW NOTE SELECTION WORKS
--------------------------------------------------------------------------
note_list_10k.toml and note_list_10q.toml each contain an ordered TOML
array of tables, one per note the company COULD disclose, e.g.:

    notes_10k = [
        {"select": true,  "title": "Basis of Presentation" },
        {"select": true,  "title": "Summary of Significant Accounting Policies" },
        ...
    ]

Only entries with select = true are generated, and they are numbered 1..N
strictly in the order they appear in the file — NOT by any fixed mapping
from title to number. This means:
  - The 10-Q's notes are typically a subset of the 10-K's, and may change
    from quarter to quarter, simply by editing note_list_10q.toml.
  - The 10-K's own note lineup may change from year to year the same way.
  - Reordering the toml file reorders (and renumbers) the generated notes.

toml file, note_list_10q.toml, use the top-level key "notes_10k" for its array 
toml file, note_list_10q.toml, uses "notes_10q" as top-level key 
_read_note_array() below accepts either name
so this keeps working regardless of which one is actually used.

--------------------------------------------------------------------------
NOTE_TAGGING_METADATA
--------------------------------------------------------------------------
This is the second thing both notes_agent.py and notes_config.py need per
note: which us-gaap textblock concept a note maps to, and (for the notes
whose deterministic figures are inserted via token substitution — see
notes_agent.py) which granular XBRL facts live inside its text block.
This dict is keyed by TITLE (matching the toml "title" field exactly),
never by note number, so re-numbering/reordering notes in the toml file
never requires touching this dict.

The three notes present in the toml files but not yet implemented
("Stock-Based Compensation", "Fair Value Measurements", "Business
Combinations" — select = false in both files as of this writing) have
placeholder metadata below (a best-guess textblock concept, no granular
facts, implemented=False). If one of these is ever flipped to
select = true before real prompt guidance and granular tagging is written
for it, the note will still generate (via notes_agent.py's generic
fallback guidance) and its textblock will still be tagged, just without
any deterministic figure-tagging. FUTURE ENHANCEMENT.

--------------------------------------------------------------------------
STYLE TOLERANCE (DEFAULT_END_OF_NOTES_PATTERN)
--------------------------------------------------------------------------
docx_service.py's create_10k_edgar_html() now builds the entire filing
body with _docx_body_to_html_parts(preserve_style=True) instead of just
the intro/cover fragment (see that function's docstring). Two
consequences for the patterns below:

  1. TAG NAME: "Item 9." / "Item 2." print as whatever heading level the
     source docx paragraph actually used — confirmed against a real
     generated sec-10k.html to be <h3>, not <p>. The 10-K pattern below
     was previously written as literal `<p>Item 9\\.`, which NEVER
     matched that <h3> heading — the last selected note's end boundary
     silently failed to be found regardless of styling, and that note's
     textblock never got tagged (logged as "(end boundary not found)").
     Both patterns are now tag-agnostic (`<[a-zA-Z0-9]+[^>]*>`), matching
     the same convention already used for note headings in
     notes_config.py's _heading_pattern().
  2. STYLE/BOLD: the same tag-agnostic pattern's `[^>]*` already absorbs
     any inline style="..." attribute preserve_style=True adds, and an
     optional `(?:<strong>)?...(?:</strong>)?` pair tolerates the heading
     text being bold-wrapped.

Both patterns still match the exact same plain, unstyled heading text
they matched before — this is purely additive tolerance, verified against
the real generated 10-K HTML.
"""

from __future__ import annotations

import os
import logging
import tomllib
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelectedNote:
    number: int      # 1-indexed, assigned purely by toml order among select=true entries
    title: str       # exact title text from the toml file


# ---------------------------------------------------------------------------
# TOML loading
# ---------------------------------------------------------------------------

def _toml_filename(report_type: str, quarter: int = None) -> str:
    """
    Resolve the note-selection toml filename for this report type.

    10-K always uses note_list_10k.toml. 10-Q now has THREE separate
    quarterly toml files (note_list_10q-q1.toml / -q2.toml / -q3.toml)
    instead of one shared note_list_10q.toml — each quarter's note lineup
    can be configured independently, and each quarter's item files live in
    their own directory tree (see docx_service.py's BASE_10Q + quarter
    directory scheme). quarter is therefore REQUIRED for "10-Q".
    """
    if report_type == "10-K":
        return "note_list_10k.toml"
    if report_type == "10-Q":
        if quarter not in (1, 2, 3):
            raise ValueError(
                f"quarter must be 1, 2, or 3 for report_type '10-Q' (got {quarter!r})"
            )
        return f"note_list_10q-q{quarter}.toml"
    raise ValueError(f"Unknown report_type {report_type!r}; expected '10-K' or '10-Q'.")


def _read_note_array(report_type: str, quarter: int = None) -> list:
    """
    Read the raw, file-ordered list of {"select": bool, "title": str}
    entries for the given report type (and, for a 10-Q, quarter). Returns
    [] if the file is missing, unparsable, or contains no recognizable
    array.
    """
    filename = _toml_filename(report_type, quarter)

    path = os.path.join(settings.DATA_USER_INPUT_DIR, filename)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        logger.warning(f"[notes_registry] {filename} not found at {path}; no notes will be generated.")
        return []
    except Exception as exc:
        logger.warning(f"[notes_registry] Could not parse {filename}: {exc}; no notes will be generated.")
        return []

    # 10-k toml file uses the key "notes_10k" 
    # 10-q toml file uses the key "notes_10q" 
    for key in ("notes_10k", "notes_10q"):
        value = data.get(key)
        if isinstance(value, list):
            return value

    logger.warning(
        f"[notes_registry] {filename} has no 'notes_10k'/'notes_10q' array; no notes will be generated."
    )
    return []


def get_selected_notes(report_type: str, quarter: int = None) -> list[SelectedNote]:
    """
    Return the SELECTED notes for this report type (and, for a 10-Q,
    quarter — REQUIRED, since each quarter now has its own toml file),
    numbered 1..N in the order they appear in the toml file. Entries with
    select = false are skipped entirely (they do not consume a number).
    """
    raw = _read_note_array(report_type, quarter)
    selected = [entry for entry in raw if entry.get("select") and entry.get("title")]
    return [
        SelectedNote(number=i + 1, title=str(entry["title"]).strip())
        for i, entry in enumerate(selected)
    ]


# ---------------------------------------------------------------------------
# XBRL tagging metadata, keyed by title (never by number)
# ---------------------------------------------------------------------------

# Proper thousands-grouped integer — matches "7,009,009" but stops before a
# trailing sentence comma the way a greedy [\d,]+ would not (see
# notes_config.py's original docstring for the full rationale).
_INT = r'(?:\d{1,3}(?:,\d{3})*)'

# Matches the digit/comma amount immediately after a "$" that itself
# immediately follows a closing ">" — i.e. the numeric content of any
# "<td ...>$1,200,000</td>"-style cell, regardless of the <td>'s
# attributes (Python's re module only allows FIXED-width lookbehind, so
# this can't include the variable-width "<td ...>label</td><td>$" prefix).
# Despite the name, this is a generic table-cell-amount pattern, not
# lease-specific — it's also reused below for the "Short-term and
# Long-term Debt" note's maturity table, which produces cells in the
# identical "<td>$1,234,567</td>" shape.
_LEASE_AMOUNT_PATTERN = rf'(?<=>\$)({_INT})'


@dataclass(frozen=True)
class NoteTaggingInfo:
    textblock_concept: str
    granular: Optional[list] = field(default=None)
    implemented: bool = True


NOTE_TAGGING_METADATA: dict = {
    "Basis of Presentation": NoteTaggingInfo(
        textblock_concept="us-gaap:BasisOfAccountingPolicyPolicyTextBlock",
    ),
    "Summary of Significant Accounting Policies": NoteTaggingInfo(
        textblock_concept="us-gaap:SignificantAccountingPoliciesTextBlock",
        # "...gross carrying amount of $X and is depreciated ...;
        # depreciation expense for the year was $Y." (10-K) or "...
        # depreciation expense for the nine months ended September 30,
        # 2025 was $Y." (10-Q) — see notes_agent.py's
        # _build_ppe_depreciation_sentence(). PP&E gross is a balance-sheet-
        # date balance (instant); depreciation expense is period activity
        # (duration, the note's own default context). The period phrase
        # between "for" and "was $" varies by form type and (for a 10-Q)
        # by quarter/date, so it can't be a fixed-width lookbehind — matched
        # as part of the pattern instead (see _make_granular()'s docstring
        # in xbrl_tagger.py for why that's safe here).
        granular=[
            (rf'(?<=gross carrying amount of \$)({_INT})',
             "PropertyPlantAndEquipmentGross", "ixt:num-dot-decimal", "USD", "instant"),
            (rf'depreciation expense for [^$]*? was \$({_INT})',
             "Depreciation", "ixt:num-dot-decimal", "USD"),
        ],
    ),
    "Segment Information": NoteTaggingInfo(
        textblock_concept="us-gaap:SegmentReportingDisclosureTextBlock",
        # "Total operating revenue for the year was $X" (10-K) or "...for
        # the nine months ended September 30, 2025 was $X" (10-Q) — see
        # notes_agent.py's _build_revenue_detail_sentence(). This is the
        # OPERATING revenue figure (excludes Non-Operating Revenue like
        # interest income), matching the income statement's own Total
        # Revenue subtotal, so it's safe to tag as a consistent duplicate
        # of us-gaap:Revenues. Same variable-length-prefix reasoning as the
        # depreciation pattern above.
        granular=[
            (rf'Total operating revenue for [^$]*? was \$({_INT})',
             "Revenues", "ixt:num-dot-decimal", "USD"),
        ],
    ),
    "Income Taxes": NoteTaggingInfo(
        textblock_concept="us-gaap:IncomeTaxDisclosureTextBlock",
        granular=[
            (r'Income tax expense for [^$]*? was \$([\d]{1,3}(?:,\d{3})*)',
             "IncomeTaxExpenseBenefit", "ixt:num-dot-decimal", "USD"),
            (r'(?<=representing an effective tax rate of )([\d]+\.[\d]+)',
             "EffectiveIncomeTaxRateContinuingOperations",
             "ixt:num-dot-decimal", "pure", "duration", "3", "-2"),
            (r'(?<=pre-tax income of \$)([\d]{1,3}(?:,\d{3})*)',
             "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
             "ixt:num-dot-decimal", "USD"),
        ],
    ),
    "Earnings Per Share": NoteTaggingInfo(
        textblock_concept="us-gaap:EarningsPerShareTextBlock",
        granular=[
            (r'(?<=was \$)([\d]+\.[\d]+)',
             "EarningsPerShareBasic", "ixt:num-dot-decimal", "usdPerShare"),
            (r'(?<=net income of \$)([\d]{1,3}(?:,\d{3})*)',
             "NetIncomeLoss", "ixt:num-dot-decimal", "USD"),
            (r'(?<=shares of )([\d,]+)',
             "WeightedAverageNumberOfSharesOutstandingBasic",
             "ixt:num-dot-decimal", "shares"),
            (r'(?<=also \$)([\d]+\.[\d]+)',
             "EarningsPerShareDiluted", "ixt:num-dot-decimal", "usdPerShare"),
        ],
    ),
    # Renamed from "Risk Management Activities and Fair Value" to match
    # note_list_10k.toml / note_list_10q.toml's "Commitments and
    # Contingencies" entry. Content (credit/liquidity/market risk, debt at
    # fair value) is unchanged for now — only the heading text changes.
    # Ron may want to revisit textblock_concept if this note's content is
    # later reworked toward litigation/purchase-commitment disclosures
    # more typical of a GAAP "Commitments and Contingencies" note.
    "Commitments and Contingencies": NoteTaggingInfo(
        textblock_concept="us-gaap:CommitmentsAndContingenciesDisclosureTextBlock",
    ),
    "Short-term and Long-term Debt": NoteTaggingInfo(
        textblock_concept="us-gaap:DebtDisclosureTextBlock",
        granular=[
            (r'(?<=Short-term debt outstanding was \$)([\d]{1,3}(?:,\d{3})*)',
             "ShortTermBorrowings", "ixt:num-dot-decimal", "USD", "instant"),
            (r'(?<=long-term debt outstanding was \$)([\d]{1,3}(?:,\d{3})*)',
             "LongTermDebtNoncurrent", "ixt:num-dot-decimal", "USD", "instant"),
            # Maturity table (notes_agent.py's _build_debt_maturity_table_html)
            # — same positional-matching approach as the Leases table below.
            # Scoped to long-term debt ONLY (loan_rows filtered to
            # term_months > 12 in compute_note_info_node), matching
            # Amazon's real 10-K structure for this schedule — that's why
            # the Total row below tags as plain LongTermDebt rather than
            # something that would double-count ShortTermBorrowings.
            #
            # CAUTION: this still only matches correctly against the
            # 7-cell "real 5-year schedule" branch of that function
            # (Year1..Year5, Thereafter, Total). When
            # ni["debt_maturity_available"] is False (no loan_rows
            # supplied), that function falls back to a DIFFERENT 3-cell
            # shape that mixes short- and long-term debt ("Due within one
            # year" / "Due after one year" / "Total") — this positional
            # list would mistag that shape (e.g. "Due after one year" as
            # Year Two). If the fallback branch is ever expected to fire
            # in practice, this granular list needs to become conditional
            # on debt_maturity_available (e.g. by making `granular` a
            # callable like notes_agent.py's `guidance`/`build_replacement`
            # lambdas) rather than a static list.
            # Arelle confirmed these six are INSTANT-type concepts in the
            # taxonomy (a future amount as of the balance-sheet date, like
            # the rest of a maturity schedule) — not duration facts, despite
            # naming like "InNextTwelveMonths" that might suggest otherwise.
            # Originally tagged "duration" here, which Arelle flagged as
            # [xbrl.4.7.2:contextPeriodType] ... period type instant
            # conflict with context — fixed to "instant" below.
            (_LEASE_AMOUNT_PATTERN,
             "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN,
             "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN,
             "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN,
             "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN,
             "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN,
             "LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive",
             "ixt:num-dot-decimal", "USD", "instant"),
            # Total row — confirmed against Amazon's real 10-K, which tags
            # the total-of-schedule row with plain us-gaap:LongTermDebt
            # (an instant fact, balance-sheet-date total), not a schedule-
            # specific "total" concept.
            (_LEASE_AMOUNT_PATTERN,
             "LongTermDebt", "ixt:num-dot-decimal", "USD", "instant"),
            # Rate breakdown table (notes_agent.py's
            # _build_debt_rate_breakdown_html) — now scoped to long-term
            # loans only (see _build_debt_rate_breakdown's docstring), so
            # debt_weighted_avg_rate is a genuine long-term-debt rate,
            # matching MSFT's real us-gaap:ShortTermDebtWeightedAverage-
            # InterestRate but for the long-term side. The same value
            # appears twice in the table ("At or below X%" / "Above X%");
            # anchoring on "At or below " only tags the first occurrence,
            # since re.sub with count=1 stops there — the second, identical
            # number is left as plain (untagged) text, which is fine since
            # the fact is already captured once. The two dollar-bucket
            # amounts either side of it have no standard us-gaap concept
            # (neither Amazon nor MSFT discloses an above/below-average-
            # rate split) and stay untagged.
            #
            # NOTE the concept name's casing: the real taxonomy element is
            # "Longterm" (lowercase "term"), confirmed against a real
            # filed 10-K's XBRL — us-gaap_LongtermDebtWeightedAverage-
            # InterestRate — NOT "LongTerm" as the rest of this codebase
            # capitalizes it (LongTermDebt, LongTermDebtNoncurrent, etc).
            # XBRL concept names are case-sensitive; the original
            # "LongTermDebtWeightedAverageInterestRate" doesn't exist in
            # the schema at all, which is why Arelle reported
            # [ix11.12.1.2:missingReferences] rather than a value error.
            (r'(?<=At or below )([\d]+\.[\d]+)(?=% \(weighted average\))',
             "LongtermDebtWeightedAverageInterestRate",
             "ixt:num-dot-decimal", "pure", "instant", "4", "-2"),
        ],
    ),
    # Renamed from "Leases and Commitments" to "Leases" to match
    # note_list_10k.toml / note_list_10q.toml. General commitments and
    # contingencies content now belongs to the "Commitments and
    # Contingencies" note above instead of being folded in here — see
    # notes_agent.py's guidance for this note.
    "Leases": NoteTaggingInfo(
        textblock_concept="us-gaap:LesseeOperatingLeaseLiabilityMaturityTableTextBlock",
        granular=[
            (_LEASE_AMOUNT_PATTERN, "LesseeOperatingLeaseLiabilityPaymentsDueNextTwelveMonths",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN, "LesseeOperatingLeaseLiabilityPaymentsDueYearTwo",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN, "LesseeOperatingLeaseLiabilityPaymentsDueYearThree",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN, "LesseeOperatingLeaseLiabilityPaymentsDueYearFour",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN, "LesseeOperatingLeaseLiabilityPaymentsDueYearFive",
             "ixt:num-dot-decimal", "USD", "instant"),
            (_LEASE_AMOUNT_PATTERN, "LesseeOperatingLeaseLiabilityPaymentsDue",
             "ixt:num-dot-decimal", "USD", "instant"),
            (r'Operating cash flow for [^$]*? was \$([\d]{1,3}(?:,\d{3})*)',
             "NetCashProvidedByUsedInOperatingActivities", "ixt:num-dot-decimal", "USD"),
        ],
    ),
    # --- Not yet implemented — present in the toml files for future use ---
    # (select = false in both note_list_10k.toml and note_list_10q.toml as
    # of this writing). See module docstring above. FUTURE ENHANCEMENT.
    "Stock-Based Compensation": NoteTaggingInfo(
        textblock_concept="us-gaap:ShareBasedCompensationTextBlock",
        implemented=False,
    ),
    "Fair Value Measurements": NoteTaggingInfo(
        textblock_concept="us-gaap:FairValueDisclosuresTextBlock",
        implemented=False,
    ),
    "Business Combinations": NoteTaggingInfo(
        textblock_concept="us-gaap:BusinessCombinationDisclosureTextBlock",
        implemented=False,
    ),
}


def get_tagging_info(title: str) -> NoteTaggingInfo:
    """
    Look up XBRL tagging metadata for a note title. Unknown titles (a
    brand-new note added to the toml file with no matching entry above)
    fall back to an UNTAGGED result rather than guessing at a us-gaap
    concept name that might not exist in the taxonomy — add a real
    NoteTaggingInfo entry to NOTE_TAGGING_METADATA above for any genuinely
    new note title.
    """
    info = NOTE_TAGGING_METADATA.get(title)
    if info is None:
        logger.warning(
            f"[notes_registry] No tagging metadata for note title {title!r}; "
            "this note's textblock will not be XBRL-tagged. Add a "
            "NoteTaggingInfo entry to NOTE_TAGGING_METADATA in notes_registry.py."
        )
        return NoteTaggingInfo(textblock_concept="", implemented=False)
    return info


# ---------------------------------------------------------------------------
# Default end-of-notes boundary, by report type. Used for the LAST
# selected note's end_pattern in notes_config.py, since there is no "next
# note heading" to bound against for the final note.
#
# Tag-agnostic (`<[a-zA-Z0-9]+[^>]*>`) and tolerant of an optional
# <strong> wrapper — see the module docstring's "STYLE TOLERANCE" section
# above for why (fixes a real bug: the 10-K pattern's previous literal
# `<p>Item 9\.` never matched the actual <h3> heading, silently breaking
# the last selected note's end-boundary detection and leaving its
# textblock untagged).
# ---------------------------------------------------------------------------

DEFAULT_END_OF_NOTES_PATTERN = {
    # Verified against the actual generated sec-10k.html: the notes
    # section is immediately followed by an <h3>Item 9. ...</h3> heading
    # in Part II (not a <p>, as an earlier version of this pattern
    # assumed).
    "10-K": r"<[a-zA-Z0-9]+[^>]*>(?:<strong>)?\s*Item 9\.\s*Changes in and Disagreements",
    # NOT yet verified against a real generated sec-10q.html — this is a
    # reasonable guess (Item 1's Notes are usually followed by Item 2,
    # MD&A) but should be confirmed. Pass notes_end_pattern explicitly to
    # get_notes_config() if the real boundary text differs.
    "10-Q": r"<[a-zA-Z0-9]+[^>]*>(?:<strong>)?\s*Item 2\.\s*Management's Discussion",
}

