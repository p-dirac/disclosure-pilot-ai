"""
Multi-agent LangGraph workflow for generating Notes to Financial Statements
using ChatOllama.

Workflow:
  analyze_financials → validate_financials → compute_note_info → enrich_context
      → generate_notes → compliance_review → END

Notes generated:
  WHICH notes get generated, in what order, and under what number is no
  longer fixed here — it's read from note_list_10k.toml / note_list_10q.toml
  via notes_registry.get_selected_notes(), which returns only the notes
  marked select = true, numbered 1..N in the order they appear in the
  applicable toml file. This lets the 10-Q's note lineup be a (changeable)
  subset of the 10-K's, and lets the 10-K's own lineup change from year to
  year, without touching any code. See notes_registry.py for the full
  design and notes_registry.NOTE_TAGGING_METADATA for which note titles
  currently have dedicated prompt guidance and deterministic figure
  substitution (_NOTE_HANDLERS below) vs. a generic fallback (currently:
  Stock-Based Compensation, Fair Value Measurements, Business
  Combinations — FUTURE ENHANCEMENT).

Output files:
  10-K: item082-Notes-to-Financial-Statements.docx  → DATA_10K_PART2
  10-Q: item011-Notes-to-Financial-Statements.docx  → DATA_10Q_PART1
"""

import logging
import os
import re
import calendar
from datetime import date
from typing import Optional, TypedDict

import tomllib
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph

from app.agents.notes_registry import SelectedNote, get_selected_notes
from app.core.config import settings
from app.schemas.schemas import BalanceSheetRow, CashFlowRow, IncomeStatementRow

logger = logging.getLogger(__name__)

ollama_model = os.getenv("OLLAMA_MODEL",    "gemma3")
ollama_url   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

logger.info(f"[notes_agent] OLLAMA_MODEL: {ollama_model}")
logger.info(f"[notes_agent] OLLAMA_BASE_URL: {ollama_url}")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class NoteInfo(TypedDict):
    """Computed values that feed the eight notes."""
    # Human-readable period phrase for note sentences — "the year" for a
    # 10-K, or "the three/six/nine months ended <date>" for a 10-Q. Every
    # note sentence that used to hardcode "for the year" now uses this
    # instead, so a 10-Q's notes describe its actual YTD period rather than
    # always claiming to be annual — the underlying NUMBERS were already
    # fixed to be YTD-aware (see _cur()/_pri() below), but the WORDING
    # around them wasn't, which is exactly as misleading as a wrong number.
    period_phrase:           str
    # Column headers for the Segment Information revenue-by-category table
    # — "Year ended<br/>December 31, 2025" (10-K) or "Nine Months
    # ended<br/>September 30, 2025" etc. (10-Q), same duration-aware idea
    # as period_phrase above but capitalized/formatted for a table header
    # rather than mid-sentence prose, and computed for BOTH the current and
    # prior comparative period. <br/> (not "\n") is what actually wraps a
    # line in the rendered HTML table.
    table_header_current:    str
    table_header_prior:      str
    # Balance sheet totals
    total_assets_current:   float
    total_assets_prior:     float
    total_liab_current:     float
    total_liab_prior:       float
    total_equity_current:   float
    total_equity_prior:     float
    # Short-term / long-term debt (from balance sheet rows by account name)
    short_term_debt:        float
    long_term_debt:         float
    # Income statement totals
    total_revenue_current:  float
    total_revenue_prior:    float
    total_expense_current:  float
    total_expense_prior:    float
    net_income_current:     float
    net_income_prior:       float
    # Tax-related
    income_tax_expense:     float
    pretax_income:          float
    effective_tax_rate_pct: float
    # EPS-related
    shares_outstanding:     float     # sourced from env / toml; default 1 000 000
    basic_eps:              float
    diluted_eps:            float
    # Depreciation & PP&E (Leases note)
    depreciation_current:   float
    ppe_gross:              float
    # Accounts payable / receivable (shown in the shared data block for context)
    accounts_receivable:    float
    accounts_payable:       float
    # Operating cash flow (Note 8 commitments context)
    operating_cash_flow:    float
    # Entity-wide disclosure inputs (Note 3, ASC 280) — derived only from
    # categories/amounts already present in income_stmt_rows, never invented
    # labels or a fabricated split.
    revenue_by_category:    list[str]   # e.g. ["Hardware: $59,850,000", ...] (current year, inline sentence)
    revenue_by_category_detail: list    # [(name, current_amt, prior_amt), ...] for the 2-year table
    expense_category_labels: str        # e.g. "Cost of Goods Sold, Salaries and Wages, ..."
    # Real 5-year debt maturity schedule (ASC 470-10-50-1), computed by
    # amortizing bookkeeper.loans — None/empty if no loan_rows were supplied,
    # in which case Note 7 falls back to stating only the short-term/
    # long-term split rather than fabricating a 5-year breakdown.
    debt_maturity_years:       list[int]
    debt_maturity_amounts:     list[float]   # aligned with debt_maturity_years
    debt_maturity_thereafter:  float
    debt_maturity_total:       float
    debt_maturity_available:   bool
    # Rate-range breakdown (also from bookkeeper.loans) — real per-loan
    # rate data, not an estimated split.
    debt_weighted_avg_rate:    float
    debt_rate_at_or_below_avg: float
    debt_rate_above_avg:       float


class BusinessContext(TypedDict):
    company_name:            str
    business_description:    str
    industry:                str
    geographic_focus:        str
    fiscal_year:             str
    shares_outstanding:      str
    income_tax_rate:         str
    functional_currency:     str
    reporting_currency:      str
    lease_description:       str
    lease_payment_yr1:       str
    lease_payment_yr2:       str
    lease_payment_yr3:       str
    lease_payment_yr4:       str
    lease_payment_yr5:       str
    lease_payment_total:     str
    lease_payment_years:     list[str]   # e.g. ["2026", "2027", ... "2030"]
    debt_description:        str
    auditor_firm:            str
    debt_schedule:           str
    legal_proceedings:       str
    purchase_commitments:    str
    codm_title:              str


class ValidationResult(TypedDict):
    passed:   bool
    warnings: list[str]


class LoanRow(TypedDict):
    """Mirrors bookkeeper.loans exactly (see db-loans.sql) — one row per loan."""
    loan_id:         str
    asset_id:        str
    principal:       float
    rate:            float   # annual rate; may be stored as a fraction (0.055) or
                              # a percentage (5.5) — normalized in _amortize_loan.
    term_months:     int
    start_date:      date
    monthly_payment: float


class NotesState(TypedDict):
    report_type:  str           # "10-Q" or "10-K"
    year:         int
    quarter:      Optional[int]
    period_label: str
    prior_label:  str

    # Business context supplied by the frontend (optional user overrides)
    notes_context: Optional[dict]

    # Raw financial rows (passed in at graph invocation)
    balance_sheet_rows:  list[BalanceSheetRow]
    income_stmt_rows:    list[IncomeStatementRow]
    cash_flow_rows:      list[CashFlowRow]
    # Optional: per-loan detail from bookkeeper.loans, enabling a REAL 5-year
    # debt maturity schedule (Note 7 / ASC 470-10-50-1) computed by
    # amortizing each loan rather than an undifferentiated short/long-term
    # split. If not supplied by the caller, compute_note_info_node falls
    # back to the short-term/long-term split only — it never fabricates a
    # 5-year breakdown without real per-loan data to support one.
    loan_rows:           Optional[list[LoanRow]]

    # Plain-English summaries built by analyze_financials_node
    balance_sheet_summary: str
    income_summary:        str
    cash_flow_summary:     str

    # Derived by nodes
    note_info:             NoteInfo
    context:               BusinessContext
    validation:            ValidationResult
    notes_narrative:       str
    compliance_notes:      list[str]
    status:                str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct_change(current: float, prior: float) -> float:
    if prior == 0:
        return 0.0
    return round((current - prior) / abs(prior) * 100, 1)


def _fmt(n: float) -> str:
    """Format a dollar amount for narrative prose."""
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.1f} million"
    return f"${n:,.0f}"


# Small local models (e.g. Gemma3 via Ollama) are unreliable at rendering a
# literal HTML table when asked to do so inline — in practice they tend to
# flatten it into a run-on string ("2026$1,200,0002027$1,200,000..."). Rather
# than trust the model to draw the Note 8 lease maturity schedule, the prompt
# asks it to drop in this exact token, and the real table (built
# deterministically from the context data) is substituted in afterward.
_LEASE_TABLE_TOKEN = "{{LEASE_MATURITY_TABLE}}"


def _build_lease_maturity_table_html(ctx: "BusinessContext") -> str:
    """
    Deterministically build the Note 8 lease maturity schedule as an HTML
    table directly from context values — not the LLM — so the figures and
    table structure are always correct regardless of model behavior.
    """
    yearly = [
        (ctx["lease_payment_years"][0], ctx["lease_payment_yr1"]),
        (ctx["lease_payment_years"][1], ctx["lease_payment_yr2"]),
        (ctx["lease_payment_years"][2], ctx["lease_payment_yr3"]),
        (ctx["lease_payment_years"][3], ctx["lease_payment_yr4"]),
        (ctx["lease_payment_years"][4], ctx["lease_payment_yr5"]),
    ]
    rows = "".join(f"<tr><td>{yr}</td><td>{amt}</td></tr>" for yr, amt in yearly)
    return (
        "<table>"
        "<tr><th>Fiscal Year</th><th>Payment Amount</th></tr>"
        f"{rows}"
        f"<tr><td>Total</td><td>{ctx['lease_payment_total']}</td></tr>"
        "</table>"
    )


# Same rationale as the lease table above, applied to Note 5: regex-matching
# arbitrary LLM-authored prose to find "the EPS number" is unreliable — in
# practice Gemma3 rewords the sentence structure freely (e.g. writing net
# income as "Net income ... was $37.9 million" ahead of the real EPS figure),
# which caused xbrl_tagger.py's granular pattern (anchored on the phrase
# "was $") to tag the WRONG number (net income instead of EPS). Rather than
# chase ever-more-specific regex anchors against prose the model is free to
# rephrase, Note 5's numbers are built deterministically in Python with a
# FIXED, known wording, and the LLM is only asked to insert this exact token
# for it (see the Note 5 prompt guidance below) — same pattern as the lease
# table.
_EPS_DETAIL_TOKEN = "{{EPS_DETAIL_SENTENCE}}"


def _build_eps_detail_sentence(ni: "NoteInfo") -> str:
    """
    Deterministically build the sentence stating basic/diluted EPS, net
    income, and weighted-average shares outstanding, with fixed wording so
    xbrl_tagger.py's granular regex patterns (in notes_config.py) can
    reliably find each figure regardless of what the LLM writes around it.
    """
    shares_fmt = f"{int(round(ni['shares_outstanding'])):,}"
    net_income_fmt = f"{ni['net_income_current']:,.0f}"
    return (
        f"Basic earnings per share was ${ni['basic_eps']:.4f}, calculated by "
        f"dividing net income of ${net_income_fmt} by the weighted average "
        f"number of outstanding common shares of {shares_fmt}. Diluted "
        f"earnings per share was also ${ni['diluted_eps']:.4f}."
    )


# Note 2's PP&E/depreciation figures had the SAME failure mode as every
# other note above, before this fix: the guidance simply handed the model
# two literal numbers ("...using the gross figure $X and depreciation
# expense $Y...") and trusted it to restate them correctly. Observed
# failure: the model didn't misstate the figure, it invented its OWN
# "{{SOMETHING}}"-style placeholder instead of writing the number at all
# ("Depreciation expense totaled {{REPARATION_EXPENSE}} during the year") —
# a new variant of the same underlying problem (free-form figure-copying
# is unreliable), just manifesting as a fabricated token instead of a wrong
# number. Since compliance_review_node's placeholder auto-fill is a
# best-effort SECOND LLM call (and can itself fail to fill correctly, or
# not run at all if some other step already used the day's LLM budget),
# it's a safety net, not a substitute for not needing the model to state
# this figure freely in the first place. Same deterministic-sentence-plus-
# token approach as every other numeric note now applies here too.
_PPE_DEPRECIATION_TOKEN = "{{PPE_DEPRECIATION_SENTENCE}}"


def _build_ppe_depreciation_sentence(ni: "NoteInfo") -> str:
    """
    Deterministically build the sentence stating PP&E gross carrying
    amount and current-year depreciation expense, with fixed wording so
    the figures are never left to the model to restate (or, as observed,
    to invent a placeholder for instead).
    """
    ppe_gross_fmt = f"{ni['ppe_gross']:,.0f}"
    dep_fmt = f"{ni['depreciation_current']:,.0f}"
    return (
        f"Property, plant, and equipment (PP&E) is reported at a gross carrying "
        f"amount of ${ppe_gross_fmt} and is depreciated using the straight-line "
        f"method over its estimated useful life; depreciation expense for "
        f"{ni['period_phrase']} was ${dep_fmt}."
    )


# Note 3's total operating revenue, Note 4's tax detail, and Note 7's debt
# figures all had the SAME failure mode as Note 5's original EPS bug: a
# regex anchored on assumed LLM phrasing ("was $", "representing X% of")
# worked for a while, then silently broke (Note 7's debt tags stopped
# matching entirely) once the model reworded the sentence ("totaled $" /
# "balance of $" instead of "was $"). Rather than keep patching regexes
# against prose the model is free to rephrase, these three notes now use
# the same deterministic-sentence-plus-token approach as Note 5 and Note 8.
_REVENUE_DETAIL_TOKEN = "{{REVENUE_DETAIL_SENTENCE}}"


def _build_revenue_detail_sentence(ni: "NoteInfo") -> str:
    """Deterministically state Note 3's total operating revenue figure."""
    return f"Total operating revenue for {ni['period_phrase']} was ${ni['total_revenue_current']:,.0f}."


def _build_revenue_by_category_table_html(ni: "NoteInfo") -> str:
    """
    2-year comparative revenue-by-category table (current vs. prior period),
    built from income_stmt_rows — which already carries both periods, so no
    new data is needed. A genuine 3rd year isn't available anywhere in this
    pipeline yet (see the comment in compute_note_info_node), so this stays
    at 2 years rather than fabricating an earlier one. Includes a Total row
    summing every category for both periods.
    """
    rows = "".join(
        f"<tr><td>{name}</td><td>${cur:,.0f}</td><td>${prior:,.0f}</td></tr>"
        for name, cur, prior in ni["revenue_by_category_detail"]
    )
    total_cur = sum(cur for _, cur, _ in ni["revenue_by_category_detail"])
    total_prior = sum(prior for _, _, prior in ni["revenue_by_category_detail"])
    return (
        "<table>"
        f"<tr><th>Category</th><th>{ni['table_header_current']}</th><th>{ni['table_header_prior']}</th></tr>"
        f"{rows}"
        f"<tr><td>Total Revenue</td><td>${total_cur:,.0f}</td><td>${total_prior:,.0f}</td></tr>"
        "</table>"
    )


def _build_segment_detail_sentence(ni: "NoteInfo") -> str:
    """
    Deterministically build Note 3's full quantitative disclosure: total
    operating revenue, revenue by category (the ASC 280/ASU 2023-07
    entity-wide product/service-line disclosure — built from real
    categories already on income_stmt_rows, never an invented split), a
    2-year comparative table for that same breakdown, and segment
    profit/assets, which for a single reportable segment equal the
    Company's consolidated net income and total assets by definition, not
    a separately invented figure.
    """
    revenue_detail = _build_revenue_detail_sentence(ni)
    breakdown = (
        f" Revenue by category was: {'; '.join(ni['revenue_by_category'])}."
        if ni["revenue_by_category"] else ""
    )
    table = (
        f" The following table presents revenue by category for the current and prior year: "
        f"{_build_revenue_by_category_table_html(ni)}"
        if ni["revenue_by_category_detail"] else ""
    )
    segment_metrics = (
        f" Segment profit was ${ni['net_income_current']:,.0f} and total segment assets were "
        f"${ni['total_assets_current']:,.0f}, consistent with the Company's consolidated totals "
        "given its single reportable segment."
    )
    return f"{revenue_detail}{breakdown}{table}{segment_metrics}"


_TAX_DETAIL_TOKEN = "{{TAX_DETAIL_SENTENCE}}"


def _build_tax_detail_sentence(ni: "NoteInfo") -> str:
    """
    Deterministically state Note 4's income tax expense, effective tax
    rate, and pre-tax income together in one fixed sentence.
    """
    return (
        f"Income tax expense for {ni['period_phrase']} was ${ni['income_tax_expense']:,.0f}, "
        f"representing an effective tax rate of {ni['effective_tax_rate_pct']}% "
        f"of pre-tax income of ${ni['pretax_income']:,.0f}."
    )


_DEBT_DETAIL_TOKEN = "{{DEBT_DETAIL_SENTENCE}}"


def _build_debt_detail_sentence(ni: "NoteInfo") -> str:
    """Deterministically state Note 7's short-term and long-term debt balances."""
    return (
        f"Short-term debt outstanding was ${ni['short_term_debt']:,.0f}, and "
        f"long-term debt outstanding was ${ni['long_term_debt']:,.0f}."
    )


def _build_debt_maturity_table_html(ni: "NoteInfo") -> str:
    """
    Build Note 7's ASC 470-10-50-1 five-year maturity table — long-term
    debt only, matching Amazon's real 10-K structure ("future principal
    payments for our total long-term debt"). The caller now filters
    loan_rows down to long-term instruments (term_months > 12) before this
    ever runs; a short-term revolving credit facility isn't part of this
    schedule at all.

    If bookkeeper.loans data was supplied (ni['debt_maturity_available']),
    this is a REAL per-loan amortization schedule (see
    _compute_debt_maturity_schedule) — each future year's figure is the sum
    of actual principal due that year across every long-term loan, not a
    guessed split of a lump balance.

    If no loan data was supplied, falls back to the only two real figures
    available — amounts due within one year vs. after one year, matching
    the balance sheet's own short-term/long-term split — rather than
    fabricating a five-year breakdown with nothing behind it. This
    fallback intentionally does NOT invent per-year amounts for years 2-5.
    NOTE: this fallback still mixes short- and long-term debt (it's the
    only real data available without loan-level detail) and does not
    correspond to the long-term-only granular XBRL tags added for the
    real-schedule branch above — see notes_registry.py's caution comment
    on the "Short-term and Long-term Debt" note's granular list.
    """
    if ni["debt_maturity_available"]:
        rows = "".join(
            f"<tr><td>{yr}</td><td>${amt:,.0f}</td></tr>"
            for yr, amt in zip(ni["debt_maturity_years"], ni["debt_maturity_amounts"])
        )
        return (
            "<table>"
            "<tr><th>Fiscal Year</th><th>Principal Due</th></tr>"
            f"{rows}"
            f"<tr><td>Thereafter</td><td>${ni['debt_maturity_thereafter']:,.0f}</td></tr>"
            f"<tr><td>Total</td><td>${ni['debt_maturity_total']:,.0f}</td></tr>"
            "</table>"
        )
    return (
        "<table>"
        "<tr><th>Maturity</th><th>Amount</th></tr>"
        f"<tr><td>Due within one year</td><td>${ni['short_term_debt']:,.0f}</td></tr>"
        f"<tr><td>Due after one year</td><td>${ni['long_term_debt']:,.0f}</td></tr>"
        f"<tr><td>Total</td><td>${ni['short_term_debt'] + ni['long_term_debt']:,.0f}</td></tr>"
        "</table>"
    )


def _build_debt_rate_breakdown_html(ni: "NoteInfo") -> str:
    """Real rate breakdown for long-term debt only, split on its own balance-weighted average interest rate."""
    return (
        "<table>"
        "<tr><th>Interest Rate</th><th>Outstanding Balance</th></tr>"
        f"<tr><td>At or below {ni['debt_weighted_avg_rate']*100:.2f}% (weighted average)</td>"
        f"<td>${ni['debt_rate_at_or_below_avg']:,.0f}</td></tr>"
        f"<tr><td>Above {ni['debt_weighted_avg_rate']*100:.2f}% (weighted average)</td>"
        f"<td>${ni['debt_rate_above_avg']:,.0f}</td></tr>"
        "</table>"
    )


def _build_debt_note_replacement(ni: "NoteInfo") -> str:
    """
    Combined replacement for Note 7's single token: the existing debt
    balance sentence (kept word-for-word, since notes_config.py's granular
    XBRL tagging regex is anchored to its exact wording), the maturity
    table, and — when bookkeeper.loans data is available — a rate-range
    breakdown. All pieces stay behind the SAME token — the substitution
    mechanism supports one token per note, so this note's entire
    quantitative payload travels together.
    """
    rate_block = (
        f" The following table summarizes outstanding long-term debt by interest rate: "
        f"{_build_debt_rate_breakdown_html(ni)}"
        if ni["debt_maturity_available"] else ""
    )
    return (
        f"{_build_debt_detail_sentence(ni)} The following table summarizes the Company's long-term debt "
        f"by contractual maturity: {_build_debt_maturity_table_html(ni)}{rate_block}"
    )


def _get_llm(temp: 0.3) -> ChatOllama:
    # Model loads into GPU on llm script start.
    # After llm script finishes, "keep_alive"    
    # triggers GPU model unloading after 10 min. 
    # num_ctx: context window (in tokens) the model uses to remember 
    # conversation history and input prompt.
    # num_predict: maximum number of tokens the model is allowed to 
    # generate in its response.
    llm = ChatOllama(
        model=ollama_model, 
        base_url=ollama_url, 
        num_ctx=16384,      
        num_predict=2048,    
        temperature=temp, 
        model_kwargs={"keep_alive": "10m"}  
        )
    return llm


def _summarize_balance_sheet(rows: list[BalanceSheetRow], period: str, prior: str) -> str:
    assets      = [r for r in rows if "Asset"     in r.category]
    liabilities = [r for r in rows if "Liability" in r.category]
    equity      = [r for r in rows if "Equity"    in r.category]
    ta_c = sum(r.current_period for r in assets)
    ta_p = sum(r.prior_period   for r in assets)
    tl_c = sum(r.current_period for r in liabilities)
    te_c = sum(r.current_period for r in equity)
    return (
        f"Balance Sheet as of {period}: "
        f"Total Assets {_fmt(ta_c)} (prior {_fmt(ta_p)}), "
        f"Total Liabilities {_fmt(tl_c)}, Total Equity {_fmt(te_c)}."
    )


def _summarize_income(rows: list[IncomeStatementRow], period: str) -> str:
    revenues = [r for r in rows if "Revenue" in r.category or "Income" in r.category]
    expenses = [r for r in rows if r.category not in ("Revenue", "Income")]
    tr_c = sum(r.current_period      for r in revenues)
    tr_p = sum(r.prior_period        for r in revenues)
    te_c = sum(abs(r.current_period) for r in expenses)
    net  = tr_c - te_c
    return (
        f"Income Statement for {period}: "
        f"Revenue {_fmt(tr_c)} (prior {_fmt(tr_p)}), "
        f"Expenses {_fmt(te_c)}, Net Income {_fmt(net)}."
    )


def _summarize_cash_flow(rows: list[CashFlowRow], period: str) -> str:
    op = next((r for r in rows if "Net Cash from Operating" in r.description), None)
    if op:
        return (
            f"Cash Flow for {period}: "
            f"Operating Cash {_fmt(op.current_period)} (prior {_fmt(op.prior_period)})."
        )
    return f"Cash flow data available for {period}."


# ---------------------------------------------------------------------------
# Node 1 – analyze_financials_node
# ---------------------------------------------------------------------------

def analyze_financials_node(state: NotesState) -> NotesState:
    """Build plain-English summaries from raw rows."""
    state["balance_sheet_summary"] = _summarize_balance_sheet(
        state["balance_sheet_rows"], state["period_label"], state["prior_label"]
    )
    state["income_summary"] = _summarize_income(
        state["income_stmt_rows"], state["period_label"]
    )
    state["cash_flow_summary"] = _summarize_cash_flow(
        state["cash_flow_rows"], state["period_label"]
    )
    state["status"] = "analyzed"
    return state


# ---------------------------------------------------------------------------
# Node 2 – validate_financials_node
# ---------------------------------------------------------------------------

def validate_financials_node(state: NotesState) -> NotesState:
    """Sanity-check the financial data before downstream nodes consume it."""
    warnings: list[str] = []
    rows_bs = state["balance_sheet_rows"]
    rows_is = state["income_stmt_rows"]
    rows_cf = state["cash_flow_rows"]

    total_assets = sum(r.current_period for r in rows_bs if "Asset"     in r.category)
    total_liab   = sum(r.current_period for r in rows_bs if "Liability" in r.category)
    total_equity = sum(r.current_period for r in rows_bs if "Equity"    in r.category)

    lhs, rhs = total_assets, total_liab + total_equity
    if lhs != 0 and abs(lhs - rhs) / abs(lhs) > 0.01:
        warnings.append(
            f"Balance sheet may not balance: Assets={_fmt(lhs)}, "
            f"Liabilities+Equity={_fmt(rhs)} (diff {_fmt(lhs - rhs)})."
        )
    if total_equity < 0:
        warnings.append("Negative total equity detected — verify retained earnings.")

    revenues = sum(
        r.current_period for r in rows_is
        if "Revenue" in r.category or "Income" in r.category
    )
    if revenues == 0:
        warnings.append("No revenue found in income statement.")

    if not rows_cf:
        warnings.append("Cash flow statement is empty.")

    passed = len(warnings) == 0
    for w in warnings:
        logger.warning(f"[validate_financials] {w}")

    state["validation"] = ValidationResult(passed=passed, warnings=warnings)
    state["status"] = "validated"
    return state


# ---------------------------------------------------------------------------
# Node 3 – compute_note_info_node
# ---------------------------------------------------------------------------

def _add_months(d: date, months: int) -> date:
    """Add whole months to a date, clamping the day to the target month's length."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _amortize_loan_future_principal(
    loan: LoanRow, as_of: date, future_years: list[int],
) -> dict[int, float]:
    """
    Simulate ONE loan's monthly amortization from its start_date using the
    loan's own stored monthly_payment (never a recomputed/assumed payment —
    the actual figure on file is used), and bucket the PRINCIPAL portion of
    every payment due AFTER `as_of` by calendar year, for each year in
    `future_years` (the next 5 fiscal years). Anything paid after the last
    of those years is implicitly excluded here and rolled into "thereafter"
    by the caller (see _compute_debt_maturity_schedule).

    `rate` is normalized to a decimal: bookkeeper.loans.rate is
    numeric(15,4) with no documented convention, so any value >= 1 is
    treated as a whole-number percentage (e.g. 5.5 -> 0.055) rather than
    assumed to already be a fraction.
    """
    r = loan["rate"] / 100.0 if loan["rate"] >= 1 else loan["rate"]
    monthly_rate = r / 12.0
    balance = loan["principal"]
    per_year = {y: 0.0 for y in future_years}
    for i in range(1, loan["term_months"] + 1):
        if balance <= 0.01:
            break
        interest = balance * monthly_rate
        principal_portion = min(loan["monthly_payment"] - interest, balance)
        pay_date = _add_months(loan["start_date"], i)
        balance = round(balance - principal_portion, 2)
        if pay_date > as_of and pay_date.year in per_year:
            per_year[pay_date.year] += principal_portion
    return per_year


def _normalize_rate(rate: float) -> float:
    """
    Normalize bookkeeper.loans.rate to a decimal fraction. numeric(15,4)
    doesn't document whether a row was entered as a fraction (0.055) or a
    whole-number percentage (5.5), so any value >= 1 is treated as a
    percentage. Shared by every function that reads loan.rate so the
    convention can't drift between them.
    """
    return rate / 100.0 if rate >= 1 else rate


def _build_debt_rate_breakdown(loan_rows: list[LoanRow], as_of: date) -> dict[str, float]:
    """
    Bucket each loan's remaining balance (as of `as_of`) into an
    at-or-below/above split — but on the portfolio's own
    balance-weighted average interest rate, not a hardcoded threshold like
    5%, which means nothing once market rates move and was never derived
    from this company's actual debt anyway. The weighted average itself
    (sum of balance*rate / sum of balance — the standard way a "weighted
    average interest rate" is computed for a real ASC 470 disclosure) is
    also returned, since it's a legitimate figure to disclose on its own,
    the same way the Leases note discloses a weighted-average discount
    rate.

    `loan_rows` here is expected to already be filtered to long-term
    loans only (term_months > 12) by the caller, matching the maturity
    table above — a revolving credit facility's rate shouldn't be blended
    into a "long-term debt" weighted average.
    """
    balances = [_loan_balance_as_of(loan, as_of) for loan in loan_rows]
    rates = [_normalize_rate(loan["rate"]) for loan in loan_rows]
    total_balance = sum(balances)
    weighted_avg_rate = (
        sum(b * r for b, r in zip(balances, rates)) / total_balance
        if total_balance > 0 else 0.0
    )

    at_or_below_avg = 0.0
    above_avg = 0.0
    for balance, rate in zip(balances, rates):
        if rate <= weighted_avg_rate:
            at_or_below_avg += balance
        else:
            above_avg += balance
    return {
        "weighted_avg_rate": weighted_avg_rate,
        "at_or_below_avg": at_or_below_avg,
        "above_avg": above_avg,
    }


def _loan_balance_as_of(loan: LoanRow, as_of: date) -> float:
    """
    Remaining principal balance for one loan as of a given date — the
    portion of _amortize_loan_future_principal's simulation that only needs
    the balance, not the future bucketing, kept as a separate pass so
    _compute_debt_maturity_schedule can cross-check the loans-table-derived
    total debt against the GL-derived short_term_debt + long_term_debt.
    """
    r = _normalize_rate(loan["rate"])
    monthly_rate = r / 12.0
    balance = loan["principal"]
    for i in range(1, loan["term_months"] + 1):
        if balance <= 0.01:
            break
        pay_date = _add_months(loan["start_date"], i)
        if pay_date > as_of:
            break
        interest = balance * monthly_rate
        principal_portion = min(loan["monthly_payment"] - interest, balance)
        balance = round(balance - principal_portion, 2)
    return max(balance, 0.0)


def _compute_debt_maturity_schedule(
    loan_rows: list[LoanRow], as_of: date, num_years: int = 5,
) -> Optional[dict]:
    """
    Aggregate a real 5-year-plus-thereafter debt maturity schedule (ASC
    470-10-50-1) across every loan in bookkeeper.loans, by amortizing each
    one individually rather than splitting a lump short/long-term balance
    across invented yearly buckets. Returns None if no loan rows are
    supplied, so the caller can fall back to the short-term/long-term split
    instead of fabricating a schedule with nothing behind it.
    """
    if not loan_rows:
        return None

    future_years = [as_of.year + i for i in range(1, num_years + 1)]
    per_year_total = {y: 0.0 for y in future_years}
    total_balance = 0.0
    for loan in loan_rows:
        per_year_total_this_loan = _amortize_loan_future_principal(loan, as_of, future_years)
        for y, amt in per_year_total_this_loan.items():
            per_year_total[y] += amt
        total_balance += _loan_balance_as_of(loan, as_of)

    scheduled_within_window = sum(per_year_total.values())
    thereafter = max(total_balance - scheduled_within_window, 0.0)

    return {
        "years": future_years,
        "per_year": per_year_total,
        "thereafter": thereafter,
        "total_balance": total_balance,
    }


def compute_note_info_node(state: NotesState) -> NotesState:
    """
    Derive the quantitative inputs for all eight notes:
    - Balance sheet totals (assets, liabilities, equity)
    - Short-term and long-term debt balances (Note 7)
    - Income statement totals and tax figures (Notes 4, 5)
    - EPS from net income ÷ shares outstanding (Note 5)
    - Depreciation, PP&E, AR, AP (Notes 1, 2, 6, 8)
    - Operating cash flow (Note 8)
    """
    rows_bs = state["balance_sheet_rows"]
    rows_is = state["income_stmt_rows"]
    rows_cf = state["cash_flow_rows"]
    ctx_raw = state.get("notes_context") or {}

    # Notes to Financial Statements are always tagged (via notes_config.py)
    # at the filing's YTD context — for a 10-K that's the same as the full
    # fiscal year, but for a 10-Q it's Jan 1 through the current quarter-end,
    # NOT the standalone quarter. IncomeStatementRow.current_period/
    # prior_period hold the QUARTER-only figures on a 10-Q (matching the
    # Income Statement's own "Three Months Ended" columns) while
    # .ytd_current/.ytd_prior hold the YTD figures. Every income-statement-
    # derived note figure below (revenue, tax, pretax income, net income,
    # EPS, depreciation) needs the YTD value on a 10-Q, or it silently tags
    # a quarter-only number at a YTD context — a real (concept, context)
    # collision against the Income Statement table's own correct YTD figure,
    # which is exactly what Arelle's arelle:duplicateFacts / oime:
    # disallowedDuplicateFacts errors were catching. Balance-sheet rows
    # (point-in-time, no YTD concept applies) and cash-flow rows (already
    # YTD by construction on a 10-Q — see build_cash_flow) are unaffected
    # and keep using current_period/prior_period as before.
    is_10q = state["report_type"] == "10-Q"

    # Every note sentence that used to hardcode "for the year" now uses
    # this instead (see NoteInfo.period_phrase's docstring for why: the
    # underlying NUMBERS were already fixed to be YTD-aware below, but the
    # WORDING around them wasn't, which is exactly as misleading as a wrong
    # number would be). Duration words match docx_service.py's own
    # YTD_DURATION convention ("Three/Six/Nine Months").
    if is_10q and state.get("quarter"):
        _q_end_months   = {1: 3, 2: 6, 3: 9}
        _duration_words = {1: "three months", 2: "six months", 3: "nine months"}
        _duration_cap   = {1: "Three Months", 2: "Six Months", 3: "Nine Months"}
        _end_month = _q_end_months[state["quarter"]]
        _end_date_cur = date(state["year"],     _end_month, calendar.monthrange(state["year"],     _end_month)[1])
        _end_date_pri = date(state["year"] - 1, _end_month, calendar.monthrange(state["year"] - 1, _end_month)[1])
        period_phrase = f"the {_duration_words[state['quarter']]} ended {_end_date_cur.strftime('%B %d, %Y')}"
        # Table header wraps after "ended" — <br/> is what actually
        # produces a line break in the rendered HTML table (a literal "\n"
        # would just collapse to a space).
        table_header_current = f"{_duration_cap[state['quarter']]} ended<br/>{_end_date_cur.strftime('%B %d, %Y')}"
        table_header_prior   = f"{_duration_cap[state['quarter']]} ended<br/>{_end_date_pri.strftime('%B %d, %Y')}"
    else:
        period_phrase = "the year"
        table_header_current = f"Year ended<br/>December 31, {state['year']}"
        table_header_prior   = f"Year ended<br/>December 31, {state['year'] - 1}"

    def _cur(r):
        if is_10q and getattr(r, "ytd_current", None) is not None:
            return r.ytd_current
        return r.current_period

    def _pri(r):
        if is_10q and getattr(r, "ytd_prior", None) is not None:
            return r.ytd_prior
        return r.prior_period

    # ── Balance sheet totals ──────────────────────────────────────────────────
    assets_c = [r for r in rows_bs if "Asset"     in r.category]
    liab_c   = [r for r in rows_bs if "Liability" in r.category]
    equity_c = [r for r in rows_bs if "Equity"    in r.category]

    ta_cur = sum(r.current_period for r in assets_c)
    ta_pri = sum(r.prior_period   for r in assets_c)
    tl_cur = sum(r.current_period for r in liab_c)
    tl_pri = sum(r.prior_period   for r in liab_c)
    te_cur = sum(r.current_period for r in equity_c)
    te_pri = sum(r.prior_period   for r in equity_c)

    # ── Debt (Note 7) — match by account name keywords ───────────────────────
    short_debt = sum(
        abs(r.current_period) for r in rows_bs
        if any(k in r.acct_name for k in ("Short-term Debt", "Short-Term Debt", "Current Portion"))
    )
    long_debt = sum(
        abs(r.current_period) for r in rows_bs
        if any(k in r.acct_name for k in ("Long-term Debt", "Long-Term Debt", "Notes Payable"))
    )

    # ── Income statement totals ───────────────────────────────────────────────
    rev_rows = [r for r in rows_is if "Revenue" in r.category or "Income" in r.category]
    # logger.info(f"rev_rows: {rev_rows}")
    exp_rows = [r for r in rows_is if r.category not in ("Revenue", "Income")]
    tr_cur = sum(_cur(r)      for r in rev_rows)
    tr_pri = sum(_pri(r)        for r in rev_rows)
    te_cur_is = sum(abs(_cur(r)) for r in exp_rows)
    te_pri_is = sum(abs(_pri(r))   for r in exp_rows)
    ni_cur = tr_cur - te_cur_is
    ni_pri = tr_pri - te_pri_is

    # Note 3 (Segment Information) reports OPERATING revenue only. Interest
    # Income (account 4300) has category="Revenue" but acct_subtype=
    # "Non-Operating Revenue" — it legitimately belongs in tr_cur/ni_cur
    # above (net income must include ALL income, operating and non-
    # operating), but it does NOT belong in the "total revenue" figure
    # disclosed by segment/geography, which should match the income
    # statement's own Total Revenue subtotal (110,930,751 / $110.9 million
    # — excludes interest income). Kept as a SEPARATE sum rather than
    # changing rev_rows/tr_cur directly, since that would silently also
    # subtract interest income out of net income, EPS, retained earnings,
    # and everything else downstream of ni_cur.
    operating_rev_rows = [r for r in rev_rows if r.acct_subtype != "Non-Operating Revenue"]
    op_rev_cur = sum(_cur(r) for r in operating_rev_rows)
    op_rev_pri = sum(_pri(r)   for r in operating_rev_rows)

    # ── Entity-wide disclosure inputs (Note 3, ASC 280 / ASU 2023-07) ────────
    # Grouped by acct_name (the specific product/service line — e.g.
    # "Hardware", "Software", "Consulting" — however the chart of accounts
    # actually names them) rather than the broader accounting `category`
    # bucket ("Product Revenue"/"Service Revenue"), which is too coarse for
    # a real product-line disclosure and would otherwise collapse distinct
    # revenue streams into one or two generic labels. Still never an
    # invented product line or a fabricated split of a lump sum — if the
    # chart of accounts only has one revenue account, this collapses to a
    # single line, which is the honest answer for that data shape.
    #
    # Both current AND prior period are captured here (income_stmt_rows
    # already carries both — no new data needed) for a real 2-year
    # comparative table. A genuine 3rd year isn't available anywhere in
    # this pipeline yet, so the table stays at 2 years rather than
    # fabricating an earlier one.
    rev_by_cat: dict = {}
    rev_by_cat_prior: dict = {}
    for r in operating_rev_rows:
        rev_by_cat[r.acct_name] = rev_by_cat.get(r.acct_name, 0.0) + _cur(r)
        rev_by_cat_prior[r.acct_name] = rev_by_cat_prior.get(r.acct_name, 0.0) + _pri(r)
    revenue_by_category = [f"{name}: ${amt:,.0f}" for name, amt in rev_by_cat.items()]
    revenue_by_category_detail = [
        (name, amt, rev_by_cat_prior.get(name, 0.0)) for name, amt in rev_by_cat.items()
    ]

    expense_categories_seen = sorted({r.category for r in exp_rows if r.category})
    expense_category_labels = ", ".join(expense_categories_seen)

    # ── Debt maturity schedule (Note 7, ASC 470-10-50-1) ─────────────────────
    q_end_months = {1: 3, 2: 6, 3: 9, 4: 12}
    if state["report_type"] == "10-Q" and state.get("quarter"):
        end_month = q_end_months[state["quarter"]]
        as_of = date(state["year"], end_month, calendar.monthrange(state["year"], end_month)[1])
    else:
        as_of = date(state["year"], 12, 31)

    loan_rows = state.get("loan_rows")
    # Amazon's real 10-K structure for this schedule — "future principal
    # payments for our total long-term debt" — covers long-term debt only;
    # a revolving credit facility or other short-term instrument isn't part
    # of it. bookkeeper.loans has no explicit short/long-term flag, but
    # term_months gives us one: a loan with an ORIGINAL term over 12 months
    # is long-term debt by definition (ASC 470), even though its next 12
    # months of principal show up as the schedule's own "next twelve
    # months" row — a loan with a ≤12-month original term (e.g. the
    # revolving credit facility) is a short-term instrument and is excluded
    # entirely, not just from the "thereafter" bucket.
    long_term_loan_rows = (
        [l for l in loan_rows if l["term_months"] > 12] if loan_rows else None
    )
    schedule = (
        _compute_debt_maturity_schedule(long_term_loan_rows, as_of)
        if long_term_loan_rows else None
    )
    if schedule:
        debt_maturity_years      = schedule["years"]
        debt_maturity_amounts    = [schedule["per_year"][y] for y in schedule["years"]]
        debt_maturity_thereafter = schedule["thereafter"]
        debt_maturity_total      = schedule["total_balance"]
        debt_maturity_available  = True
        rate_breakdown           = _build_debt_rate_breakdown(long_term_loan_rows, as_of)
        debt_weighted_avg_rate    = rate_breakdown["weighted_avg_rate"]
        debt_rate_at_or_below_avg = rate_breakdown["at_or_below_avg"]
        debt_rate_above_avg       = rate_breakdown["above_avg"]
        # Cross-check against the GL-derived long-term balance only — the
        # schedule is now long-term-debt-only (see above), so it should
        # reconcile against long_debt alone, not short_debt + long_debt.
        if abs(debt_maturity_total - long_debt) > max(1.0, 0.01 * long_debt):
            logger.error(
                f"[compute_note_info] Loan-table long-term debt total ({_fmt(debt_maturity_total)}) "
                f"does not reconcile with GL long-term debt ({_fmt(long_debt)}) — "
                "check bookkeeper.loans (term_months classification) against the chart of accounts."
            )
    else:
        debt_maturity_years      = []
        debt_maturity_amounts    = []
        debt_maturity_thereafter = 0.0
        debt_maturity_total      = 0.0
        debt_maturity_available  = False
        debt_weighted_avg_rate    = 0.0
        debt_rate_at_or_below_avg = 0.0
        debt_rate_above_avg       = 0.0

    # ── Tax (Note 4) ──────────────────────────────────────────────────────────
    # MUST mirror _classify()/_TAX_SUBTYPES in docx_service.py exactly — that
    # function partitions income-statement rows by acct_subtype == "Tax
    # Expense" (not by a substring match on acct_name/category), because
    # that's what actually determines the single "Income Tax Expense" row
    # the person sees on the rendered income statement. An earlier version
    # of this filter used `"Tax" in r.acct_name or "Tax" in r.category`,
    # which is looser — it silently picked up an ADDITIONAL tax-related
    # account (one that has "Tax" in its name/category but a different
    # acct_subtype) that the income statement itself excludes. That
    # produced two different numbers for the same us-gaap:
    # IncomeTaxExpenseBenefit concept (the income statement's real total
    # vs. this note's inflated total) — Arelle correctly flagged that as
    # an inconsistent duplicate fact. Matching the exact same subtype
    # filter keeps the two figures identical by construction.
    tax_exp = sum(
        abs(_cur(r)) for r in rows_is
        if getattr(r, "acct_subtype", None) == "Tax Expense"
    )
    pretax = ni_cur + tax_exp   # add back tax to get pre-tax income

    # The effective tax rate is always computed from GL data — there is no
    # frontend override field for this (replaced by the lease-payment
    # inputs; see BusinessContext.lease_payment_yr1..yr5).
    eff_rate = round(tax_exp / pretax * 100, 1) if pretax != 0 else 0.0

    # ── EPS (Note 5) — shares from notes_context or settings fallback ────────
    # The frontend sends shares_outstanding as a display-formatted string
    # (e.g. "10,000,000"), which float() cannot parse directly — it raises
    # ValueError on the comma, and the except clause below was silently
    # falling back to the hardcoded 1,000,000 default. That's a 10x
    # undercount of the REAL 10,000,000 shares, which is exactly why basic
    # EPS came out 10x too high ($37.9424 instead of $3.7942). Strip
    # commas/$ before parsing, same as _parse_lease_payment_raw does for the
    # lease-payment fields.
    try:
        _toml_for_shares = _load_notes_intro()
        _shares_raw = (ctx_raw.get("shares_outstanding") or
                       _toml_for_shares.get("shares_outstanding") or
                       getattr(settings, "SHARES_OUTSTANDING", 1_000_000))
        shares = float(str(_shares_raw).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        shares = 1_000_000.0
    basic_eps   = round(ni_cur / shares, 4) if shares != 0 else 0.0
    diluted_eps = basic_eps   # simplified: no dilution adjustment in MVP

    # ── Depreciation & PP&E (Notes 2, 8) ─────────────────────────────────────
    # Reverted to the original acct_name/category substring match — there
    # was never a CONFIRMED mismatch for depreciation (only ppe_gross had
    # an actual Arelle duplicate-fact conflict), and the "exact category"
    # version of this same fix (tried below for ppe_gross) turned out to
    # zero this out in production: the real chart_of_acct data apparently
    # categorizes this account under a generic bucket (e.g. "Expense"),
    # not the specific "Depreciation Expense" sub-category, so an exact
    # category match matched nothing.
    # COA: "Depreciation Expense","Expense","Operating Expense"
    dep_cur = sum(
        abs(_cur(r)) for r in rows_is
        if r.acct_name.strip() == "Depreciation Expense"
        #if "Depreciation" in r.acct_name or "Depreciation" in r.category
    )
    # Match by EXACT account NAME — neither a loose acct_name substring
    # search (the original approach) nor an exact CATEGORY match (what
    # this comment used to recommend) is right. The substring version
    # risked sweeping in an unrelated account whose name happens to
    # contain "Property"/"Plant"/"Equip", which is what caused the
    # original Arelle "inconsistent duplicate fact" error ($9,800,000 here
    # vs. $9,771,284 on the balance sheet). Switching to an EXACT category
    # match to fix that overshot the other way and zeroed this out
    # entirely: the real chart_of_acct data evidently categorizes this
    # account under the generic "Asset" bucket, not the specific
    # "Property, Plant & Equip" sub-category, so an exact category match
    # matched zero rows. Matching the exact ACCOUNT NAME instead — the
    # literal label already confirmed to appear as this balance sheet
    # row's own acct_name ("Property, Plant & Equip") — avoids both
    # failure modes: it can't over-match (no substring fuzziness) and
    # can't under-match on category bucketing it doesn't control.
    # COA: "Property, Plant & Equip","Asset","Fixed Asset"
    ppe_gross = sum(
        r.current_period for r in rows_bs
        if r.acct_name.strip() == "Property, Plant & Equip"
    )

    # ── AR / AP (shown in the shared data block for context) ────────────────
    ar = sum(r.current_period for r in rows_bs if "Receivable" in r.acct_name)
    ap = sum(abs(r.current_period) for r in rows_bs if "Payable" in r.acct_name)

    # ── Operating cash flow ───────────────────────────────────────────────────
    op_cf_row = next(
        (r for r in rows_cf if "Net Cash from Operating" in r.description), None
    )
    op_cf = op_cf_row.current_period if op_cf_row else 0.0

    state["note_info"] = NoteInfo(
        period_phrase=period_phrase,
        table_header_current=table_header_current,
        table_header_prior=table_header_prior,
        total_assets_current=ta_cur,
        total_assets_prior=ta_pri,
        total_liab_current=tl_cur,
        total_liab_prior=tl_pri,
        total_equity_current=te_cur,
        total_equity_prior=te_pri,
        short_term_debt=short_debt,
        long_term_debt=long_debt,
        total_revenue_current=op_rev_cur,
        total_revenue_prior=op_rev_pri,
        total_expense_current=te_cur_is,
        total_expense_prior=te_pri_is,
        net_income_current=ni_cur,
        net_income_prior=ni_pri,
        income_tax_expense=tax_exp,
        pretax_income=pretax,
        effective_tax_rate_pct=eff_rate,
        shares_outstanding=shares,
        basic_eps=basic_eps,
        diluted_eps=diluted_eps,
        depreciation_current=dep_cur,
        ppe_gross=ppe_gross,
        accounts_receivable=ar,
        accounts_payable=ap,
        operating_cash_flow=op_cf,
        revenue_by_category=revenue_by_category,
        revenue_by_category_detail=revenue_by_category_detail,
        expense_category_labels=expense_category_labels,
        debt_maturity_years=debt_maturity_years,
        debt_maturity_amounts=debt_maturity_amounts,
        debt_maturity_thereafter=debt_maturity_thereafter,
        debt_maturity_total=debt_maturity_total,
        debt_maturity_available=debt_maturity_available,
        debt_weighted_avg_rate=debt_weighted_avg_rate,
        debt_rate_at_or_below_avg=debt_rate_at_or_below_avg,
        debt_rate_above_avg=debt_rate_above_avg,
    )
    state["status"] = "note_info_computed"
    #logger.info(
    #    f"[compute_note_info] tr_all={_fmt(tr_cur)}, tr_operating={_fmt(op_rev_cur)}, "
    #    f"ni={_fmt(ni_cur)}, tax={_fmt(tax_exp)}, eff_rate={eff_rate}%, EPS={basic_eps}"
    #)
    return state


# ---------------------------------------------------------------------------
# Node 4 – enrich_context_node
# ---------------------------------------------------------------------------

def _load_notes_intro() -> dict:
    """
    Read business_info.toml from DATA_USER_INPUT_DIR.
    Reuses the same TOML file that mda_agent uses, supplemented by
    notes-specific keys if present.
    Returns {} if the file is missing or unparsable.
    """
    toml_path = os.path.join(settings.DATA_USER_INPUT_DIR, "business_info.toml")
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        # logger.info(f"[enrich_context] Loaded business_info.toml from {toml_path}")
        return data
    except FileNotFoundError:
        logger.warning(f"[enrich_context] business_info.toml not found at {toml_path}; using defaults.")
        return {}
    except Exception as exc:
        logger.warning(f"[enrich_context] Could not parse business_info.toml: {exc}; using defaults.")
        return {}


def enrich_context_node(state: NotesState) -> NotesState:
    """
    Populate BusinessContext for Notes generation.

    Priority (highest → lowest):
      1. notes_context dict from the frontend API request
      2. business_info.toml values
      3. Hard-coded neutral fallbacks
    """
    ctx  = state.get("notes_context") or {}
    toml = _load_notes_intro()

    def _get(form_key: str, toml_key: str, fallback: str) -> str:
        return ctx.get(form_key) or toml.get(toml_key) or fallback

    def _parse_lease_payment_raw(yr_key: str) -> float:
        """
        Parse a future annual lease payment override (frontend → toml →
        default) to a raw float. Accepts plain numbers or comma-formatted
        strings (e.g. "1200000" or "1,200,000").
        """
        raw = ctx.get(yr_key) or toml.get(yr_key) or "1200000"
        try:
            return float(str(raw).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            return 1_200_000.0

    def _fmt_lease_payment(val: float) -> str:
        """
        Render a lease payment as a plain dollar figure, e.g. "$1,200,000" —
        NOT the "$1.2 million" abbreviation _fmt() uses elsewhere. The Note 8
        granular tagging regex in xbrl_tagger.py (_LEASE_AMOUNT_PATTERN)
        expects a plain digit/comma string right after "$"; an abbreviated
        "million" figure would silently fail to match and leave these facts
        untagged.
        """
        return f"${val:,.0f}"

    fiscal_year_int = state["year"]
    lease_payment_years = [str(fiscal_year_int + i) for i in range(1, 6)]
    lease_payment_raw = [_parse_lease_payment_raw(f"lease_payment_yr{i}") for i in range(1, 6)]
    lease_payment_total = _fmt_lease_payment(sum(lease_payment_raw))

    ni = state["note_info"]

    state["context"] = BusinessContext(
        company_name=_get("company_name", "company_name", "[Company Name]"),
        business_description=_get(
            "company_description", "business_description",
            "the development, sale, and support of products and services in its core markets"
        ),
        industry=_get("company_industry", "industry", "its primary industry"),
        geographic_focus=_get("company_geo_focus", "geographic_focus", "primarily North America"),
        fiscal_year=str(state["year"]),
        # Notes-specific fields — accept overrides, else use computed/fallback
        shares_outstanding=ctx.get("shares_outstanding") or toml.get("shares_outstanding") or
                           f"{ni['shares_outstanding']:,.0f}",
        income_tax_rate=ctx.get("income_tax_rate") or toml.get("income_tax_rate") or
                        f"{ni['effective_tax_rate_pct']}%",
        functional_currency=_get("functional_currency", "functional_currency", "U.S. dollar (USD)"),
        reporting_currency=_get("reporting_currency", "reporting_currency", "U.S. dollar (USD)"),
        lease_description=_get(
            "lease_description", "lease_description",
            "operating leases for office space and equipment with remaining terms of 1 to 5 years"
        ),
        lease_payment_yr1=_fmt_lease_payment(lease_payment_raw[0]),
        lease_payment_yr2=_fmt_lease_payment(lease_payment_raw[1]),
        lease_payment_yr3=_fmt_lease_payment(lease_payment_raw[2]),
        lease_payment_yr4=_fmt_lease_payment(lease_payment_raw[3]),
        lease_payment_yr5=_fmt_lease_payment(lease_payment_raw[4]),
        lease_payment_total=lease_payment_total,
        lease_payment_years=lease_payment_years,
        debt_description=_get(
            "debt_description", "debt_description",
            "revolving credit facility and term loans at prevailing market interest rates"
        ),
        auditor_firm=_get("auditor_firm", "auditor_firm", "[Auditor Firm Name]"),
        debt_schedule=_get("debt_schedule", "debt_schedule", "monthly"),
        legal_proceedings=_get(
            "legal_proceedings", "legal_proceedings",
            "no material pending legal proceedings other than ordinary routine litigation "
            "incidental to its business"
        ),
        purchase_commitments=_get(
            "purchase_commitments", "purchase_commitments",
            "no significant non-cancelable purchase commitments or guarantees of third-party "
            "obligations outside the ordinary course of business"
        ),
        # ASU 2023-07 requires identifying the CODM by title/role. Defaults to
        # CEO — the common real-world case for a single-segment company —
        # but is overridable via notes_context/business_info.toml like every
        # other business fact here, never hardcoded as fact for every filer.
        codm_title=_get("codm_title", "codm_title", "Chief Executive Officer"),
    )
    state["status"] = "context_enriched"
    return state


# ---------------------------------------------------------------------------
# Node 5 – generate_notes_node
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Synthetic style exemplars — narrative-register guidance only, never a
# source of facts or numbers. These are written fresh for this project, NOT
# copied from any real company's actual filed 10-K: a filed 10-K is still
# that company's copyrighted text (filing something with the SEC doesn't
# place it in the public domain — only works authored by the U.S.
# government itself are public domain), so an excerpt from a real filing
# risks the model closely mimicking or reproducing protected expression.
# These exemplars use an invented company and placeholder-obvious figures
# purely to demonstrate formal SEC prose cadence and structure; the
# guidance below tells the model explicitly not to reuse any fact from them.
# ---------------------------------------------------------------------------
_SEGMENT_STYLE_EXAMPLE = (
    "EXAMPLE STYLE ONLY (invented company, invented numbers — do not reuse any fact, "
    "name, or figure from this example; it exists solely to show tone and structure):\n"
    '  "Example Corp operates as a single reportable segment, providing widgets and '
    "related support services to commercial customers in North America. The Chief "
    "Executive Officer serves as the Company's Chief Operating Decision Maker and "
    "evaluates performance and allocates resources based on consolidated revenue and "
    "operating results, consistent with how the business is managed as a single unit. "
    "Substantially all of the Company's revenue is generated from the sale of "
    "hardware products, with a smaller portion derived from service and support "
    "contracts. Significant expense categories include cost of revenue, personnel-"
    'related costs, and marketing expenditures."'
)

_DEBT_STYLE_EXAMPLE = (
    "EXAMPLE STYLE ONLY (invented company, invented numbers — do not reuse any fact, "
    "name, or figure from this example; it exists solely to show tone and structure):\n"
    '  "Example Corp maintains a revolving credit facility and term loan arrangements '
    "used to fund working capital and general corporate purposes. Borrowings bear "
    "interest at prevailing market rates, and the related agreements contain customary "
    "affirmative and negative covenants. As of the balance sheet date, the Company was "
    'in compliance with all such covenants."'
)


# ---------------------------------------------------------------------------
# Per-note prompt guidance, keyed by TITLE (never by a fixed note number) —
# so this dict stays correct regardless of what number a note ends up with
# once note_list_10k.toml / note_list_10q.toml determine the actual lineup
# and order. Each entry has:
#   "guidance"          : callable(ni, ctx) -> str — the per-note prompt
#                          instructions (NOT including the "Note N – Title:"
#                          heading, which generate_notes_node prepends).
#   "token"              : OPTIONAL exact token the LLM is asked to emit in
#                          place of a figure it must not state itself.
#   "build_replacement"  : OPTIONAL callable(ni, ctx) -> str that builds the
#                          deterministic replacement text for "token".
# A title with NO entry here (e.g. the three not-yet-implemented notes in
# the toml files) gets a generic fallback guidance string instead — see
# generate_notes_node — so selecting one doesn't crash, it just doesn't get
# dedicated wording or granular figure-tagging yet. FUTURE ENHANCEMENT.
_NOTE_HANDLERS = {
    "Basis of Presentation": {
        "guidance": lambda ni, ctx, report_type: (
            "State that the financial statements are prepared in conformity with U.S. GAAP.\n"
            "  Identify the company, reporting period, and reporting currency.\n"
            f"  State the auditing firm: {ctx['auditor_firm']}.\n"
            + (
                "  Note that the interim financial statements are unaudited and reflect all\n"
                "  adjustments necessary for a fair presentation."
                if report_type == "10-Q" else
                "  Note that the annual financial statements have been audited in accordance\n"
                "  with the standards of the Public Company Accounting Oversight Board (United\n"
                "  States) (PCAOB) — do not describe them as unaudited or interim."
            )
        ),
    },
    "Summary of Significant Accounting Policies": {
        "guidance": lambda ni, ctx, report_type: (
            "Cover: revenue recognition, cash and cash equivalents, accounts receivable,\n"
            "  inventories, income taxes, and earnings per share, each in one or two sentences,\n"
            "  in words only — no dollar amounts for any of these topics.\n"
            "  For PP&E, do NOT write your own sentence about the depreciation method, useful\n"
            "  life, or gross/net basis — the token below already states all of that, along\n"
            "  with the real gross carrying amount and depreciation expense figures, so a\n"
            "  sentence written first would only restate it a moment before the token does.\n"
            "  Simply write, on its own line, EXACTLY this token and nothing else:\n"
            f"  {_PPE_DEPRECIATION_TOKEN}"
        ),
        "token": _PPE_DEPRECIATION_TOKEN,
        "build_replacement": lambda ni, ctx: _build_ppe_depreciation_sentence(ni),
    },
    "Segment Information": {
        "guidance": lambda ni, ctx, report_type: (
            "Describe the company's operating segments based on its industry and geography.\n"
            "  If the company operates as a single reportable segment, state that clearly.\n"
            f"  State that the {ctx['codm_title']} serves as the Company's Chief Operating\n"
            "  Decision Maker (CODM) — required by ASU 2023-07 — and that the CODM uses segment\n"
            "  revenue and profit to assess performance and allocate resources, consistent with\n"
            "  managing the business as a single unit.\n"
            + (
                "  Substantially all revenue is derived from a single revenue category — state\n"
                "  this plainly rather than describing a breakdown across categories that don't\n"
                "  exist in the data.\n"
                if len(ni["revenue_by_category"]) <= 1 else
                "  Write a genuine, substantive discussion of revenue by category or geography —\n"
                "  describe the business drivers, trends, and composition in your own words,\n"
                "  simply without citing a specific dollar figure (describe direction and\n"
                "  character of performance instead, e.g. \"revenue is concentrated in...\" or\n"
                "  \"grew due to...\").\n"
            )
            + f"  Name the Company's significant expense categories in words only, no dollar\n"
            f"  figures: {ni['expense_category_labels']}.\n"
            f"{_SEGMENT_STYLE_EXAMPLE}\n"
            "  Write the actual substantive discussion for THIS company using the real facts\n"
            "  above, in your own words — never reuse the example's company name or figures.\n"
            "  Then on its own line write EXACTLY this token and nothing else:\n"
            f"  {_REVENUE_DETAIL_TOKEN}\n"
            "  The token will be replaced automatically with total revenue, revenue by category,\n"
            "  and segment profit/assets — do not restate any of those figures yourself."
        ),
        "token": _REVENUE_DETAIL_TOKEN,
        "build_replacement": lambda ni, ctx: _build_segment_detail_sentence(ni),
    },
    "Income Taxes": {
        "guidance": lambda ni, ctx, report_type: (
            "Write a genuine, substantive discussion of deferred tax assets/liabilities and\n"
            "  any valuation allowances — describe their nature and the company's assessment in\n"
            "  your own words, simply without citing specific dollar amounts, percentages, or\n"
            "  tax rates. The 'more likely than not' realizability conclusion is a SINGLE\n"
            "  assessment that covers both the deferred tax assets and the absence of a\n"
            "  valuation allowance — state it ONCE, in whichever paragraph fits best, and do\n"
            "  not repeat it again later when you discuss the other topic. Write the actual\n"
            "  substantive discussion directly — every note in this filing contains real\n"
            "  analysis, never a placeholder disclaimer. Then on its own line write EXACTLY\n"
            f"  this token and nothing else: {_TAX_DETAIL_TOKEN}"
        ),
        "token": _TAX_DETAIL_TOKEN,
        "build_replacement": lambda ni, ctx: _build_tax_detail_sentence(ni),
    },
    "Earnings Per Share": {
        "guidance": lambda ni, ctx, report_type: (
            "Write a genuine, substantive discussion, in one or two sentences, of what\n"
            "  potentially dilutive securities the company has outstanding (e.g., stock\n"
            "  options, warrants, convertible instruments) and how they would be treated for\n"
            "  diluted EPS purposes if they exist; if none exist, state that basic and diluted\n"
            "  EPS are therefore the same. Keep this discussion entirely qualitative and\n"
            "  focused on the nature of any dilutive securities themselves — the exact share\n"
            "  count, dollar amount, and EPS values are supplied automatically by the token\n"
            "  below. Then on its own line write EXACTLY this token and nothing else:\n"
            f"  {_EPS_DETAIL_TOKEN}\n"
            "  The token will be replaced with the exact basic/diluted EPS, net income, and\n"
            "  weighted-average shares outstanding sentence automatically, guaranteed to match\n"
            "  the figures actually tagged in the filing."
        ),
        "token": _EPS_DETAIL_TOKEN,
        "build_replacement": lambda ni, ctx: _build_eps_detail_sentence(ni),
    },
    # Renamed from "Risk Management Activities and Fair Value" to match
    # note_list_10k.toml / note_list_10q.toml's "Commitments and
    # Contingencies" entry, and rewritten to match the actual GAAP
    # definition of this note (ASC 440 Commitments / ASC 450
    # Contingencies) — legal proceedings, purchase commitments and
    # guarantees, and loss contingencies — rather than the credit-risk /
    # fair-value discussion that used to live here under the old title.
    # That content's textblock_concept was likewise updated in
    # notes_registry.py from us-gaap:FairValueDisclosuresTextBlock to
    # us-gaap:CommitmentsAndContingenciesDisclosureTextBlock to match.
    "Commitments and Contingencies": {
        "guidance": lambda ni, ctx, report_type: (
            "Discuss legal proceedings: state whether the company is currently subject to "
            f"any material legal proceedings, using this as the factual basis: {ctx['legal_proceedings']}.\n"
            "  If no material proceedings exist, say so plainly rather than describing hypothetical risk.\n"
            "  Discuss purchase commitments and guarantees: describe any non-cancelable purchase\n"
            "  obligations, indemnifications, or guarantees of third-party obligations, using this as\n"
            f"  the factual basis: {ctx['purchase_commitments']}.\n"
            "  Discuss loss contingencies per ASC 450: state whether any loss contingencies exist that\n"
            "  are probable and reasonably estimable (which would require accrual), reasonably possible\n"
            "  but not estimable (which would require disclosure only), or remote (which would not\n"
            "  require disclosure) — and which of these categories applies here, consistent with the\n"
            "  legal proceedings and purchase commitments facts above.\n"
            "  Keep this discussion scoped strictly to legal proceedings, purchase commitments,\n"
            "  guarantees, and loss contingencies — credit risk, liquidity risk, interest-rate\n"
            "  risk, and fair value of financial instruments belong to a different note. Describe\n"
            "  nature and category using qualitative language only, in words."
        ),
    },
    "Short-term and Long-term Debt": {
        "guidance": lambda ni, ctx, report_type: (
            (
                "The Company had no outstanding debt as of the balance sheet date. State this "
                "plainly in one or two sentences and do not describe any credit facility, term "
                "loan, or covenant — there is nothing to disclose beyond that fact. Then on its "
                f"own line write EXACTLY this token and nothing else: {_DEBT_DETAIL_TOKEN}"
            )
            if ni["short_term_debt"] == 0 and ni["long_term_debt"] == 0 else
            (
                f"Describe the debt arrangements: {ctx['debt_description']}.\n"
                f"  Mention debt payment schedule: {ctx['debt_schedule']}.\n"
                "  Discuss interest rates, maturity dates, and covenants where applicable, using\n"
                "  qualitative language only — the specific short-term and long-term debt balances\n"
                "  and the maturity schedule are supplied automatically by the token below, so this\n"
                "  discussion itself needs no dollar figures at all.\n"
                "  If the debt description above mentions covenants, state plainly whether the\n"
                "  Company was in compliance with them as of the balance sheet date, based only on\n"
                "  what the description actually says — do not invent a compliance status it\n"
                "  doesn't support, and do not mention covenants at all if none were described.\n"
                f"{_DEBT_STYLE_EXAMPLE}\n"
                "  Write the actual substantive discussion for THIS company using the real facts\n"
                "  above, in your own words — never reuse the example's company name or figures.\n"
                "  Then on its own line write EXACTLY this token and nothing else:\n"
                f"  {_DEBT_DETAIL_TOKEN}"
            )
        ),
        "token": _DEBT_DETAIL_TOKEN,
        "build_replacement": lambda ni, ctx: _build_debt_note_replacement(ni),
    },
    # Renamed from "Leases and Commitments" to "Leases" to match
    # note_list_10k.toml / note_list_10q.toml. The trailing "other
    # contractual commitments and contingencies" discussion is intentionally
    # dropped here — that topic now belongs to the dedicated "Commitments
    # and Contingencies" note above, avoiding duplicated content between
    # the two notes.
    "Leases": {
        "guidance": lambda ni, ctx, report_type: (
            f"Describe lease arrangements: {ctx['lease_description']}.\n"
            "  Report operating lease right-of-use assets and lease liabilities on the balance\n"
            "  sheet, in words only — no dollar figures anywhere in this sentence.\n"
            "  Write one sentence introducing the future minimum lease payment schedule (for example,\n"
            "  \"Future minimum lease payments for the next five years are summarized below:\"), then on\n"
            "  its own line write EXACTLY this token and nothing else — no numbers, no table, no\n"
            f"  commentary, no mention of operating cash flow: {_LEASE_TABLE_TOKEN}\n"
            "  The token will be replaced automatically with the complete, correctly formatted\n"
            "  payment schedule table and the operating cash flow sentence — write only the\n"
            "  one introductory sentence above yourself, and let the token stand alone for\n"
            "  everything else."
        ),
        "token": _LEASE_TABLE_TOKEN,
        "build_replacement": lambda ni, ctx: (
            f"{_build_lease_maturity_table_html(ctx)} "
            f"Operating cash flow for {ni['period_phrase']} was ${ni['operating_cash_flow']:,.0f}."
        ),
    },
}


def _generic_note_guidance(title: str) -> str:
    """
    Fallback prompt guidance for a selected note title with no dedicated
    entry in _NOTE_HANDLERS (currently: Stock-Based Compensation, Fair
    Value Measurements, Business Combinations — see notes_registry.py's
    NOTE_TAGGING_METADATA docstring). FUTURE ENHANCEMENT: replace with
    dedicated guidance + deterministic figure substitution once these
    notes are implemented, the same way the notes above were.
    """
    return (
        "Write a genuine, substantive GAAP-consistent disclosure for this note, using\n"
        "  only the company and financial context provided above. State figures as concrete\n"
        "  dollar amounts only when they are present in the data block; where a specific\n"
        "  figure is not given, describe that topic qualitatively instead."
    )


def _build_note_data_block(ni: NoteInfo, ctx: BusinessContext, report_type: str) -> str:
    """
    Render computed note data into a structured block for the LLM prompt.

    IMPORTANT: income tax expense/rate/pre-tax income, EPS/net income/shares
    outstanding, operating cash flow, and the individual lease-payment
    figures are deliberately NOT included here, even though notes_agent.py
    computes them. Those facts are now stated via deterministic sentences
    (_build_tax_detail_sentence, _build_eps_detail_sentence,
    _build_debt_detail_sentence, _build_lease_maturity_table_html, plus the
    operating cash flow sentence) inserted via token substitution — see
    generate_notes_node. Leaving them in this shared block (visible to the
    model for every note, not just the one that "owns" a given figure)
    caused Gemma3 to restate them in its own prose anyway, DESPITE explicit
    "don't restate this yourself" instructions in the note-specific prompt
    text — the model doesn't reliably honor negative constraints once a
    labeled number is sitting in its context. The result was each figure
    appearing twice per note: once in the model's own (untagged) sentence,
    once in the injected (tagged) sentence. Removing them from here doesn't
    affect the tagged figures' accuracy at all, since the deterministic
    sentence builders read straight from `ni`/`ctx`, never from what the
    model writes.
    """
    direction = lambda pct: "increased" if pct >= 0 else "decreased"
    rev_chg = _pct_change(ni["total_revenue_current"], ni["total_revenue_prior"])
    ni_chg  = _pct_change(ni["net_income_current"],    ni["net_income_prior"])

    block = f"""
COMPANY & PERIOD CONTEXT (use these exact figures — do NOT invent numbers):
  Company:                {ctx['company_name']}
  Description:            {ctx['business_description']}
  Industry:               {ctx['industry']}
  Geographic focus:       {ctx['geographic_focus']}
  Fiscal year:            {ctx['fiscal_year']}
  Functional currency:    {ctx['functional_currency']}
  Reporting currency:     {ctx['reporting_currency']}

BALANCE SHEET DATA:
  Total Assets (current): {_fmt(ni['total_assets_current'])}
  Total Assets (prior):   {_fmt(ni['total_assets_prior'])}
  Total Liabilities:      {_fmt(ni['total_liab_current'])}
  Total Equity:           {_fmt(ni['total_equity_current'])}
  Accounts Receivable:    {_fmt(ni['accounts_receivable'])}
  Accounts Payable:       {_fmt(ni['accounts_payable'])}
  PP&E (gross):           {_fmt(ni['ppe_gross'])}

INCOME STATEMENT DATA:
  Revenue trend:          {direction(rev_chg)} {abs(rev_chg)}% vs prior year
  Total Expenses:         {_fmt(ni['total_expense_current'])}
  Net income trend:       {direction(ni_chg)} {abs(ni_chg)}% vs prior year
  Depreciation:           {_fmt(ni['depreciation_current'])}

ADDITIONAL CONTEXT:
  Lease arrangements:         {ctx['lease_description']}
  Debt arrangements:          {ctx['debt_description']}
"""
    return block


def _ensure_heading_paragraph_breaks(narrative: str, note_headings: list[str]) -> str:
    """
    Guarantee every note heading is wrapped in its own <h3>...</h3>
    element, on its own paragraph, separated from the body text that
    follows.

    _generate_single_note() always returns "<h3>{heading}</h3>\\n\\n{body}"
    — wrapping the heading in <h3> tags is what makes HtmlToDocx render it
    as a real "Heading 3" style paragraph in the docx (left-aligned,
    matching every other section heading in the filing), rather than a
    plain paragraph the model might run straight into the first sentence
    of body text on the same line (observed, back when headings were
    plain text: "Note 5 - Earnings Per Share Basic earnings per share was
    $3.1619..."). Whatever downstream step turns paragraphs into
    <p>/<h3> tags would then produce ONE element containing both the
    heading and the body's opening sentence — which breaks
    notes_config.py's heading_pattern (it expects the heading ALONE in
    its own element), silently leaving that entire note untagged (no
    textblock, no granular facts).

    This must be re-applied any time an LLM call produces or rewrites the
    full narrative — not just the initial generation in generate_notes_node,
    but also compliance_review_node's bracket-fill pass, which asks the
    model to "return the COMPLETE revised narrative" as plain text and can
    just as easily drop, mangle, or fail to preserve the <h3>...</h3>
    wrapper as an earlier version of this same rewrite could merge a bare
    heading onto the body's line.

    `note_headings` is the exact list of "Note N – Title" strings for THIS
    generation (built from notes_registry.get_selected_notes(), so it
    reflects whatever notes/numbers the current toml selection produced —
    no note count or number is assumed here).
    """
    for _title in note_headings:
        # The model/docx round-trip doesn't reliably keep the en-dash in
        # "Note N – Title" — sometimes it comes back as a plain hyphen
        # ("Note N - Title"). Try both so the fix applies regardless.
        for _variant in (_title, _title.replace('–', '-')):
            # First, normalize away any heading tag pair a rewrite may
            # have left directly around this exact text — the right tag,
            # the wrong tag/level, or just a stale wrapper — down to the
            # bare heading text. Without this, the wrap-and-separate step
            # below could nest a brand-new <h3> inside an old, still-
            # present wrapper instead of replacing it.
            narrative = re.sub(
                rf'<h[1-6]>\s*{re.escape(_variant)}\s*</h[1-6]>',
                _variant,
                narrative,
            )
        for _variant in (_title, _title.replace('–', '-')):
            if _variant not in narrative:
                continue
            # Always re-wrap and re-normalize the separator to exactly one
            # blank line — regardless of whether the heading was already
            # followed by a blank line, a single space (the merged-heading
            # bug), or nothing at all. An earlier version of this
            # substitution only fired when the heading ran straight into
            # non-whitespace with no gap, which meant an already
            # well-separated but still-unwrapped heading (e.g. right after
            # the tag-stripping step above) was silently left unwrapped.
            wrapped = f"<h3>{_variant}</h3>"
            _new_narrative = re.sub(
                re.escape(_variant) + r'\s*',
                wrapped + '\n\n',
                narrative,
                count=1,
            )
            if _new_narrative != narrative:
                narrative = _new_narrative
            break
    return narrative


def _build_company_context_block(ctx: BusinessContext) -> str:
    """
    Full company identity — name, business description, industry,
    geographic focus, plus fiscal year/currency. Reserved for the two
    notes whose actual topic IS the business/segment description (Basis
    of Presentation, Segment Information) — see
    _NOTES_NEEDING_FULL_COMPANY_CONTEXT below.
    """
    return f"""COMPANY & PERIOD CONTEXT (use these exact figures — do NOT invent numbers):
  Company:                {ctx['company_name']}
  Description:            {ctx['business_description']}
  Industry:               {ctx['industry']}
  Geographic focus:       {ctx['geographic_focus']}
  Fiscal year:            {ctx['fiscal_year']}
  Functional currency:    {ctx['functional_currency']}
  Reporting currency:     {ctx['reporting_currency']}
"""


def _build_minimal_period_context_block(ctx: BusinessContext) -> str:
    """
    Period/currency identifiers only — no business description, industry,
    or geographic focus.

    Why this exists: every note used to get the full company context block
    above unconditionally, including its business description and
    geographic focus. Observed result: Income Taxes, EPS, and Leases —
    notes with nothing else to open with — all defaulted to restating
    "TIME FLUX LLC is engaged in the development, sale, and support of
    products and services... primarily focusing on North America" almost
    verbatim, duplicating what Notes 1 and 3 already say. Trimming this
    for notes whose actual topic isn't the business description removes
    the temptation at the source, rather than trying to instruct the
    model not to use information it was just handed.
    """
    return f"""PERIOD CONTEXT (use these exact figures — do NOT invent numbers):
  Company:                {ctx['company_name']}
  Fiscal year:            {ctx['fiscal_year']}
  Functional currency:    {ctx['functional_currency']}
  Reporting currency:     {ctx['reporting_currency']}
"""


# Only these two notes' actual subject matter IS the company/business
# description — every other handled note gets the trimmed period-only
# block instead (see _build_minimal_period_context_block). Notes with no
# dedicated handler (future-enhancement titles) still get the full block,
# since their generic guidance may reasonably need to introduce the
# company.
_NOTES_NEEDING_FULL_COMPANY_CONTEXT = {"Basis of Presentation", "Segment Information"}


def _build_trend_data_block(ni: NoteInfo) -> str:
    """Directional revenue/net-income trend only — no dollar figures, since
    these notes discuss direction and character of performance in words,
    not specific amounts (the specific amounts are tagged elsewhere)."""
    direction = lambda pct: "increased" if pct >= 0 else "decreased"
    rev_chg = _pct_change(ni["total_revenue_current"], ni["total_revenue_prior"])
    ni_chg  = _pct_change(ni["net_income_current"],    ni["net_income_prior"])
    return f"""TREND DATA (qualitative direction only — do not restate as a dollar figure):
  Revenue trend:     {direction(rev_chg)} {abs(rev_chg)}% vs prior year
  Net income trend:  {direction(ni_chg)} {abs(ni_chg)}% vs prior year
"""


# Notes whose guidance references directional trend rather than a specific,
# already-embedded ctx figure need the trend block above; every other
# handled note gets everything it needs straight from ctx/its own token, so
# passing it the trend block too would just be unused noise in its prompt.
# Notes with NO dedicated handler (future-enhancement titles) also get the
# trend block by default, since their generic guidance may reasonably touch
# on performance direction.
_NOTES_NEEDING_TREND_DATA = {"Segment Information"}


def _build_tax_status_block(ni: NoteInfo) -> str:
    """
    Qualitative-only profitability/tax-paying fact for the Income Taxes note.

    Why this exists: Income Taxes' guidance asks for a "genuine, substantive
    discussion of deferred tax assets/liabilities and valuation allowances"
    but — like every other handled note — receives no financial data beyond
    company/period identifiers, leaving the model to invent that discussion
    from nothing. Observed result (see notes-log-4): the model defaulted to
    generic pre-revenue-startup boilerplate ("has not generated taxable
    income since inception"), which flatly contradicts the real income tax
    expense and effective rate stated one sentence later by this note's own
    TAX_DETAIL token. This block gives the single fact needed to prevent
    that — profitable and tax-paying, or not — without handing over dollar
    figures or rates, which the guidance already (correctly) keeps out of
    the narrative and reserves for the token.
    """
    if ni["net_income_current"] > 0 and ni["income_tax_expense"] > 0:
        status = (
            "The company was profitable and recognized income tax expense in the current "
            "period. Do not describe it as pre-revenue, loss-making, not yet generating "
            "taxable income, or not expecting to owe tax — none of those are true here."
        )
    elif ni["net_income_current"] <= 0:
        status = (
            "The company reported a net loss in the current period. It is reasonable to "
            "discuss the resulting deferred tax assets and any related valuation allowance "
            "assessment."
        )
    else:
        status = (
            "The company was profitable in the current period but recognized no income tax "
            "expense; discuss valuation allowance or other reasons a profitable company "
            "might have no current tax expense, if relevant, without inventing a specific "
            "cause not evidenced elsewhere."
        )
    return f"TAX STATUS (qualitative only — do not state dollar figures, rates, or percentages here):\n  {status}\n"


_NOTES_NEEDING_TAX_STATUS = {"Income Taxes"}


def _build_per_note_data_block(title: str, ni: NoteInfo, ctx: BusinessContext) -> str:
    """
    Build ONLY the data this one note's guidance actually needs, instead of
    the full balance-sheet/income-statement/lease/debt block every note used
    to receive regardless of relevance. Most handled notes need nothing
    beyond period/currency identifiers because their specific figures are
    already embedded directly in their own guidance text (ctx['debt_description'],
    ctx['legal_proceedings'], etc.) or supplied via their own token.
    """
    if title in _NOTES_NEEDING_FULL_COMPANY_CONTEXT or title not in _NOTE_HANDLERS:
        block = _build_company_context_block(ctx)
    else:
        block = _build_minimal_period_context_block(ctx)
    if title in _NOTES_NEEDING_TREND_DATA or title not in _NOTE_HANDLERS:
        block += "\n" + _build_trend_data_block(ni)
    if title in _NOTES_NEEDING_TAX_STATUS:
        block += "\n" + _build_tax_status_block(ni)
    return block


def _strip_fake_numbers_before_token_in_body(body: str, token: str) -> str:
    """
    Same rationale as the old cross-note fake-number strip, scoped to a
    single note's own body text: despite explicit instructions, the model
    sometimes invents a dollar figure in its own commentary somewhere before
    its token. Since the token's substituted replacement is the ONLY
    guaranteed-correct source of figures for this note, drop any sentence
    containing a dollar-figure pattern anywhere before the token.
    """
    token_idx = body.find(token)
    if token_idx == -1:
        return body
    before = body[:token_idx]
    after = body[token_idx:]
    paragraphs = before.split('\n\n')
    cleaned_paragraphs = []
    for para in paragraphs:
        # Split only on a '.'/'!'/'?' followed by whitespace — not on the
        # decimal point inside a figure like "$73.7", which has no space
        # after the period.
        sentences = re.split(r'(?<=[.!?])\s+', para)
        kept = [s for s in sentences if not re.search(r'\$\d', s)]
        cleaned_para = ' '.join(s for s in kept if s.strip()).strip()
        if cleaned_para:
            cleaned_paragraphs.append(cleaned_para)
    cleaned = '\n\n'.join(cleaned_paragraphs)
    if cleaned:
        cleaned += '\n\n'
    return cleaned + after


def _generate_single_note(
    llm: ChatOllama,
    heading: str,
    note: "SelectedNote",
    ni: NoteInfo,
    ctx: BusinessContext,
    report_type: str,
    report_period_intro: str,
    validation_note: str,
) -> str:
    """
    Generate ONE note's body text via a single, focused LLM call, passing
    only the guidance and data relevant to this note — not the full note
    lineup and shared data block every note used to receive regardless of
    relevance.

    Crucially, the model is never asked to author the note's own heading:
    Python prepends `heading` deterministically once the body comes back.
    That removes the single biggest cause of untagged notes in the old
    all-at-once prompt — a heading the model dropped, merged onto the same
    line as the body text, renumbered after an earlier note went missing, or
    wrapped in markdown emphasis that broke notes_config.py's heading regex.
    With headings authored by Python instead, every note's heading is
    guaranteed present, in its own paragraph, in the exact expected format,
    every time.
    """
    handler = _NOTE_HANDLERS.get(note.title)
    if handler:
        guidance_text = handler["guidance"](ni, ctx, report_type)
        token = handler.get("token")
    else:
        logger.info(
            f"[generate_notes] {note.title!r} has no dedicated prompt guidance yet; "
            "using generic fallback guidance. See _generic_note_guidance()."
        )
        guidance_text = _generic_note_guidance(note.title)
        token = None

    data_block = _build_per_note_data_block(note.title, ni, ctx)

    system_prompt = (
        "You are a senior financial reporting specialist with expertise in SEC filings. "
        "Write professional, factual Notes to Financial Statements suitable for inclusion "
        "in a SEC Form 10-K or 10-Q filing. Use formal accounting language consistent with "
        "U.S. GAAP. Write ONLY the body text of this one note — its heading is added "
        "separately, so do not repeat the note's title anywhere in your response. "
        "State every figure as a concrete dollar amount or percentage from the data below; "
        "where a specific figure is not present in the data block, describe that topic "
        "qualitatively instead, in words. Never leave a bracket placeholder such as "
        "[Insert…] in the output. Write real, substantive discussion for every topic "
        "requested below — this note is always included in full in the filing."
        + (
            f" This note has one exact token to insert, {token}, in place of the specific "
            "figures it covers: write the surrounding discussion in words, then place the "
            "token alone on its own line, exactly as written, with no numbers or commentary "
            "substituted in around it."
            if token else ""
        )
    )

    user_prompt = f"""
Write the body of "{heading}" for a {report_type} {report_period_intro}.

{data_block}{validation_note}

Guidance for this note:
  {guidance_text}

Write 2-5 substantive paragraphs (or equivalent) for this note alone, citing the data
above wherever it applies. Start directly with the first sentence of the note body —
do not include the note's title or number anywhere in your response.
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    response = llm.invoke(messages)
    content = response.content
    body = (
        content if isinstance(content, str)
        else "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    )

    # Log the raw, unprocessed response before any stripping or token
    # substitution. mda_agent.py's equivalent logging (added after several
    # rounds of guessing at why tokens weren't matching) turned out to be the
    # single most useful diagnostic there — it showed the model wasn't using
    # any bracket variant of the token at all, just writing the fact out
    # directly in its own prose. Adding the same visibility here before
    # changing anything, since notes_agent's token mechanism may or may not
    # have the same failure mode — some of these tokens stand in for a full
    # HTML table (Note 8) or multi-figure sentence (Note 5's EPS), which is
    # a structurally different case from MDA's single-fact sentences, so it
    # shouldn't be assumed the fix would be identical without seeing the
    # actual responses first.
    #logger.info(f"[generate_notes] {note.title!r} raw response: {body!r}")

    # Strip stray markdown emphasis markers some local models add even though
    # this narrative is plain SEC filing prose, not markdown (see the
    # historical note in generate_notes_node's module docstring era).
    body = body.replace("**", "").replace("__", "").strip()

    # Belt-and-braces: if the model restated the heading at the very top of
    # its body anyway, drop that one restated line rather than duplicate it.
    for variant in (heading, heading.replace('–', '-')):
        if body.lower().startswith(variant.lower()):
            body = body[len(variant):].lstrip(" \t\n:–-")
            break

    if token:
        body = _strip_fake_numbers_before_token_in_body(body, token)
        if handler.get("build_replacement"):
            replacement = handler["build_replacement"](ni, ctx)
            if token in body:
                body = body.replace(token, replacement)
                # Nothing legitimate is ever expected after the substituted
                # fact — drop anything hallucinated past that point (same
                # failure mode as the old cross-note truncation fix, e.g.
                # the Leases note's fabricated second lease-payment table).
                end_idx = body.find(replacement) + len(replacement)
                body = body[:end_idx]
            else:
                logger.warning(
                    f"[generate_notes] {note.title} token not found in model output; "
                    "appending the deterministic fact to the end of the note instead."
                )
                body = body.rstrip() + "\n\n" + replacement

    # Wrapped in <h3>...</h3> (rather than left as bare text) so that once
    # this narrative round-trips through the TipTap editor and reaches
    # HtmlToDocx().add_html_to_document() in docx_service.py, the heading
    # becomes a real "Heading 3" paragraph style in the docx — the same
    # style every other section heading in the filing already uses (Item
    # headings, Part headings, via docx_service.py's _add_heading()) —
    # rather than a plain paragraph that merely looks bold. Left
    # unstyled/unaligned here deliberately: Word's built-in "Heading 3"
    # style (and _add_heading()'s use of it elsewhere) is left-aligned by
    # default, matching the rest of the document, so no explicit alignment
    # needs to be set on this tag.
    return f"<h3>{heading}</h3>\n\n{body.strip()}\n"


def generate_notes_node(state: NotesState) -> NotesState:
    """
    Node 5: Generate the SELECTED Notes to Financial Statements via Ollama.

    Generates ONE note per LLM call (see _generate_single_note), looping over
    notes_registry.get_selected_notes(state["report_type"]) in order. WHICH
    notes get generated, in what order, and under what number comes entirely
    from that toml-driven selection — no note count or number is hardcoded
    here.
    """
    # Block generation if the balance sheet does not balance
    if state["validation"]["warnings"]:
        balance_warnings = [w for w in state["validation"]["warnings"] if "balance" in w.lower()]
        if balance_warnings:
            logger.error(
                f"[generate_notes] Blocking notes generation — balance sheet imbalance: {balance_warnings}"
            )
            state["notes_narrative"] = (
                "ERROR: The balance sheet does not balance and the Notes to Financial Statements "
                "cannot be generated with reliable figures. Please review the financial data and "
                "correct the discrepancy before regenerating.\n\n"
                + "\n".join(f"  • {w}" for w in balance_warnings)
            )
            state["status"] = "blocked_imbalance"
            return state

    selected_notes = get_selected_notes(state["report_type"], state.get("quarter"))
    if not selected_notes:
        logger.error(
            f"[generate_notes] No notes are selected for {state['report_type']} — check "
            "note_list_10k.toml / note_list_10q.toml (every entry has select = false)."
        )
        state["notes_narrative"] = (
            "ERROR: No notes are marked select = true in the applicable note list "
            "configuration file. Please select at least one note and regenerate."
        )
        state["status"] = "blocked_no_notes"
        return state

    llm = _get_llm(temp=0.05)
    ni  = state["note_info"]
    ctx = state["context"]

    other_warnings = [w for w in state["validation"]["warnings"] if "balance" not in w.lower()]
    validation_note = ""
    if other_warnings:
        validation_note = (
            "\nDATA NOTES (reference only where relevant, do not invent explanations):\n"
            + "\n".join(f"  - {w}" for w in other_warnings)
        )

    report_period_intro = (
        f"quarterly report for the period ended {state['period_label'].split('ended')[-1].strip()}"
        if state["report_type"] == "10-Q"
        else f"annual report for the fiscal year ended December 31, {state['year']}"
    )

    # Build the "Note N – Title" heading strings for THIS generation, purely
    # from the toml-driven selection — nothing here assumes eight notes or
    # any fixed numbering.
    note_headings = [f"Note {n.number} – {n.title}" for n in selected_notes]

    note_blocks = []
    for heading, note in zip(note_headings, selected_notes):
        #logger.info(f"[generate_notes] Generating {heading!r}...")
        note_blocks.append(
            _generate_single_note(
                llm, heading, note, ni, ctx,
                state["report_type"], report_period_intro, validation_note,
            )
        )

    narrative = "\n\n".join(note_blocks)
    #logger.info(f"narrative 1: {narrative}")
    state["notes_narrative"] = narrative
    state["status"] = "notes_generated"
    return state


# ---------------------------------------------------------------------------
# Node 6 – compliance_review_node
# ---------------------------------------------------------------------------

_BRACKET_RE = re.compile(r"\[.{3,80}?\]")

# The model occasionally invents its OWN "{{SOMETHING}}"-style placeholder —
# mimicking the exact double-curly-brace style of our real substitution
# tokens (_REVENUE_DETAIL_TOKEN, _TAX_DETAIL_TOKEN, etc.) — for a figure
# whose guidance actually already gave it as plain text to restate
# (observed: Note 2's guidance states the depreciation figure directly as
# text, no token involved, yet the model wrote "Depreciation expense
# totaled {{REPARATION_EXPENSE}} during the year" instead of copying the
# given number). By the time compliance_review_node runs, every REAL token
# has already been substituted away in generate_notes_node, so ANY
# "{{...}}" pattern still present here is, by construction, one of these
# hallucinated placeholders — never a real, still-pending one — and can
# be treated exactly like a bracket placeholder: sent through the same
# LLM auto-fill pass below.
_CURLY_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]{3,80}?\}\}")

# Minimum word count expected in the gap between a note's heading and its
# own deterministic figure/table — below this, treat the note as having
# skipped its requested qualitative discussion entirely (see
# compliance_review_node's "thin note" check below).
_MIN_DISCUSSION_WORDS = 12


def _note_discussion_word_count(narrative: str, heading: str, boundary_text: Optional[str]) -> Optional[int]:
    """
    Count the words in the gap between a note's heading and either
    `boundary_text` (typically that note's own deterministic replacement
    sentence/table) or, if boundary_text isn't found, the end of the
    narrative. Returns None if the heading itself can't be found — that's
    a different, already-handled problem (see the missing-note check),
    not a "thin content" one.
    """
    heading_idx = -1
    heading_len = 0
    for variant in (heading, heading.replace('–', '-')):
        idx = narrative.find(variant)
        if idx != -1:
            heading_idx = idx
            heading_len = len(variant)
            break
    if heading_idx == -1:
        return None
    start = heading_idx + heading_len
    end = narrative.find(boundary_text, start) if boundary_text else -1
    if end == -1:
        end = len(narrative)
    gap = narrative[start:end].strip()
    return len(gap.split()) if gap else 0


def _expected_granular_values(ni: "NoteInfo", ctx: "BusinessContext", selected_notes: list["SelectedNote"]) -> dict:
    """
    For every granular XBRL fact this app tags, derive the CORRECT
    formatted value string by running that fact's own extraction regex
    (from notes_registry.NOTE_TAGGING_METADATA) against the guaranteed-
    correct deterministic sentence/table it's normally extracted from —
    rather than reimplementing each figure's exact string formatting a
    second time here and risking that copy drifting out of sync with
    what the granular regex actually expects.

    Only notes actually in `selected_notes` are considered. Note
    selection is user-driven and varies by filing — a 10-Q commonly omits
    notes a 10-K always includes (observed: "Summary of Significant
    Accounting Policies", "Short-term and Long-term Debt", and "Leases"
    all absent from a real Q2 10-Q's own note_list_10q-q2.toml selection).
    _NOTE_HANDLERS is a fixed, filing-independent registry of every note
    THIS APP KNOWS HOW to build a deterministic replacement for — it does
    NOT mean that note was actually generated for this particular filing.
    Iterating it unfiltered used to produce a flood of false-positive
    "heading could not be located" warnings for notes that were never
    supposed to be in the document at all, alongside any genuine warnings
    for notes that WERE selected but got corrupted — with no way to tell
    the two apart from the log alone.

    Several notes (the debt-maturity and lease-payment schedule tables)
    reuse the SAME bare numeric pattern for every row, since the table
    cells have no distinguishing text of their own — only ROW ORDER
    identifies which concept a given cell belongs to. `occurrence` below
    is that 0-based row order: the Nth entry in notes_registry.py sharing
    a given pattern is matched against the Nth occurrence of that pattern
    in the note's own correct_text, in listed order. This only works
    because notes_registry.py's entries for these tables are already
    written in the same row order the table itself is built in (see
    notes_registry.py's "same positional-matching approach" comments).

    Returns {concept_name: (pattern, correct_value_string, note_title, occurrence)}.
    `note_title` and `occurrence` let _reassert_granular_figures scope its
    search to the right note AND the right row within that note, instead
    of taking the first match anywhere in the whole document — see that
    function's docstring for the production bug this fixes.
    """
    from app.agents.notes_registry import NOTE_TAGGING_METADATA

    selected_titles = {n.title for n in selected_notes}

    expected = {}
    for title, handler in _NOTE_HANDLERS.items():
        if title not in selected_titles:
            continue
        if not handler.get("build_replacement"):
            continue
        correct_text = handler["build_replacement"](ni, ctx)
        tagging = NOTE_TAGGING_METADATA.get(title)
        if not tagging or not tagging.granular:
            continue
        seen_count: dict[str, int] = {}
        match_cache: dict[str, list] = {}
        for pattern, concept, *_rest in tagging.granular:
            occurrence = seen_count.get(pattern, 0)
            seen_count[pattern] = occurrence + 1
            if pattern not in match_cache:
                match_cache[pattern] = list(re.finditer(pattern, correct_text))
            matches = match_cache[pattern]
            if occurrence < len(matches):
                expected[concept] = (pattern, matches[occurrence].group(1), title, occurrence)
    return expected


def _note_boundaries(narrative: str, selected_notes: list["SelectedNote"]) -> dict[str, tuple[int, int]]:
    """
    Locate each selected note's own [start, end) character span within
    the plain-text narrative, so figure-reassertion can be scoped to a
    single note instead of searching the whole document — see
    _reassert_granular_figures' docstring for why that scoping matters.

    Headings are matched as the exact "Note N – Title" text
    generate_notes_node/compliance_review_node insert (see note_headings
    in those functions) — this is plain text at this stage, not yet HTML,
    so no tag-matching is needed here (contrast notes_config.py's
    _heading_pattern, which matches this same heading AFTER it's been
    through the docx→HTML pipeline further downstream).

    A note whose heading can't be found at all is simply omitted from the
    returned dict — its granular figures then fall through to
    _reassert_granular_figures' "note heading not found" warning, which is
    the correct signal (a missing heading is a bigger problem than this
    function is meant to fix; see _reassert_note_replacements for the
    fuzzy whole-note-text fallback that handles that case).
    """
    positions = []
    for note in selected_notes:
        heading = f"Note {note.number} – {note.title}"
        idx = narrative.find(heading)
        if idx != -1:
            positions.append((idx, note.title))
    positions.sort(key=lambda p: p[0])

    boundaries = {}
    for i, (start, title) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(narrative)
        boundaries[title] = (start, end)
    return boundaries


def _reassert_granular_figures(
    narrative: str, selected_notes: list["SelectedNote"], ni: "NoteInfo", ctx: "BusinessContext",
) -> tuple[str, list[str]]:
    """
    Safety net re-applied after every full-narrative-rewrite LLM call in
    compliance_review_node (the bracket/curly-placeholder auto-fill AND
    the thin-note-discussion auto-expand). Both of those calls are asked
    to "return the COMPLETE revised narrative" and explicitly told not to
    alter existing figures — but a rewrite pass can still silently reword
    or round an already-correct, previously-substituted deterministic
    figure anyway (observed: the PP&E-gross figure, correctly substituted
    as "$9,771,284" during generation, reappearing as a naive
    round-to-nearest-$100k "$9,800,000" after an UNRELATED repair pass —
    the thin-note expand — rewrote the whole document to add a missing
    discussion paragraph elsewhere). For every granular fact, if its
    anchor phrase is still present but the number after it no longer
    matches the correct, independently-derived value, overwrite just that
    number back to the correct one.

    SCOPED PER NOTE (fixes a real production bug): each concept's
    expected value also carries which NOTE it belongs to and which
    OCCURRENCE (row order) of its pattern within that note — see
    _expected_granular_values. This function searches only within that
    note's own [start, end) slice of the narrative (via _note_boundaries)
    and picks the matching occurrence-th match there, NOT the first match
    anywhere in the whole document. An earlier version searched the whole
    narrative unconditionally, which was fine for concepts with a unique,
    fully-worded anchor phrase (e.g. "Total operating revenue for ... was
    $") but silently wrong for the debt-maturity and lease-payment
    schedule tables, which both reuse the exact same bare
    "<td>$NNN</td>"-cell pattern for every row (no per-row anchor text
    exists to distinguish "Year Two" from "Year Three", let alone from a
    totally different note's table). Confirmed in production:
    LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths and
    LesseeOperatingLeaseLiabilityPaymentsDueNextTwelveMonths each
    "restored" Note 2's (Segment Information's) Hardware Sales table cell
    to their own note's figure, corrupting Note 2's revenue table twice
    in a row — because re.search(pattern, narrative) found the first
    "<td>$NNN</td>" in the ENTIRE document, which happened to sit in Note
    2, not in either note's own maturity/payment schedule.

    Boundaries and matches are recomputed FRESH on every concept (rather
    than once up front) so a splice made earlier in this same loop can't
    leave a later concept's offsets stale — cheap here (a handful of
    concepts per note, a handful of notes per filing), and correctness
    matters far more than the trivial extra scan cost.

    NOTE: this only covers concepts notes_registry.NOTE_TAGGING_METADATA
    actually lists as granular for a given note. A note whose deterministic
    fact has no granular entry there (observed: the PP&E/depreciation
    sentence) gets NO protection from this function — see
    _reassert_note_replacements below for a notes_registry-independent
    safety net that catches those too.

    Returns (narrative, warnings): if a rewrite pass reworded the anchor
    phrase itself (not just the number after it), or removed/renamed the
    note's own heading, the fact can't be located at all — previously
    this was a silent no-op with no log line and no visible warning
    anywhere. That let a genuinely wrong figure (see the module-level
    docstring's Note 3/Segment Information revenue-figure incident) reach
    the filed document with nothing in the app indicating anything was
    checked, let alone that the check failed to even locate the fact.
    Every such "could not verify at all" case is now surfaced the same
    way an actual mismatch is.
    """
    warnings: list[str] = []
    expected = _expected_granular_values(ni, ctx, selected_notes)

    for concept, (pattern, correct_value, title, occurrence) in expected.items():
        boundaries = _note_boundaries(narrative, selected_notes)
        span = boundaries.get(title)
        if span is None:
            msg = (
                f"us-gaap:{concept}'s note ({title!r}) heading could not be "
                "located in the narrative at all after a rewrite pass — this "
                "figure could not be verified or restored and needs manual review."
            )
            logger.warning(f"[compliance_review] {msg}")
            warnings.append(msg)
            continue
        start, end = span
        note_matches = list(re.finditer(pattern, narrative[start:end]))
        if occurrence >= len(note_matches):
            msg = (
                f"us-gaap:{concept}'s expected anchor phrase was not found at all "
                "after a rewrite pass — this figure could not be verified or "
                "restored and needs manual review."
            )
            logger.warning(f"[compliance_review] {msg}")
            warnings.append(msg)
            continue
        m = note_matches[occurrence]
        if m.group(1) != correct_value:
            local_start, local_end = m.span(1)
            abs_start, abs_end = start + local_start, start + local_end
            msg = (
                f"us-gaap:{concept} was altered by a rewrite pass "
                f"({m.group(1)!r} -> restored to {correct_value!r})."
            )
            logger.warning(f"[compliance_review] {msg}")
            warnings.append(msg)
            narrative = narrative[:abs_start] + correct_value + narrative[abs_end:]
            logger.info(f"narrative 2: {narrative}")
    return narrative, warnings


def _build_fuzzy_number_pattern(exact_text: str) -> str:
    """
    Turn an exact, known-correct deterministic sentence/table into a regex
    that matches that SAME text regardless of what a rewrite pass did to
    its numbers — every run of digits (with internal commas/periods, e.g.
    "7,009,009" or "15.6") becomes a `[\\d,\\.]+` wildcard, everything else
    stays literal. Used to relocate a deterministic fact whose wording
    survived a rewrite intact but whose number(s) did not.
    """
    escaped = re.escape(exact_text)
    return re.sub(r'\d[\d,\.]*', lambda m: r'[\d,\.]+', escaped)


def _reassert_note_replacements(
    narrative: str, selected_notes: list["SelectedNote"], ni: "NoteInfo", ctx: "BusinessContext",
) -> tuple[str, list[str]]:
    """
    Stronger, notes_registry-independent companion to
    _reassert_granular_figures. That function can only protect a fact if
    NOTE_TAGGING_METADATA lists it as a granular concept for that note —
    it does NOT for every deterministic sentence (observed in production:
    the "Summary of Significant Accounting Policies" note's PP&E/
    depreciation sentence has no granular entry there at all, so when a
    whole-document rewrite pass swapped its depreciation figure for a
    DIFFERENT note's income tax figure — both ending up reading the same
    wrong number — nothing caught it; the tax note's own figure got
    silently restored by _reassert_granular_figures while the depreciation
    note's did not).

    This checks EVERY handled note's own known-correct replacement text
    directly, independent of notes_registry: if the exact text is no
    longer present verbatim, search for it via a fuzzy pattern that only
    treats its numbers as wildcards (see _build_fuzzy_number_pattern) —
    matching the same sentence/table regardless of what number(s) a
    rewrite pass substituted in — and restore the exact correct text over
    whatever is found. If even the fuzzy pattern can't locate it (wording
    itself changed, not just numbers), leave it for manual review rather
    than guess where to splice a replacement in.

    Returns (narrative, warnings). Previously both the "restored" and the
    "could not be located" cases only reached the application logger —
    never state["compliance_notes"] — so a real numeric error could pass
    through with the frontend reporting nothing wrong. Both cases are now
    also returned as warnings for the caller to add to compliance_notes.
    """
    warnings: list[str] = []
    for note in selected_notes:
        handler = _NOTE_HANDLERS.get(note.title)
        if not (handler and handler.get("build_replacement")):
            continue
        correct_text = handler["build_replacement"](ni, ctx)
        if correct_text in narrative:
            continue
        pattern = _build_fuzzy_number_pattern(correct_text)
        m = re.search(pattern, narrative)
        if m:
            msg = (
                f"'{note.title}' deterministic fact was altered by a rewrite pass; "
                "restored the exact known-correct text."
            )
            logger.warning(f"[compliance_review] {msg}")
            warnings.append(msg)
            narrative = narrative[:m.start()] + correct_text + narrative[m.end():]
            logger.info(f"narrative 3: {narrative}")
        else:
            msg = (
                f"'{note.title}' deterministic fact could not be located after a "
                "rewrite pass (wording changed beyond a number substitution); "
                "leaving as-is — flag for manual review."
            )
            logger.warning(f"[compliance_review] {msg}")
            warnings.append(msg)
    return narrative, warnings


# A full-narrative-rewrite prompt wraps the current draft in
# "--- DRAFT ---" / "--- END DRAFT ---" markers purely so the model can
# tell where the content it's editing starts and ends. Observed in
# production: the model sometimes echoes one of these literal markers back
# at the very start of its answer anyway (e.g. "--- DRAFT --- Note 1 –
# Basis of Presentation..."), which breaks notes_config.py's heading match
# for whatever note immediately follows the leaked marker text — the exact
# cause of Note 1 coming back untagged after an auto-fill pass. Since these
# markers should never legitimately appear in filed narrative text, strip
# them unconditionally from any rewrite response before doing anything else
# with it.
_DRAFT_MARKER_RE = re.compile(r"-{2,}\s*(?:END\s+)?DRAFT\s*-{2,}", re.IGNORECASE)


def _strip_draft_markers(text: str) -> str:
    return _DRAFT_MARKER_RE.sub("", text).strip()


def _strip_markdown_emphasis(text: str) -> str:
    """
    Strip stray markdown emphasis markers (**bold**, __bold__) that a local
    model can add even though this narrative is plain SEC filing prose, not
    markdown.

    _generate_single_note() already does this once, on each note's initial
    body (see its own "Strip stray markdown emphasis" comment) — but that
    happens BEFORE compliance_review_node's two full-narrative rewrite
    passes below (thin-note expand, then bracket/curly-fill), and each of
    those passes asks the model to "return the COMPLETE revised narrative."
    A rewrite is exactly as free to reintroduce "**Note 8 – Leases**" as
    the original per-note call was to write it in the first place — and
    until this function existed, nothing stripped it back out afterward.
    Literal asterisks surviving into the note heading's own <p>/<hN>
    doesn't just look wrong in the filed document: it also breaks
    notes_config.py's heading_pattern regex, which matches the heading text
    exactly and has no tolerance for stray "**" inside it — silently
    leaving that note (and, via an unmatched end_pattern, potentially the
    PRECEDING note too) untagged. This is believed to be the root cause of
    a previously observed "Note 8 has double-asterisk markdown formatting
    breaking regex boundary detection" failure, since Note 8 (Leases) is
    exactly the kind of note the thin-note-expand pass targets (see that
    pass's own docstring: notes that jump straight from heading to table).

    Must be called on the output of EVERY full-narrative rewrite in this
    module, the same way _ensure_heading_paragraph_breaks() must be.
    """
    return text.replace("**", "").replace("__", "")


# A full-narrative-rewrite LLM call (bracket/curly-fill, thin-note expand)
# is asked to "return the COMPLETE revised narrative" — but a local model
# can occasionally ignore that instruction and return something far
# shorter instead (observed: an auto-fill pass asked to replace a SINGLE
# leftover placeholder came back so truncated that 7 of the document's 8
# notes vanished entirely, and the one that survived was itself
# corrupted). Blindly accepting that response with
# `state["notes_narrative"] = narrative` — which every rewrite pass in
# this file used to do unconditionally — silently destroys the rest of
# the filing. _accept_rewrite_if_plausible() is the gate that prevents
# that: reject an implausible rewrite and keep the previous, known-good
# narrative instead. A repair attempt that visibly failed (surfaced via
# the existing "still left" / "still thin" checks, which run against
# whatever narrative is ultimately kept) is far less harmful than one
# that appeared to succeed while quietly deleting most of the document.
_MIN_REWRITE_LENGTH_RATIO = 0.6


def _accept_rewrite_if_plausible(
    old_narrative: str, new_narrative: str, note_headings: list[str], context_label: str,
) -> str:
    if len(new_narrative) < len(old_narrative) * _MIN_REWRITE_LENGTH_RATIO:
        logger.warning(
            f"[compliance_review] {context_label} rewrite rejected: response was "
            f"{len(new_narrative)} chars vs. {len(old_narrative)} chars before "
            "(looks truncated/incomplete); keeping the previous narrative instead."
        )
        return old_narrative

    def _headings_present(text: str) -> int:
        return sum(
            1 for h in note_headings
            if h in text or h.replace('–', '-') in text
        )

    old_count = _headings_present(old_narrative)
    new_count = _headings_present(new_narrative)
    if new_count < old_count:
        logger.warning(
            f"[compliance_review] {context_label} rewrite rejected: only "
            f"{new_count}/{len(note_headings)} note headings present afterward "
            f"vs. {old_count}/{len(note_headings)} before; keeping the previous "
            "narrative instead."
        )
        return old_narrative

    return new_narrative


def compliance_review_node(state: NotesState) -> NotesState:
    """
    Scan the generated notes for:
      1. Remaining bracket placeholders — attempt LLM auto-fill, then surface any
         that could not be filled.
      2. Missing required notes (every note SELECTED in note_list_10k.toml /
         note_list_10q.toml for this report_type must be present).
      3. Propagate any data validation warnings from validate_financials_node.
    """
    narrative = state["notes_narrative"]
    #logger.info(f"narrative 4: {narrative}")
    notes: list[str] = []
    ni  = state["note_info"]
    ctx = state["context"]

    selected_notes = get_selected_notes(state["report_type"], state.get("quarter"))
    note_headings = [f"Note {n.number} – {n.title}" for n in selected_notes]

    # --- Check for notes with a heading + token but NO substantive discussion ---
    # Observed: the model sometimes jumps straight from a note's heading to
    # its deterministic figure/table with nothing in between (e.g. "Note 3
    # – Segment Information" followed immediately by "Total operating
    # revenue for the year was $X.", with none of the requested discussion
    # of segments/geography/business drivers; similarly "Note 8 – Leases"
    # followed immediately by the lease-maturity table with no lease
    # description at all). Nothing else catches this: the heading is
    # present and the token substituted correctly, so no other check
    # considers anything "missing." Measure the word count between each
    # token-bearing note's heading and its own deterministic content.
    #
    # This runs BEFORE the placeholder check below (not after, as in an
    # earlier version) because this expand pass is itself a full-narrative
    # rewrite, and rewrites have been observed to reintroduce curly-brace
    # placeholders in OTHER, previously-clean notes as a side effect —
    # running the placeholder check afterward means anything this pass
    # reintroduces still gets caught, instead of silently surviving
    # because the one-time placeholder check already happened.
    thin_notes = []
    for note in selected_notes:
        handler = _NOTE_HANDLERS.get(note.title)
        if not (handler and handler.get("build_replacement")):
            continue
        heading = f"Note {note.number} – {note.title}"
        replacement = handler["build_replacement"](ni, ctx)
        word_count = _note_discussion_word_count(narrative, heading, replacement)
        if word_count is not None and word_count < _MIN_DISCUSSION_WORDS:
            thin_notes.append((heading, note.title))

    if thin_notes:
        logger.info(
            f"[compliance_review] {len(thin_notes)} note(s) have little/no discussion; attempting to expand."
        )
        llm = _get_llm(temp=0.05) # low temp for factual gap-fill
        data_block = _build_note_data_block(ni, ctx, state["report_type"])
        thin_guidance = "\n\n".join(
            f"{heading}:\n  {_NOTE_HANDLERS[title]['guidance'](ni, ctx)}"
            for heading, title in thin_notes
        )
        expand_prompt = f"""
The following Notes to Financial Statements draft has one or more notes that jump straight
from their heading to a figure or table, with NO discussion paragraph — the requested
qualitative content was skipped entirely for these notes. For EACH note listed below, insert
a substantive discussion paragraph immediately after its heading (BEFORE any existing
sentence, number, or token already in that note), following its original guidance. Do not
remove, reword, round, or move any existing sentence, figure, or table anywhere in the
document, including in notes NOT listed below — only ADD the missing discussion paragraph
to the listed notes. Do not introduce any new [bracketed] or {{{{double-curly}}}} placeholders.
Return the COMPLETE revised narrative — do not summarise or truncate. Begin your answer
directly with the narrative content itself — do not include the "--- DRAFT ---" /
"--- END DRAFT ---" marker lines below anywhere in your answer; they are only there to
show you where the draft begins and ends.

--- DRAFT ---
{narrative}
--- END DRAFT ---

{data_block}

Notes needing an inserted discussion paragraph:

{thin_guidance}
"""
        response = llm.invoke([HumanMessage(content=expand_prompt)])
        content = response.content
        rewritten = (
            content if isinstance(content, str)
            else "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        )
        rewritten = _strip_draft_markers(rewritten)
        # A full-narrative rewrite is just as free to reintroduce stray
        # "**"/"__" emphasis as the original per-note generation was — see
        # _strip_markdown_emphasis' docstring — so strip it again here,
        # before the heading text is checked/relied on below.
        rewritten = _strip_markdown_emphasis(rewritten)
        # Reject an implausible response (drastically shorter, or missing
        # headings that were present before) rather than blindly accepting
        # it — see _accept_rewrite_if_plausible's docstring.
        rewritten = _accept_rewrite_if_plausible(narrative, rewritten, note_headings, "thin-note expand")
        # Same reasoning as the placeholder-fill pass below: this call
        # rewrites the whole narrative, so re-apply the heading-break fix
        # AND re-assert every deterministic figure (a rewrite pass can
        # silently reword/round an already-correct number even when told
        # not to — see _reassert_granular_figures' docstring). Both the
        # concept-level check (notes_registry-driven) and the broader
        # note-level check (notes_registry-independent — catches facts
        # like PP&E/depreciation that have no granular metadata entry)
        # run here.
        narrative = _ensure_heading_paragraph_breaks(rewritten, note_headings)
        logger.info(f"narrative 5: {narrative}")
        narrative, granular_warnings = _reassert_granular_figures(narrative, selected_notes, ni, ctx)
        narrative, replacement_warnings = _reassert_note_replacements(narrative, selected_notes, ni, ctx)
        logger.info(f"narrative 5.5: {narrative}")
        notes.extend(granular_warnings)
        notes.extend(replacement_warnings)
        state["notes_narrative"] = narrative

        still_thin = []
        for heading, title in thin_notes:
            handler = _NOTE_HANDLERS[title]
            replacement = handler["build_replacement"](ni, ctx)
            word_count = _note_discussion_word_count(narrative, heading, replacement)
            if word_count is not None and word_count < _MIN_DISCUSSION_WORDS:
                still_thin.append(heading)
        if still_thin:
            notes.append(
                "The following notes still lack substantive discussion after an "
                "auto-expand attempt and may need manual review: " + ", ".join(still_thin)
            )

    # --- Check for remaining bracket / curly-brace placeholders ---
    # Runs AFTER the thin-note expand above so it also catches any
    # placeholder that pass reintroduced elsewhere in the document.
    leftover = _BRACKET_RE.findall(narrative) + _CURLY_PLACEHOLDER_RE.findall(narrative)
    if leftover:
        logger.info(
            f"[compliance_review] {len(leftover)} placeholder(s) remain; attempting auto-fill."
        )
        llm = _get_llm(temp=0.05) # low temp for factual gap-fill
        data_block = _build_note_data_block(ni, ctx, state["report_type"])
        fill_prompt = f"""
The following Notes to Financial Statements draft still contains unfilled placeholders —
either [bracketed] or {{{{double-curly}}}} style. Replace EVERY one with specific text
drawn from the data below.
Return the COMPLETE revised narrative — do not summarise or truncate.

--- DRAFT ---
{narrative}
--- END DRAFT ---

{data_block}

Rules:
- Replace every [bracketed placeholder] AND every {{{{double-curly placeholder}}}} with real text.
- Do not add new brackets or curly-brace placeholders.
- Do not remove, reword, or round any other existing sentence, figure, or table.
- Keep all existing note headings and formatting.
- Begin your answer directly with the narrative content itself — do not include the
  "--- DRAFT ---" / "--- END DRAFT ---" marker lines above anywhere in your answer; they
  are only there to show you where the draft begins and ends.
"""
        response = llm.invoke([HumanMessage(content=fill_prompt)])
        content = response.content
        rewritten = (
            content if isinstance(content, str)
            else "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        )
        rewritten = _strip_draft_markers(rewritten)
        # Same reasoning as the thin-note-expand pass above: a full
        # rewrite is just as free to reintroduce stray "**"/"__" emphasis
        # as the original per-note generation was — see
        # _strip_markdown_emphasis' docstring.
        rewritten = _strip_markdown_emphasis(rewritten)
        # Reject an implausible response (drastically shorter, or missing
        # headings that were present before) rather than blindly accepting
        # it — see _accept_rewrite_if_plausible's docstring. This is the
        # exact failure observed in production: asked to fix ONE leftover
        # placeholder, the model returned a response so truncated that 7
        # of 8 notes vanished; without this guard that response was
        # accepted outright, silently destroying the rest of the filing.
        rewritten = _accept_rewrite_if_plausible(narrative, rewritten, note_headings, "placeholder auto-fill")
        # This LLM call was asked to "return the COMPLETE revised
        # narrative" — it's regenerating/rewriting the whole document, not
        # just filling in brackets, so it can just as easily reintroduce
        # the merged-heading bug that generate_notes_node already fixed
        # (see _ensure_heading_paragraph_breaks' docstring), or silently
        # reword/round an already-correct figure (see
        # _reassert_granular_figures' docstring). Re-apply both, plus the
        # broader notes_registry-independent note-level check (catches
        # facts like PP&E/depreciation that have no granular metadata
        # entry — see _reassert_note_replacements' docstring for the
        # production failure this specifically caught).
        narrative = _ensure_heading_paragraph_breaks(rewritten, note_headings)
        #logger.info(f"narrative 6: {narrative}")
        narrative, granular_warnings = _reassert_granular_figures(narrative, selected_notes, ni, ctx)
        narrative, replacement_warnings = _reassert_note_replacements(narrative, selected_notes, ni, ctx)
        #logger.info(f"narrative 6.5: {narrative}")
        notes.extend(granular_warnings)
        notes.extend(replacement_warnings)
        state["notes_narrative"] = narrative

        still_left = _BRACKET_RE.findall(narrative) + _CURLY_PLACEHOLDER_RE.findall(narrative)
        if still_left:
            notes.append(
                "The following placeholders could not be auto-filled and require manual input: "
                + ", ".join(dict.fromkeys(still_left))
            )

    # --- Final unconditional reassert pass ---
    # The two reassert calls above only run inside the thin-note-expand
    # and placeholder-autofill branches — i.e. only when THIS function
    # already knows a full-narrative rewrite happened. That leaves a gap:
    # if a deterministic figure gets corrupted by any other path (e.g.
    # during initial per-note generation, before compliance_review_node
    # ever runs), neither branch above fires and nothing checks it. Run
    # both checks one more time, unconditionally, on whatever narrative
    # is about to be returned — this costs nothing when nothing is wrong,
    # and catches corruption regardless of which code path caused it.
    narrative, granular_warnings = _reassert_granular_figures(narrative, selected_notes, ni, ctx)
    narrative, replacement_warnings = _reassert_note_replacements(narrative, selected_notes, ni, ctx)
    notes.extend(granular_warnings)
    notes.extend(replacement_warnings)
    state["notes_narrative"] = narrative

    # --- Verify every SELECTED note is present ---
    # Checking only the bare "Note N" substring is NOT enough — if the
    # model drops one note and renumbers the rest of its own headings
    # sequentially (observed: with "Commitments and Contingencies"
    # dropped entirely, the model labeled Debt "Note 6" instead of its
    # assigned "Note 7" and Leases "Note 7" instead of "Note 8"), the
    # substring "Note 7" is still technically present in the document —
    # just attached to the WRONG note — and a bare-number check would
    # wrongly conclude nothing is missing. Requiring the full heading
    # (number AND this note's own title together) catches that: Debt's
    # renumbered heading satisfies "Note 6" but not "Note 7 – Short-term
    # and Long-term Debt", so the actually-missing note is still flagged.
    for heading in note_headings:
        variants = (heading, heading.replace('–', '-'))
        if not any(v.lower() in narrative.lower() for v in variants):
            notes.append(f"Required '{heading}' appears to be missing from the notes.")

    # --- Propagate validation warnings ---
    if state["validation"]["warnings"]:
        notes.extend(
            [f"Data warning: {w}" for w in state["validation"]["warnings"]]
        )

    state["compliance_notes"] = notes
    state["status"] = "complete"

    if notes:
        logger.warning(f"[compliance_review] {len(notes)} note(s): {notes}")

    return state


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_notes_graph() -> StateGraph:
    """Build the six-node LangGraph workflow for Notes to Financial Statements."""
    workflow = StateGraph(NotesState)

    workflow.add_node("analyze_financials",  analyze_financials_node)
    workflow.add_node("validate_financials", validate_financials_node)
    workflow.add_node("compute_note_info",   compute_note_info_node)
    workflow.add_node("enrich_context",      enrich_context_node)
    workflow.add_node("generate_notes",      generate_notes_node)
    workflow.add_node("compliance_review",   compliance_review_node)

    workflow.set_entry_point("analyze_financials")
    workflow.add_edge("analyze_financials",  "validate_financials")
    workflow.add_edge("validate_financials", "compute_note_info")
    workflow.add_edge("compute_note_info",   "enrich_context")
    workflow.add_edge("enrich_context",      "generate_notes")
    workflow.add_edge("generate_notes",      "compliance_review")
    workflow.add_edge("compliance_review",   END)

    return workflow.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_quarterly_notes(
    balance_sheet: list[BalanceSheetRow],
    income_stmt:   list[IncomeStatementRow],
    cash_flow:     list[CashFlowRow],
    year:          int,
    quarter:       int,
    period_label:  str,
    prior_label:   str,
    notes_context: Optional[dict] = None,
    loan_rows:     Optional[list[LoanRow]] = None,
) -> tuple[str, list[str], list[str]]:
    """
    Generate Notes to Financial Statements for a 10-Q quarterly report.

    loan_rows: see generate_annual_notes — same optional, pre-fetched
    bookkeeper.loans detail, same graceful fallback if omitted.

    Returns (narrative, compliance_notes, validation_warnings).
    Output file: item011-Notes-to-Financial-Statements.docx → DATA_10Q_PART1
    """
    graph = build_notes_graph()
    initial_state = NotesState(
        report_type="10-Q",
        year=year,
        quarter=quarter,
        period_label=period_label,
        prior_label=prior_label,
        notes_context=notes_context,
        balance_sheet_rows=balance_sheet,
        income_stmt_rows=income_stmt,
        cash_flow_rows=cash_flow,
        loan_rows=loan_rows,
        balance_sheet_summary="",
        income_summary="",
        cash_flow_summary="",
        note_info={},           # type: ignore[typeddict-item]
        context={},             # type: ignore[typeddict-item]
        validation=ValidationResult(passed=True, warnings=[]),
        notes_narrative="",
        compliance_notes=[],
        status="pending",
    )
    result = await graph.ainvoke(initial_state)
    return result["notes_narrative"], result["compliance_notes"], result["validation"]["warnings"]


async def generate_annual_notes(
    balance_sheet: list[BalanceSheetRow],
    income_stmt:   list[IncomeStatementRow],
    cash_flow:     list[CashFlowRow],
    year:          int,
    period_label:  str,
    prior_label:   str,
    notes_context: Optional[dict] = None,
    loan_rows:     Optional[list[LoanRow]] = None,
) -> tuple[str, list[str], list[str]]:
    """
    Generate Notes to Financial Statements for a 10-K annual report.

    loan_rows: optional per-loan detail from bookkeeper.loans (see
    financial_service.get_loans), enabling Note 7's real 5-year maturity
    schedule and weighted-average-rate breakdown. Pre-fetched by the
    caller and passed in here — same pattern as balance_sheet/income_stmt/
    cash_flow — rather than this function holding a live DB session
    itself. If omitted, Note 7 gracefully falls back to the short-term/
    long-term split only.

    Returns (narrative, compliance_notes, validation_warnings).
    Output file: item082-Notes-to-Financial-Statements.docx → DATA_10K_PART2
    """
    graph = build_notes_graph()
    initial_state = NotesState(
        report_type="10-K",
        year=year,
        quarter=None,
        period_label=period_label,
        prior_label=prior_label,
        notes_context=notes_context,
        balance_sheet_rows=balance_sheet,
        income_stmt_rows=income_stmt,
        cash_flow_rows=cash_flow,
        loan_rows=loan_rows,
        balance_sheet_summary="",
        income_summary="",
        cash_flow_summary="",
        note_info={},           # type: ignore[typeddict-item]
        context={},             # type: ignore[typeddict-item]
        validation=ValidationResult(passed=True, warnings=[]),
        notes_narrative="",
        compliance_notes=[],
        status="pending",
    )
    result = await graph.ainvoke(initial_state)
    return result["notes_narrative"], result["compliance_notes"], result["validation"]["warnings"]
