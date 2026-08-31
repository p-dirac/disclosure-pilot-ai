"""
xbrl_tagger.py
==============
Post-processor that converts a plain SEC EDGAR HTML file (sec-10-k.html or
sec-10-q.html) into an Inline XBRL (iXBRL) XHTML file accepted by EDGAR.

Two-step process
────────────────
1. US-GAAP MAPPINGS  – a dict that maps human-readable label text as it appears
   in our HTML tables to the correct us-gaap taxonomy concept and metadata.

2. HTML → iXBRL      – BeautifulSoup walks every <td> in every financial table,
   looks up the row label, and wraps the numeric value in the appropriate
   <ix:nonFraction> (or <ix:nonNumeric> for text) tag.

Usage
─────
    from xbrl_tagger import tag_filing

    tag_filing(
        html_path   = "/tmp/appio/reports/sec-10-k.html",
        output_path = "/tmp/appio/reports/sec-10-k.htm",   # MUST end in .htm (EFM 5.01.01)
        form_type   = "10-K",         # or "10-Q"
        entity_name = "Acme Corp",
        ticker      = "XYZ",          
        cik         = "0001234567",   # 10-digit zero-padded CIK (EFM 6.05.23)
        period_end  = "2025-12-31",   # ISO date — balance-sheet instant
        period_start= "2025-01-01",   # ISO date — income/cashflow start
        currency    = "USD",
        taxonomy_year = 2026,         # matches us-gaap-2026 and dei-2026
        # schemaRef → acme-20251231.xsd (local extension XSD).
        # Place acme-20251231.xsd, acme-pre.xml, dei-2026.xsd,
        # and us-gaap-2026/ alongside sec-10-k.htm before running Arelle.
    )

Dependencies
────────────
    pip install beautifulsoup4 lxml --break-system-packages
"""

from __future__ import annotations

import os
import re
import tomllib
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, Tag


def load_business_info(path: Optional[str] = None) -> dict:
    """
    Read business_info.toml — the single source of truth for entity/DEI
    facts (entity name, EIN, address, filer status, auditor, etc.). Same
    file and same DATA_USER_INPUT_DIR convention notes_agent.py's
    _load_notes_intro() uses, so this app has exactly one business-info
    file, not one per module.

    Returns {} if the file is missing or unparsable — callers should treat
    every key as optional and fall back to a safe literal default, the
    same way this module always has.
    """
    if path is None:
        try:
            from app.core.config import settings
            path = os.path.join(settings.DATA_USER_INPUT_DIR, "business_info.toml")
        except Exception:
            path = os.path.join(os.environ.get("DATA_USER_INPUT_DIR", "C:\\AppIO\\user_input"),
                                 "business_info.toml")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        print(f"[xbrl_tagger] business_info.toml not found at {path}; using hardcoded fallbacks.")
        return {}
    except Exception as exc:
        print(f"[xbrl_tagger] could not parse business_info.toml: {exc}; using hardcoded fallbacks.")
        return {}


_FILER_CATEGORY_DISPLAY = {
    "large_accelerated": "Large Accelerated Filer",
    "accelerated":        "Accelerated Filer",
    "non_accelerated":    "Non-accelerated Filer",
}


def _yn(value: bool) -> str:
    return "Yes" if value else "No"


# ─────────────────────────────────────────────────────────────────────────────
# 1.  US-GAAP CONCEPT MAPPINGS
# ─────────────────────────────────────────────────────────────────────────────
#
# Structure of each entry:
#   "label text (lower-cased, stripped)" : {
#       "concept"   : "us-gaap:ConceptName",   # taxonomy element
#       "type"      : "instant" | "duration",   # balance-sheet vs P&L / CF
#       "balance"   : "debit" | "credit",       # normal balance side
#       "negate"    : True | False,             # True → multiply stored value ×-1
#                                               #   (contra-assets, expenses shown
#                                               #    as positive in our tables but
#                                               #    must be negative in XBRL)
#   }
#
# "type" drives context selection in the tagger:
#   instant  → uses period_end date only   (balance-sheet snapshot)
#   duration → uses period_start..period_end range (P&L / cash flow)
#
# Add or adjust entries to match the exact label text in your HTML tables.
# ─────────────────────────────────────────────────────────────────────────────

GAAP_MAPPINGS: dict[str, dict] = {

    # ── Balance Sheet — Assets ───────────────────────────────────────────────
    "cash and equivalents":             {"concept": "us-gaap:CashAndCashEquivalentsAtCarryingValue",
                                         "type": "instant", "balance": "debit",  "negate": False},
    "short-term investments":           {"concept": "us-gaap:ShortTermInvestments",
                                         "type": "instant", "balance": "debit",  "negate": False},
    "accounts receivable":              {"concept": "us-gaap:AccountsReceivableNetCurrent",
                                         "type": "instant", "balance": "debit",  "negate": False},
    "allowance for doubtful accounts":  {"concept": "us-gaap:AllowanceForDoubtfulAccountsReceivableCurrent",
                                         "type": "instant", "balance": "credit", "negate": False},
    "inventory":                        {"concept": "us-gaap:InventoryNet",
                                         "type": "instant", "balance": "debit",  "negate": False},
    "prepaid expenses":                 {"concept": "us-gaap:PrepaidExpenseAndOtherAssetsCurrent",
                                         "type": "instant", "balance": "debit",  "negate": False},
    "property, plant & equip":          {"concept": "us-gaap:PropertyPlantAndEquipmentGross",
                                         "type": "instant", "balance": "debit",  "negate": False},
    "property, plant and equipment":    {"concept": "us-gaap:PropertyPlantAndEquipmentGross",
                                         "type": "instant", "balance": "debit",  "negate": False},
    "accumulated depreciation":         {"concept": "us-gaap:AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
                                         "type": "instant", "balance": "credit", "negate": False},
    "total assets":                     {"concept": "us-gaap:Assets",
                                         "type": "instant", "balance": "debit",  "negate": False},

    # ── Balance Sheet — Liabilities ──────────────────────────────────────────
    "accounts payable":                 {"concept": "us-gaap:AccountsPayableCurrent",
                                         "type": "instant", "balance": "credit", "negate": False},
    "accrued liabilities":              {"concept": "us-gaap:AccruedLiabilitiesCurrent",
                                         "type": "instant", "balance": "credit", "negate": False},
    "short-term debt":                  {"concept": "us-gaap:ShortTermBorrowings",
                                         "type": "instant", "balance": "credit", "negate": False},
    "unearned revenue":                 {"concept": "us-gaap:DeferredRevenueCurrent",
                                         "type": "instant", "balance": "credit", "negate": False},
    "long-term debt":                   {"concept": "us-gaap:LongTermDebtNoncurrent",
                                         "type": "instant", "balance": "credit", "negate": False},
    "total liabilities":                {"concept": "us-gaap:Liabilities",
                                         "type": "instant", "balance": "credit", "negate": False},

    # ── Balance Sheet — Equity ───────────────────────────────────────────────
    "common stock":                     {"concept": "us-gaap:CommonStockValue",
                                         "type": "instant", "balance": "credit", "negate": False},
    "retained earnings":                {"concept": "us-gaap:RetainedEarningsAccumulatedDeficit",
                                         "type": "instant", "balance": "credit", "negate": "display"},
    "treasury stock":                   {"concept": "us-gaap:TreasuryStockValue",
                                         "type": "instant", "balance": "debit",  "negate": False},
    "dividends paid":                   {"concept": "us-gaap:DividendsCommonStockCash",
                                         "type": "instant", "balance": "debit",  "negate": False},
    "total equity":                     {"concept": "us-gaap:StockholdersEquity",
                                         "type": "instant", "balance": "credit", "negate": "display"},
    "total liabilities + equity":       {"concept": "us-gaap:LiabilitiesAndStockholdersEquity",
                                         "type": "instant", "balance": "credit", "negate": False},
    "total liabilities and equity":     {"concept": "us-gaap:LiabilitiesAndStockholdersEquity",
                                         "type": "instant", "balance": "credit", "negate": False},

    # ── Income Statement — Revenue ───────────────────────────────────────────
    # Hardware Sales / Software Sales / Consulting are disaggregated via a
    # custom dimension (tifx:RevenueProductOrServiceAxis — renamed from
    # tifx:ProductOrServiceAxis, which collided with the base taxonomy's own
    # srt:ProductOrServiceAxis per EFM.6.07.16) rather than separate
    # concepts. us-gaap:ProductRevenue and us-gaap:LicenseAndServiceRevenue
    # don't exist in the 2026 taxonomy, and ad hoc substitutes like
    # SalesRevenueGoodsNet are deprecated — but the standard revenue concept
    # itself, RevenueFromContractWithCustomerExcludingAssessedTax, can
    # legitimately be reported more than once in the same period as long as
    # each instance is qualified by a distinct dimension member. All three
    # facts share the SAME concept and context type as "Total Revenue"
    # below, differing only by the [Axis]=[Member] segment — that's what
    # disaggregates them without inventing a non-existent GAAP concept.
    #
    # "consulting" previously had NO dimension tuple here, which meant it
    # was tagged as an undimensioned (default-context) fact of this same
    # concept — i.e. indistinguishable from a "total" in that context.
    # DQC.US.0117.9574 then compared that undimensioned $1,562,000
    # Consulting figure against the Hardware+Software dimensional sum as if
    # it SHOULD equal their total, which it was never meant to. Consulting
    # is a genuine third disaggregation member, not the total — it now gets
    # its own member (tifx:ConsultingMember) exactly like Hardware/Software,
    # so the three members are each independently dimensioned and none of
    # them is mistaken for the aggregate.
    "hardware sales":                   {"concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                                         "type": "duration", "balance": "credit", "negate": False,
                                         "dimension": ("tifx:RevenueProductOrServiceAxis", "tifx:HardwareMember")},
    "software sales":                   {"concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                                         "type": "duration", "balance": "credit", "negate": False,
                                         "dimension": ("tifx:RevenueProductOrServiceAxis", "tifx:SoftwareMember")},
    "consulting":                       {"concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                                         "type": "duration", "balance": "credit", "negate": False,
                                         "dimension": ("tifx:RevenueProductOrServiceAxis", "tifx:ConsultingMember")},
    "total revenue":                    {"concept": "us-gaap:Revenues",
                                         "type": "duration", "balance": "credit", "negate": False},

    # ── Income Statement — COGS / Gross Profit ───────────────────────────────
    "cost of goods sold":               {"concept": "us-gaap:CostOfGoodsAndServicesSold",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "gross profit":                     {"concept": "us-gaap:GrossProfit",
                                         "type": "duration", "balance": "credit", "negate": "display"},

    # ── Income Statement — Operating Expenses ────────────────────────────────
    "salaries and wages":               {"concept": "us-gaap:LaborAndRelatedExpense",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "supplies expense":                 {"concept": "us-gaap:SuppliesExpense",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "lease expense":                    {"concept": "us-gaap:OperatingLeaseExpense",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "rent expense":                     {"concept": "us-gaap:OperatingLeaseExpense",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "bad debt expense":                 {"concept": "us-gaap:ProvisionForDoubtfulAccounts",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "marketing expense":                {"concept": "us-gaap:MarketingExpense",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "r&d expense":                      {"concept": "us-gaap:ResearchAndDevelopmentExpense",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "research and development expense": {"concept": "us-gaap:ResearchAndDevelopmentExpense",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "depreciation expense":             {"concept": "us-gaap:DepreciationAndAmortization",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "legal & professional fees":        {"concept": "us-gaap:ProfessionalFees",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "total operating expenses":         {"concept": "us-gaap:OperatingExpenses",
                                         "type": "duration", "balance": "debit",  "negate": False},
    # NOTE: an all-in "Total Expenses" row (COGS + every operating expense +
    # interest expense + income tax) existed only under the OLD, incorrectly
    # flattened Income Statement layout. Now that docx_service.py emits the
    # correct 5-section GAAP structure (see IncomeStatementTable.jsx), that
    # row no longer prints at all — it's superseded by "Gross Profit",
    # "Total Operating Expenses", "Total Other Income / (Expenses)", and
    # "Income Before Income Tax" below, each with their own mapping. No
    # "total expenses" key is needed here anymore.
    "operating income / (loss)":        {"concept": "us-gaap:OperatingIncomeLoss",
                                         "type": "duration", "balance": "credit", "negate": "display"},
    "operating income":                 {"concept": "us-gaap:OperatingIncomeLoss",
                                         "type": "duration", "balance": "credit", "negate": "display"},

    # ── Income Statement — Other / Non-Operating ─────────────────────────────
    "interest income":                  {"concept": "us-gaap:InterestIncomeOther",
                                         "type": "duration", "balance": "credit", "negate": False},
    # us-gaap:InterestExpenseNonoperating (added in the 2024 taxonomy),
    # not the older us-gaap:InterestExpense — DQC rule 0181 flags
    # InterestExpense/InterestExpenseOperating when they're a descendant of
    # NonoperatingIncomeExpense in the calculation linkbase, which is
    # exactly this Income Statement's structure ("interest expense" rolls
    # up into "total other income / (expenses)" -> NonoperatingIncomeExpense
    # below). Same balance (debit) and period type (duration) as
    # InterestExpense, so no other change needed here.
    "interest expense":                 {"concept": "us-gaap:InterestExpenseNonoperating",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "total other income / (expenses)":  {"concept": "us-gaap:NonoperatingIncomeExpense",
                                         "type": "duration", "balance": "credit", "negate": "display"},
    "income before income tax":         {"concept": "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                                         "type": "duration", "balance": "credit", "negate": "display"},
    "income tax expense":               {"concept": "us-gaap:IncomeTaxExpenseBenefit",
                                         "type": "duration", "balance": "debit",  "negate": False},
    # Alias: docx_service.py's _income_stmt_label() now displays this row
    # as "Income tax" rather than "Income Tax Expense" (the chart-of-
    # accounts acct_name is unchanged) -- same concept, just a second
    # label-text key so the row still resolves during tagging.
    "income tax":                       {"concept": "us-gaap:IncomeTaxExpenseBenefit",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "net income / (loss)":              {"concept": "us-gaap:NetIncomeLoss",
                                         "type": "duration", "balance": "credit", "negate": "display"},
    "net income":                       {"concept": "us-gaap:NetIncomeLoss",
                                         "type": "duration", "balance": "credit", "negate": "display"},

    # ── Cash Flow ────────────────────────────────────────────────────────────
    "depreciation & amortization":      {"concept": "us-gaap:DepreciationDepletionAndAmortization",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "depreciation and amortization":    {"concept": "us-gaap:DepreciationDepletionAndAmortization",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "changes in accounts receivable":   {"concept": "us-gaap:IncreaseDecreaseInAccountsReceivable",
                                         "type": "duration", "balance": "credit", "negate": "display_invert"},
    "changes in inventory":             {"concept": "us-gaap:IncreaseDecreaseInInventories",
                                         "type": "duration", "balance": "credit", "negate": "display_invert"},
    "changes in prepaid expenses":      {"concept": "us-gaap:IncreaseDecreaseInPrepaidDeferredExpenseAndOtherAssets",
                                         "type": "duration", "balance": "credit", "negate": "display_invert"},
    "changes in accounts payable":      {"concept": "us-gaap:IncreaseDecreaseInAccountsPayable",
                                         "type": "duration", "balance": "debit",  "negate": "display"},
    "changes in accrued liabilities":   {"concept": "us-gaap:IncreaseDecreaseInAccruedLiabilities",
                                         "type": "duration", "balance": "debit",  "negate": "display"},
    "changes in unearned revenue":      {"concept": "us-gaap:IncreaseDecreaseInDeferredRevenue",
                                         "type": "duration", "balance": "debit",  "negate": "display"},
    "net cash from operating activities": {"concept": "us-gaap:NetCashProvidedByUsedInOperatingActivities",
                                         "type": "duration", "balance": "debit",  "negate": "display"},
    "purchase of short-term investments": {"concept": "us-gaap:PaymentsToAcquireShortTermInvestments",
                                         "type": "duration", "balance": "credit", "negate": False},
    "purchase of property, plant & equip": {"concept": "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
                                         "type": "duration", "balance": "credit", "negate": False},
    "capital expenditures":             {"concept": "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
                                         "type": "duration", "balance": "credit", "negate": False},
    "net cash from investing activities": {"concept": "us-gaap:NetCashProvidedByUsedInInvestingActivities",
                                         "type": "duration", "balance": "debit",  "negate": "display"},
    # DQC.US.0015.2820: Proceeds-from-debt concepts have NO negated label —
    # they represent cash actually received and must always be tagged
    # non-negative, regardless of how the source HTML happens to
    # parenthesize the row. "display" mode (which trusts the source's
    # parens as the sign) let a parenthesized net-financing presentation
    # leak through as a negative ProceedsFromIssuanceOfLongTermDebt value
    # for both years in the log. Fixed sign (False = always positive) is
    # the correct mode for both proceeds rows, same as "issuance of common
    # stock" below.
    # UPDATE: financial_service.py's build_cash_flow() now splits gross
    # proceeds from gross repayments for both debt accounts (see
    # _acct_gross_by_name), rather than netting new-borrowing against
    # repayments into one signed figure. That resolves DQC.US.0015.2820 at
    # the root instead of via a sign trade-off: ProceedsFromShortTermDebt /
    # ProceedsFromIssuanceOfLongTermDebt are now ALWAYS the gross new-
    # borrowing amount alone, which is by construction never negative, so
    # "negate": False (always positive) is correct and safe again -- no
    # data-dependent sign needed here anymore. The repayment side of each
    # account gets its own separate, always-non-negative concept below.
    "proceeds from short-term debt":    {"concept": "us-gaap:ProceedsFromShortTermDebt",
                                         "type": "duration", "balance": "credit", "negate": False},
    "repayments of short-term debt":    {"concept": "us-gaap:RepaymentsOfShortTermDebt",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "proceeds from long-term debt":     {"concept": "us-gaap:ProceedsFromIssuanceOfLongTermDebt",
                                         "type": "duration", "balance": "credit", "negate": False},
    "repayments of long-term debt":     {"concept": "us-gaap:RepaymentsOfLongTermDebt",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "issuance of common stock":         {"concept": "us-gaap:ProceedsFromIssuanceOfCommonStock",
                                         "type": "duration", "balance": "debit",  "negate": False},
    "purchase of treasury stock":       {"concept": "us-gaap:PaymentsForRepurchaseOfCommonStock",
                                         "type": "duration", "balance": "credit", "negate": False},
    "dividends paid":                   {"concept": "us-gaap:PaymentsOfDividendsCommonStock",
                                         "type": "duration", "balance": "credit", "negate": False},
    "net cash from financing activities": {"concept": "us-gaap:NetCashProvidedByUsedInFinancingActivities",
                                         "type": "duration", "balance": "debit",  "negate": "display"},
    "net increase in cash":             {"concept": "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
                                         "type": "duration", "balance": "debit",  "negate": "display"},
    # "Cash at Beginning of Period" is dated at the END of the PRIOR
    # period — one year earlier than the column it sits in (the 2025
    # column's beginning-of-period balance IS the 2024 column's
    # end-of-period balance). instant_offset=1 tells _tag_table() to shift
    # this row's dates back one extra year relative to its column index.
    # Without it, this row was (wrongly) dated at each column's OWN
    # period-end — the same date "Cash at End of Period" uses one column
    # over — which silently collided with that row's fact in the `seen`
    # de-dup and dropped one of the two.
    "cash at beginning of period":      {"concept": "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
                                         "type": "instant", "balance": "debit",  "negate": False,
                                         "instant_offset": 1},
    "cash at end of period":            {"concept": "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
                                         "type": "instant", "balance": "debit",  "negate": False},
}


# ─────────────────────────────────────────────────────────────────────────────
# 2.  CONTEXT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

class ContextRegistry:
    """Tracks unique XBRL contexts and renders the <ix:header> block."""

    def __init__(self, cik: str, period_start: str, period_end: str, ticker: str = ""):
        self.cik          = cik
        self.ticker       = ticker.lower()
        self.period_start = period_start   # ISO "YYYY-MM-DD"
        self.period_end   = period_end
        self._contexts: dict[str, dict] = {}

    def instant_id(self, as_of: str) -> str:
        key = f"ctx_instant_{as_of.replace('-', '')}"
        if key not in self._contexts:
            self._contexts[key] = {"type": "instant", "date": as_of}
        return key

    def duration_id(self, start: str, end: str) -> str:
        key = f"ctx_dur_{start.replace('-','')}_{end.replace('-','')}"
        if key not in self._contexts:
            self._contexts[key] = {"type": "duration", "start": start, "end": end}
        return key

    def dimensional_duration_id(self, start: str, end: str,
                                 dimension: str, member: str) -> str:
        """
        Return (creating if needed) a duration context qualified by one
        explicit-member dimension, e.g. tifx:RevenueProductOrServiceAxis =
        tifx:HardwareMember — used to disaggregate a single us-gaap concept
        (like RevenueFromContractWithCustomerExcludingAssessedTax) into
        multiple facts sharing the same concept and period but distinguished
        by dimension, instead of needing a separate extension concept per
        product line.

        dimension / member are QNames exactly as they should appear in the
        instance, e.g. "tifx:RevenueProductOrServiceAxis" / "tifx:HardwareMember".
        """
        member_local = member.split(":")[-1]
        key = f"ctx_dur_{start.replace('-','')}_{end.replace('-','')}_{member_local}"
        if key not in self._contexts:
            self._contexts[key] = {
                "type": "duration", "start": start, "end": end,
                "dimension": dimension, "member": member,
            }
        return key

    def render_xbrl_header(self, entity_name: str, ticker: str, currency: str,
                            taxonomy_year: int = 2026,
                            skip_concepts: Optional[set] = None,
                            business_info: Optional[dict] = None,
                            form_type: str = "10-K") -> str:
        """
        Return the full <ix:header> … </ix:header> block as a raw XML string.

        IMPORTANT: This string is injected verbatim via string replacement (not via
        BeautifulSoup DOM insertion) so that namespace-prefixed elements like
        <ix:header>, <ix:references>, <xbrli:context> etc. are preserved exactly.
        BeautifulSoup drops or mangles namespace prefixes when inserting nodes.

        skip_concepts: dei:* concept local names (e.g. {"EntityRegistrantName"})
        that are now tagged INLINE on the visible cover page by
        tag_cover_page(). Their hidden ix:hidden duplicates are dropped here
        so the same (concept, contextRef) pair is never emitted twice —
        EDGAR/Arelle rejects that as a duplicate fact (EFM.6.05.12 /
        arelle:duplicateFacts), even when the two values happen to agree.

        business_info: dict loaded from business_info.toml (see
        load_business_info()). Every value below is read from this dict
        with .get(key, <same literal the hardcoded version used before>),
        so an empty/missing business_info.toml reproduces the old
        hardcoded behavior exactly — nothing regresses if the file isn't
        there yet. Once business_info.toml is populated, every DEI fact
        below tracks it instead of a second, easily-drifted hardcoded copy.
        """
        skip_concepts = skip_concepts or set()
        biz = business_info or {}
        is_10k = (form_type != "10-Q")
        # Derive a datestamp for the extension XSD filename (e.g. 20251231)
        period_end_clean = self.period_end.replace('-', '')

        # EFM.6.05.20 / DQC.US.0006.14: DocumentFiscalPeriodFocus must match
        # the actual duration of the period being reported. Fiscal year end
        # is hardcoded to Dec 31 below (CurrentFiscalYearEndDate = "--12-31"),
        # so for a 10-Q the fiscal quarter is simply ceil(period_end month / 3).
        # A 10-K's period is always the full fiscal year, so it's always "FY".
        if is_10k:
            fiscal_period_focus = "FY"
        else:
            period_end_month = int(self.period_end[5:7])
            fiscal_period_focus = f"Q{(period_end_month - 1) // 3 + 1}"

        # The extension XSD (tifx-20251231.xsd) is named for the FISCAL YEAR
        # END, and is the single taxonomy file shared by the 10-K and every
        # 10-Q in that fiscal year — it is NOT regenerated per filing. Using
        # period_end_clean here is wrong for 10-Qs: a Q2 filing's period_end
        # is 2025-06-30, which would build a schemaRef href of
        # "tifx-20250630.xsd" — a file that doesn't exist on disk, causing
        # Arelle's [IOerror] Could not load file, followed by a
        # missingReferences error on every single dei/us-gaap fact (Arelle
        # has no schema definitions to check them against once the schemaRef
        # target fails to load).
        #
        # CurrentFiscalYearEndDate is hardcoded to "--12-31" a few lines below
        # (fiscal year end = Dec 31), so the fiscal year end date is always
        # {fiscal_year}-12-31. self.period_end's year IS the fiscal year for
        # both 10-K (period_end = Dec 31) and 10-Q (period_end = quarter end
        # within the same fiscal year), so self.period_end[:4] is safe here.
        fiscal_year_end_clean = f"{self.period_end[:4]}1231"

        filer_category_key = biz.get("filer_category", "non_accelerated")
        filer_category_display = _FILER_CATEGORY_DISPLAY.get(
            filer_category_key, "Non-accelerated Filer"
        )
        is_smaller_reporting = biz.get("is_smaller_reporting_company", True)
        is_emerging_growth   = biz.get("is_emerging_growth_company", False)
        is_shell_company      = biz.get("is_shell_company", False)
        is_wksi                = biz.get("is_well_known_seasoned_issuer", False)
        is_voluntary_filer     = biz.get("is_exempt_from_filing", False)
        filed_all_reports      = biz.get("filed_all_required_reports", True)
        submitted_idata         = biz.get("submitted_interactive_data", True)

        aggregate_market_value = str(biz.get("aggregate_market_value", "0")).replace(",", "")
        shares_outstanding = str(biz.get("shares_outstanding", "10000000")).replace(",", "")

        lines = [
            # target="" declares the default (unnamed) iXBRL target document,
            # which satisfies [arelle:ixdsTargetNotDefined]
            '<ix:header>',
            '  <ix:hidden>',
            # EntityRegistrantName uses the full-year duration context that is also
            # used for income statement/cash flow facts. This avoids creating a
            # separate ctx_dei context that would duplicate ctx_dur_{start}_{end}
            # and trigger EFM.6.05.07.
            # DEI cover facts — all required by the Cover presentation group.
            # Using the full-year duration context for string/text facts.
            f'    <ix:nonNumeric name="dei:DocumentType" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{form_type}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:AmendmentFlag" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">false</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:DocumentPeriodEndDate" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{self.period_end}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityRegistrantName" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">'
            f'{entity_name}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityCentralIndexKey" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{self.cik}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:DocumentFiscalYearFocus" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{self.period_end[:4]}</ix:nonNumeric>',
            # EFM.6.05.20 / DQC.US.0006.14: required non-empty in every
            # submission's Required Context, and must match the actual
            # reported period duration — "FY" for a 10-K, "Q1"/"Q2"/"Q3"
            # for a 10-Q (computed above as fiscal_period_focus).
            f'    <ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{fiscal_period_focus}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:CurrentFiscalYearEndDate" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">--12-31</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:DocumentTransitionReport" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">false</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityFilerCategory" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{filer_category_display}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntitySmallBusiness" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{str(is_smaller_reporting).lower()}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityEmergingGrowthCompany" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{str(is_emerging_growth).lower()}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityShellCompany" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{str(is_shell_company).lower()}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityCurrentReportingStatus" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{_yn(filed_all_reports)}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityInteractiveDataCurrent" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{_yn(submitted_idata)}</ix:nonNumeric>',
        ] + (
            # ── Annual-only DEI facts ────────────────────────────────────────
            # EFM.6.05.21 / 6.05.49: EDGAR rejects these concepts outright on
            # a 10-Q submission ("Submission type 10-Q should not have a
            # value for ..."), so they are only ever emitted for a 10-K.
            [
                f'    <ix:nonNumeric name="dei:DocumentAnnualReport" '
                f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">true</ix:nonNumeric>',
                f'    <ix:nonNumeric name="dei:DocumentFinStmtErrorCorrectionFlag" '
                f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">false</ix:nonNumeric>',
                # EFM.6.05.21: required in the SAME context as EntityFilerCategory
                # whenever that value is "Accelerated Filer" or "Large
                # Accelerated Filer" (SOX 404(b) auditor attestation applies).
                # Defaults to True for those two categories, False otherwise;
                # override via business_info.toml's icfr_auditor_attestation key
                # if a given filer's actual attestation status differs.
                f'    <ix:nonNumeric name="dei:IcfrAuditorAttestationFlag" '
                f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">'
                f'{str(biz.get("icfr_auditor_attestation", filer_category_key in ("accelerated", "large_accelerated"))).lower()}'
                f'</ix:nonNumeric>',
                f'    <ix:nonNumeric name="dei:EntityVoluntaryFilers" '
                f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{_yn(is_voluntary_filer)}</ix:nonNumeric>',
                f'    <ix:nonNumeric name="dei:EntityWellKnownSeasonedIssuer" '
                f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{_yn(is_wksi)}</ix:nonNumeric>',
                # EntityPublicFloat uses instant context
                f'    <ix:nonFraction name="dei:EntityPublicFloat" '
                f'contextRef="ctx_instant_{self.period_end.replace("-","")}" '
                f'unitRef="USD" decimals="0">{aggregate_market_value}</ix:nonFraction>',
            ] if is_10k else []
        ) + [
            f'    <ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" '
            f'contextRef="ctx_instant_{self.period_end.replace("-","")}" '
            f'unitRef="shares" decimals="0">{shares_outstanding}</ix:nonFraction>',
            # Entity identification / address facts
            f'    <ix:nonNumeric name="dei:EntityFileNumber" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("commission_file_number", "000-00000")}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityTaxIdentificationNumber" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("ein", "00-0000000")}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityIncorporationStateCountryCode" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("state_abbr", "MD")}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityAddressAddressLine1" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("address_line1", "1 Technology Drive")}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityAddressCityOrTown" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("city", "Columbia")}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityAddressStateOrProvince" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("state_abbr", "MD")}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:EntityAddressPostalZipCode" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("zip_code", "21046")}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:CityAreaCode" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("area_code", "410")}</ix:nonNumeric>',
            f'    <ix:nonNumeric name="dei:LocalPhoneNumber" '
            f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("local_phone_number", "555-9900")}</ix:nonNumeric>',
        ] + (
            # Auditor facts — EFM.6.05.54 requires these for a 10-K, and
            # EFM.6.05.54.*Unexpected REJECTS them on a 10-Q, so only emit
            # for a 10-K.
            [
                f'    <ix:nonNumeric name="dei:AuditorName" '
                f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("auditor_firm", "Clarity Accounting")}</ix:nonNumeric>',
                f'    <ix:nonNumeric name="dei:AuditorLocation" '
                f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("auditor_location", "Columbia, MD")}</ix:nonNumeric>',
                # AuditorFirmId is a token (string) type — NOT numeric. No unitRef/decimals.
                # Value must be a positive integer matching PCAOB firm ID pattern [1-9][0-9]*
                # Update auditor_firm_id in business_info.toml with your actual PCAOB ID.
                f'    <ix:nonNumeric name="dei:AuditorFirmId" '
                f'contextRef="ctx_dur_{self.period_start.replace("-","")}_{self.period_end.replace("-","")}">{biz.get("auditor_firm_id", "99")}</ix:nonNumeric>',
            ] if is_10k else []
        ) + [
            '  </ix:hidden>',
            '  <ix:references>',
            # EDGAR requires schemaRef entries for BOTH us-gaap AND dei namespaces.
            # Without the dei schemaRef, dei:EntityRegistrantName has no schema
            # definition and Arelle raises [ix11.12.1.2:missingReferences].
            # Single schemaRef pointing to the local extension XSD.
            # The extension XSD imports both us-gaap-2026 and dei-2026 locally,
            # so Arelle resolves all concepts without hitting the internet.
            # The filename follows EDGAR convention: {ticker}-{fiscal_year_end}.xsd
            # NOTE: this is the FISCAL YEAR END datestamp (fiscal_year_end_clean),
            # not the filing's own period_end — the extension XSD is one file
            # shared across the 10-K and all 10-Qs in the fiscal year, and is
            # never regenerated per quarter. Using period_end_clean here would
            # build a nonexistent filename for any 10-Q (see comment above).
            f'    <link:schemaRef xlink:type="simple" '
            f'xlink:href="{ticker.lower()}-{fiscal_year_end_clean}.xsd"/>',
            '  </ix:references>',
            '  <ix:resources>',
            # ctx_dei removed: EntityRegistrantName references
            # ctx_dur_{period_start}_{period_end} directly (set above in ix:hidden),
            # which is emitted for IS/CF facts. No separate ctx_dei context needed.
            # This eliminates [EFM.6.05.07] duplicate context error.
        ]

        # Drop any hidden dei:* fact whose concept is now tagged inline on the
        # visible cover page — see docstring above.
        if skip_concepts:
            lines = [
                ln for ln in lines
                if not any(f'name="dei:{concept}"' in ln for concept in skip_concepts)
            ]

        for ctx_id, ctx in self._contexts.items():
            lines.append(f'    <xbrli:context id="{ctx_id}">')
            lines.append('      <xbrli:entity>')
            lines.append(f'        <xbrli:identifier scheme="http://www.sec.gov/CIK">{self.cik}</xbrli:identifier>')
            if "dimension" in ctx:
                lines.append('        <xbrli:segment>')
                lines.append(f'          <xbrldi:explicitMember dimension="{ctx["dimension"]}">'
                             f'{ctx["member"]}</xbrldi:explicitMember>')
                lines.append('        </xbrli:segment>')
            lines.append('      </xbrli:entity>')
            # Both instant and duration contexts use <xbrli:period> as the wrapper.
            # For instant: <xbrli:period><xbrli:instant>DATE</xbrli:instant></xbrli:period>
            # For duration: <xbrli:period><xbrli:startDate>…</xbrli:startDate>…</xbrli:period>
            lines.append('      <xbrli:period>')
            if ctx["type"] == "instant":
                lines.append(f'        <xbrli:instant>{ctx["date"]}</xbrli:instant>')
            else:
                lines.append(f'        <xbrli:startDate>{ctx["start"]}</xbrli:startDate>')
                lines.append(f'        <xbrli:endDate>{ctx["end"]}</xbrli:endDate>')
            lines.append('      </xbrli:period>')
            lines.append('    </xbrli:context>')

        lines += [
            f'    <xbrli:unit id="USD"><xbrli:measure>iso4217:{currency}</xbrli:measure></xbrli:unit>',
            '    <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>',
            # Compound unit for per-share facts (EPS, etc.) — a plain "USD"
            # unit is wrong for these; XBRL requires a divide unit (USD per
            # share), same as unitRef="usdPerShare" in real filings (e.g.
            # Amazon's EarningsPerShareBasic/Diluted facts).
            '    <xbrli:unit id="usdPerShare">',
            '      <xbrli:divide>',
            f'        <xbrli:unitNumerator><xbrli:measure>iso4217:{currency}</xbrli:measure></xbrli:unitNumerator>',
            '        <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>',
            '      </xbrli:divide>',
            '    </xbrli:unit>',
            # Dimensionless ratio unit for percentage facts (e.g. effective
            # tax rate). Per the us-gaap percentItemType convention, the
            # VALUE tagged must be the decimal fraction (0.156), not the
            # displayed "15.6" — see the scale="-2" usage in notes_config.py.
            '    <xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>',
            '  </ix:resources>',
            '</ix:header>',
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  NUMERIC VALUE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_DASH_RE  = re.compile(r'^[—–-]+$')
_NUM_RE   = re.compile(r'^\$?\(?([\d,]+)\)?$')


def _parse_amount(text: str) -> Optional[int]:
    """
    Convert a formatted table cell value to a plain, ALWAYS NON-NEGATIVE
    integer (in dollars). Returns None if the cell is a dash / empty /
    non-numeric.

    IMPORTANT: parentheses are stripped here as pure display decoration,
    NOT read as a sign. docx_service.py now parenthesizes some rows
    (Treasury Stock, Interest Expense, Dividends Paid, Purchase of
    Treasury Stock, ...) purely for human-readable GAAP presentation --
    some of those rows are display-only parens on an underlying POSITIVE
    stored magnitude (e.g. Interest Expense), others mirror a genuinely
    negative stored value (e.g. Treasury Stock, which financial_service.py
    already stores negative so the balance-sheet subtraction math works).
    Either way, the correct XBRL sign for a given concept is a property of
    THAT CONCEPT (its natural balance vs. how it rolls up), which is
    exactly what GAAP_MAPPINGS' "negate" flag already encodes -- so sign
    must come from "negate" alone. Deriving it AGAIN from the parens here
    would double-apply (or, for a row financial_service.py already stores
    negative, cancel out) the negation and silently produce the wrong
    tagged sign. See _format_xbrl_value().
    """
    t = text.strip()
    if not t or _DASH_RE.match(t):
        return None
    clean = t.replace("$", "").replace(",", "").replace("(", "").replace(")", "").replace("-", "").strip()
    try:
        return int(clean)
    except ValueError:
        return None


def _format_xbrl_value(raw: int, negate, raw_txt: str = ""):
    """
    Return (signed_value, abs_value_str, sign_attr) for use in an
    ix:nonFraction tag, given the row/column's `negate` setting and the
    cell's original display text.

    negate is True / False for concepts whose XBRL sign is a FIXED
    property of the CONCEPT itself, independent of whatever parens
    docx_service.py used for GAAP presentation -- contra/deduction items
    like Treasury Stock, Interest Expense, Dividends Paid, COGS, Income
    Tax always roll up (add or subtract) the same way regardless of the
    period's actual numbers, so `raw` (always a non-negative magnitude
    from _parse_amount()) is simply negated or not per that fixed rule.

    negate is the string "display" for concepts whose sign is instead
    DATA-DEPENDENT -- a net/subtotal line that can legitimately be
    positive OR negative depending on the period's numbers: Net Income
    vs. Net Loss, Operating Income vs. Loss, Total Other Income vs.
    (Expenses), Net Cash Provided vs. Used, Retained Earnings vs. an
    Accumulated Deficit. There's no fixed convention to apply for these
    -- the only source of truth for which way it went is exactly what
    docx_service.py already rendered (parens = negative), so that's what
    gets trusted here instead of a negate flag.

    negate is the string "display_invert" for the ASSET-side cash-flow
    reconciling lines (Changes in Accounts Receivable / Inventory /
    Prepaid Expenses) specifically. These are a special case of
    data-dependent sign, but in the OPPOSITE direction from plain
    "display": financial_service.py's build_cash_flow() already bakes in
    the CASH-FLOW-DIRECTION sign for these three (increase in the asset
    -> negative, since that's a use of cash -- see its own "Debit-normal
    asset; increase = cash use (negative)" comments), so the source
    Python value and rendered display text are already negative exactly
    when the asset increased. But the GAAP concepts themselves
    (IncreaseDecreaseInAccountsReceivable etc.) are documented the other
    way around: positive means the asset INCREASED, independent of cash
    direction -- the cash-flow calculation linkbase's weight="-1.0" (see
    tifx-20251231_cal.xml) is what converts that GAAP-positive increase
    into a negative contribution to operating cash, not the fact's own
    sign. Tagging these three with plain "display" doubles up with that
    -1 weight (both the fact's sign AND the calc weight flip the same
    direction), which is exactly what caused
    calc11e:inconsistentCalculationUsingRounding to report a computed sum
    roughly (2x the AR+Inventory+Prepaid contributions) larger than the
    tagged total. "display_invert" reads the same parens-based sign as
    "display" but flips the conclusion, restoring the GAAP-convention
    sign so the fact and the -1 calc weight each do their own job exactly
    once.
    Liability-side reconciling lines (AP, Accrued Liabilities, Deferred/
    Unearned Revenue) do NOT need this: for a credit-normal liability,
    financial_service.py's "increase = cash source (positive)" happens to
    already match the GAAP "increase is positive" convention, so plain
    "display" is correct for those three as-is.

    The ixt:num-dot-decimal transform accepts comma thousands-separators
    — parsing them back out is exactly what this transform is for — so
    the displayed/tagged content can (and should) keep the human-readable
    "26,000,000" form rather than the bare "26000000" a plain str() gives.
    Only a leading minus sign is disallowed; negative XBRL values must be
    expressed using the sign="-" attribute with the (comma-formatted)
    absolute value as element content. Positive values carry no sign
    attribute.

    Returns
    -------
    signed_value : int  the true signed value (for the cross-table
                         duplicate-value consistency check in `seen`)
    abs_str      : str  comma-grouped absolute value (no minus sign)
    sign_attr    : str  either 'sign="-" ' (with trailing space) or ""
    """
    if negate == "display":
        stripped = raw_txt.strip()
        is_neg = stripped.startswith("(") or stripped.startswith("$(")
        v = -raw if is_neg else raw
    elif negate == "display_invert":
        stripped = raw_txt.strip()
        is_neg = stripped.startswith("(") or stripped.startswith("$(")
        v = raw if is_neg else -raw
    else:
        v = -raw if negate else raw
    if v < 0:
        return v, f"{abs(v):,}", 'sign="-" '
    return v, f"{v:,}", ""


# ─────────────────────────────────────────────────────────────────────────────
# 4.  LABEL NORMALISER
# ─────────────────────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Lower-case, collapse whitespace, strip common footnote markers."""
    t = text.lower().strip()
    t = re.sub(r'\s+', ' ', t)
    t = re.sub(r'\([\d]+\)$', '', t).strip()   # strip trailing (1) footnote refs
    return t


# ─────────────────────────────────────────────────────────────────────────────
# 5.  TABLE TAGGER
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 5A.  MULTI-COLUMN CONTEXT RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────
#
# A 10-K's Income Statement and Cash Flow Statement print THREE comparative
# columns (current year, prior year, prior-prior year); the Balance Sheet
# only ever prints two (current, prior). _tag_table() below needs to hand
# each column its own distinct context, however many columns a given row has.

def _shift_years(iso_date: str, years: int) -> str:
    """Shift an ISO date string back by `years` full years.

    Clamps Feb 29 -> Feb 28 if the target year isn't a leap year (period-end
    dates here are always Dec 31 in practice, but this keeps the helper
    correct in general rather than raising on an edge case).
    """
    d = date.fromisoformat(iso_date)
    try:
        return d.replace(year=d.year - years).isoformat()
    except ValueError:
        return d.replace(year=d.year - years, day=28).isoformat()


def _period_for_offset(period_start: str, period_end: str,
                        prior_start: str, prior_end: str,
                        offset: int) -> tuple:
    """
    Return (start, end) for `offset` full years before the base period.

    offset=0 -> the base (period_start, period_end) itself — the report's
                current-year column.
    offset=1 -> exactly (prior_start, prior_end) as supplied by the caller
                — this is intentionally NOT re-derived by subtracting a year
                from period_start/period_end, so a caller who passes a
                non-standard prior period (e.g. a stub fiscal year) is
                respected.
    offset>=2 -> prior_start/prior_end shifted (offset-1) further years back.
                 This is what a 10-K's 3rd comparative column (e.g. 2023,
                 when period_end is 2025-12-31) resolves to. Previously
                 _tag_table() just repeated the offset=1 context for every
                 column past the second, which collided with the real
                 offset=1 (concept, context) pair already tagged and got
                 silently dropped by the `seen` de-dup — so the 3rd column
                 never got tagged at all.
    """
    if offset <= 0:
        return period_start, period_end
    if offset == 1:
        return prior_start, prior_end
    return _shift_years(prior_start, offset - 1), _shift_years(prior_end, offset - 1)


def _nearest_preceding_heading_text(table: Tag) -> str:
    """
    Find the nearest h1-h6 heading anywhere before this <table> in document
    order (not just direct siblings — docx_service.py's heading and table
    are siblings today, but find_previous() walks the whole tree so this
    stays correct even if a wrapping element gets added later). Used to
    identify which financial statement (Balance Sheet / Income Statement /
    Cash Flow Statement) a given table belongs to, since docx_service.py
    always emits "Balance Sheets" / "Statements of Income" / "Statements of
    Cash Flows" as a heading immediately before each one.
    """
    heading = table.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
    return _normalise(heading.get_text()) if heading else ""


def _table_data_col_count(table: Tag) -> int:
    """Number of data columns (excluding the label column) in a table."""
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) > 1:
            return len(cells) - 1
    return 0


def _tag_table(table: Tag, registry: ContextRegistry,
               period_start: str, period_end: str,
               prior_start:  str, prior_end:  str,
               seen: dict,
               column_periods=None) -> int:
    """
    Walk a <table> element.  For each data row, extract the label from the
    first <td>, look it up in GAAP_MAPPINGS, then wrap each numeric cell's
    content with an <ix:nonFraction> tag.

    column_periods: optional override for the offset-based
    _period_for_offset() computation below, needed for 10-Q tables whose
    columns aren't simply "further years back" from period_start/prior_start
    (the assumption _period_for_offset() makes, correct for a 10-K) — e.g.
    a 10-Q Income Statement's 4 columns are 2 years x 2 DURATIONS (quarter,
    then YTD), not 4 sequential years. tag_filing() builds this per table
    by detecting which statement a table belongs to (see its own docstring)
    and passes None for tables where the default offset math is already
    correct. Two shapes are accepted:
      - list[(start, end), ...]: one (start, end) tuple per data column,
        applied uniformly to EVERY row in the table regardless of that
        row's own `instant_offset` — used for the Income Statement and
        Balance Sheet, where every row genuinely shares the same column
        dates.
      - dict[label_text, list[(start, end), ...]]: a PER-ROW override,
        keyed by the row's own normalized label text (same string
        GAAP_MAPPINGS is keyed by). A row whose label isn't a key in the
        dict falls through to the normal offset-based math below,
        untouched. This exists for tables where only ONE row's dates
        diverge from every other row's — e.g. the Cash Flow Statement's
        "Cash at Beginning of Period"; see tag_filing()'s "statements of
        cash flows" branch for why that row alone needs an override while
        every other row in the same table is already correct as-is.

    seen: shared dict of {(concept, ctx_id): signed_value} across all tables
    in the filing. A repeated (concept, context) pair IS tagged again when
    the value matches — this is standard, SEC-permitted practice (e.g. real
    10-Ks routinely tag Net Income with the identical contextRef in both
    the Income Statement and the Cash Flow Statement). Arelle's
    arelle:duplicateFacts message fires for this too, but that's an Arelle
    display-setting matter (Tools > Validation > Warn on duplicate facts >
    Inconsistent), not a document problem — confirmed that giving each
    repeat its own context doesn't change Arelle's message at all, it just
    lists both context ids. The recorded value here is used to catch a
    genuine bug: the same (concept, context) pair appearing with two
    DIFFERENT values is skipped (with a loud warning), since that's an
    actual data mismatch, not just a legitimate repeat.

    Returns the number of values tagged.
    """
    tagged = 0
    rows = table.find_all("tr")

    for row in rows:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        label_cell = cells[0]
        label_text = _normalise(label_cell.get_text())
        mapping = GAAP_MAPPINGS.get(label_text)
        if not mapping:
            continue

        concept = mapping["concept"]
        negate  = mapping["negate"]

        # instant_offset: for instant facts dated at a DIFFERENT point in
        # time than the column's own period-end — e.g. "Cash at Beginning
        # of Period" is dated at the END of the PRIOR period, one year
        # earlier than the column's own period-end. Defaults to 0 (the
        # column's own period-end/period, which is what every other row
        # uses).
        instant_offset = mapping.get("instant_offset", 0)

        # dimension: (dimension_qname, member_qname) — for rows that
        # disaggregate a standard concept via a custom axis/member instead
        # of (or in addition to) using a distinct concept, e.g. Hardware
        # Sales / Software Sales both use
        # us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax but
        # are distinguished by tifx:RevenueProductOrServiceAxis.
        dimension = mapping.get("dimension")

        # data cells: skip the label cell (index 0). Build ONE distinct
        # context per column — however many comparative columns this row
        # has (2 for a Balance Sheet, 3 for a 10-K's Income Statement /
        # Cash Flow Statement) — instead of assuming exactly two and
        # repeating the second for anything beyond that.
        #
        # row_periods resolves column_periods' two accepted shapes (see
        # this function's docstring) down to a single list-or-None for
        # THIS row: a plain list applies to every row uniformly; a dict
        # applies only to the row(s) it names by label, with every other
        # row falling through to the offset-based math below exactly as
        # if column_periods had been None.
        row_periods = (
            column_periods.get(label_text) if isinstance(column_periods, dict)
            else column_periods
        )
        data_cells = cells[1:]
        contexts = []
        for col_idx in range(len(data_cells)):
            if row_periods is not None and col_idx < len(row_periods):
                start, end = row_periods[col_idx]
            else:
                offset = col_idx + instant_offset
                start, end = _period_for_offset(period_start, period_end,
                                                 prior_start, prior_end, offset)
            if dimension:
                dim_qname, member_qname = dimension
                contexts.append(registry.dimensional_duration_id(start, end, dim_qname, member_qname))
            elif mapping["type"] == "instant":
                contexts.append(registry.instant_id(end))
            else:
                contexts.append(registry.duration_id(start, end))

        for idx, cell in enumerate(data_cells):
            ctx_id  = contexts[idx]
            raw_txt = cell.get_text()
            amount  = _parse_amount(raw_txt)
            if amount is None:
                continue

            # Track (concept, ctx_id) -> signed_value already tagged, across
            # ALL tables in the filing.
            #
            # THIRD note on this (sorry — this one settles it, backed by how
            # Arelle itself actually behaves, not secondhand documentation):
            # last round this used a DIFFERENT context per repeat
            # (ctx_..._dup2 etc.) specifically to dodge arelle:duplicateFacts.
            # Confirmed that doesn't work — Arelle raised the identical
            # message anyway, just listing both context ids. Per Arelle's own
            # docs, [arelle:duplicateFacts] uses the SAME message ID for both
            # legitimate ("consistent") and real ("inconsistent") duplicates;
            # distinguishing them is a LOCAL ARELLE SETTING (Tools →
            # Validation → Warn on duplicate facts → Inconsistent), not
            # something the instance document itself can or should encode.
            # So: back to the simple, correct version — reuse the same
            # context for a same-value repeat (exactly what Amazon's real
            # 10-K does for NetIncomeLoss), and only skip (with a loud
            # warning) when the repeat has a genuinely DIFFERENT value.
            dup_key = (concept, ctx_id)
            signed_value, xbrl_abs, sign_attr = _format_xbrl_value(amount, negate, raw_txt)
            if dup_key in seen:
                prior_value = seen[dup_key]
                if abs(prior_value - signed_value) > 1:  # $1 rounding tolerance
                    print(f"[xbrl_tagger] WARNING: {concept} @ {ctx_id} tagged "
                          f"with inconsistent values ({prior_value} vs "
                          f"{signed_value}) across two tables — this looks like "
                          f"a real data mismatch, not just a formatting repeat. "
                          f"Check the underlying source data.")
                    continue
                # else: consistent duplicate — SEC-permitted, tag it again
                # with the same context (with its own fresh id) rather than
                # skipping it or minting a new context for it.
            else:
                seen[dup_key] = signed_value

            tag_id = f"f{uuid.uuid4().hex[:8]}"

            # ── Placeholder strategy ──────────────────────────────────────────
            # BeautifulSoup's lxml HTML parser lowercases ALL tag and attribute
            # names, turning ix:nonFraction → ix:nonfraction and
            # contextRef → contextref.  Arelle (correctly) treats these as
            # unknown elements because the iXBRL schema is case-sensitive.
            #
            # Solution: store the correctly-cased ix:nonFraction markup in a
            # data-ixbrl attribute on a plain <span> placeholder.  After
            # soup serialisation (which can't mangle attribute *values*),
            # tag_filing() does a second regex pass that swaps every
            # <span data-ixbrl='...'>ABS_VALUE</span> with the real tag.
            #
            # sign="-" is added when the XBRL value is negative; the element
            # content must always be the ABSOLUTE value because
            # ixt:num-dot-decimal rejects negative strings (transformValueError).
            import html as _html
            ix_tag = (
                f'<ix:nonFraction '
                f'name="{concept}" '
                f'{sign_attr}'
                f'contextRef="{ctx_id}" '
                f'unitRef="USD" '
                f'decimals="0" '
                f'id="{tag_id}" '
                f'format="ixt:num-dot-decimal">'
                f'{xbrl_abs}'
                f'</ix:nonFraction>'
            )
            placeholder = BeautifulSoup(
                f'<span data-ixbrl="{_html.escape(ix_tag)}">{xbrl_abs}</span>',
                "lxml"
            ).find("span")
            # Preserve a leading "$" (the first/last-row GAAP convention
            # docx_service.py applies) and a wrapping "(" ")" pair (the
            # GAAP-deduction/contra convention docx_service.py applies to
            # rows like Treasury Stock or Interest Expense) as literal
            # sibling text around the tag -- cell.clear() below would
            # otherwise erase them along with the raw digits, since it
            # wipes the ENTIRE cell, not just the numeric text
            # _parse_amount() consumed. This is purely a display-fidelity
            # copy of whatever docx_service.py already rendered; it is
            # NOT where the tag's sign="-" comes from (that's "negate" in
            # GAAP_MAPPINGS, applied above in _format_xbrl_value()) -- so
            # a row can be visually parenthesized here while its
            # underlying XBRL fact is correctly tagged positive, and vice
            # versa. Without this, negative/contra rows still tag
            # correctly but silently lose their human-readable minus/
            # parens on the rendered EDGAR page.
            stripped   = raw_txt.strip()
            has_dollar = stripped.startswith("$")
            has_parens = stripped.startswith("(") or stripped.startswith("$(")
            cell.clear()
            if has_dollar:
                cell.append("$")
            if has_parens:
                cell.append("(")
            cell.append(placeholder)
            if has_parens:
                cell.append(")")
            tagged += 1

    return tagged


_BALANCE_ROW_RE = re.compile(r'^Balance as of (.+)$', re.IGNORECASE)

# Column order: [label, Common Stock, Treasury Stock, Retained Earnings,
# AOCI, Total Equity] — must match _write_stockholders_equity() /
# StockholdersEquityTable.jsx exactly.
#
# Balance-row concepts mirror GAAP_MAPPINGS' balance-sheet equity entries
# exactly (same concepts, same negate convention) — applied per COLUMN
# here instead of per ROW, since this table is transposed relative to
# every other statement in the filing.
_EQUITY_BALANCE_COLUMN_CONCEPTS = [
    ("us-gaap:CommonStockValue", False),
    # weight="-1.0" on this arc in the calc linkbase (StockholdersEquity ->
    # TreasuryStockValue) expects a POSITIVE natural-balance child value,
    # matching "treasury stock" in GAAP_MAPPINGS above -- the arc's weight
    # does the subtraction, not the tag's own sign.
    ("us-gaap:TreasuryStockValue", False),
    # Retained Earnings, AOCI, and Total Equity can each legitimately go
    # negative (an accumulated deficit, unrealized AOCI losses, or a
    # deficit that outweighs paid-in capital) -- "display" trusts
    # whatever sign docx_service.py's plain _fmt() already rendered
    # (parens = negative) rather than a fixed convention. Matches the
    # "retained earnings" / "total equity" GAAP_MAPPINGS entries used
    # for these same concepts on the Balance Sheet itself.
    ("us-gaap:RetainedEarningsAccumulatedDeficit", "display"),
    ("us-gaap:AccumulatedOtherComprehensiveIncomeLossNetOfTax", "display"),
    ("us-gaap:StockholdersEquity", "display"),
]

# Activity rows: which columns (0-indexed, same order as above) they
# drive, and with what (concept, negate). A row can drive more than one
# column at once (e.g. "Net income" affects both Retained Earnings AND
# Total Equity, tagged with the SAME concept/context — a legitimate,
# SEC-permitted consistent duplicate, same convention _tag_table() already
# uses for e.g. Net Income appearing on both the Income Statement and
# Cash Flow Statement).
_EQUITY_ACTIVITY_ROW_CONCEPTS = {
    "net income": {
        # Net Income can be a Net Loss -- data-dependent sign, same as
        # the "net income" GAAP_MAPPINGS entry used on the Income
        # Statement / Cash Flow Statement for this same concept.
        2: ("us-gaap:NetIncomeLoss", "display"),
        4: ("us-gaap:NetIncomeLoss", "display"),
    },
    "dividends declared": {
        # No calc arc references this concept directly (it isn't part of
        # any role in the calc linkbase), so this doesn't affect Arelle's
        # calc validation either way -- flipped to False purely for
        # consistency with "dividends paid" elsewhere (Balance Sheet,
        # Cash Flow Statement) and with real-EDGAR practice: tag the
        # natural-balance POSITIVE magnitude, let presentation/whatever
        # summation exists carry the sign.
        2: ("us-gaap:PaymentsOfDividends", False),
        4: ("us-gaap:PaymentsOfDividends", False),
    },
    "issuance of common stock": {
        0: ("us-gaap:StockIssuedDuringPeriodValueNewIssues", False),
        4: ("us-gaap:StockIssuedDuringPeriodValueNewIssues", False),
    },
    "purchase of treasury stock": {
        # Same rationale as "dividends declared" above.
        1: ("us-gaap:TreasuryStockValueAcquiredCostMethod", False),
        4: ("us-gaap:TreasuryStockValueAcquiredCostMethod", False),
    },
}


def _parse_equity_balance_date(label: str) -> Optional[str]:
    """'Balance as of December 31, 2025' -> '2025-12-31', or None if the
    label isn't a balance row / doesn't parse."""
    m = _BALANCE_ROW_RE.match(label.strip())
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1).strip(), "%B %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _is_stockholders_equity_table(table: Tag) -> bool:
    """
    Detect the Statement of Stockholders' Equity by its distinctive
    "Balance as of ..." row labels — no other table in this filing has
    that shape. tag_filing() uses this to route the table to
    _tag_stockholders_equity_table() INSTEAD of _tag_table(): several of
    this table's row labels ("Net income", "Common Stock", "Retained
    Earnings"...) happen to also be GAAP_MAPPINGS keys, but applying
    _tag_table()'s one-concept-per-row logic here would tag every column
    in a row with the SAME concept — wrong, since this table needs a
    different concept per column.
    """
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if cells and _BALANCE_ROW_RE.match(cells[0].get_text().strip()):
            return True
    return False


def _tag_stockholders_equity_table(table: Tag, registry: ContextRegistry, seen: dict) -> int:
    """
    Tag the Statement of Stockholders' Equity — a MATRIX table (columns =
    equity components, rows = balance snapshots / activities), fundamentally
    different from every other financial-statement table in this filing,
    which is row=concept / column=period. Here a single row can drive
    several DIFFERENT concepts at once (one per column), so this can't
    reuse _tag_table()'s one-concept-per-row design — it needs its own walk.

    Balance rows are tagged at an INSTANT context (the row's own date).
    Activity rows are tagged at a DURATION context spanning from the
    previous balance row's date to the next balance row's date — i.e. the
    period that row's activity actually covers. Column/concept mapping and
    the (concept, ctx_id) duplicate-value check both mirror _tag_table()
    exactly (same placeholder-span mechanism too) — see its docstring for
    why a same-value repeat is tagged again rather than skipped, and why a
    genuinely different value is skipped with a warning instead.

    Returns the number of values tagged.
    """
    tagged = 0
    parsed_rows = []
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        label = cells[0].get_text().strip()
        parsed_rows.append((label, _parse_equity_balance_date(label), cells))

    if not any(bd for _, bd, _ in parsed_rows):
        return 0  # not actually the equity table — bail out rather than guess

    # next_balance_date_by_idx[i] = the date of the next balance row AT OR
    # AFTER row i — used as the period-end for any activity row that
    # precedes it.
    next_balance_date_by_idx = {}
    upcoming = None
    for i in range(len(parsed_rows) - 1, -1, -1):
        _, bd, _ = parsed_rows[i]
        if bd:
            upcoming = bd
        next_balance_date_by_idx[i] = upcoming

    last_balance_date = None
    for i, (label, balance_date, cells) in enumerate(parsed_rows):
        data_cells = cells[1:]

        if balance_date:
            row_is_balance = True
            ctx_id = registry.instant_id(balance_date)
            last_balance_date = balance_date
        else:
            row_is_balance = False
            row_map = _EQUITY_ACTIVITY_ROW_CONCEPTS.get(_normalise(label))
            if not row_map:
                continue
            period_end = next_balance_date_by_idx.get(i)
            if not last_balance_date or not period_end:
                continue
            # Period START is the day AFTER the previous balance date — NOT
            # that date itself (using the balance date directly would
            # create a one-day overlap with the prior period). For the
            # 10-K's continuous fiscal-year chain, the previous balance
            # date is always Dec 31, so this naturally gives Jan 1 of the
            # next year (the old, correct-for-10-K-only behavior). For the
            # 10-Q's independent quarter/YTD rollforward blocks, the
            # previous balance date can be ANY month-end (e.g. March 31),
            # and hardcoding "Jan 1 of next year" there produced a
            # nonsensical context with startDate AFTER endDate (e.g.
            # ctx_dur_20260101_20250630) — confirmed by Arelle's
            # [xbrl.4.7.2:periodStartBeforeEnd] error.
            period_start = (
                date.fromisoformat(last_balance_date) + timedelta(days=1)
            ).isoformat()
            ctx_id = registry.duration_id(period_start, period_end)

        for col_idx, cell in enumerate(data_cells):
            if row_is_balance:
                if col_idx >= len(_EQUITY_BALANCE_COLUMN_CONCEPTS):
                    continue
                concept, negate = _EQUITY_BALANCE_COLUMN_CONCEPTS[col_idx]
            else:
                if col_idx not in row_map:
                    continue
                concept, negate = row_map[col_idx]

            raw_txt = cell.get_text()
            amount  = _parse_amount(raw_txt)
            if amount is None:
                continue

            dup_key = (concept, ctx_id)
            signed_value, xbrl_abs, sign_attr = _format_xbrl_value(amount, negate, raw_txt)
            if dup_key in seen:
                prior_value = seen[dup_key]
                if abs(prior_value - signed_value) > 1:  # $1 rounding tolerance
                    print(f"[xbrl_tagger] WARNING: {concept} @ {ctx_id} tagged "
                          f"with inconsistent values ({prior_value} vs "
                          f"{signed_value}) in the equity statement — this "
                          f"looks like a real data mismatch, not just a "
                          f"formatting repeat. Check the underlying source data.")
                    continue
            else:
                seen[dup_key] = signed_value

            tag_id = f"f{uuid.uuid4().hex[:8]}"

            import html as _html
            ix_tag = (
                f'<ix:nonFraction '
                f'name="{concept}" '
                f'{sign_attr}'
                f'contextRef="{ctx_id}" '
                f'unitRef="USD" '
                f'decimals="0" '
                f'id="{tag_id}" '
                f'format="ixt:num-dot-decimal">'
                f'{xbrl_abs}'
                f'</ix:nonFraction>'
            )
            placeholder = BeautifulSoup(
                f'<span data-ixbrl="{_html.escape(ix_tag)}">{xbrl_abs}</span>',
                "lxml"
            ).find("span")
            # Preserve a leading "$" and a wrapping "(" ")" pair exactly as
            # _tag_table() does above (e.g. the Treasury Stock column,
            # negate=True, is displayed parenthesized) -- see the long
            # comment in _tag_table() for why this is display-fidelity
            # only and is independent of the tag's sign="-" attribute.
            stripped   = raw_txt.strip()
            has_dollar = stripped.startswith("$")
            has_parens = stripped.startswith("(") or stripped.startswith("$(")
            cell.clear()
            if has_dollar:
                cell.append("$")
            if has_parens:
                cell.append("(")
            cell.append(placeholder)
            if has_parens:
                cell.append(")")
            tagged += 1

    return tagged


# ─────────────────────────────────────────────────────────────────────────────
# 5B.  COVER PAGE TAGGER
# ─────────────────────────────────────────────────────────────────────────────
#
# The DEI cover-page facts (entity name, EIN, address, securities table, etc.)
# already exist as HIDDEN facts in the ix:header (see ContextRegistry.
# render_xbrl_header). SEC EDGAR strongly prefers cover facts to be tagged
# where they are actually printed on the face of the document; hidden facts
# should be reserved for values that have no visible counterpart.
#
# This section walks the cover page — everything before the "Table of
# Contents" marker — and tags the printed text in place. It runs as a raw
# regex pass over the HTML *string*, before BeautifulSoup ever parses it,
# for the same reason _tag_table() uses the placeholder trick: lxml's HTML
# parser lowercases ix:nonNumeric → ix:nonnumeric and contextRef →
# contextref, which Arelle then rejects as unknown elements/attributes.
# Each match is swapped for a <span data-ixbrl="...">DISPLAY</span>
# placeholder; tag_filing()'s existing _restore_ix regex already scans every
# data-ixbrl span in the final serialised document, so no changes are needed
# there — the cover placeholders are restored by the same generic pass that
# restores the numeric table tags.


def _make_cover_placeholder(concept: str, ctx_id: str, display_text: str,
                            transform: Optional[str] = None) -> str:
    """Build a <span data-ixbrl="..."> placeholder wrapping an ix:nonNumeric fact."""
    import html as _html
    tag_id   = f"c{uuid.uuid4().hex[:8]}"
    fmt_attr = f'format="{transform}" ' if transform else ""
    ix_tag = (
        f'<ix:nonNumeric name="{concept}" contextRef="{ctx_id}" {fmt_attr}'
        f'id="{tag_id}">{display_text}</ix:nonNumeric>'
    )
    return f'<span data-ixbrl="{_html.escape(ix_tag)}">{display_text}</span>'


def _make_cover_numeric_placeholder(concept: str, ctx_id: str, display_text: str,
                                     unit: str = "shares", decimals: str = "INF",
                                     scale: str = "0",
                                     transform: Optional[str] = None) -> str:
    """
    Build a <span data-ixbrl="..."> placeholder wrapping an ix:nonFraction
    fact — for cover-page facts that are genuinely numeric (currently just
    EntityCommonStockSharesOutstanding), as opposed to the string/date/
    boolean facts _make_cover_placeholder handles via ix:nonNumeric.
    """
    import html as _html
    tag_id   = f"c{uuid.uuid4().hex[:8]}"
    fmt_attr = f'format="{transform}" ' if transform else ""
    ix_tag = (
        f'<ix:nonFraction name="{concept}" contextRef="{ctx_id}" unitRef="{unit}" '
        f'decimals="{decimals}" scale="{scale}" {fmt_attr}id="{tag_id}">{display_text}</ix:nonFraction>'
    )
    return f'<span data-ixbrl="{_html.escape(ix_tag)}">{display_text}</span>'


def _words_to_digits(num_text: str) -> str:
    """
    Convert a spelled-out magnitude like "10 million" to a comma-formatted
    digit string ("10,000,000"). ix:nonFraction's num-dot-decimal transform
    only parses digit-formatted numbers (see Amazon's actual 10-K:
    "10,734,920,870", not "10.7 billion") — so the cover text itself needs
    to print digits, not words, for this fact to be taggable at all.
    Leaves already-digit text (e.g. "10,000,000") unchanged.
    """
    m = re.match(r'([\d,]+(?:\.\d+)?)\s*(thousand|million|billion)?',
                 num_text.strip(), re.IGNORECASE)
    if not m:
        return num_text
    base = float(m.group(1).replace(",", ""))
    mult = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}.get(
        (m.group(2) or "").lower(), 1
    )
    return f"{int(base * mult):,}"


def tag_cover_page(html: str, registry: ContextRegistry, cover: dict) -> tuple[str, set]:
    """
    Inline-tag the SEC cover page with the DEI facts required for the Cover
    presentation group (EFM 6.05.41 / Reg S-T Rule 405).

    Parameters
    ----------
    html     : full document HTML string (already Unicode-sanitised, with
               <style> blocks and the XHTML DOCTYPE already stripped — i.e.
               call this AFTER those cleanup steps in tag_filing(), and
               BEFORE `soup = BeautifulSoup(html, "lxml")`).
    registry : the same ContextRegistry used for the financial tables. Cover
               facts reuse its entity-wide duration context
               (ctx_dur_{period_start}_{period_end}) instead of minting a new
               one, avoiding a duplicate-context error (EFM.6.05.07).
    cover    : dict with the following keys (all plain strings):
                 period_start, period_end   — "YYYY-MM-DD", drives the context
                 form_type                  — "10-K" or "10-Q" (informational only;
                                               the value is read off the "FORM 10-K"
                                               heading itself, not from this key)

    Returns
    -------
    (new_html, tagged_concepts)
        new_html        : the HTML string with cover placeholders inserted
        tagged_concepts : set of dei:* concept local names (no "dei:" prefix)
                          that were successfully matched and tagged. Pass this
                          to ContextRegistry.render_xbrl_header(skip_concepts=...)
                          so the hidden ix:header doesn't also emit them —
                          avoids EFM.6.05.12 duplicate-fact errors.

    Design notes / known limitations
    ---------------------------------
    - Matching is restricted to the substring of `html` BEFORE the first
      "<p>Table of Contents</p>" marker, so a coincidental text match deeper
      in the filing (e.g. the ticker string reappearing in a footnote) can
      never be touched.
    - Each field is matched with re.subn(..., count=1) — only the first
      (cover-page) occurrence is tagged.
    - Filer-status checkboxes ("Yes [ ]No [X]", "Non-accelerated filer [X]")
      are left untagged inline for the same reason boolballotbox needs the
      real ☐/☑ glyphs, not literal ASCII text — see the separate checkbox
      section below, which converts the glyphs AND tags them together.
    """
    marker   = re.search(
        r'<p(?:\s[^>]*)?>\s*(?:<strong>)?\s*Table of Contents\s*(?:</strong>)?\s*</p>',
        html, re.IGNORECASE
    )
    boundary = marker.start() if marker else len(html)
    cover_html, rest_html = html[:boundary], html[boundary:]

    tagged: set = set()
    dur_ctx  = registry.duration_id(cover["period_start"], cover["period_end"])
    inst_ctx = registry.instant_id(cover["period_end"])
    missing:   list = []
    mismatches: list = []

    def _wrap(concept: str, display_text: str, transform: Optional[str] = None,
              expected_key: Optional[str] = None) -> str:
        tagged.add(concept)
        # Cross-check the text actually printed on the cover against the
        # value the caller expected (typically sourced from
        # business_info.toml). A mismatch usually means report_10k.py's
        # cover template and business_info.toml have drifted apart —
        # surface it instead of silently tagging whichever one printed.
        if expected_key:
            expected = cover.get(expected_key)
            if expected and str(expected).strip() != display_text.strip():
                mismatches.append(f"{concept}: cover shows {display_text!r}, "
                                   f"expected {str(expected).strip()!r}")
        return _make_cover_placeholder(f"dei:{concept}", dur_ctx, display_text, transform)

    def _wrap_numeric(concept: str, display_text: str, unit: str = "shares",
                       decimals: str = "INF", scale: str = "0",
                       transform: Optional[str] = None,
                       expected_key: Optional[str] = None) -> str:
        tagged.add(concept)
        if expected_key:
            expected = cover.get(expected_key)
            if expected and str(expected).strip() != display_text.strip():
                mismatches.append(f"{concept}: cover shows {display_text!r}, "
                                   f"expected {str(expected).strip()!r}")
        return _make_cover_numeric_placeholder(
            f"dei:{concept}", inst_ctx, display_text, unit, decimals, scale, transform
        )

    def _apply(pattern: str, repl, field_name: str) -> None:
        nonlocal cover_html
        cover_html, count = re.subn(pattern, repl, cover_html, count=1)
        if count == 0:
            missing.append(field_name)

    def _ballot_glyph(mark: str) -> str:
        return "&#9745;" if mark.strip().upper() == "X" else "&#9744;"  # ☑ : ☐

    # ── Document type — "FORM 10-K" / "FORM 10-Q" heading ───────────────────
    _apply(
        r'(<h1(?:\s[^>]*)?>FORM )(10-K|10-Q)(</h1>)',
        lambda m: m.group(1) + _wrap("DocumentType", m.group(2)) + m.group(3),
        "DocumentType",
    )

    # ── Annual report / transition report checkboxes (10-K only) ────────────
    # Both are genuine booleans (xbrli:booleanItemType), same ballot-box-glyph
    # + ixt-sec:boolballotbox approach as EntitySmallBusiness/
    # EntityEmergingGrowthCompany below — the [X]/[ ] marks aren't adjacent
    # to a literal "true"/"false" word, so the bracket mark itself has to
    # become the tagged fact. Each checkbox's full sentence wraps onto a
    # second bold paragraph ("...SECURITIES EXCHANGE" / "ACT OF 1934"), but
    # only the FIRST paragraph (the one with the actual [X]/[ ] mark) needs
    # matching here.
    #
    # Gated on form_type != "10-Q": a 10-Q's cover has NO "[X] ANNUAL
    # REPORT..." checkbox at all — only its own "[X] QUARTERLY REPORT..."
    # sibling below — so attempting this match unconditionally always
    # failed on a 10-Q and printed a spurious "no match found" warning for
    # a fact that was never supposed to be there in the first place. Same
    # reasoning applies to TRANSITION REPORT just below: that checkbox
    # genuinely exists on BOTH forms (as the alternative to whichever of
    # ANNUAL/QUARTERLY doesn't apply), so it stays unconditional.
    if cover.get("form_type") != "10-Q":
        _apply(
            r'(<p(?:\s[^>]*)?>)(<strong>)?\[([X ]?)\]'
            r'( ANNUAL REPORT PURSUANT TO SECTION 13 OR 15\(d\) OF THE SECURITIES EXCHANGE)'
            r'(</strong>)?(</p>)',
            lambda m: (
                m.group(1) + (m.group(2) or "") + "["
                + _wrap("DocumentAnnualReport", _ballot_glyph(m.group(3)), transform="ixt-sec:boolballotbox")
                + "]" + m.group(4) + (m.group(5) or "") + m.group(6)
            ),
            "DocumentAnnualReport",
        )
    # ── Quarterly report checkbox (10-Q only) ────────────────────────────────
    # Same shape as ANNUAL/TRANSITION above, but this one was missing
    # entirely - a 10-Q's own "[X] QUARTERLY REPORT PURSUANT TO..." checkbox
    # had no handler at all, leaving it as untagged plain text while its
    # sibling TRANSITION REPORT checkbox right below it was correctly tagged.
    #
    # "(?: EXCHANGE)?" - optional, not required. Word's line-wrap for this
    # sentence depends on which paragraph/run boundary the text sits in,
    # and editing item001-10-q-cover.docx by hand (e.g. changing the
    # original "ANNUAL REPORT..." text to "QUARTERLY REPORT...") doesn't
    # reflow that boundary - "EXCHANGE" can end up on THIS paragraph
    # ("...OF THE SECURITIES EXCHANGE" / "ACT OF 1934", matching how
    # ANNUAL REPORT wraps above) or the NEXT one ("...OF THE SECURITIES" /
    # "EXCHANGE ACT OF 1934", matching how TRANSITION REPORT wraps below),
    # and the checkbox itself is on THIS paragraph either way - trailing
    # "EXCHANGE" or not doesn't change which paragraph needs tagging.
    _apply(
        r'(<p(?:\s[^>]*)?>)(<strong>)?\[([X ]?)\]'
        r'( QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15\(d\) OF THE SECURITIES(?: EXCHANGE)?)'
        r'(</strong>)?(</p>)',
        lambda m: (
            m.group(1) + (m.group(2) or "") + "["
            + _wrap("DocumentQuarterlyReport", _ballot_glyph(m.group(3)), transform="ixt-sec:boolballotbox")
            + "]" + m.group(4) + (m.group(5) or "") + m.group(6)
        ),
        "DocumentQuarterlyReport",
    )
    _apply(
        r'(<p(?:\s[^>]*)?>)(<strong>)?\[([X ]?)\]'
        r'( TRANSITION REPORT PURSUANT TO SECTION 13 OR 15\(d\) OF THE SECURITIES)'
        r'(</strong>)?(</p>)',
        lambda m: (
            m.group(1) + (m.group(2) or "") + "["
            + _wrap("DocumentTransitionReport", _ballot_glyph(m.group(3)), transform="ixt-sec:boolballotbox")
            + "]" + m.group(4) + (m.group(5) or "") + m.group(6)
        ),
        "DocumentTransitionReport",
    )

    # ── Period end date — "For the fiscal year ended December 31, 2025" ─────
    # (10-Q filings print "For the quarterly period ended ..." instead.)
    #
    # Transform name: "December 31, 2025" is a spelled-out MONTH NAME date
    # (order: monthname, day, year). The correct TR5 (2022-02-16) transform
    # is ixt:date-monthname-day-year-en — NOT ixt:date-month-day-year-en
    # (that variant is for purely numeric dates like "12/31/2025"), and NOT
    # the old TR2/3-style concatenated name "datemonthdayyearen" (invalid in
    # the 2022-02-16 namespace declared on this document — this is what
    # caused Arelle's [ix11.11.1.2:invalidTransformation] and the resulting
    # [arelle.invalidFactsSkipped] on DocumentPeriodEndDate).
    #
    # "ended\s+" (not a single literal space) — item001-10-q-cover.docx's
    # "For the quarterly period ended  {{ quarter_end_date }}" has TWO
    # spaces before the placeholder (browsers collapse it visually, so the
    # cover page looks fine, but the raw HTML this regex matches against
    # doesn't collapse it) — a single-space literal here silently failed
    # to match on every 10-Q cover, leaving DocumentPeriodEndDate untagged
    # while the visible date itself was correct.
    _apply(
        r'(<p(?:\s[^>]*)?>For the (?:fiscal year|quarterly period) ended\s+)'
        r'(<strong>)?([A-Za-z]+ \d{1,2}, \d{4})(</strong>)?(</p>)',
        lambda m: m.group(1) + (m.group(2) or "") + _wrap(
            "DocumentPeriodEndDate", m.group(3), transform="ixt:date-monthname-day-year-en"
        ) + (m.group(4) or "") + m.group(5),
        "DocumentPeriodEndDate",
    )

    # ── Commission File Number ───────────────────────────────────────────────
    # NOT anchored to the start of the <p> — the 10-Q's cover puts this line
    # after a transition-period placeholder ("For the transition period
    # from ___ to ___ Commission File Number: 001-14321"), so the literal
    # "Commission File Number: " text can appear mid-paragraph, not just
    # right after <p...>. Tolerant of an optional <strong> wrapper around
    # the value (it's bold in the source docx) — re-wrapped around the
    # tagged placeholder so the visible bold formatting survives tagging.
    _apply(
        r'(Commission File Number: )(<strong>)?([\w-]+)(</strong>)?',
        lambda m: m.group(1) + (m.group(2) or "") + _wrap(
            "EntityFileNumber", m.group(3), expected_key="commission_file_number"
        ) + (m.group(4) or ""),
        "EntityFileNumber",
    )

    # ── Registrant name — bare <h2> heading on the cover ─────────────────────
    _apply(
        r'(<h2(?:\s[^>]*)?>)([^<]+)(</h2>)',
        lambda m: m.group(1) + _wrap(
            "EntityRegistrantName", m.group(2), expected_key="entity_name"
        ) + m.group(3),
        "EntityRegistrantName",
    )

    # ── State of incorporation — printed in the jurisdiction/EIN table ───────
    # Layout changed: this is now a 2-row table (value row, then a SEPARATE
    # label row below it) rather than one cell with the label on a <br/>
    # line under the value. So this matches the value cell directly rather
    # than requiring the label text to be adjacent — safe because it's the
    # first alpha-only header cell in the document (the securities table's
    # header cells come later) and because count=1 always takes the
    # left-most / first match.
    #
    # Confirmed against a real filing (Amazon's 2025 10-K uses this exact
    # transform for "Delaware" -> dei:EntityIncorporationStateCountryCode):
    # ixt-sec:stateprovnameen converts a spelled-out state/province name to
    # its 2-letter code. An earlier version of this file wrongly assumed no
    # such transform existed and left this fact hidden-only — it does exist,
    # this corrects that.
    _apply(
        r'(<th(?:\s[^>]*)?>)(<strong>)?([A-Za-z ]+)(</strong>)?(</th>)',
        lambda m: m.group(1) + (m.group(2) or "") + _wrap(
            "EntityIncorporationStateCountryCode", m.group(3),
            transform="ixt-sec:stateprovnameen", expected_key="state_name"
        ) + (m.group(4) or "") + m.group(5),
        "EntityIncorporationStateCountryCode",
    )

    # ── EIN — printed in the jurisdiction/EIN table ──────────────────────────
    # Same layout note as above — matches the EIN-formatted value directly
    # (\d\d-\d\d\d\d\d\d\d is specific enough not to need the adjacent label).
    _apply(
        r'(<th(?:\s[^>]*)?>)(<strong>)?(\d{2}-\d{7})(</strong>)?(</th>)',
        lambda m: m.group(1) + (m.group(2) or "") + _wrap(
            "EntityTaxIdentificationNumber", m.group(3), expected_key="ein"
        ) + (m.group(4) or "") + m.group(5),
        "EntityTaxIdentificationNumber",
    )

    # ── Principal executive office address — "Street, City, ST ZIP" ─────────
    _apply(
        r'(<p(?:\s[^>]*)?>)(<strong>)?([^<,]+),\s*([^<,]+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)(</strong>)?(</p>)',
        lambda m: (
            m.group(1) + (m.group(2) or "")
            + _wrap("EntityAddressAddressLine1", m.group(3), expected_key="address_line1") + ", "
            + _wrap("EntityAddressCityOrTown", m.group(4), expected_key="city") + ", "
            + _wrap("EntityAddressStateOrProvince", m.group(5), expected_key="state_abbr") + " "
            + _wrap("EntityAddressPostalZipCode", m.group(6), expected_key="zip_code")
            + (m.group(7) or "") + m.group(8)
        ),
        "EntityAddress",
    )

    # ── Registrant's telephone number — "410-555-0100" ───────────────────────
    _apply(
        r'(<p(?:\s[^>]*)?>)(<strong>)?(\d{3})-(\d{3}-\d{4})(</strong>)?(</p>)',
        lambda m: (
            m.group(1) + (m.group(2) or "")
            + _wrap("CityAreaCode", m.group(3), expected_key="area_code") + "-"
            + _wrap("LocalPhoneNumber", m.group(4), expected_key="local_phone")
            + (m.group(5) or "") + m.group(6)
        ),
        "EntityPhoneNumber",
    )

    # ── Securities registered pursuant to Section 12(b) — table row ─────────
    _apply(
        r'(<tr>\s*<td(?:\s[^>]*)?>)([^<]+)(</td>\s*<td(?:\s[^>]*)?>)([^<]+)(</td>\s*<td(?:\s[^>]*)?>)([^<]+)(</td>\s*</tr>)',
        lambda m: (
            m.group(1) + _wrap("Security12bTitle", m.group(2), expected_key="security_title")
            + m.group(3) + _wrap("TradingSymbol", m.group(4), expected_key="ticker")
            + m.group(5) + _wrap("SecurityExchangeName", m.group(6), expected_key="exchange")
            + m.group(7)
        ),
        "Securities12b",
    )

    # ── Shares outstanding — "As of [date], N million shares ... outstanding" ──
    # dei:EntityCommonStockSharesOutstanding is genuinely numeric (unlike
    # everything else on the cover so far), so it needs ix:nonFraction, not
    # ix:nonNumeric — confirmed against Amazon's actual 10-K, which tags it
    # with unitRef="shares", format="ixt:num-dot-decimal", decimals="INF".
    # That transform only parses digit-formatted numbers, so "10 million"
    # gets rewritten to "10,000,000" in the visible text as part of tagging
    # it — same tradeoff as the checkbox glyphs earlier (can't tag text in
    # a form no registered transform understands).
    _apply(
        r'(<p(?:\s[^>]*)?>As of [A-Za-z]+ \d{1,2}, \d{4}, )'
        r'([\d,]+(?:\.\d+)?\s*(?:thousand|million|billion)?)'
        r'( shares of the registrant.s common stock)',
        lambda m: m.group(1) + _wrap_numeric(
            "EntityCommonStockSharesOutstanding", _words_to_digits(m.group(2)),
            unit="shares", decimals="INF", scale="0", transform="ixt:num-dot-decimal"
        ) + m.group(3),
        "EntityCommonStockSharesOutstanding",
    )

    # ── Filer-status Yes/No checkboxes ───────────────────────────────────────
    # CORRECTED from an earlier version of this file, which used ballot-box
    # glyphs (☐/☑) + the ixt-sec:yesnoballotbox transform. Ron inspected
    # Amazon's actual live 10-K cover page and found neither: the real tags
    # are plain <ix:nonNumeric name="dei:EntityWellKnownSeasonedIssuer">Yes</ix:nonNumeric>
    # and <ix:nonNumeric name="dei:EntityVoluntaryFilers">No</ix:nonNumeric> —
    # no format/transform attribute at all. Since dei:yesNoItemType's valid
    # values are literally the strings "Yes"/"No", and the label words
    # "Yes"/"No" already appear verbatim in the printed text, no transform
    # is needed — the content IS the value. So: tag whichever LABEL WORD
    # ("Yes" or "No") matches the box that's actually checked, leaving both
    # bracket marks exactly as printed (no glyph conversion needed either).
    def _wrap_yes_no(m) -> str:
        concept = _YES_NO_QUEUE.pop(0)
        yes_mark, sep, no_mark = m.group(1), m.group(2), m.group(3)
        if concept is None:
            # This occurrence has no corresponding dei concept. Leave both
            # bracket marks exactly as printed, untagged.
            return f"Yes [{yes_mark}]{sep}No [{no_mark}]"
        if yes_mark.strip().upper() == "X":
            yes_label, no_label = _wrap(concept, "Yes"), "No"
        else:
            yes_label, no_label = "Yes", _wrap(concept, "No")
        return f"{yes_label} [{yes_mark}]{sep}{no_label} [{no_mark}]"

    # Order matches each form's own standard cover-page sequence — a 10-Q's
    # cover has a DIFFERENT set of Yes/No lines than a 10-K's, not just a
    # subset, so this can't reuse one hardcoded queue for both. re.subn
    # (count=1) called once per concept naturally consumes the FIRST
    # remaining (still-unwrapped) occurrence each time, so order here must
    # match each form's actual document order.
    #
    # Confirmed against the actual generated sec-10-q.htm: (1) "has filed
    # all reports required...for the past 90 days" DOES map to a real
    # concept (dei:EntityCurrentReportingStatus — the same one the 10-K's
    # equivalent line uses) — an earlier version of this left it untagged
    # on the mistaken assumption it was pure boilerplate with no DEI
    # element. (2) Interactive Data. Shell Company is a THIRD Yes/No line
    # on the 10-Q cover, but is rendered with ballot glyphs (☐/☒) rather
    # than "[X]"/"[ ]" brackets, so it can't go through this same
    # bracket-pattern queue — see the dedicated pattern just below instead.
    if cover.get("form_type") == "10-Q":
        _YES_NO_QUEUE = [
            "EntityCurrentReportingStatus",
            "EntityInteractiveDataCurrent",
        ]
    else:
        _YES_NO_QUEUE = [
            "EntityWellKnownSeasonedIssuer",
            "EntityVoluntaryFilers",
            "EntityCurrentReportingStatus",
            "EntityInteractiveDataCurrent",
        ]
    _n_yes_no = len(_YES_NO_QUEUE)
    for _ in range(_n_yes_no):
        _apply(
            r'Yes \[([X ]?)\](\s*)No \[([X ]?)\]',
            _wrap_yes_no,
            f"YesNoCheckbox({_YES_NO_QUEUE[0]})",
        )

    # ── Shell Company — ballot-glyph checkbox, not bracket notation ─────────
    # This line renders as "Yes  ☐    No  ☒" (or the HTML-entity form
    # "&#9744;" for the empty box) instead of "Yes [ ] No [X]" — a
    # completely different visual representation from every other Yes/No
    # line on the cover, so it needs its own pattern rather than joining
    # the bracket-based queue above.
    #
    # dei:EntityShellCompany is xbrli:booleanItemType, NOT yesNoItemType —
    # confirmed from a real filing's actual markup:
    #   <ix:nonNumeric name="dei:EntityShellCompany" format="ixt:fixed-false"
    #                  ...>☒</ix:nonNumeric>
    # ixt:fixed-false/ixt:fixed-true are CONSTANT transforms — they always
    # output that literal value ("false"/"true") no matter what's inside
    # the tag. So the glyph itself is purely cosmetic display content; what
    # actually encodes the answer is WHICH transform gets applied to
    # WHICHEVER glyph is checked. Real filings tag only the checked glyph
    # (leaving the unchecked one, and both "Yes"/"No" words, untouched) —
    # so: Yes-checked -> tag that glyph with ixt:fixed-true; No-checked ->
    # tag that glyph with ixt:fixed-false.
    _CHECKED_GLYPHS   = ("☒", "☑", "&#9746;", "&#9745;")
    _UNCHECKED_GLYPHS = ("☐", "&#9744;")
    _glyph_alt = "|".join(re.escape(g) for g in _CHECKED_GLYPHS + _UNCHECKED_GLYPHS)

    def _wrap_shell_company(m) -> str:
        ws1, yes_glyph, ws2, ws3, no_glyph = (
            m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        )
        yes_checked = yes_glyph in _CHECKED_GLYPHS
        if yes_checked:
            tagged_yes_glyph = _wrap("EntityShellCompany", yes_glyph, transform="ixt:fixed-true")
            return f"Yes{ws1}{tagged_yes_glyph}{ws2}No{ws3}{no_glyph}"
        tagged_no_glyph = _wrap("EntityShellCompany", no_glyph, transform="ixt:fixed-false")
        return f"Yes{ws1}{yes_glyph}{ws2}No{ws3}{tagged_no_glyph}"

    _apply(
        rf'Yes(\s*)({_glyph_alt})(\s*)No(\s*)({_glyph_alt})',
        _wrap_shell_company,
        "EntityShellCompany",
    )

    # ── Filer category / smaller reporting company / EGC checkboxes ─────────
    # Layout changed: this is now a real 2-column x 3-row table (one
    # checkbox per cell) rather than five checkboxes packed into a single
    # paragraph — so each cell is matched and tagged independently instead
    # of one combined regex. th vs td doesn't matter for tagging (row 1
    # happens to render as <th> per the header-row convention _is_header_row
    # uses) — <t[hd]> matches either.
    #
    # EntityFilerCategory IS tagged — an earlier version of this file
    # skipped it, worried about getting the required enum casing wrong.
    # Confirmed against the DEI taxonomy's own documentation string
    # ("Indicate whether the registrant is one of the following: Large
    # Accelerated Filer, Accelerated Filer, Non-accelerated Filer"): the
    # exact required values are "Large Accelerated Filer",
    # "Accelerated Filer", "Non-accelerated Filer" (note the inconsistent
    # capitalization is correct — "Non-accelerated" keeps a lowercase
    # "a", unlike the other two). Since the printed label is lowercase
    # ("Non-accelerated filer") and there's no transform to re-case text,
    # whichever ONE of the three is actually checked gets its label text
    # rewritten to the correctly-cased value as part of tagging it — same
    # tradeoff as the checkbox glyphs and shares-outstanding digits
    # elsewhere. The other two (unchecked) labels are left exactly as
    # printed, untagged.
    #
    # EntitySmallBusiness and EntityEmergingGrowthCompany ARE genuine
    # booleans (xbrli:booleanItemType, not yesNoItemType), and — unlike
    # EntityFilerCategory — they're REQUIRED cover facts regardless of
    # their value: they must be tagged whether true OR false, never
    # omitted just because the answer happens to be false. There's no
    # adjacent "true"/"false" word to tag directly (unlike the Yes/No
    # questions above), so these two still need the ballot-box-glyph +
    # ixt-sec:boolballotbox approach: convert the bracket mark to the real
    # ☐/☑ glyph and tag that, regardless of which state it's in.
    def _tag_filer_category_label(label_text: str, mark: str, correct_value: str) -> str:
        if mark.strip().upper() == "X":
            return _wrap("EntityFilerCategory", correct_value)
        return label_text

    def _cell_checkbox_pattern(label: str) -> str:
        return rf'(<t[hd](?:\s[^>]*)?>)({re.escape(label)}) \[([X ]?)\](</t[hd]>)'

    _apply(
        _cell_checkbox_pattern("Large accelerated filer"),
        lambda m: m.group(1) + _tag_filer_category_label(m.group(2), m.group(3), "Large Accelerated Filer")
                  + " [" + _ballot_glyph(m.group(3)) + "]" + m.group(4),
        "FilerCategory(LargeAccelerated)",
    )
    _apply(
        _cell_checkbox_pattern("Non-accelerated filer"),
        lambda m: m.group(1) + _tag_filer_category_label(m.group(2), m.group(3), "Non-accelerated Filer")
                  + " [" + _ballot_glyph(m.group(3)) + "]" + m.group(4),
        "FilerCategory(NonAccelerated)",
    )
    _apply(
        _cell_checkbox_pattern("Accelerated filer"),
        lambda m: m.group(1) + _tag_filer_category_label(m.group(2), m.group(3), "Accelerated Filer")
                  + " [" + _ballot_glyph(m.group(3)) + "]" + m.group(4),
        "FilerCategory(Accelerated)",
    )
    _apply(
        _cell_checkbox_pattern("Smaller reporting company"),
        lambda m: m.group(1) + m.group(2) + " ["
                  + _wrap("EntitySmallBusiness", _ballot_glyph(m.group(3)), transform="ixt-sec:boolballotbox")
                  + "]" + m.group(4),
        "EntitySmallBusiness",
    )
    _apply(
        _cell_checkbox_pattern("Emerging growth company"),
        lambda m: m.group(1) + m.group(2) + " ["
                  + _wrap("EntityEmergingGrowthCompany", _ballot_glyph(m.group(3)), transform="ixt-sec:boolballotbox")
                  + "]" + m.group(4),
        "EntityEmergingGrowthCompany",
    )

    # ── Aggregate market value — "...was $152,000,000." (right after the ─────
    # filer-category table). Genuinely numeric (ix:nonFraction, not
    # ix:nonNumeric) — same reasoning and transform as
    # EntityCommonStockSharesOutstanding above: ixt:num-dot-decimal only
    # parses digit-formatted numbers, so a "$152 million"-style figure gets
    # rewritten to "$152,000,000" in the visible text as part of tagging it.
    #
    # 10-K only: a 10-Q cover has no aggregate-market-value disclosure at
    # all (EntityPublicFloat is specifically "as of the last business day
    # of the registrant's most recently completed second fiscal quarter",
    # an annual-report-only concept), so this always failed to match on a
    # 10-Q and printed a spurious "no match found" warning for a fact that
    # was never supposed to be on that cover in the first place.
    if cover.get("form_type") != "10-Q":
        _apply(
            r'(<p(?:\s[^>]*)?>The aggregate market value of the registrant.s common stock '
            r'held by non-affiliates.*?was \$)'
            r'([\d,]+(?:\.\d+)?\s*(?:thousand|million|billion)?)'
            r'(\.</p>)',
            lambda m: m.group(1) + _wrap_numeric(
                "EntityPublicFloat", _words_to_digits(m.group(2)),
                unit="USD", decimals="0", scale="0", transform="ixt:num-dot-decimal",
                expected_key="aggregate_market_value"
            ) + m.group(3),
            "EntityPublicFloat",
        )

    if missing:
        print(f"[xbrl_tagger] cover page: no match found for: {', '.join(missing)} "
              f"— check that the cover wording matches the regexes in tag_cover_page().")
    if mismatches:
        print("[xbrl_tagger] cover page: value mismatches vs business_info.toml:")
        for msg in mismatches:
            print(f"    {msg}")

    return cover_html + rest_html, tagged


# ─────────────────────────────────────────────────────────────────────────────
# 5C.  NOTES / TEXT BLOCK TAGGER
# ─────────────────────────────────────────────────────────────────────────────
#
# Tags "Notes to Consolidated Financial Statements" using the same two-part
# convention real 10-Ks use (confirmed against Amazon's actual 2025 10-K):
#   1. TEXT BLOCK tagging — the entire note, heading through its last
#      paragraph, wrapped in ONE fact using a *TextBlock concept (e.g.
#      us-gaap:OrganizationConsolidationBasisOfPresentationBusiness
#      DescriptionAndAccountingPoliciesTextBlock for Note 1).
#   2. GRANULAR tagging — specific numbers/policy statements *inside* that
#      same note (e.g. "three segments" -> us-gaap:NumberOfOperatingSegments),
#      nested inside the text-block fact, same idea as every other tag in
#      this file.
#
# Why this needs a DIFFERENT placeholder mechanism than everything else in
# this file: every other tag wraps simple text with no nested markup, which
# fits the existing <span data-ixbrl="...">DISPLAY</span> placeholder (its
# restore regex assumes [^<]* — no nested tags — inside the span). A note's
# real content is dozens of ordinary <p>/<div>/<span> elements, which a
# single placeholder like that can't hold. Amazon's real filing solves this
# with ix:continuation chaining across separately-styled top-level blocks;
# this version takes the simpler path of wrapping ALL of a note's content
# in ONE ix:nonNumeric container (which — like <div> — is allowed to
# contain block-level HTML directly, no continuation needed, AS LONG AS the
# note's content is one contiguous run of siblings, not already split across
# unrelated containers for layout reasons). That covers the common case;
# continuation chaining is a real fallback for the future if a specific
# note turns out to need it.
#
# Mechanism: instead of one placeholder holding the whole (concept, content)
# pair as an escaped attribute, this inserts two lightweight, INDEPENDENT
# markers — one where the opening <ix:nonNumeric ...> tag belongs, one
# where the closing </ix:nonNumeric> belongs — each simple enough (no
# content between open and close) to survive BeautifulSoup + a dedicated
# restore pass, while every real element in between is left completely
# untouched and renders normally.


def _make_open_tag_placeholder(concept: str, ctx_id: str,
                                extra_attrs: str = "") -> str:
    """Marker restored to a bare opening <ix:nonNumeric ...> tag."""
    import html as _html
    tag_id = f"n{uuid.uuid4().hex[:8]}"
    open_tag = f'<ix:nonNumeric name="{concept}" contextRef="{ctx_id}" {extra_attrs}id="{tag_id}">'
    return f'<span data-ixbrl-open="{_html.escape(open_tag)}"></span>'


def _make_close_tag_placeholder() -> str:
    """Marker restored to a bare closing </ix:nonNumeric> tag."""
    return '<span data-ixbrl-close="&lt;/ix:nonNumeric&gt;"></span>'


def tag_auditor_report_block(html: str, registry: ContextRegistry) -> tuple:
    """
    Inline-tag the auditor's signature block at the end of the "Report of
    Independent Registered Public Accounting Firm" section. EFM 6.05.45
    requires dei:AuditorName / AuditorLocation / AuditorFirmId to be
    VISIBLE for a 10-K — hidden-only (all render_xbrl_header() used to do)
    isn't sufficient.

    Expects the standard signature-block layout:
        <p>/s/  {Auditor Name}</p>
        <p>PCAOB ID: {Firm ID}</p>
        <p>{City, State}</p>
    matched as ONE combined pattern spanning all three paragraphs, rather
    than three separate patterns — so a coincidental "City, ST"-shaped
    string elsewhere in the filing can never be mistaken for the
    auditor's location; only text immediately following this exact
    signature block gets tagged as AuditorLocation.

    Tolerant of the styled body output produced by create_10k_edgar_html()
    (preserve_style=True): each <p> may carry an inline style="..."
    attribute (e.g. the body-font declaration every paragraph gets), and
    each captured value (name / firm id / location) may be wrapped in
    <strong>...</strong> if that run happened to be bold in the source
    docx — same tolerance tag_cover_page()'s regexes already use.

    Returns (new_html, tagged_concepts) — tagged_concepts feeds into
    tag_filing()'s skip_concepts so render_xbrl_header() doesn't also
    emit these three hidden (harmless but pointless once they're visible).
    """
    tagged: set = set()
    ctx_id = registry.duration_id(registry.period_start, registry.period_end)

    pattern = (
        r'(<p(?:\s[^>]*)?>/s/\s*(?:<strong>)?\s*)([^<]+?)(\s*(?:</strong>)?\s*</p>\s*'
        r'<p(?:\s[^>]*)?>PCAOB ID:\s*(?:<strong>)?\s*)(\d+)(\s*(?:</strong>)?\s*</p>\s*'
        r'<p(?:\s[^>]*)?>(?:<strong>)?\s*)'
        r'([^<]+?)(\s*(?:</strong>)?\s*</p>)'
    )

    def _repl(m):
        name_text     = m.group(2).strip()
        firm_id_text  = m.group(4).strip()
        location_text = m.group(6).strip()

        tagged.update({"AuditorName", "AuditorFirmId", "AuditorLocation"})
        name_tag     = _make_cover_placeholder("dei:AuditorName", ctx_id, name_text)
        firm_id_tag  = _make_cover_placeholder("dei:AuditorFirmId", ctx_id, firm_id_text)
        location_tag = _make_cover_placeholder("dei:AuditorLocation", ctx_id, location_text)

        return (
            m.group(1) + name_tag + m.group(3) + firm_id_tag +
            m.group(5) + location_tag + m.group(7)
        )

    new_html, count = re.subn(pattern, _repl, html, count=1)
    if count == 0:
        print("[xbrl_tagger] auditor report: no match found for signature block "
              "(/s/ Name, PCAOB ID: NN, City, ST) — AuditorName/Location/FirmId "
              "will remain hidden-only, which fails EFM 6.05.45 for a 10-K.")
    return new_html, tagged


_TABLE_STYLE_RE = re.compile(r'(<table style=")([^"]*)(")')
_CELL_STYLE_RE  = re.compile(r'(<(?:td|th)(?: class="[^"]*")? style=")([^"]*)(")')


def _style_notes_tables(body: str) -> str:
    """
    Give notes-section tables real borders and fit-to-content sizing.

    Notes tables don't set tcBorders in the source .docx (unlike the
    financial-statement tables, which always call _add_cell_border), so
    _docx_body_to_html_parts()'s _cell_has_border() check finds nothing to
    carry over and they render borderless. They also inherit the blanket
    `width: 100%` every table gets, which stretches a narrow 2-3 column
    note table's columns edge-to-edge across the full page instead of
    sizing to its own content.

    Scoped to just this note's `body` slice (called from
    tag_notes_section(), before the open/close ix:nonNumeric markers are
    added) so it never touches the financial-statement tables or the
    cover/TOC tables in the intro, which are handled by a separate
    convert_intro_to_html() call.
    """
    def _table_repl(m):
        style = m.group(2)
        if "width: 100%" in style:
            style = style.replace("width: 100%", "width: auto; max-width: 100%")
        return m.group(1) + style + m.group(3)

    def _cell_repl(m):
        style = m.group(2)
        if "border:" not in style:
            style = style.rstrip()
            if style and not style.endswith(";"):
                style += ";"
            style += " border: 1px solid #000;"
        return m.group(1) + style + m.group(3)

    body = _TABLE_STYLE_RE.sub(_table_repl, body)
    body = _CELL_STYLE_RE.sub(_cell_repl, body)
    return body


def tag_notes_section(html: str, registry: ContextRegistry, notes: list) -> tuple:
    """
    Tag the Notes to Financial Statements section.

    Parameters
    ----------
    html     : full document HTML string, at the SAME pipeline stage as
               tag_cover_page() — already Unicode-sanitised, BEFORE
               BeautifulSoup parses it.
    registry : the shared ContextRegistry (reuses its duration/instant
               contexts, same as every other tagger in this file).
    notes    : list of dicts, one per note, each:
                 {
                   "heading_pattern": regex (as a string) matching the
                       note's heading through wherever text-block content
                       should START, e.g. r'<h[1-6]>Note 1 [^<]*</h[1-6]>'.
                       Tagging starts immediately AFTER this match.
                   "end_pattern": regex matching where this note's content
                       ENDS — typically the NEXT note's heading, or a
                       section-closing marker if this is the last note.
                       Tagging ends immediately BEFORE this match.
                   "textblock_concept": "us-gaap:...TextBlock" (no "us-gaap:"
                       prefix needed only if you want a different namespace —
                       pass the full prefixed name).
                   "period_start"/"period_end": ISO date strings for this
                       note's context (usually the filing's own full-year
                       period, but kept per-note in case a note needs a
                       different one).
                   "granular": OPTIONAL list of (pattern, concept, transform,
                       unit) tuples for facts INSIDE this note, 5-tuples
                       (pattern, concept, transform, unit, period_type) where
                       period_type is "instant" to use an as-of-period_end
                       context instead of the note's own duration context
                       (needed for concepts the taxonomy declares as instant,
                       e.g. lease-maturity-schedule facts), or 7-tuples
                       (pattern, concept, transform, unit, period_type,
                       decimals, scale) to control precision/scaling — e.g.
                       scale="6" to turn a displayed "7.0" (as in "$7.0
                       million") into 7,000,000, or scale="-2" to turn a
                       displayed "15.6" (as in "15.6%") into the 0.156
                       decimal fraction percentItemType concepts expect.
                       `unit` is None for non-numeric (ix:nonNumeric) tags,
                       or a unit id string (e.g. "shares", "pure") for
                       numeric (ix:nonFraction) tags. `pattern` must have
                       exactly one capture group (the text to tag).
                 }

    Returns
    -------
    (new_html, tagged_concepts) — same contract as tag_cover_page().
    """
    tagged: set = set()
    missing: list = []

    for note in notes:
        start_m = re.search(note["heading_pattern"], html)
        if not start_m:
            missing.append(note.get("textblock_concept", note["heading_pattern"]))
            continue
        end_m = re.search(note["end_pattern"], html[start_m.end():])
        if not end_m:
            missing.append(f"{note['textblock_concept']} (end boundary not found)")
            continue

        # Include the heading itself in the tagged fact — matches real-world
        # practice (e.g. Amazon's filings tag the note title as part of the
        # same disclosure fact, via continuedat/ix:continuation because
        # their title sits in a separately-styled, non-contiguous span).
        # Our heading and body are plain contiguous siblings in the DOM, so
        # no continuation chaining is needed — just start the wrap at the
        # heading instead of after it.
        body_start = start_m.start()
        body_end   = start_m.end() + end_m.start()
        body        = html[body_start:body_end]
        body        = _style_notes_tables(body)

        ctx_id = registry.duration_id(note["period_start"], note["period_end"])
        concept = note["textblock_concept"]

        # ── Granular facts nested inside this note ───────────────────────────
        # These use the SAME simple placeholder as every other tag in this
        # file (safe here because an individual granular fact, e.g. "three"
        # or "six years", has no nested tags of its own).
        #
        # Each tuple is either:
        #   (pattern, concept, transform, unit)                — duration context
        #   (pattern, concept, transform, unit, "instant")      — instant context
        # Most narrative facts share the note's own duration period. But some
        # us-gaap concepts are themselves declared as INSTANT in the taxonomy
        # — notably the lease-maturity-schedule concepts (LesseeOperatingLease
        # LiabilityPaymentsDue*), which represent a scheduled future payment
        # as of the balance sheet date, not activity during a period. Tagging
        # an instant-type concept with a duration context is a genuine
        # xbrl.4.7.2:contextPeriodType error, not just a style nit — the 5th
        # tuple element lets a granular fact opt into an instant context (as
        # of this note's period_end) instead of reusing the textblock's
        # duration context.
        # Each granular tuple is one of:
        #   (pattern, concept, transform, unit)
        #   (pattern, concept, transform, unit, period_type)
        #   (pattern, concept, transform, unit, period_type, decimals, scale)
        # The 7-element form is for figures that aren't exact digit strings —
        # a rounded "$X.Y million" narrative figure (scale="6" turns the
        # displayed "7.0" into 7,000,000; decimals set coarse enough that it
        # reconciles as a consistent duplicate against the exact GL-sourced
        # value tagged elsewhere, e.g. decimals="-5" for a figure precise to
        # the nearest $100k), or a percentage (scale="-2" turns the displayed
        # "15.6" into the 0.156 decimal fraction the percentItemType
        # concepts actually expect — see notes_config.py for the reasoning
        # behind each one's chosen decimals/scale).
        for granular_tuple in note.get("granular", []):
            if len(granular_tuple) == 7:
                pattern, sub_concept, transform, unit, period_type, decimals, scale = granular_tuple
            elif len(granular_tuple) == 5:
                pattern, sub_concept, transform, unit, period_type = granular_tuple
                decimals, scale = "INF", "0"
            else:
                pattern, sub_concept, transform, unit = granular_tuple
                period_type = "duration"
                decimals, scale = "INF", "0"

            def _make_granular(m, _concept=sub_concept, _transform=transform,
                                _unit=unit, _period_type=period_type,
                                _decimals=decimals, _scale=scale):
                tagged.add(_concept)
                _ctx_id = (
                    registry.instant_id(note["period_end"])
                    if _period_type == "instant" else ctx_id
                )
                # Preserve any text the pattern matched OUTSIDE the numeric
                # capture group (group 1) — non-empty only for patterns that
                # can't use a lookbehind assertion (Python's re requires
                # FIXED-width lookbehind, which can't express a
                # date-dependent phrase like "for the nine months ended
                # September 30, 2025"). Every existing lookbehind-based
                # pattern has match start/end == group(1) start/end
                # already, so prefix/suffix are "" and this changes nothing
                # for them.
                _prefix = m.string[m.start():m.start(1)]
                _suffix = m.string[m.end(1):m.end()]
                if _unit:
                    _tag = _make_cover_numeric_placeholder(
                        f"us-gaap:{_concept}" if ":" not in _concept else _concept,
                        _ctx_id, m.group(1), unit=_unit, decimals=_decimals,
                        scale=_scale, transform=_transform
                    )
                else:
                    _tag = _make_cover_placeholder(
                        f"us-gaap:{_concept}" if ":" not in _concept else _concept,
                        _ctx_id, m.group(1), transform=_transform
                    )
                return _prefix + _tag + _suffix
            body = re.sub(pattern, _make_granular, body, count=1)

        # ── The text block itself — wraps the (possibly now partially
        # granular-tagged) body between an open/close marker pair ──────────
        tagged.add(concept.split(":")[-1])
        open_marker  = _make_open_tag_placeholder(concept, ctx_id)
        close_marker = _make_close_tag_placeholder()
        html = html[:body_start] + open_marker + body + close_marker + html[body_end:]

    if missing:
        print(f"[xbrl_tagger] notes section: no match found for: {', '.join(missing)} "
              f"— check heading_pattern/end_pattern against the actual HTML.")

    return html, tagged


# ─────────────────────────────────────────────────────────────────────────────
# 6.  ROOT NAMESPACE DECLARATIONS
# ─────────────────────────────────────────────────────────────────────────────

IXBRL_NAMESPACES = {
    "xmlns":        "http://www.w3.org/1999/xhtml",
    "xmlns:ix":     "http://www.xbrl.org/2013/inlineXBRL",
    "xmlns:xbrli":  "http://www.xbrl.org/2003/instance",
    "xmlns:link":   "http://www.xbrl.org/2003/linkbase",
    "xmlns:xlink":  "http://www.w3.org/1999/xlink",
    "xmlns:dei":    "http://xbrl.sec.gov/dei/2026",
    "xmlns:us-gaap":"http://fasb.org/us-gaap/2026",
    "xmlns:ixt":    "http://www.xbrl.org/inlineXBRL/transformation/2022-02-16",
    # ixt-sec: the SEC's OWN transform registry (separate from the general
    # ixt registry above) — required for boolballotbox / yesnoballotbox,
    # used to tag the Yes/No and true/false checkboxes on the cover page.
    # Confirmed against a real EDGAR filing's root <html> tag. Missing
    # this earlier meant "ixt-sec:boolballotbox" was an unresolvable
    # prefix, so Arelle silently skipped the transform and validated the
    # raw ☐/☑ glyph directly against the schema type — exactly the
    # xmlSchema:valueError seen in the log.
    "xmlns:ixt-sec":"http://www.sec.gov/inlineXBRL/transformation/2015-08-31",
    "xmlns:iso4217":"http://www.xbrl.org/2003/iso4217",
    # xbrldi: needed for <xbrldi:explicitMember> in dimensional (segment)
    # contexts — e.g. the Hardware/Software Sales revenue disaggregation.
    "xmlns:xbrldi": "http://xbrl.org/2006/xbrldi",
}


# ─────────────────────────────────────────────────────────────────────────────
# 7.  PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def tag_filing(
    html_path:    str,
    output_path:  str,
    form_type:    str,        # "10-K" or "10-Q"
    entity_name:  str,
    ticker:       str,       
    cik:          str,
    period_end:   str,        # "YYYY-MM-DD"  balance-sheet date / fiscal year end
    period_start: str,        # "YYYY-MM-DD"  start of income / cash-flow period
    prior_end:    str  = "",  # defaults to one year before period_end
    prior_start:  str  = "",  # defaults to one year before period_start
    currency:     str  = "USD",
    taxonomy_year: int = 2026,
    # ── 10-Q-only per-table period overrides ─────────────────────────────────
    # period_start/period_end/prior_start/prior_end above are always the YTD
    # current/prior duration (== the full fiscal year for a 10-K). These
    # three exist ONLY so a 10-Q's Income Statement ("Three Months Ended"
    # columns, a shorter duration than YTD) and Balance Sheet (prior column
    # = prior FISCAL YEAR-END by SEC convention, not prior YTD-end) get the
    # right dates instead of every table sharing one YTD date pair. None
    # (the default, and what a 10-K always passes) preserves the exact prior
    # behavior. See _tag_table()'s column_periods docstring for the mechanism.
    quarter_start:       str = None,
    prior_quarter_start: str = None,
    bs_prior_end:        str = None,
    # ── Cover-page fields (see tag_cover_page) ───────────────────────────────
    tag_cover:              bool = True,
    ein:                    str  = "",   # e.g. "31-1234567"
    commission_file_number: str  = "",   # e.g. "001-31123"
    address_line1:          str  = "",   # e.g. "1 Technology Drive"
    city:                   str  = "",
    state_abbr:             str  = "",   # 2-letter, e.g. "MD"
    zip_code:               str  = "",
    area_code:              str  = "",   # e.g. "410"
    local_phone:            str  = "",   # e.g. "555-0100"
    security_title:         str  = "",   # e.g. "Common Stock, without par value"
    exchange:               str  = "",   # e.g. "NASDAQ"
    # ── Notes to Financial Statements (see tag_notes_section) ────────────────
    notes:                  list = None, # list of note configs; None/empty = skip
) -> str:
    """
    Read html_path, inject iXBRL tags into every financial table, the
    cover page, AND the Notes to Financial Statements, write output_path
    as XHTML, and return the output path string.

    The cover-page fields above (ein, address_line1, ... exchange) aren't
    used to *generate* text — they're only needed so tag_cover_page()'s
    regexes know which dei:* concept a given printed value maps to. Pass
    tag_cover=False to skip cover tagging entirely and keep the current
    hidden-only behavior.

    notes: see tag_notes_section()'s docstring for the exact per-note dict
    shape. Pass None (the default) or [] to skip notes tagging entirely.
    """
    # ── Enforce .htm output extension (EFM 5.01.01) ────────────────────────
    # EDGAR requires inline XBRL files to use the .htm extension.
    # ── Derive prior period if not supplied ──────────────────────────────────
    if not prior_end:
        pe = date.fromisoformat(period_end)
        prior_end = pe.replace(year=pe.year - 1).isoformat()
    if not prior_start:
        ps = date.fromisoformat(period_start)
        prior_start = ps.replace(year=ps.year - 1).isoformat()

    # ── business_info.toml is the single source of truth for entity/DEI facts ──
    # Any cover-page kwarg the caller left at its default ("") falls back to
    # business_info.toml here, so callers no longer need to duplicate these
    # values by hand — they only need to override when testing a one-off value.
    biz = load_business_info()
    entity_name            = entity_name            or biz.get("company_name", entity_name)
    ticker                 = ticker                 or biz.get("ticker", ticker)
    ein                    = ein                    or biz.get("ein", "")
    commission_file_number = commission_file_number or biz.get("commission_file_number", "")
    address_line1          = address_line1          or biz.get("address_line1", "")
    city                   = city                   or biz.get("city", "")
    state_abbr             = state_abbr             or biz.get("state_abbr", "")
    zip_code               = zip_code               or biz.get("zip_code", "")
    area_code              = area_code              or biz.get("area_code", "")
    local_phone            = local_phone            or biz.get("local_phone_number", "")
    security_title         = security_title         or biz.get("security_class", "")
    exchange               = exchange               or biz.get("exchange", "")

    # ── Parse HTML ───────────────────────────────────────────────────────────
    # Suppress XMLParsedAsHTMLWarning — we intentionally parse XHTML with the
    # lxml HTML parser so BeautifulSoup can manipulate the financial tables.
    # The ix:header block is injected as a raw string (not via DOM) to preserve
    # namespace prefixes, so lxml's HTML handling is not a correctness concern.
    import warnings
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

    html = Path(html_path).read_text(encoding="utf-8")

    # ── Sanitise disallowed Unicode characters (EFM 5.02.01.01) ─────────────
    # EDGAR only permits printable ASCII (U+0020-U+007E) plus standard
    # whitespace.  AI-generated narrative (MD&A) frequently contains
    # typographic quotes, dashes, and other non-ASCII glyphs that fail
    # EFM 5.02.01.01.  Replace each with its nearest ASCII equivalent.
    _UNICODE_SUBS = [
        ('’', "'"),   # RIGHT SINGLE QUOTATION MARK  →  apostrophe
        ('‘', "'"),   # LEFT SINGLE QUOTATION MARK   →  apostrophe
        ('“', '"'),   # LEFT DOUBLE QUOTATION MARK   →  straight "
        ('”', '"'),   # RIGHT DOUBLE QUOTATION MARK  →  straight "
        ('–', '-'),   # EN DASH                      →  hyphen
        ('—', '--'),  # EM DASH                      →  double hyphen
        ('…', '...'), # HORIZONTAL ELLIPSIS          →  three dots
        (' ', ' '),   # NO-BREAK SPACE               →  regular space
        ('^', ''),    # CARET (appears in footnotes) →  strip
    ]
    for bad, good in _UNICODE_SUBS:
        html = html.replace(bad, good)

    # ── Strip <style> blocks (EFM 5.02.05) ───────────────────────────────────
    # Inline <style> elements are disallowed in EDGAR iXBRL documents.
    # Remove them entirely; all presentational CSS must be inline or absent.
    html = re.sub(r'<style[^>]*>.*?</style>', '', html,
                  flags=re.DOTALL | re.IGNORECASE)

    # ── Strip XHTML 1.0 Strict DOCTYPE ───────────────────────────────────────
    # Arelle validates the file against whatever schema is declared in DOCTYPE.
    # The strict XHTML DTD rejects both <ix:nonFraction> (unknown element) and
    # <u> inside <td> (wrong content model), producing hundreds of
    # [lxml.SCHEMAV_ELEMENT_CONTENT] errors.  iXBRL/EDGAR filings must NOT
    # carry a strict HTML DOCTYPE — remove it entirely so Arelle falls back to
    # the XBRL instance schema for validation instead.
    html = re.sub(
        r"<!DOCTYPE\s+html[^>]*>",
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # ── Strip any pre-existing XML declaration ───────────────────────────────
    # If the source .html already starts with its own <?xml ...?> declaration
    # (e.g. left over from convert_to_edgar_html's own serialization), lxml's
    # HTML parser doesn't recognize a processing instruction in an HTML
    # context and mangles it into a literal comment node instead:
    #   <!--?xml version='1.0' encoding='UTF-8'?-->
    # That stray comment survives all the way to the final .htm and crashes
    # Arelle's iXBRLViewerPlugin ("'ModelComment' object has no attribute
    # 'qname'") when it walks the document's top-level children expecting
    # only real elements. Strip it here, before BeautifulSoup ever sees it —
    # tag_filing() prepends its own single, correct declaration at the very
    # end (see the `xhtml = '<?xml version="1.0" encoding="UTF-8"?>\n' + xhtml`
    # line), so this one would only ever be redundant or, as here, mangled.
    html = re.sub(r'^\s*<\?xml[^>]*\?>\s*', '', html, count=1)

    # ── Replace <u>…</u> with CSS-underlined <span> ──────────────────────────
    # <u> is a presentation element disallowed in strict XHTML inside <td>.
    # SEC EDGAR also flags it.  Render underlines with inline CSS instead.
    html = re.sub(
        r"<u>(.*?)</u>",
        r'<span style="text-decoration:underline">\1</span>',
        html,
        flags=re.DOTALL,
    )

    # ── Build context registry ───────────────────────────────────────────────
    # Built before cover-page tagging (and before BeautifulSoup parses the
    # document) so tag_cover_page() can reuse the same entity-wide duration
    # context instead of minting a duplicate one.
    registry = ContextRegistry(cik=cik, period_start=period_start, period_end=period_end, ticker=ticker)

    # ── Tag the cover page (raw string pass, must run before BeautifulSoup) ──
    cover_tagged_concepts: set = set()
    if tag_cover:
        cover_info = {
            "period_start": period_start,
            "period_end": period_end,
            "form_type": form_type,
            "entity_name": entity_name,
            "ticker": ticker,
            "ein": ein,
            "commission_file_number": commission_file_number,
            "address_line1": address_line1,
            "city": city,
            "state_abbr": state_abbr,
            "state_name": biz.get("state_of_incorporation", ""),
            "zip_code": zip_code,
            "area_code": area_code,
            "local_phone": local_phone,
            "security_title": security_title,
            "exchange": exchange,
        }
        html, cover_tagged_concepts = tag_cover_page(html, registry, cover_info)

    # ── Tag the auditor's signature block (also a raw string pass) ──────────
    # Must run on the same raw html string, before BeautifulSoup parses it —
    # same reasoning as tag_cover_page.
    #
    # 10-K only: a 10-Q's financial statements are unaudited (interim
    # reporting), so there's no "Report of Independent Registered Public
    # Accounting Firm" section — and no auditor's signature block — on a
    # 10-Q at all. EFM 6.05.45 (the requirement this function exists to
    # satisfy) is itself a 10-K-only rule, per the function's own warning
    # message. Calling this unconditionally always failed to find a
    # signature block on a 10-Q and printed that warning for a requirement
    # that never applied to begin with.
    auditor_tagged_concepts: set = set()
    if form_type == "10-K":
        html, auditor_tagged_concepts = tag_auditor_report_block(html, registry)

    # ── Tag the Notes to Financial Statements (also a raw string pass) ──────
    notes_tagged_concepts: set = set()
    if notes:
        html, notes_tagged_concepts = tag_notes_section(html, registry, notes)

    soup = BeautifulSoup(html, "lxml")

    # ── Tag every financial table ────────────────────────────────────────────
    # One shared dict across all tables tracks each (concept, context) pair's
    # already-tagged value, so a legitimate repeat (same value) still gets
    # tagged, while an inconsistent repeat (different value — a real bug)
    # gets skipped and flagged (EFM 6.05.12 / arelle:duplicateFacts).
    total_tagged = 0
    seen: dict = {}
    for table in soup.find_all("table"):
        if _is_stockholders_equity_table(table):
            # Transposed layout (columns=concepts, not periods) — needs its
            # own walk, not _tag_table()'s one-concept-per-row logic. See
            # _is_stockholders_equity_table()'s docstring for why this can't
            # just fall through to the generic path.
            total_tagged += _tag_stockholders_equity_table(table, registry, seen=seen)
        else:
            # 10-Q per-table period overrides (see tag_filing()'s docstring
            # for quarter_start/prior_quarter_start/bs_prior_end). These are
            # all None for a 10-K, so column_periods stays None and every
            # table falls through to _tag_table()'s existing offset-based
            # math unchanged.
            column_periods = None
            if quarter_start:
                heading = _nearest_preceding_heading_text(table)
                n_cols  = _table_data_col_count(table)
                if heading == "statements of income" and n_cols == 4:
                    # 4 columns here are 2 years x 2 DURATIONS (quarter,
                    # then YTD) — NOT 4 sequential years, which is what
                    # _period_for_offset()'s offset=2/3 would otherwise
                    # produce (it shifts prior_start/prior_end back one
                    # more full year per offset, correct for a 10-K's
                    # extra comparative YEARS, wrong for a 10-Q's extra
                    # comparative DURATION).
                    column_periods = [
                        (quarter_start,       period_end),
                        (prior_quarter_start, prior_end),
                        (period_start,        period_end),
                        (prior_start,         prior_end),
                    ]
                elif heading == "balance sheets" and bs_prior_end:
                    # Instant facts only use the "end" of each tuple, so
                    # the start values here are unused placeholders.
                    # bs_prior_end = prior FISCAL YEAR-END (SEC convention
                    # for a 10-Q's comparative Balance Sheet column) —
                    # prior_end (prior YTD-end / prior same-quarter-end)
                    # would be wrong here; those two only coincide for Q4.
                    column_periods = [
                        (period_start, period_end),
                        (prior_start,  bs_prior_end),
                    ]
                # Cash Flow Statement: every OTHER row's 2 columns are
                # exactly (period_start,period_end)/(prior_start,prior_end)
                # — the YTD current/prior dates — which offset 0/1 already
                # resolve to correctly with no override needed.
                #
                # "Cash at Beginning of Period" is the one exception, and
                # needs a per-row override (see _tag_table()'s dict-shaped
                # column_periods). Its instant_offset=1 shifts the
                # column's own period-END back one full YEAR to land on
                # "the prior column's own period-end" — correct for a
                # 10-K, where the current and prior columns are
                # consecutive fiscal years (2024-12-31 really is exactly
                # one year before 2025-12-31, and really is the correct
                # opening balance for the 2025 column). A 10-Q's current
                # and prior CASH FLOW columns are NOT consecutive: they're
                # two separate, non-adjacent 6-month YTD windows (Jan-Jun
                # 2025 vs Jan-Jun 2024), so "one year back from this
                # column's period-end" lands on the WRONG date entirely —
                # e.g. the current column's beginning-of-period balance
                # (the true Jan 1, 2025 opening balance) was landing on
                # 2024-06-30 instead, which happens to be exactly the date
                # the PRIOR column's "Cash at End of Period" already uses
                # — so the two got tagged under the SAME context with two
                # DIFFERENT values, and _tag_table()'s duplicate-value
                # check (correctly) rejected the second one with a loud
                # warning instead of silently tagging a wrong number.
                #
                # The correct beginning-of-period date for EACH column is
                # that column's own prior FISCAL YEAR-END — bs_prior_end
                # for the current column (2024-12-31, matching the Balance
                # Sheet's own comparative column exactly, per standard
                # EDGAR practice: the balance is definitionally identical,
                # so reusing that exact context is intentional, not a
                # bug — the SAME "expected duplicate fact" pattern already
                # relied on elsewhere in this file), and one further year
                # back (2023-12-31) for the prior column.
                #
                # Only reachable when quarter_start is set (10-Q only —
                # see the `if quarter_start:` guard above this whole
                # block), so the 10-K path is untouched: its Cash Flow
                # table still falls through to column_periods=None and
                # the original offset-based math, where the "prior
                # column's own period-end" assumption IS correct.
                elif heading == "statements of cash flows" and bs_prior_end:
                    column_periods = {
                        "cash at beginning of period": [
                            (bs_prior_end, bs_prior_end),
                            (_shift_years(bs_prior_end, 1), _shift_years(bs_prior_end, 1)),
                        ],
                    }

            total_tagged += _tag_table(
                table, registry,
                period_start, period_end,
                prior_start,  prior_end,
                seen=seen,
                column_periods=column_periods,
            )

    # ── Update <html> element with iXBRL namespaces ──────────────────────────
    # Do this before serialising so the namespaces appear in the output <html> tag.
    html_tag = soup.find("html")
    if html_tag:
        for attr, val in IXBRL_NAMESPACES.items():
            html_tag[attr] = val
        # Add ticker-specific extension namespace.
        # EFM.6.07.04 requires targetNamespace = http://www.{ticker}.com/{yyyymmdd}
        ticker_lower = ticker.lower()
        # Must match the extension XSD's own targetNamespace exactly (e.g.
        # tifx-20251231.xsd declares targetNamespace="http://www.tifx.com/20251231"),
        # which is keyed to the FISCAL YEAR END, not this filing's period_end.
        # Using period_end here is the same bug as the schemaRef href above:
        # a 10-Q's period_end (e.g. 2025-06-30) would declare a namespace that
        # doesn't match the schema's targetNamespace, breaking every tifx:-
        # prefixed extension concept/dimension (e.g. tifx:RevenueProductOrServiceAxis).
        fiscal_year_end_clean = f"{period_end[:4]}1231"
        html_tag[f"xmlns:{ticker_lower}"] = f"http://www.{ticker_lower}.com/{fiscal_year_end_clean}"

    # ── Serialise to string ───────────────────────────────────────────────────
    xhtml = str(soup)

    # ── Replace ix:nonFraction placeholders with correctly-cased raw tags ─────
    #
    # WHY: BeautifulSoup's lxml HTML parser lowercases every element and
    # attribute name.  We avoided this for ix:header by raw-string injection.
    # For ix:nonFraction we used the same trick in reverse: _tag_table stored
    # the correctly-cased markup in a data-ixbrl attribute on a plain <span>.
    # Now we swap each placeholder back to the real ix:nonFraction tag.
    #
    # The placeholder looks like (HTML-escaped inside the attribute):
    #   <span data-ixbrl="&lt;ix:nonFraction ...&gt;VALUE&lt;/ix:nonFraction&gt;">VALUE</span>
    import html as _html
    def _restore_ix(m: re.Match) -> str:
        escaped_tag = m.group(2)          # group(2) = value inside the quote chars
        return _html.unescape(escaped_tag)

    # BeautifulSoup switches to single-quote attribute delimiters when the value
    # contains double quotes (our ix:nonFraction markup has contextRef="..." etc.)
    # Use a backreference (['"])…\1 to match whichever quote style BS chose.
    xhtml = re.sub(
        r"""<span data-ixbrl=(['"])(.+?)\1>[^<]*</span>""",
        _restore_ix,
        xhtml,
        flags=re.DOTALL,
    )

    # ── Restore notes-section open/close markers (tag_notes_section) ────────
    # Unlike the placeholder above, these hold NO content between open and
    # close — <span data-ixbrl-open="...">​</span> — so there's no [^<]*
    # nested-tag restriction to worry about; each marker independently
    # unescapes to a bare opening or closing <ix:nonNumeric> tag, and
    # everything real that sat between the two markers in the source
    # (ordinary paragraphs, headings, etc.) was never touched.
    def _restore_open(m: re.Match) -> str:
        return _html.unescape(m.group(2))

    def _restore_close(m: re.Match) -> str:
        return _html.unescape(m.group(2))

    xhtml = re.sub(
        r"""<span data-ixbrl-open=(['"])(.+?)\1>\s*</span>""",
        _restore_open, xhtml, flags=re.DOTALL,
    )
    xhtml = re.sub(
        r"""<span data-ixbrl-close=(['"])(.+?)\1>\s*</span>""",
        _restore_close, xhtml, flags=re.DOTALL,
    )

    # ── Inject ix:header via raw-string replacement ──────────────────────────
    #
    # WHY: BeautifulSoup strips namespace prefixes when inserting parsed nodes,
    # turning <ix:header> into a plain HTML <header> element which fails EDGAR
    # schema validation ([lxml.SCHEMAV_ELEMENT_CONTENT], [ix11.8.1.3], etc.).
    # Injecting the block as a raw string preserves every ix:/xbrli:/link: prefix.
    #
    header_block = registry.render_xbrl_header(
        entity_name, ticker, currency, taxonomy_year,
        skip_concepts=cover_tagged_concepts | auditor_tagged_concepts,
        business_info=biz,
        form_type=form_type,
    )

    # [ix11.8.1.2:headerDisplayNone]: ix:header must be inside a <div style="display:none">
    # so browsers suppress it from visual rendering of the filing.
    header_block = '<div style="display:none">\n' + header_block + '\n</div>'

    # Find the opening <body> tag (may have attributes) and insert right after it.
    body_match = re.search(r'(<body[^>]*>)', xhtml, re.IGNORECASE)
    if body_match:
        insert_pos = body_match.end()
        xhtml = xhtml[:insert_pos] + "\n" + header_block + "\n" + xhtml[insert_pos:]
    else:
        # Fallback: prepend to content (should never happen)
        xhtml = header_block + "\n" + xhtml

    # ── Ensure XML declaration for EDGAR ─────────────────────────────────────
    if not xhtml.startswith("<?xml"):
        xhtml = '<?xml version="1.0" encoding="UTF-8"?>\n' + xhtml

    # ── Re-encode raw ballot-box glyphs (EFM 5.02.01.01) ─────────────────────
    # EDGAR (EFM 5.02.01.01) disallows raw non-ASCII characters appearing
    # directly in the filed .htm. This used to be a narrow two-character
    # replace for the ☐/☑ checkbox glyphs (BeautifulSoup decodes the
    # "&#9744;"/"&#9745;" entities emitted by _ballot_glyph back into raw
    # Unicode when it parses/serializes each placeholder <span>), but that
    # missed ☒ (U+2612, BALLOT BOX WITH X — also used by _CHECKED_GLYPHS)
    # and any other non-ASCII character that reaches the output by other
    # means — e.g. a literal "§" from cover-page boilerplate text copied
    # out of a source .docx. Rather than special-case each glyph as it's
    # discovered, sweep the entire final string once, right before it's
    # written: any character outside ASCII becomes a numeric character
    # reference. This keeps every visible glyph identical (numeric
    # references render the same character) while guaranteeing EFM
    # 5.02.01.01 compliance regardless of where the character came from.
    # Existing entities like "&#9744;" are already pure ASCII text, so
    # this pass never double-encodes them.
    xhtml = "".join(ch if ord(ch) < 128 else f"&#{ord(ch)};" for ch in xhtml)

    Path(output_path).write_text(xhtml, encoding="utf-8")
    print(f"[xbrl_tagger] Tagged {total_tagged} table values + "
          f"{len(cover_tagged_concepts)} cover-page facts + "
          f"{len(notes_tagged_concepts)} notes facts → {output_path}")
    return output_path
