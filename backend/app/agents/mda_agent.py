"""
Multi-agent LangGraph workflow for generating MD&A narratives using ChatOllama.

Workflow:
  analyze_financials → validate_financials → compute_kpis → enrich_context
      → generate_mda → compliance_review → END

New nodes vs. original:
  validate_financials_node  – sanity-checks computed totals; flags anomalies
  compute_kpis_node         – derives ratios / YoY % changes used as bracket fill
  enrich_context_node       – pulls business profile text from DB / env to fill
                               company-description brackets
  compliance_review_node    – scans narrative for missing placeholders and
                               ensures required SEC disclosure sections exist

generate_mda_node design (matches notes_agent.py's per-note loop):
  The section lineup for a report_type (see _section_titles) is generated
  ONE section per LLM call via _generate_single_mda_section, instead of one
  giant call asked to write every section at once. Each call receives only
  the guidance (_section_guidance) and data (_build_section_data_block)
  relevant to that specific section — not the full KPI/context dump every
  section used to receive regardless of relevance. Python — not the model —
  authors and prepends each section's heading, the same fix notes_agent.py
  uses to guarantee every heading is present, in its own paragraph, in the
  exact expected format, immune to markdown emphasis or merged-heading
  regressions.
"""

from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.schemas.schemas import BalanceSheetRow, IncomeStatementRow, CashFlowRow
import os
import re
import logging
import tomllib

logger = logging.getLogger(__name__)

# must set env variables to run properly, do not use defaults
ollama_host  = os.getenv("OLLAMA_HOST",     "localhost")
ollama_model = os.getenv("OLLAMA_MODEL",    "gemma3")
# OLLAMA_BASE_URL is set in docker-compose.yml
ollama_url   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

logger.info(f"OLLAMA_HOST: {ollama_host}")
logger.info(f"OLLAMA_MODEL: {ollama_model}")
logger.info(f"OLLAMA_BASE_URL: {ollama_url}")
assert ollama_url != "http://localhost:11434", "Error: OLLAMA_BASE_URL should use IP address"

class SuppressOllamaInfoLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Check if it's specifically an INFO level log targeting Ollama
        is_info = record.levelno == logging.INFO
        is_ollama = "11434" in record.getMessage() or "/api/chat" in record.getMessage()
        
        if is_info and is_ollama:
            return False  # Drop only INFO logs for Ollama
            
        return True       # Allow WARNING/ERROR for Ollama, and all logs for other requests

# Attach to httpx logger only
logging.getLogger("httpx").addFilter(SuppressOllamaInfoLogs())

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class KPIs(TypedDict):
    revenue_current:        float
    revenue_prior:          float
    revenue_change_pct:     float
    expenses_current:       float
    expenses_prior:         float
    expenses_change_pct:    float
    net_income_current:     float
    net_income_prior:       float
    net_income_change_pct:  float
    total_assets_current:   float
    total_assets_prior:     float
    total_liab_current:     float
    total_equity_current:   float
    current_ratio:          float
    operating_cash_current: float
    operating_cash_prior:   float
    gross_margin_pct:       float
    # 10-K only — capital expenditures and depreciation expense
    capex_current:          float
    capex_prior:            float
    capex_change_pct:       float
    depreciation_current:   float
    depreciation_prior:     float
    # PP&E cumulative balance sheet value (distinct from period capex activity)
    ppe_balance_current:    float
    ppe_balance_prior:      float
    # Effective tax rate — feeds the Critical Accounting Estimates section so
    # it isn't left to invent a rate with zero grounding data.
    income_tax_current:        float
    income_tax_prior:          float
    effective_tax_rate_current: float
    effective_tax_rate_prior:   float
    # Cost of Goods Sold and true "Operating Expenses" — reconcile exactly to
    # the Income Statement's own "Cost of Goods Sold" and "Total Operating
    # Expenses" lines. These are DISTINCT from expenses_current/prior above,
    # which is total costs and expenses (COGS + opex + interest + tax) used
    # only internally for the net income roll-forward — it must never be
    # shown to the model labeled as "Operating Expenses" or a reader who
    # checks it against the Income Statement's Total Operating Expenses line
    # will find it doesn't reconcile.
    cogs_current:               float
    cogs_prior:                 float
    cogs_change_pct:             float
    operating_expenses_current: float
    operating_expenses_prior:   float
    operating_expenses_change_pct: float


class BusinessContext(TypedDict):
    """Populated by enrich_context_node from env vars / settings."""
    company_name:        str
    business_description: str
    industry:            str
    geographic_focus:    str
    strategic_initiatives: str
    risk_factors:        str
    accounting_estimates: str
    fiscal_year:         str


class ValidationResult(TypedDict):
    passed:   bool
    warnings: list[str]


class ReportState(TypedDict):
    report_type:    str          # "10-Q" or "10-K"
    year:           int
    quarter:        Optional[int]
    period_label:   str
    prior_label:    str

    # Business context supplied by the user via the frontend
    mda_context:         Optional[dict]

    # Raw financial rows (passed in at graph invocation)
    balance_sheet_rows:  list[BalanceSheetRow]
    income_stmt_rows:    list[IncomeStatementRow]
    cash_flow_rows:      list[CashFlowRow]

    # Derived by nodes
    balance_sheet_summary: str
    income_summary:        str
    cash_flow_summary:     str
    kpis:                  KPIs
    context:               BusinessContext
    validation:            ValidationResult
    mda_narrative:         str
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
    """Format a dollar amount as '$X.X million' or '$X,XXX'."""
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.1f} million"
    return f"${n:,.0f}"


def _summarize_balance_sheet(rows: list[BalanceSheetRow], period: str, prior: str) -> str:
    assets      = [r for r in rows if "Asset"     in r.category]
    liabilities = [r for r in rows if "Liability" in r.category]
    equity      = [r for r in rows if "Equity"    in r.category]

    total_assets_curr  = sum(r.current_period for r in assets)
    total_assets_prior = sum(r.prior_period   for r in assets)
    total_liab_curr    = sum(r.current_period for r in liabilities)
    total_eq_curr      = sum(r.current_period for r in equity)

    return (
        f"Balance Sheet as of {period}: "
        f"Total Assets {_fmt(total_assets_curr)} (prior year {_fmt(total_assets_prior)}), "
        f"Total Liabilities {_fmt(total_liab_curr)}, "
        f"Total Equity {_fmt(total_eq_curr)}."
    )


def _summarize_income(rows: list[IncomeStatementRow], period: str, prior: str) -> str:
    revenues = [r for r in rows if "Revenue" in r.category or "Income" in r.category]
    expenses = [r for r in rows if r.category not in ("Revenue", "Income")]

    total_rev       = sum(r.current_period      for r in revenues)
    total_rev_prior = sum(r.prior_period        for r in revenues)
    total_exp       = sum(abs(r.current_period) for r in expenses)
    net             = total_rev - total_exp

    return (
        f"Income Statement for {period}: "
        f"Revenue {_fmt(total_rev)} (prior {_fmt(total_rev_prior)}), "
        f"Operating Expenses {_fmt(total_exp)}, "
        f"Net Income {_fmt(net)}."
    )


def _summarize_cash_flow(rows: list[CashFlowRow], period: str) -> str:
    operating = next(
        (r for r in rows if "Net Cash from Operating" in r.description), None
    )
    if operating:
        return (
            f"Cash Flow for {period}: "
            f"Net Cash from Operating Activities {_fmt(operating.current_period)} "
            f"(prior {_fmt(operating.prior_period)})."
        )
    return f"Cash flow data available for {period}."


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


# ---------------------------------------------------------------------------
# Node 1 – analyze_financials_node  (unchanged role, now populates summaries)
# ---------------------------------------------------------------------------

def analyze_financials_node(state: ReportState) -> ReportState:
    """Compute plain-English summaries from raw rows."""
    state["balance_sheet_summary"] = _summarize_balance_sheet(
        state["balance_sheet_rows"], state["period_label"], state["prior_label"]
    )
    state["income_summary"] = _summarize_income(
        state["income_stmt_rows"], state["period_label"], state["prior_label"]
    )
    state["cash_flow_summary"] = _summarize_cash_flow(
        state["cash_flow_rows"], state["period_label"]
    )
    state["status"] = "analyzed"
    return state


# ---------------------------------------------------------------------------
# Node 2 – validate_financials_node
# ---------------------------------------------------------------------------

def validate_financials_node(state: ReportState) -> ReportState:
    """
    Sanity-check the financial data before downstream nodes consume it.
    Flags common issues (negative equity, assets ≠ liabilities + equity, etc.)
    so the compliance node can surface them in the narrative or stop generation.
    """
    warnings: list[str] = []
    rows_bs  = state["balance_sheet_rows"]
    rows_is  = state["income_stmt_rows"]
    rows_cf  = state["cash_flow_rows"]

    total_assets = sum(r.current_period for r in rows_bs if "Asset"     in r.category)
    total_liab   = sum(r.current_period for r in rows_bs if "Liability" in r.category)
    total_equity = sum(r.current_period for r in rows_bs if "Equity"    in r.category)

    # Accounting equation: Assets ≈ Liabilities + Equity  (allow 1 % tolerance)
    # Liabilities are stored as signed ledger values (negative credits), so abs() is used above.
    # Equity may contain signed entries (e.g. Common Stock negative, Treasury Stock positive).
    lhs = total_assets
    rhs = total_liab + total_equity
    if lhs != 0 and abs(lhs - rhs) / abs(lhs) > 0.01:
        warnings.append(
            f"Balance sheet may not balance: Assets={_fmt(lhs)}, "
            f"Liabilities+Equity={_fmt(rhs)} (diff {_fmt(lhs - rhs)})."
        )

    if total_equity < 0:
        warnings.append("Negative total equity detected — verify retained earnings calculation.")

    revenues = sum(
        r.current_period for r in rows_is
        if "Revenue" in r.category or "Income" in r.category
    )
    if revenues == 0:
        warnings.append("No revenue found in income statement — income data may be missing.")

    if not rows_cf:
        warnings.append("Cash flow statement is empty.")

    passed = len(warnings) == 0
    if warnings:
        for w in warnings:
            logger.warning(f"[validate_financials] {w}")

    state["validation"] = ValidationResult(passed=passed, warnings=warnings)
    state["status"] = "validated"
    return state


# ---------------------------------------------------------------------------
# Node 3 – compute_kpis_node
# ---------------------------------------------------------------------------

def compute_kpis_node(state: ReportState) -> ReportState:
    """
    Derive the KPIs that directly fill in the MD&A bracket placeholders:
      - Revenue / expense YoY % changes  → fills [Specify Drivers], [Percentage or Dollar Amount]
      - Current ratio                     → fills [Calculate and State Current Ratio]
      - Gross margin                      → fills results-of-operations section
      - Operating cash flow comparison    → fills liquidity section
    """
    rows_bs = state["balance_sheet_rows"]
    rows_is = state["income_stmt_rows"]
    rows_cf = state["cash_flow_rows"]

    # --- Income statement KPIs ---
    revenues_curr  = sum(r.current_period for r in rows_is if "Revenue" in r.category or "Income" in r.category)
    revenues_prior = sum(r.prior_period   for r in rows_is if "Revenue" in r.category or "Income" in r.category)
    expenses_curr  = sum(abs(r.current_period) for r in rows_is if r.category not in ("Revenue", "Income"))
    expenses_prior = sum(abs(r.prior_period)   for r in rows_is if r.category not in ("Revenue", "Income"))
    net_curr  = revenues_curr  - expenses_curr
    net_prior = revenues_prior - expenses_prior

    # --- Balance sheet KPIs ---
    assets_curr  = sum(r.current_period for r in rows_bs if "Asset"     in r.category)
    assets_prior = sum(r.prior_period   for r in rows_bs if "Asset"     in r.category)
    liab_curr    = sum(r.current_period for r in rows_bs if "Liability" in r.category)
    equity_curr  = sum(r.current_period for r in rows_bs if "Equity"    in r.category)

    # Current ratio proxy: total assets / total liabilities (simplified)
    current_ratio = round(assets_curr / liab_curr, 2) if liab_curr != 0 else 0.0

    # Gross margin
    gross_margin_pct = round((net_curr / revenues_curr * 100), 1) if revenues_curr != 0 else 0.0

    # --- Cash flow KPIs ---
    op_cf_row = next((r for r in rows_cf if "Net Cash from Operating" in r.description), None)
    op_cf_curr  = op_cf_row.current_period if op_cf_row else 0.0
    op_cf_prior = op_cf_row.prior_period   if op_cf_row else 0.0

    # Capital expenditures — sourced from the cash flow investing section.
    # build_cash_flow() now uses "Purchase of Property, Plant & Equip" as the row
    # description; fall back to "Capital Expenditures" for backwards compatibility.
    # Values are stored as negative cash outflows; abs() gives the spend amount.
    capex_row = next(
        (r for r in rows_cf if "Purchase of Property" in r.description
         or "Capital Expenditures" in r.description),
        None
    )
    capex_curr  = abs(capex_row.current_period) if capex_row else 0.0
    capex_prior = abs(capex_row.prior_period)   if capex_row else 0.0

    # Depreciation expense — sourced from the income statement rows whose
    # category or name matches "Depreciation".  Stored as an expense (debit),
    # so get_gl_balances_range() returns a negative value; abs() gives the charge.
    dep_curr  = sum(
        abs(r.current_period) for r in rows_is
        if "Depreciation" in r.acct_name or "Depreciation" in r.category
    )
    dep_prior = sum(
        abs(r.prior_period) for r in rows_is
        if "Depreciation" in r.acct_name or "Depreciation" in r.category
    )

    # PP&E cumulative balance sheet value — distinct from period capex activity.
    # The balance sheet shows total accumulated cost of assets; capex shows only
    # what was purchased during the period.  Both are needed for accurate MD&A.
    ppe_curr = sum(
        r.current_period for r in rows_bs
        if any(k in r.acct_name for k in ("Property", "Plant", "Equip"))
    )
    ppe_prior = sum(
        r.prior_period for r in rows_bs
        if any(k in r.acct_name for k in ("Property", "Plant", "Equip"))
    )

    # Effective tax rate — isolate the tax expense row and back out pre-tax
    # income (all other expenses) so Critical Accounting Estimates has a real
    # rate to cite instead of being left with zero numeric grounding.
    #
    # NOTE: match on acct_name as well as category (not category alone). A
    # prior version matched only `r.category == "Income Tax Expense"` /
    # `r.category == "Cost of Goods Sold"` and silently computed 0 for both
    # in production — the category field's exact text apparently doesn't
    # match those literals for every dataset, even though the account is
    # correctly categorized well enough to appear in the Income Statement
    # itself. dep_curr/ppe_curr below already use an acct_name substring
    # match for exactly this reason and were unaffected; the same pattern is
    # applied here so a category-text mismatch can never again silently
    # zero out a required figure.
    tax_curr  = sum(
        abs(r.current_period) for r in rows_is
        if "Income Tax" in r.category or "Income Tax" in r.acct_name
    )
    tax_prior = sum(
        abs(r.prior_period) for r in rows_is
        if "Income Tax" in r.category or "Income Tax" in r.acct_name
    )
    pretax_curr  = revenues_curr  - (expenses_curr  - tax_curr)
    pretax_prior = revenues_prior - (expenses_prior - tax_prior)
    eff_tax_rate_curr  = round((tax_curr  / pretax_curr  * 100), 1) if pretax_curr  else 0.0
    eff_tax_rate_prior = round((tax_prior / pretax_prior * 100), 1) if pretax_prior else 0.0

    # Cost of Goods Sold and true Operating Expenses — isolated the same way
    # as tax above, so operating_expenses_curr reconciles EXACTLY to the
    # Income Statement's own "Total Operating Expenses" line (COGS, interest
    # expense, and income tax expense are reported as their own line items on
    # the statement and must not be folded into "Operating Expenses" here).
    interest_exp_curr  = sum(
        abs(r.current_period) for r in rows_is
        if "Interest Expense" in r.category or "Interest Expense" in r.acct_name
    )
    interest_exp_prior = sum(
        abs(r.prior_period) for r in rows_is
        if "Interest Expense" in r.category or "Interest Expense" in r.acct_name
    )
    cogs_curr  = sum(
        abs(r.current_period) for r in rows_is
        if "Cost of Goods Sold" in r.category or "Cost of Goods Sold" in r.acct_name
    )
    cogs_prior = sum(
        abs(r.prior_period) for r in rows_is
        if "Cost of Goods Sold" in r.category or "Cost of Goods Sold" in r.acct_name
    )
    opex_curr  = expenses_curr  - cogs_curr  - interest_exp_curr  - tax_curr
    opex_prior = expenses_prior - cogs_prior - interest_exp_prior - tax_prior

    if cogs_curr == 0 or tax_curr == 0:
        logger.error(
            "[compute_kpis] COGS or tax expense computed as $0 despite non-zero total "
            f"expenses ({_fmt(expenses_curr)}) — category/acct_name matching likely still "
            f"failed to find the row. cogs_curr={cogs_curr}, tax_curr={tax_curr}. "
            "Categories seen in income_stmt_rows: "
            f"{sorted(set(r.category for r in rows_is))}"
        )

    state["kpis"] = KPIs(
        revenue_current=revenues_curr,
        revenue_prior=revenues_prior,
        revenue_change_pct=_pct_change(revenues_curr, revenues_prior),
        expenses_current=expenses_curr,
        expenses_prior=expenses_prior,
        expenses_change_pct=_pct_change(expenses_curr, expenses_prior),
        net_income_current=net_curr,
        net_income_prior=net_prior,
        net_income_change_pct=_pct_change(net_curr, net_prior),
        total_assets_current=assets_curr,
        total_assets_prior=assets_prior,
        total_liab_current=liab_curr,
        total_equity_current=equity_curr,
        current_ratio=current_ratio,
        operating_cash_current=op_cf_curr,
        operating_cash_prior=op_cf_prior,
        gross_margin_pct=gross_margin_pct,
        capex_current=capex_curr,
        capex_prior=capex_prior,
        capex_change_pct=_pct_change(capex_curr, capex_prior),
        depreciation_current=dep_curr,
        depreciation_prior=dep_prior,
        ppe_balance_current=ppe_curr,
        ppe_balance_prior=ppe_prior,
        income_tax_current=tax_curr,
        income_tax_prior=tax_prior,
        effective_tax_rate_current=eff_tax_rate_curr,
        effective_tax_rate_prior=eff_tax_rate_prior,
        cogs_current=cogs_curr,
        cogs_prior=cogs_prior,
        cogs_change_pct=_pct_change(cogs_curr, cogs_prior),
        operating_expenses_current=opex_curr,
        operating_expenses_prior=opex_prior,
        operating_expenses_change_pct=_pct_change(opex_curr, opex_prior),
    )
    state["status"] = "kpis_computed"
    # logger.info(f"[compute_kpis] revenue_change_pct={state['kpis']['revenue_change_pct']}%")
    return state


# ---------------------------------------------------------------------------
# Node 4 – enrich_context_node
# ---------------------------------------------------------------------------

def _load_mda_intro() -> dict:
    """
    Read business_info.toml from DATA_USER_INPUT_DIR (container path).
    Returns a dict with the file's key/value pairs, or an empty dict if the
    file is missing or cannot be parsed (so the workflow always continues).
    Keys sourced from this file: company_name, industry,
    geographic_focus, business_description.
    """
    toml_path = os.path.join(settings.DATA_USER_INPUT_DIR, "business_info.toml")
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        # logger.info(f"[enrich_context] Loaded business_info.toml from {toml_path}")
        return data
    except FileNotFoundError:
        logger.warning(
            f"[enrich_context] business_info.toml not found at {toml_path}; using defaults."
        )
        return {}
    except Exception as exc:
        logger.warning(
            f"[enrich_context] Could not parse business_info.toml: {exc}; using defaults."
        )
        return {}


def enrich_context_node(state: ReportState) -> ReportState:
    """
    Populate BusinessContext.

    Priority (highest → lowest) for each field:
      1. Frontend form value  (mda_context dict from the API request)
      2. business_info.toml value (read from DATA_USER_INPUT_DIR)
      3. Hard-coded neutral fallback

    The four fields sourced from business_info.toml are:
      company_name, industry, geographic_focus, business_description.
    The remaining fields (strategic_initiatives, risk_factors,
    accounting_estimates) are supplied only via the frontend form or fallback.
    """
    ctx  = state.get("mda_context") or {}
    toml = _load_mda_intro()

    def _get(form_key: str, toml_key: str, fallback: str) -> str:
        """Return first non-empty value among: form → toml → fallback."""
        return ctx.get(form_key) or toml.get(toml_key) or fallback

    state["context"] = BusinessContext(
        company_name=_get(
            "company_name", "company_name",
            "[Company Name]"
        ),
        business_description=_get(
            "company_description", "business_description",
            "the development, sale, and support of products and services in its core markets"
        ),
        industry=_get(
            "company_industry", "industry",
            "its primary industry"
        ),
        geographic_focus=_get(
            "company_geo_focus", "geographic_focus",
            "primarily United States"
        ),
        strategic_initiatives=_get(
            "company_strategic_initiatives", "",
            "expanding our customer base, improving operational efficiency, and investing in product innovation"
        ),
        risk_factors=_get(
            "company_risk_factors", "",
            (
                "changes in market conditions, increased competition, "
                "technological advancements, regulatory changes, and the "
                "success of our strategic initiatives"
            )
        ),
        accounting_estimates=_get(
            "company_accounting_estimates", "",
            "allowance for doubtful accounts, useful lives of long-lived assets, and income tax valuation allowances"
        ),
        fiscal_year=str(state["year"]),
    )
    state["status"] = "context_enriched"
    return state


# ---------------------------------------------------------------------------
# Node 5 – generate_mda_node
# ---------------------------------------------------------------------------

def _build_kpi_block(k: KPIs, ctx: BusinessContext, report_type: str = "10-Q") -> str:
    """Render KPIs into a structured block passed to the LLM prompt.

    Capital Expenditures and Depreciation Expense are included only for 10-K
    reports; they are omitted from 10-Q prompts because those filings do not
    contain a dedicated Capital Expenditures section.
    """
    direction = lambda pct: "increased" if pct >= 0 else "decreased"
    block = f"""
COMPUTED KPIs (use these exact figures — do NOT invent numbers):
  Company:               {ctx['company_name']}
  Business description:  {ctx['business_description']}
  Industry:              {ctx['industry']}
  Geographic focus:      {ctx['geographic_focus']}
  Strategic initiatives: {ctx['strategic_initiatives']}
  Risk factors:          {ctx['risk_factors']}
  Critical accounting estimates: {ctx['accounting_estimates']}

  Revenue:          {_fmt(k['revenue_current'])}  ({direction(k['revenue_change_pct'])} {abs(k['revenue_change_pct'])}% vs prior)
  Prior Revenue:    {_fmt(k['revenue_prior'])}
  Cost of Goods Sold:       {_fmt(k['cogs_current'])}  ({direction(k['cogs_change_pct'])} {abs(k['cogs_change_pct'])}% vs prior)
  Prior Cost of Goods Sold: {_fmt(k['cogs_prior'])}
  Total Operating Expenses: {_fmt(k['operating_expenses_current'])}  ({direction(k['operating_expenses_change_pct'])} {abs(k['operating_expenses_change_pct'])}% vs prior)
  Prior Operating Expenses: {_fmt(k['operating_expenses_prior'])}
  Net Income:       {_fmt(k['net_income_current'])}  ({direction(k['net_income_change_pct'])} {abs(k['net_income_change_pct'])}% vs prior)
  Prior Net Income: {_fmt(k['net_income_prior'])}
  Total Assets:     {_fmt(k['total_assets_current'])}  (prior {_fmt(k['total_assets_prior'])})
  Total Liab.:      {_fmt(k['total_liab_current'])}
  Total Equity:     {_fmt(k['total_equity_current'])}
  Current Ratio:    {k['current_ratio']:.2f}x
  Gross Margin:     {k['gross_margin_pct']}%
  Op. Cash Flow:    {_fmt(k['operating_cash_current'])}  (prior {_fmt(k['operating_cash_prior'])})
  Effective Tax Rate: {k['effective_tax_rate_current']}%  (prior year {k['effective_tax_rate_prior']}%)
  Income Tax Expense: {_fmt(k['income_tax_current'])}  (prior year {_fmt(k['income_tax_prior'])})
"""
    if report_type == "10-K":
        block += (
            f"  Capital Expenditures (period activity): {_fmt(k['capex_current'])}"
            f"  ({direction(k['capex_change_pct'])} {abs(k['capex_change_pct'])}%"
            f" vs prior year {_fmt(k['capex_prior'])})\n"
            f"  PP&E Balance (balance sheet, cumulative): {_fmt(k['ppe_balance_current'])}"
            f"  (prior year {_fmt(k['ppe_balance_prior'])})\n"
            f"  Depreciation Expense: {_fmt(k['depreciation_current'])}"
            f"  (prior year {_fmt(k['depreciation_prior'])})\n"
        )
    return block


def _section_titles(report_type: str, year: int) -> list[str]:
    """
    Ordered section headings for this report_type. Nothing about section
    COUNT is hardcoded elsewhere — generate_mda_node and
    compliance_review_node's _REQUIRED_SECTIONS check both key off these
    same title strings, so adding/removing/reordering a section here is
    the single place that changes the filing's actual lineup.
    """
    if report_type == "10-Q":
        return [
            "Overview and Executive Summary",
            "Results of Operations",
            "Liquidity and Capital Resources",
            "Critical Accounting Policies",
            "Forward-Looking Statements",
        ]
    return [
        f"Overview of Business and {year} Highlights",
        "Results of Operations",
        "Liquidity and Capital Resources",
        "Capital Expenditures",
        "Critical Accounting Estimates",
        "Forward-Looking Statements",
    ]


def _dir_word(pct: float) -> str:
    return "increased" if pct >= 0 else "decreased"


def _build_section_facts(title: str, k: KPIs) -> dict[str, str]:
    """
    Build {fact_id: exact_sentence} pairs for the hard numbers in ONE section.

    History: an earlier version of this asked the model to place an opaque
    bracketed token (e.g. "[[REV_SENTENCE]]") in its prose, to be swapped for
    the sentence below after generation — mirroring the token-substitution
    mitigation already established for the Notes pipeline. Across several
    runs (see mda-log-3), the raw model responses showed this model never
    places a token in ANY bracket style — it always writes the fact out
    directly, in its own words, and (reassuringly) gets every number right
    when simply given the plain figures. Fighting that behavior by demanding
    a token produced only a systematic 100% "missing token" rate and, worse,
    duplicated every fact once the missing-token fallback appended the
    sentence a second time.

    The strategy now is: ask the model to state each fact directly (still
    giving it the exact pre-written sentence as the required content, so it
    isn't computing anything itself), then verify afterward, by checking
    each sentence's numeric anchors (dollar figures, percentages) are
    present verbatim in the model's own prose. Only a fact whose numbers
    are actually missing or altered gets appended — a fact the model
    already stated correctly, even if reworded, is left alone. See
    _generate_single_mda_section for the verification logic.
    """
    if "Overview" in title:
        return {
            "REV_HIGHLIGHT": (
                f"Total revenue {_dir_word(k['revenue_change_pct'])} by "
                f"{_fmt(abs(k['revenue_current'] - k['revenue_prior']))} to "
                f"{_fmt(k['revenue_current'])}, a {abs(k['revenue_change_pct'])}% "
                f"{_dir_word(k['revenue_change_pct'])} from the prior year."
            ),
            "NI_HIGHLIGHT": (
                f"Net income {_dir_word(k['net_income_change_pct'])} by "
                f"{_fmt(abs(k['net_income_current'] - k['net_income_prior']))} to "
                f"{_fmt(k['net_income_current'])}, a {abs(k['net_income_change_pct'])}% "
                f"{_dir_word(k['net_income_change_pct'])} from the prior year."
            ),
        }
    if title == "Results of Operations":
        return {
            "REV_SENTENCE": (
                f"Revenue {_dir_word(k['revenue_change_pct'])} by "
                f"{_fmt(abs(k['revenue_current'] - k['revenue_prior']))}, or "
                f"{abs(k['revenue_change_pct'])}%, to {_fmt(k['revenue_current'])} compared to "
                f"{_fmt(k['revenue_prior'])} in the prior year period."
            ),
            "COGS_SENTENCE": (
                f"Cost of Goods Sold was {_fmt(k['cogs_current'])}, "
                f"{_dir_word(k['cogs_change_pct'])} {abs(k['cogs_change_pct'])}% from "
                f"{_fmt(k['cogs_prior'])} in the prior year."
            ),
            "OPEX_SENTENCE": (
                f"Total Operating Expenses were {_fmt(k['operating_expenses_current'])}, "
                f"{_dir_word(k['operating_expenses_change_pct'])} "
                f"{abs(k['operating_expenses_change_pct'])}% from "
                f"{_fmt(k['operating_expenses_prior'])} in the prior year."
            ),
            "NI_SENTENCE": (
                f"Net income was {_fmt(k['net_income_current'])}, "
                f"{_dir_word(k['net_income_change_pct'])} {abs(k['net_income_change_pct'])}% "
                f"from {_fmt(k['net_income_prior'])} in the prior year."
            ),
        }
    if title == "Liquidity and Capital Resources":
        return {
            "ASSETS_SENTENCE": (
                f"Total assets were {_fmt(k['total_assets_current'])} as of period end, "
                f"compared to {_fmt(k['total_assets_prior'])} at the end of the prior year, "
                f"with a current ratio of {k['current_ratio']:.2f}x."
            ),
            "CASHFLOW_SENTENCE": (
                f"Operating cash flow was {_fmt(k['operating_cash_current'])}, compared to "
                f"{_fmt(k['operating_cash_prior'])} in the prior year."
            ),
        }
    if title == "Capital Expenditures":
        return {
            "CAPEX_SENTENCE": (
                f"Capital expenditures totaled {_fmt(k['capex_current'])}, "
                f"{_dir_word(k['capex_change_pct'])} {abs(k['capex_change_pct'])}% from "
                f"{_fmt(k['capex_prior'])} in the prior year."
            ),
            "PPE_SENTENCE": (
                f"The Property, Plant & Equipment balance was {_fmt(k['ppe_balance_current'])} "
                f"as of period end, compared to {_fmt(k['ppe_balance_prior'])} in the prior year."
            ),
            "DEP_SENTENCE": (
                f"Depreciation expense was {_fmt(k['depreciation_current'])} for the period, "
                f"compared to {_fmt(k['depreciation_prior'])} in the prior year."
            ),
        }
    if "Critical Accounting" in title:
        return {
            "TAX_SENTENCE": (
                f"The effective tax rate was {k['effective_tax_rate_current']}%, compared to "
                f"{k['effective_tax_rate_prior']}% in the prior year, with income tax expense "
                f"of {_fmt(k['income_tax_current'])} compared to {_fmt(k['income_tax_prior'])} "
                f"in the prior year."
            ),
        }
    return {}


def _build_section_data_block(title: str, k: KPIs, ctx: BusinessContext) -> str:
    """
    Build the data block for ONE MD&A section — only the figures and
    context relevant to that section, not the full KPI/context dump every
    section used to receive regardless of relevance (mirrors
    notes_agent.py's _build_per_note_data_block).

    Hard numbers for this section are supplied separately as pre-verified
    sentences via _build_section_facts — this data block carries only
    qualitative/supporting context, never a number the model could restate
    incorrectly.
    """

    if "Overview" in title:
        return f"""
COMPANY CONTEXT (all information needed for this section is provided below):
  Company:               {ctx['company_name']}
  Business description:  {ctx['business_description']}
  Industry:              {ctx['industry']}
  Geographic focus:      {ctx['geographic_focus']}
  Strategic initiatives: {ctx['strategic_initiatives']}

Two pre-verified highlight facts are listed below — see the instructions for exactly
how to state them. Do not restate revenue or net income figures anywhere else in this
section beyond what those two facts already say.
"""
    if title == "Results of Operations":
        return f"""
CONTEXT: Gross Margin is {k['gross_margin_pct']}%.

Four pre-verified facts are listed below — see the instructions for exactly how to state
them. Revenue, Cost of Goods Sold, Total Operating Expenses, and Net Income must ONLY use
the figures in those four facts — do not recompute or re-derive any of them yourself
anywhere in this section (for example, never compute an expense figure by subtracting net
income from revenue — the four facts below are the complete and only correct figures for
this section).
"""
    if title == "Liquidity and Capital Resources":
        return f"""
CONTEXT: Total Liabilities are {_fmt(k['total_liab_current'])}; Total Equity is
{_fmt(k['total_equity_current'])}.

Two pre-verified facts are listed below — see the instructions for exactly how to state
them. Total assets, the current ratio, and operating cash flow must ONLY use the figures
in those facts.
"""
    if title == "Capital Expenditures":
        return """
Three pre-verified facts are listed below — see the instructions for exactly how to
state them. Capital expenditures, the PP&E balance, and depreciation expense must ONLY
use the figures in those facts — do not recompute them.
"""
    if "Critical Accounting" in title:
        return f"""
COMPANY CONTEXT (all information needed for this section is provided below):
  Critical accounting estimates: {ctx['accounting_estimates']}

One pre-verified fact is listed below — see the instructions for exactly how to state
it. The effective tax rate and income tax expense must ONLY use the figures in that fact.
No other estimate mentioned above (e.g. useful life of assets, allowance rates) has an
associated percentage or dollar figure in this data set — discuss those qualitatively
only, and never invent a number for them.
"""
    if "Forward-Looking" in title:
        return f"""
COMPANY CONTEXT (all information needed for this section is provided below):
  Risk factors: {ctx['risk_factors']}
"""
    return ""


def _section_guidance(title: str, report_type: str, year: int) -> str:
    """Per-section instructions — only what THIS section needs to cover."""
    if "Overview" in title:
        guidance = (
            "Describe the company's business, industry, and geographic focus using the "
            "context provided. Mention the key strategic initiatives pursued during the period."
        )
        if report_type == "10-K":
            guidance += (
                " Highlight the fiscal year's key accomplishments — revenue growth, net "
                "income change, and any other notable results — using the figures provided."
            )
        return guidance
    if title == "Results of Operations":
        return (
            "State revenue with its exact percentage change versus the prior period. State "
            "Cost of Goods Sold and Total Operating Expenses as two separate figures, each "
            "with its exact percentage change — do not merge them into one combined "
            "'expenses' number. State net income or net loss with the exact percentage "
            "change. Discuss the drivers behind these results using the figures provided."
        )
    if title == "Liquidity and Capital Resources":
        return (
            "Cite total assets, liabilities, equity, and the current ratio from the figures "
            "provided. Discuss operating cash flow and working capital position."
        )
    if title == "Capital Expenditures":
        return (
            f"Report capital expenditures (new asset purchases during {year}) using the "
            "exact figure provided, along with its year-over-year change. Note that the "
            "Property, Plant & Equipment balance on the balance sheet reflects the cumulative "
            "cost of assets acquired over time and is distinct from the current-year capital "
            "expenditure activity. Report depreciation expense for the year and explain its "
            "relationship to the existing asset base."
        )
    if "Critical Accounting" in title:
        return (
            "list the accounting estimates provided and explain their significance to the "
            "financial statements. Where the discussion touches on income taxes, cite the "
            "exact effective tax rate and income tax expense figures provided. Do not state "
            "a percentage or dollar figure for any other estimate — the data for those "
            "estimates is qualitative only."
        )
    if "Forward-Looking" in title:
        return (
            "Include a standard forward-looking-statements disclaimer that cites the "
            "specific risk factors provided."
        )
    return "Write a substantive discussion for this section using the data provided."


_NUMERIC_ANCHOR_RE = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|thousand))?|\d+\.?\d*%")


def _numeric_anchors(sentence: str) -> list[str]:
    """Extract every dollar figure and percentage from a pre-verified sentence."""
    return _NUMERIC_ANCHOR_RE.findall(sentence)


def _generate_single_mda_section(
    llm: ChatOllama,
    heading: str,
    guidance_text: str,
    data_block: str,
    facts: dict[str, str],
    report_type: str,
    report_period_intro: str,
    validation_note: str,
) -> str:
    """
    Generate ONE MD&A section's body text via a single, focused LLM call,
    passing only the guidance and data relevant to that section — not the
    full section lineup and KPI/context dump every section used to receive
    regardless of relevance.

    The model is never asked to author the section's own heading: Python
    prepends `heading` deterministically once the body comes back — the
    same fix notes_agent.py's _generate_single_note uses to guarantee every
    heading is present, in its own paragraph, immune to markdown emphasis
    or merged-heading regressions.

    Hard numbers are handled by verification, not token insertion. An
    earlier version asked the model to place an opaque bracketed token
    (e.g. "[[REV_SENTENCE]]") to be swapped for the real sentence after
    generation. Raw responses (see mda-log-3) showed this model never does
    that, in any bracket style — it always writes the fact out directly in
    its own words, and gets the numbers right when simply given the plain
    figures. So instead: the model is told to state each fact directly
    using the pre-written sentence as its required content, and afterward
    each sentence's numeric anchors (dollar figures, percentages — see
    _numeric_anchors) are checked for literal presence in the model's own
    prose. A fact whose numbers are all present, even if reworded around
    them, is left alone — nothing is appended, so nothing is duplicated. A
    fact missing even one anchor (altered, dropped, or fabricated) gets its
    full corrective sentence appended, exactly as before.
    """
    facts_block = ""
    if facts:
        facts_list = "\n".join(f"  - {sentence}" for sentence in facts.values())
        facts_block = f"""
REQUIRED FACTS: the following have already been fact-checked and finalized. Your narrative
must state each one, using these exact dollar amounts and percentages — reword the
surrounding sentence however reads most naturally, but do not change, round differently, or
omit any number below:
{facts_list}
"""

    system_prompt = (
        "You are a senior financial analyst specialising in SEC regulatory filings. "
        "Write professional, factual Management's Discussion and Analysis (MD&A) narrative "
        "suitable for inclusion in a SEC Form 10-Q or 10-K filing. Use formal business "
        "language. Write ONLY the body text of this one section — its heading is added "
        "separately, so do not repeat the section title anywhere in your response. "
        "State every figure as a concrete dollar amount or percentage exactly as given in "
        "the data below; never recompute, round differently, or derive a figure yourself "
        "(for example, never compute an expense total by subtracting net income from "
        "revenue). If a figure is not present anywhere in the data block, omit that "
        "sentence entirely rather than estimating, inferring, or fabricating one."
    )

    user_prompt = f"""
Write the body of the "{heading}" section of the MD&A for a {report_type} {report_period_intro}.

{data_block}{validation_note}
{facts_block}
Guidance for this section:
  {guidance_text}

Write 1-3 substantive paragraphs for this section alone. Start directly with the first
sentence of the section body — do not include the section's title anywhere in your
response.
"""

    #logger.info(f"user_prompt: {user_prompt}")
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

    #logger.info(f"[generate_mda] '{heading}' raw response: {body!r}")

    # Strip stray markdown emphasis some local models add even though this
    # narrative is plain SEC filing prose, not markdown.
    body = body.replace("**", "").replace("__", "").strip()

    # Belt-and-braces: drop a restated heading line if the model added one anyway.
    if body.lower().startswith(heading.lower()):
        body = body[len(heading):].lstrip(" \t\n:–-")

    # Verify, don't blindly append: only add a fact's sentence if the model's
    # own prose is actually missing one of its numeric anchors. This is what
    # replaced the token mechanism — appending unconditionally (as the token
    # fallback did) duplicated every fact the model had already stated
    # correctly in its own words, which was happening 100% of the time.
    for fact_id, sentence in facts.items():
        anchors = _numeric_anchors(sentence)
        missing = [a for a in anchors if a not in body]
        if missing:
            logger.warning(
                f"[generate_mda] '{heading}' response is missing {missing} from the "
                f"{fact_id!r} fact — appending the full sentence. Raw response logged above."
            )
            body = f"{body.strip()} {sentence}"

    # Wrapped in <h3>...</h3> (rather than left as bare text) so that once
    # this narrative round-trips through the TipTap editor and reaches
    # HtmlToDocx().add_html_to_document() in docx_service.py, the heading
    # becomes a real "Heading 3" paragraph style in the docx — the same
    # style every other section heading in the filing already uses (Item
    # headings, Part headings, via docx_service.py's _add_heading()) —
    # rather than a plain paragraph that merely looks bold. Left
    # unstyled/unaligned here deliberately: Word's built-in "Heading 3"
    # style is left-aligned by default, matching the rest of the
    # document, so no explicit alignment needs to be set on this tag. See
    # notes_agent.py's identical fix for the parallel change there.
    return f"<h3>{heading}</h3>\n\n{body.strip()}\n"


def generate_mda_node(state: ReportState) -> ReportState:
    """
    Node 5: Generate the MD&A narrative via Ollama, one section per LLM call
    (see _generate_single_mda_section), looping over the section titles for
    this report_type (see _section_titles). Passing only each section's own
    guidance and data avoids diluting the model's attention across the full
    lineup and figure set on every call.
    """
    if state["validation"]["warnings"]:
        balance_warnings = [w for w in state["validation"]["warnings"] if "balance" in w.lower()]
        if balance_warnings:
            # A balance sheet imbalance means the equity figure is unreliable.
            # Do NOT let the LLM report wrong numbers — surface a clear error instead.
            logger.error(f"[generate_mda] Blocking MD&A generation — balance sheet does not balance: {balance_warnings}")
            state["mda_narrative"] = (
                "ERROR: The balance sheet does not balance and the MD&A cannot be generated "
                "with reliable figures. Please review the financial data and correct the "
                "discrepancy before regenerating.\n\n"
                + "\n".join(f"  • {w}" for w in balance_warnings)
            )
            state["status"] = "blocked_imbalance"
            return state

    llm = _get_llm(temp=0.1)
    k   = state["kpis"]
    ctx = state["context"]

    other_warnings = [w for w in state["validation"]["warnings"] if "balance" not in w.lower()]
    validation_note = ""
    if other_warnings:
        validation_note = (
            "\nDATA NOTES (reference where relevant, do not invent explanations):\n"
            + "\n".join(f"  - {w}" for w in other_warnings)
        )

    if state["report_type"] == "10-Q":
        quarter_map  = {1: "first", 2: "second", 3: "third"}
        quarter_name = quarter_map.get(state.get("quarter", 1), "")
        report_period_intro = (
            f"quarterly report for the {quarter_name} quarter ({state['period_label']}, "
            f"Q{state['quarter']} {state['year']}, compared with {state['prior_label']})"
        )
    else:
        report_period_intro = (
            f"annual report for {state['period_label']}, compared with {state['prior_label']}"
        )

    section_titles = _section_titles(state["report_type"], state["year"])

    section_blocks = []
    for heading in section_titles:
        #logger.info(f"[generate_mda] Generating {heading!r}...")
        guidance_text = _section_guidance(heading, state["report_type"], state["year"])
        data_block    = _build_section_data_block(heading, k, ctx)
        facts         = _build_section_facts(heading, k)
        section_blocks.append(
            _generate_single_mda_section(
                llm, heading, guidance_text, data_block, facts,
                state["report_type"], report_period_intro, validation_note,
            )
        )

    state["mda_narrative"] = "\n\n".join(section_blocks)
    state["status"] = "mda_generated"
    return state


# ---------------------------------------------------------------------------
# Node 6 – compliance_review_node
# ---------------------------------------------------------------------------

# Bracket pattern: anything like [Insert...], [Name of...], [Percentage...], etc.
_BRACKET_RE = re.compile(r"\[.{3,80}?\]")

# Required SEC sections in an MD&A
_REQUIRED_SECTIONS = [
    "Results of Operations",
    "Liquidity",
    "Capital Resources",
    "Forward-Looking",
]


def _strip_markdown_emphasis(text: str) -> str:
    """
    Strip stray markdown emphasis markers (**bold**, __bold__) that a local
    model can add even though this narrative is plain SEC filing prose, not
    markdown.

    _generate_single_mda_section() already does this once, on each
    section's initial body (see its own "Strip stray markdown emphasis"
    comment) — but that happens BEFORE compliance_review_node's
    bracket-fill rewrite below, which asks the model to "return the
    COMPLETE revised narrative." That rewrite is exactly as free to
    reintroduce "**Results of Operations**" as the original per-section
    call was to write it in the first place, and nothing stripped it back
    out afterward. See notes_agent.py's identically-named helper and its
    docstring for the parallel bug this fixes there (a stray "**" around a
    Note heading broke notes_config.py's heading regex); MD&A's own
    section headings aren't matched by any XBRL-tagging regex, so this is
    purely a document-quality fix here — but leftover literal asterisks in
    a filed SEC document are a real defect regardless.
    """
    return text.replace("**", "").replace("__", "")


def _ensure_mda_heading_paragraph_breaks(narrative: str, section_titles: list[str]) -> str:
    """
    Guarantee every MD&A section heading is wrapped in its own
    <h3>...</h3> element, on its own paragraph, separated from the body
    text that follows — the same fix notes_agent.py's
    _ensure_heading_paragraph_breaks applies to Notes.

    _generate_single_mda_section() always returns
    "<h3>{heading}</h3>\\n\\n{body}" — wrapping the heading in <h3> tags is
    what makes HtmlToDocx render it as a real "Heading 3" style paragraph
    in the docx (left-aligned, matching every other section heading in
    the filing), rather than a plain paragraph. generate_mda_node's
    initial assembly reliably produces this shape (heading is
    Python-authored, never touches the LLM), but compliance_review_node's
    bracket-fill pass below regenerates the WHOLE narrative from scratch
    as plain text, and the model doing that rewrite is not guaranteed to
    preserve the <h3>...</h3> wrapper or the blank-line separation — it
    could drop the tags, merge the heading onto the next sentence, or
    both, any of which would collapse into a single plain paragraph once
    it reaches docx_service.py's HtmlToDocx round-trip, no longer a
    distinct, real heading.
    """
    for _title in section_titles:
        # First, normalize away any heading tag pair a rewrite may have
        # left directly around this exact text — the right tag, the
        # wrong tag/level, or just a stale wrapper — down to the bare
        # heading text, so the wrap-and-separate step below can't nest a
        # brand-new <h3> inside an old, still-present wrapper.
        narrative = re.sub(
            rf'<h[1-6]>\s*{re.escape(_title)}\s*</h[1-6]>',
            _title,
            narrative,
        )
        if _title not in narrative:
            continue
        # Always re-wrap and re-normalize the separator to exactly one
        # blank line — regardless of whether the heading was already
        # followed by a blank line, a single space (the merged-heading
        # bug), or nothing at all. Requiring a non-whitespace character
        # immediately after the heading (as an earlier version of this
        # substitution did) would silently skip re-wrapping an
        # already-well-separated-but-unwrapped heading, e.g. right after
        # the tag-stripping step above.
        wrapped = f"<h3>{_title}</h3>"
        _new_narrative = re.sub(
            re.escape(_title) + r'\s*',
            wrapped + '\n\n',
            narrative,
            count=1,
        )
        if _new_narrative != narrative:
            narrative = _new_narrative
    return narrative


# Same reasoning as notes_agent.py's identically-named constant: a
# full-narrative rewrite asked to "return the COMPLETE revised narrative"
# can instead come back drastically truncated. Reject an implausible
# rewrite and keep the previous, known-good narrative instead of silently
# destroying most of the MD&A.
_MDA_MIN_REWRITE_LENGTH_RATIO = 0.6


def _accept_mda_rewrite_if_plausible(
    old_narrative: str, new_narrative: str, section_titles: list[str], context_label: str,
) -> str:
    if len(new_narrative) < len(old_narrative) * _MDA_MIN_REWRITE_LENGTH_RATIO:
        logger.warning(
            f"[compliance_review] {context_label} rewrite rejected: response was "
            f"{len(new_narrative)} chars vs. {len(old_narrative)} chars before "
            "(looks truncated/incomplete); keeping the previous narrative instead."
        )
        return old_narrative

    old_count = sum(1 for h in section_titles if h.lower() in old_narrative.lower())
    new_count = sum(1 for h in section_titles if h.lower() in new_narrative.lower())
    if new_count < old_count:
        logger.warning(
            f"[compliance_review] {context_label} rewrite rejected: only "
            f"{new_count}/{len(section_titles)} section headings present afterward "
            f"vs. {old_count}/{len(section_titles)} before; keeping the previous "
            "narrative instead."
        )
        return old_narrative

    return new_narrative


def compliance_review_node(state: ReportState) -> ReportState:
    """
    Scan the generated narrative for:
      1. Remaining bracket placeholders — surfaces them so the user knows
         what still needs manual completion.
      2. Missing required SEC MD&A sections.

    If unfilled brackets are found, the node makes ONE additional LLM call
    to attempt auto-fill using the KPI / context data already in state.
    """
    narrative = state["mda_narrative"]
    notes: list[str] = []

    # --- Check for remaining brackets ---
    leftover = _BRACKET_RE.findall(narrative)
    if leftover:
        # logger.info(f"[compliance_review] {len(leftover)} bracket(s) remain; attempting auto-fill.")
        llm = _get_llm(temp=0.1)   # low temp for factual gap-fill
        k   = state["kpis"]
        ctx = state["context"]
        fill_prompt = f"""
The following MD&A draft still contains unfilled bracket placeholders.
Replace EVERY bracket with specific text drawn from the data below.
Return the COMPLETE revised narrative — do not summarise or truncate.

--- DRAFT ---
{narrative}
--- END DRAFT ---

{_build_kpi_block(k, ctx, report_type=state["report_type"])}

Rules:
- Replace every [bracketed placeholder] with real text.
- Do not add new brackets.
- Keep all existing section headings and formatting.
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
        section_titles = _section_titles(state["report_type"], state["year"])
        # This rewrite is asked to "return the COMPLETE revised
        # narrative" — exactly the same shape of LLM call as
        # notes_agent.py's two full-narrative rewrite passes, and subject
        # to the same failure modes: stray "**"/"__" markdown emphasis
        # reappearing, a section heading and its body merging onto one
        # line, or (worst case) a drastically truncated response. Apply
        # the same three guards here.
        rewritten = _strip_markdown_emphasis(rewritten)
        rewritten = _accept_mda_rewrite_if_plausible(narrative, rewritten, section_titles, "bracket auto-fill")
        narrative = _ensure_mda_heading_paragraph_breaks(rewritten, section_titles)
        state["mda_narrative"] = narrative

        # Re-check after auto-fill
        still_left = _BRACKET_RE.findall(narrative)
        if still_left:
            notes.append(
                f"The following placeholders could not be auto-filled and require manual input: "
                + ", ".join(dict.fromkeys(still_left))   # deduplicated
            )

    # --- Check required sections ---
    for section in _REQUIRED_SECTIONS:
        if section.lower() not in narrative.lower():
            notes.append(f"Required section '{section}' appears to be missing from the narrative.")

    # --- Propagate validation warnings as review notes ---
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

def build_mda_graph() -> StateGraph:
    """Build the full 6-node LangGraph workflow."""
    workflow = StateGraph(ReportState)

    workflow.add_node("analyze_financials",  analyze_financials_node)
    workflow.add_node("validate_financials", validate_financials_node)
    workflow.add_node("compute_kpis",        compute_kpis_node)
    workflow.add_node("enrich_context",      enrich_context_node)
    workflow.add_node("generate_mda",        generate_mda_node)
    workflow.add_node("compliance_review",   compliance_review_node)

    workflow.set_entry_point("analyze_financials")
    workflow.add_edge("analyze_financials",  "validate_financials")
    workflow.add_edge("validate_financials", "compute_kpis")
    workflow.add_edge("compute_kpis",        "enrich_context")
    workflow.add_edge("enrich_context",      "generate_mda")
    workflow.add_edge("generate_mda",        "compliance_review")
    workflow.add_edge("compliance_review",   END)

    return workflow.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_quarterly_mda(
    balance_sheet: list[BalanceSheetRow],
    income_stmt:   list[IncomeStatementRow],
    cash_flow:     list[CashFlowRow],
    year:          int,
    quarter:       int,
    period_label:  str,
    prior_label:   str,
    mda_context:   Optional[dict] = None,
) -> tuple[str, list[str], list[str]]:
    """
    Generate a 10-Q MD&A narrative.
    Returns (narrative, compliance_notes, validation_warnings).
    validation_warnings is non-empty when financial data anomalies were detected
    (e.g. balance sheet does not balance) so callers can surface them to the frontend.
    """
    graph = build_mda_graph()
    initial_state = ReportState(
        report_type="10-Q",
        year=year,
        quarter=quarter,
        period_label=period_label,
        prior_label=prior_label,
        mda_context=mda_context,
        balance_sheet_rows=balance_sheet,
        income_stmt_rows=income_stmt,
        cash_flow_rows=cash_flow,
        balance_sheet_summary="",
        income_summary="",
        cash_flow_summary="",
        kpis={},           # type: ignore[typeddict-item]
        context={},        # type: ignore[typeddict-item]
        validation=ValidationResult(passed=True, warnings=[]),
        mda_narrative="",
        compliance_notes=[],
        status="pending",
    )
    result = await graph.ainvoke(initial_state)
    return result["mda_narrative"], result["compliance_notes"], result["validation"]["warnings"]


async def generate_annual_mda(
    balance_sheet: list[BalanceSheetRow],
    income_stmt:   list[IncomeStatementRow],
    cash_flow:     list[CashFlowRow],
    year:          int,
    period_label:  str,
    prior_label:   str,
    mda_context:   Optional[dict] = None,
) -> tuple[str, list[str], list[str]]:
    """
    Generate a 10-K MD&A narrative.
    Returns (narrative, compliance_notes, validation_warnings).
    validation_warnings is non-empty when financial data anomalies were detected
    (e.g. balance sheet does not balance) so callers can surface them to the frontend.
    """
    graph = build_mda_graph()
    initial_state = ReportState(
        report_type="10-K",
        year=year,
        quarter=None,
        period_label=period_label,
        prior_label=prior_label,
        mda_context=mda_context,
        balance_sheet_rows=balance_sheet,
        income_stmt_rows=income_stmt,
        cash_flow_rows=cash_flow,
        balance_sheet_summary="",
        income_summary="",
        cash_flow_summary="",
        kpis={},           # type: ignore[typeddict-item]
        context={},        # type: ignore[typeddict-item]
        validation=ValidationResult(passed=True, warnings=[]),
        mda_narrative="",
        compliance_notes=[],
        status="pending",
    )
    result = await graph.ainvoke(initial_state)
    return result["mda_narrative"], result["compliance_notes"], result["validation"]["warnings"]

def check_speed(response):
    # Extract Ollama's native performance metrics from LangChain metadata
    # metadata = response.response_metadata.get("message", {}).get("eval_count")

    if "eval_count" in response.response_metadata:
        # 1. Total output tokens generated
        tokens_generated = response.response_metadata["eval_count"]
        
        # 2. Time spent generating tokens (converted from nanoseconds to seconds)
        generation_time_sec = response.response_metadata["eval_duration"] / 1_000_000_000
        
        # 3. Calculate Speed
        tokens_per_second = tokens_generated / generation_time_sec
        
        print(f"--- GPU Benchmark Results ---")
        print(f"Tokens Generated: {tokens_generated}")
        print(f"Generation Time : {generation_time_sec:.2f} seconds")
        print(f"Speed           : {tokens_per_second:.2f} tokens/sec")
    else:
        print("Metadata metrics not found. Check if the model is running correctly.")

