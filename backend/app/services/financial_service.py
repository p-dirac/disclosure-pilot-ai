"""
Financial data service.
Queries the bookkeeper schema tables to produce balance sheets,
income statements, and cash flow statements.

Database schema (gem-db-init.sql):
  bookkeeper.chart_of_acct   – account_number (VARCHAR PK), account_name,
                                account_type, account_subtype, normal_balance
  bookkeeper.general_ledger  – VIEW: joins journal_lines + journal_entries
                                (WHERE status = 'POSTED')
                                columns: line_id, entry_date, account_number,
                                         debit, credit, currency, description
  bookkeeper.depreciation    – depreciation_id, asset_id, year,
                                annual_depreciation, accumulated_thru_year
  bookkeeper.accounts_payable / accounts_receivable – amount, invoice_date, status
"""
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import date, timedelta
import calendar
import logging

from app.schemas.schemas import BalanceSheetRow, IncomeStatementRow, CashFlowRow, StockholdersEquityRow

logger = logging.getLogger(__name__)

# ── Type sets (match bookkeeper.chart_of_acct.account_type exactly) ──────────
ASSET_CATEGORIES     = {"Asset"}
LIABILITY_CATEGORIES = {"Liability"}
EQUITY_CATEGORIES    = {"Equity"}
REVENUE_CATEGORIES   = {"Revenue"}
EXPENSE_CATEGORIES   = {"Expense"}

# Contra-asset accounts carry a CREDIT normal balance and reduce total assets
# (e.g. Accumulated Depreciation, Allowance for Doubtful Accounts).
# Their balances must be negated on the balance sheet so they reduce assets.
CONTRA_ASSET_SUBTYPES = {"Contra Asset"}

# Contra-equity accounts: both Treasury Stock (3200) and Dividends Paid (3300)
# share account_subtype = "Contra Equity" in the DB, so they cannot be
# distinguished by subtype alone.  We use the stable account_number PK instead.
#
# Treasury Stock (3200) — PERMANENT contra-equity: shown as its own BS line
#   (cumulative balance, negated).  Not folded into Retained Earnings.
#
# Dividends Paid (3300) — TEMPORARY contra-equity: should be closed into RE
#   at year-end.  Since closing entries may not be posted, deducted inside
#   _compute_retained_earnings.  No separate BS row.
#
TREASURY_STOCK_ACCT   = "3200"
DIVIDENDS_PAID_ACCT   = "3300"
RETAINED_EARNINGS_ACCT = "3100"

# Keep for any code that still checks account_subtype generically
CONTRA_EQUITY_SUBTYPES    = {"Contra Equity"}
RETAINED_EARNINGS_SUBTYPE = "Retained Earnings"


# ── Date helpers ──────────────────────────────────────────────────────────────

def _quarter_dates(year: int, quarter: int) -> tuple[date, date]:
    """Return (start_date, end_date) for a given quarter."""
    q_start_months = {1: 1, 2: 4, 3: 7, 4: 10}
    q_end_months   = {1: 3, 2: 6, 3: 9, 4: 12}
    start_month = q_start_months[quarter]
    end_month   = q_end_months[quarter]
    end_day = calendar.monthrange(year, end_month)[1]
    return date(year, start_month, 1), date(year, end_month, end_day)


def _year_dates(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


# ── Chart of accounts ─────────────────────────────────────────────────────────

def get_chart_of_accounts(db: Session) -> list[dict]:
    """Fetch all chart of accounts entries ordered by account_number."""
    stmt = text("""
        SELECT account_number, account_name, account_type, account_subtype, normal_balance
        FROM bookkeeper.chart_of_acct
        ORDER BY account_number
    """)
    rows = db.execute(stmt).fetchall()
    return [
        {
            "account":        r.account_number,   # VARCHAR e.g. "1000"
            "acct_name":      r.account_name,
            "category":       r.account_type,      # Asset | Liability | Equity | Revenue | Expense
            "acct_subtype":   r.account_subtype,
            "normal_balance": r.normal_balance,    # Debit | Credit
        }
        for r in rows
    ]


# ── Loans (bookkeeper.loans — see db-loans.sql) ───────────────────────────────

def get_loans(db: Session) -> list[dict]:
    """
    Fetch every row of bookkeeper.loans, shaped to match notes_agent.py's
    LoanRow TypedDict field-for-field so the caller can pass this straight
    into NotesState["loan_rows"] with no further transformation. Feeds
    Note 7's real 5-year maturity schedule and weighted-average-rate
    breakdown (see _compute_debt_maturity_schedule / _build_debt_rate_breakdown
    in notes_agent.py) — without this wired in, Note 7 silently falls back
    to the short-term/long-term split only, which is graceful but not the
    full disclosure.
    """
    stmt = text("""
        SELECT loan_id, asset_id, principal, rate, term_months, start_date, monthly_payment
        FROM bookkeeper.loans
        ORDER BY loan_id
    """)
    rows = db.execute(stmt).fetchall()
    return [
        {
            "loan_id":         r.loan_id,
            "asset_id":        r.asset_id,
            "principal":       float(r.principal),
            "rate":            float(r.rate),
            "term_months":     r.term_months,
            "start_date":      r.start_date,
            "monthly_payment": float(r.monthly_payment),
        }
        for r in rows
    ]


# ── GL balance helpers ────────────────────────────────────────────────────────

def get_gl_balances_cumulative(
    db: Session,
    as_of: date,
    exclude_account_numbers: Optional[list[str]] = None,
) -> dict[str, float]:
    """
    Cumulative balance per account through a given date, sign-corrected by
    normal_balance from chart_of_acct:
      Debit-normal  accounts → debit − credit  (positive = balance exists)
      Credit-normal accounts → credit − debit  (positive = balance exists)

    Used for balance sheet accounts.  Pass exclude_account_numbers to omit
    specific accounts (e.g. Retained Earnings which is computed separately).
    """
    excl_clause = ""
    params: dict = {"as_of": as_of}
    if exclude_account_numbers:
        excl_clause = "AND gl.account_number != ALL(:excl)"
        params["excl"] = exclude_account_numbers

    stmt = text(f"""
        SELECT
            gl.account_number,
            coa.normal_balance,
            COALESCE(SUM(gl.debit),  0) AS total_debit,
            COALESCE(SUM(gl.credit), 0) AS total_credit
        FROM bookkeeper.general_ledger gl
        JOIN bookkeeper.chart_of_acct coa ON coa.account_number = gl.account_number
        WHERE gl.entry_date <= :as_of
          {excl_clause}
        GROUP BY gl.account_number, coa.normal_balance
    """)
    rows = db.execute(stmt, params).fetchall()
    result: dict[str, float] = {}
    for r in rows:
        if r.normal_balance == "Debit":
            result[r.account_number] = float(r.total_debit) - float(r.total_credit)
        else:
            result[r.account_number] = float(r.total_credit) - float(r.total_debit)
    return result


def get_gl_balances_range(
    db: Session,
    start: date,
    end: date,
) -> dict[str, float]:
    """
    Net balance per account for a specific date range, sign-corrected by
    normal_balance:
      Revenue  (Credit-normal) → credit − debit  (positive = earned)
      Expense  (Debit-normal)  → debit  − credit  (positive = incurred)

    Used for income statement accounts.  The caller applies a sign flip
    (×−1) for expense rows so they appear as positive costs on the statement.
    """
    stmt = text("""
        SELECT
            gl.account_number,
            coa.normal_balance,
            COALESCE(SUM(gl.debit),  0) AS total_debit,
            COALESCE(SUM(gl.credit), 0) AS total_credit
        FROM bookkeeper.general_ledger gl
        JOIN bookkeeper.chart_of_acct coa ON coa.account_number = gl.account_number
        WHERE gl.entry_date BETWEEN :start AND :end
        GROUP BY gl.account_number, coa.normal_balance
    """)
    rows = db.execute(stmt, {"start": start, "end": end}).fetchall()
    result: dict[str, float] = {}
    for r in rows:
        if r.normal_balance == "Debit":
            result[r.account_number] = float(r.total_debit) - float(r.total_credit)
        else:
            result[r.account_number] = float(r.total_credit) - float(r.total_debit)
    return result


def _acct_range_by_name(db: Session, acct_name: str, start: date, end: date) -> float:
    """
    Period activity for a named account (matched by account_name ILIKE),
    sign-corrected by normal_balance. Returns a positive value when the
    account has normal-balance activity.

    Promoted out of build_cash_flow's former nested _named_acct_range so
    build_stockholders_equity() can call the exact same logic rather than
    a second, independently-written implementation that could silently
    drift apart from it (the same class of bug fixed earlier for
    us-gaap:IncomeTaxExpenseBenefit — see net_income_for_period below for
    the sibling fix that motivated this one).
    """
    stmt = text("""
        SELECT
            coa.normal_balance,
            COALESCE(SUM(gl.debit),  0) AS total_debit,
            COALESCE(SUM(gl.credit), 0) AS total_credit
        FROM bookkeeper.general_ledger gl
        JOIN bookkeeper.chart_of_acct coa ON coa.account_number = gl.account_number
        WHERE gl.entry_date BETWEEN :start AND :end
          AND coa.account_name ILIKE :name
        GROUP BY coa.normal_balance
    """)
    total = 0.0
    for r in db.execute(stmt, {"start": start, "end": end, "name": acct_name}).fetchall():
        if r.normal_balance == "Debit":
            total += float(r.total_debit) - float(r.total_credit)
        else:
            total += float(r.total_credit) - float(r.total_debit)
    return total


def net_income_for_period(db: Session, coa: list[dict], start: date, end: date) -> float:
    """
    Compute Net Income the SAME way build_income_statement() does — via
    get_gl_balances_range() plus per-account category summation in Python.

    Root cause of an earlier $1 FY2023 mismatch between the Income
    Statement and Cash Flow Statement's Net Income: a separate SQL-side
    aggregation summed debits/credits with one SUM() per normal_balance
    group, while build_income_statement sums many individual per-account
    float values in Python — two independently written aggregations of
    "the same" total can drift apart purely from floating-point summation
    order. Using this SAME function everywhere Net Income is needed
    (cash flow, and now the stockholders' equity statement) guarantees
    they always match exactly, not just approximately.
    """
    bal = get_gl_balances_range(db, start, end)
    revenue  = sum(bal.get(a["account"], 0.0) for a in coa if a["category"] in REVENUE_CATEGORIES)
    expenses = sum(bal.get(a["account"], 0.0) for a in coa if a["category"] in EXPENSE_CATEGORIES)
    return revenue - expenses


# ── Retained Earnings computation ─────────────────────────────────────────────

def _compute_retained_earnings(db: Session, as_of: date) -> float:
    """
    Compute retained earnings as of a given date.

    Two components:
      1. Direct GL postings to the Retained Earnings account (opening balances
         and manual year-end closing entries).  Credit-normal → credit − debit.
      2. Cumulative net income since inception: Revenue credits − Expense debits
         through as_of.  These represent un-closed period activity.

    Closing-entry double-count is avoided because year-end closing entries
    simultaneously (a) debit Revenue/credit Expense to zero them out, and
    (b) credit the RE account.  Component 1 captures the RE credit; component 2
    then nets to zero for those same years — each year's net income is counted
    exactly once whether or not closing entries have been posted.

    Contra-equity accounts (Dividends Paid, Treasury Stock) are deducted here
    because without year-end closing entries they never flow into the RE GL
    account.  Deducting them in _compute_retained_earnings means the balance
    sheet must NOT show them as separate line items — they are already embedded
    in the RE figure, so displaying them again would double-count the reduction.

    Formula:  RE = direct_re_postings + cumulative_revenue − cumulative_expenses
                   − cumulative_contra_equity
    """
    # Component 1 — direct postings to the Retained Earnings GL account
    re_stmt = text("""
        SELECT COALESCE(SUM(gl.credit), 0) - COALESCE(SUM(gl.debit), 0) AS net
        FROM bookkeeper.general_ledger gl
        JOIN bookkeeper.chart_of_acct coa ON coa.account_number = gl.account_number
        WHERE gl.entry_date <= :as_of
          AND coa.account_type = 'Equity'
          AND coa.account_subtype = :re_subtype
    """)
    direct_re = float(
        db.execute(re_stmt, {"as_of": as_of, "re_subtype": RETAINED_EARNINGS_SUBTYPE}).scalar() or 0
    )
    #logger.info(f"  RE direct_gl_balance as_of={as_of}: {direct_re:.2f}")

    # Component 2 — cumulative un-closed revenue and expense activity
    rev_exp_stmt = text("""
        SELECT
            coa.account_number,
            coa.account_name,
            coa.account_type,
            COALESCE(SUM(gl.credit), 0) - COALESCE(SUM(gl.debit), 0) AS credit_net,
            COALESCE(SUM(gl.debit),  0) - COALESCE(SUM(gl.credit), 0) AS debit_net
        FROM bookkeeper.general_ledger gl
        JOIN bookkeeper.chart_of_acct coa ON coa.account_number = gl.account_number
        WHERE gl.entry_date <= :as_of
          AND coa.account_type IN ('Revenue', 'Expense')
        GROUP BY coa.account_number, coa.account_name, coa.account_type
        ORDER BY coa.account_type, coa.account_number
    """)
    rev_exp_rows = db.execute(rev_exp_stmt, {"as_of": as_of}).fetchall()

    total_revenue  = 0.0
    total_expenses = 0.0
    for r in rev_exp_rows:
        if r.account_type == "Revenue":
            total_revenue  += float(r.credit_net)
            #logger.info(f"  RE revenue  acct={r.account_number} {r.account_name}: {r.credit_net:.2f}")
        else:
            total_expenses += float(r.debit_net)
            #logger.info(f"  RE expense  acct={r.account_number} {r.account_name}: {r.debit_net:.2f}")

    # Component 3 — cumulative Dividends Paid deduction.
    # Dividends Paid is a temporary contra-equity account that should be closed
    # into RE at year-end (DR Retained Earnings / CR Dividends Paid).  Since
    # closing entries may not be posted, deduct the cumulative debit-net here.
    # Query by account_number = 3300 (Dividends Paid) directly.
    # Treasury Stock (3200) is intentionally excluded — it is a permanent
    # contra-equity shown as its own separate balance sheet line.
    div_stmt = text("""
        SELECT COALESCE(SUM(gl.debit), 0) - COALESCE(SUM(gl.credit), 0) AS debit_net
        FROM bookkeeper.general_ledger gl
        WHERE gl.entry_date <= :as_of
          AND gl.account_number = :div_acct
    """)
    dividends_reduction = float(
        db.execute(div_stmt, {"as_of": as_of, "div_acct": DIVIDENDS_PAID_ACCT}).scalar() or 0
    )
    #logger.info(f"  RE dividends_reduction as_of={as_of}: {dividends_reduction:.2f}")

    result = direct_re + total_revenue - total_expenses - dividends_reduction
    #logger.info(
    #    f"_compute_retained_earnings as_of={as_of}: "
    #    f"direct_re={direct_re:.2f}, revenue={total_revenue:.2f}, "
    #    f"expenses={total_expenses:.2f}, dividends={dividends_reduction:.2f}, RE={result:.2f}"
    #)
    return result


# ── Balance sheet ─────────────────────────────────────────────────────────────

def build_balance_sheet(
    db: Session,
    current_start: date, current_end: date,
    prior_start: date, prior_end: date,
) -> list[BalanceSheetRow]:
    """
    Build a two-period balance sheet.

    Sign convention (all values positive when the account carries a balance,
    negative when the account is a contra that reduces its section):
      Normal Asset        – Debit-normal  → debit − credit  (positive)
      Contra Asset        – Credit-normal → credit − debit, then NEGATED
                            (e.g. Accumulated Depreciation, Allowance for Doubtful Accts)
      Liabilities         – Credit-normal → credit − debit  (positive)
      Normal Equity       – Credit-normal → credit − debit  (positive)
      Treasury Stock      – Debit-normal contra-equity; cumulative balance,
                            shown as its own negative line item
      Retained Earnings   – Computed by _compute_retained_earnings;
                            includes cumulative deduction for Dividends Paid
                            (no separate BS row for Dividends Paid)
    """
    coa = get_chart_of_accounts(db)

    # Collect account numbers that need special handling so we can exclude them
    # from the main cumulative balance dict and handle them individually below.
    # Identify special equity accounts by account_number (stable PK).
    re_acct_numbers = [RETAINED_EARNINGS_ACCT]
    contra_equity_nos = [TREASURY_STOCK_ACCT, DIVIDENDS_PAID_ACCT]
    contra_asset_nos = [
        a["account"] for a in coa
        if a["category"] == "Asset" and a["acct_subtype"] in CONTRA_ASSET_SUBTYPES
    ]

    # Main cumulative dict excludes RE and contra accounts; they're handled below.
    exclude = re_acct_numbers + contra_equity_nos + contra_asset_nos
    current_bal = get_gl_balances_cumulative(db, current_end, exclude_account_numbers=exclude)
    prior_bal   = get_gl_balances_cumulative(db, prior_end,   exclude_account_numbers=exclude)

    # Separate full dict for contra ASSET lookups (cumulative is correct for
    # Accumulated Depreciation, Allowance for Doubtful Accounts etc. — these
    # accumulate legitimately across years and are never closed out).
    full_current = get_gl_balances_cumulative(db, current_end)
    full_prior   = get_gl_balances_cumulative(db, prior_end)

    # NOTE: Dividends Paid is NOT displayed as a separate balance sheet row —
    # it is deducted inside _compute_retained_earnings (Component 3).
    # Treasury Stock IS displayed as its own row (cumulative) using full_current
    # / full_prior, which are already computed above for contra-asset lookups.
    current_retained = _compute_retained_earnings(db, current_end)
    prior_retained   = _compute_retained_earnings(db, prior_end)

    # Use the stable account_number PK to identify the three special equity
    # accounts.  Both 3200 and 3300 share subtype "Contra Equity" so subtype
    # alone cannot distinguish them; account_number is unambiguous.
    re_nos        = {RETAINED_EARNINGS_ACCT}
    treasury_nos  = {TREASURY_STOCK_ACCT}
    dividends_nos = {DIVIDENDS_PAID_ACCT}

    #logger.info(
    #    "BS equity account classification: "
    #    f"RE={re_nos}, treasury={treasury_nos}, dividends={dividends_nos}"
    #)

    rows: list[BalanceSheetRow] = []

    for acct in coa:
        acct_no  = acct["account"]
        cat      = acct["category"]
        sub      = acct["acct_subtype"]
        name     = acct["acct_name"]

        if cat in ASSET_CATEGORIES:
            if sub in CONTRA_ASSET_SUBTYPES:
                # Credit-normal contra-asset: negate so it reduces the asset section.
                cur_val = -full_current.get(acct_no, 0.0)
                pri_val = -full_prior.get(acct_no, 0.0)
                #logger.info(f"  BS contra-asset acct={acct_no} {name}: current={cur_val:.2f}, prior={pri_val:.2f}")
                rows.append(BalanceSheetRow(
                    account=acct_no,
                    acct_name=name,
                    category=cat,
                    current_period=cur_val,
                    prior_period=pri_val,
                ))
            else:
                rows.append(BalanceSheetRow(
                    account=acct_no,
                    acct_name=name,
                    category=cat,
                    current_period=current_bal.get(acct_no, 0.0),
                    prior_period=prior_bal.get(acct_no, 0.0),
                ))

        elif cat in LIABILITY_CATEGORIES:
            rows.append(BalanceSheetRow(
                account=acct_no,
                acct_name=name,
                category=cat,
                current_period=current_bal.get(acct_no, 0.0),
                prior_period=prior_bal.get(acct_no, 0.0),
            ))

        elif cat in EQUITY_CATEGORIES:
            if acct_no in re_nos:
                # Retained Earnings — use computed value (includes dividends deduction).
                #logger.info(
                #    f"  BS equity RE acct={acct_no} {name}: "
                #    f"current={current_retained:.2f}, prior={prior_retained:.2f}"
                #)
                rows.append(BalanceSheetRow(
                    account=acct_no,
                    acct_name=name,
                    category=cat,
                    current_period=current_retained,
                    prior_period=prior_retained,
                ))
            elif acct_no in dividends_nos:
                # Dividends Paid — already deducted in _compute_retained_earnings.
                # No separate BS row; emitting one would double-count the reduction.
                #logger.info(f"  BS equity SKIP dividends acct={acct_no} {name}")
                pass
            elif acct_no in treasury_nos:
                # Treasury Stock — permanent contra-equity shown as own negative line.
                # Debit-normal: full_current returns debit−credit (positive); negate.
                cur_val = -full_current.get(acct_no, 0.0)
                pri_val = -full_prior.get(acct_no, 0.0)
                #logger.info(f"  BS treasury acct={acct_no} {name}: current={cur_val:.2f}, prior={pri_val:.2f}")
                rows.append(BalanceSheetRow(
                    account=acct_no,
                    acct_name=name,
                    category=cat,
                    current_period=cur_val,
                    prior_period=pri_val,
                ))
            else:
                # Normal equity (Common Stock, Additional Paid-in Capital, etc.)
                cur_val = current_bal.get(acct_no, 0.0)
                pri_val = prior_bal.get(acct_no, 0.0)
                #logger.info(f"  BS equity acct={acct_no} {name}: current={cur_val:.2f}, prior={pri_val:.2f}")
                rows.append(BalanceSheetRow(
                    account=acct_no,
                    acct_name=name,
                    category=cat,
                    current_period=cur_val,
                    prior_period=pri_val,
                ))

    return rows


# ── Income statement ──────────────────────────────────────────────────────────

def build_income_statement(
    db: Session,
    current_start: date, current_end: date,
    prior_start: date, prior_end: date,
    prior2_start: date = None, prior2_end: date = None,
    ytd_start: date = None, ytd_end: date = None,
    ytd_prior_start: date = None, ytd_prior_end: date = None,
) -> list[IncomeStatementRow]:
    """
    Build income statement for current/prior periods, optionally with YTD.

    For 10-K reports supply prior2_start/prior2_end to get a third annual column.
    For 10-Q reports leave those as None; prior2_period will be None and the
    frontend will omit the column.

    get_gl_balances_range returns sign-corrected values:
      Revenue  → positive (credit-normal)
      Expense  → positive (debit-normal)
    So expense rows do NOT need sign negation here.
    """
    coa             = get_chart_of_accounts(db)
    current_bal     = get_gl_balances_range(db, current_start, current_end)
    prior_bal       = get_gl_balances_range(db, prior_start,   prior_end)
    prior2_bal      = get_gl_balances_range(db, prior2_start,  prior2_end)  if prior2_start  else {}
    ytd_current_bal = get_gl_balances_range(db, ytd_start,     ytd_end)     if ytd_start     else {}
    ytd_prior_bal   = get_gl_balances_range(db, ytd_prior_start, ytd_prior_end) if ytd_prior_start else {}

    rows: list[IncomeStatementRow] = []
    for acct in coa:
        acct_no = acct["account"]
        cat     = acct["category"]
        if cat in REVENUE_CATEGORIES | EXPENSE_CATEGORIES:
            rows.append(IncomeStatementRow(
                account=acct_no,
                acct_name=acct["acct_name"],
                category=cat,
                acct_subtype=acct["acct_subtype"],
                current_period=current_bal.get(acct_no, 0.0),
                prior_period=prior_bal.get(acct_no, 0.0),
                prior2_period=prior2_bal.get(acct_no, 0.0) if prior2_start else None,
                ytd_current=ytd_current_bal.get(acct_no, 0.0) if ytd_start else None,
                ytd_prior=ytd_prior_bal.get(acct_no, 0.0)     if ytd_prior_start else None,
            ))
    return rows


# ── Cash flow statement ───────────────────────────────────────────────────────

def build_cash_flow(
    db: Session,
    current_start: date, current_end: date,
    prior_start: date, prior_end: date,
    prior2_start: date = None, prior2_end: date = None,
) -> list[CashFlowRow]:
    """
    Indirect-method cash flow statement.

    Working capital changes are computed as the difference in cumulative GL
    balances between the day before period start and period end for each
    balance sheet account.
      Increase in asset     = use of cash   (negative)
      Increase in liability = source of cash (positive)

    Supply prior2_start/prior2_end for a third column (10-K); leave None for 10-Q.
    """
    coa = get_chart_of_accounts(db)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _acct_bal_as_of(account_number: str, as_of: date) -> float:
        """Cumulative sign-corrected balance for one account through as_of."""
        stmt = text("""
            SELECT
                coa.normal_balance,
                COALESCE(SUM(gl.debit),  0) AS total_debit,
                COALESCE(SUM(gl.credit), 0) AS total_credit
            FROM bookkeeper.general_ledger gl
            JOIN bookkeeper.chart_of_acct coa ON coa.account_number = gl.account_number
            WHERE gl.account_number = :acct AND gl.entry_date <= :as_of
            GROUP BY coa.normal_balance
        """)
        row = db.execute(stmt, {"acct": account_number, "as_of": as_of}).fetchone()
        if not row:
            return 0.0
        if row.normal_balance == "Debit":
            return float(row.total_debit) - float(row.total_credit)
        return float(row.total_credit) - float(row.total_debit)

    def _acct_change(account_number: str, period_start: date, period_end: date) -> float:
        """Change in cumulative balance over the period."""
        return (
            _acct_bal_as_of(account_number, period_end)
            - _acct_bal_as_of(account_number, period_start - timedelta(days=1))
        )

    def _find_acct(name: str) -> Optional[dict]:
        return next((a for a in coa if a["acct_name"].strip().lower() == name.lower()), None)

    def _named_acct_range(acct_name: str, start: date, end: date) -> float:
        return _acct_range_by_name(db, acct_name, start, end)

    def _p2(fn, *args, **kwargs):
        """Call fn only when prior2 period is requested."""
        return fn(*args, **kwargs) if prior2_start else None

    # ── Find account numbers for cash balance lookup ──────────────────────────
    cash_acct = _find_acct("Cash and Equivalents")
    cash_acct_no = cash_acct["account"] if cash_acct else None

    # ── OPERATING ─────────────────────────────────────────────────────────────
    def _net_income_via_coa(start: date, end: date) -> float:
        return net_income_for_period(db, coa, start, end)

    cur_net_income = _net_income_via_coa(current_start, current_end)
    pri_net_income = _net_income_via_coa(prior_start,   prior_end)
    p2_net_income  = _net_income_via_coa(prior2_start, prior2_end) if prior2_start else None

    # Non-cash expense add-backs
    # Depreciation Expense (6600): debit-normal, already reduced net income, add back
    cur_dep = _named_acct_range("Depreciation Expense", current_start, current_end)
    pri_dep = _named_acct_range("Depreciation Expense", prior_start,   prior_end)
    p2_dep  = _p2(_named_acct_range, "Depreciation Expense", prior2_start, prior2_end)

    # Bad Debt Expense: non-cash add-back (same logic as income statement).
    # Use get_gl_balances_range — identical to how the income statement fetches
    # period activity — keyed by the account_number found in COA.  This avoids
    # any name-matching or account_type-filter issues that caused earlier bugs.
    # Clamp to >= 0: a net-credit period (reversal > new expense) shows zero,
    # not a negative, on the cash flow.
    _bad_debt_acct = _find_acct("Bad Debt Expense")
    _bad_debt_no   = _bad_debt_acct["account"] if _bad_debt_acct else None
    #logger.info(f"CF bad_debt_acct lookup: {_bad_debt_acct}")
    #logger.info(f"CF bad_debt_no: {_bad_debt_no!r}")
    #logger.info("CF COA expense acct_names: %s",
    #            [repr(a["acct_name"]) for a in coa if a["category"] == "Expense"])

    def _bad_debt_period(start: date, end: date) -> float:
        if not _bad_debt_no:
            logger.warning("CF _bad_debt_period: account not found in COA, returning 0.0")
            return 0.0
        bal = get_gl_balances_range(db, start, end)
        raw = bal.get(_bad_debt_no, 0.0)
        # get_gl_balances_range returns debit - credit for Debit-normal accounts.
        # In 2024 the GL has net debit activity (+69,687) so raw > 0.
        # In 2023 the GL has net credit activity (-61,375) — a reversal period —
        # so raw < 0.  The income statement caller negates expense rows for
        # display, so it always shows the magnitude as positive.  The cash flow
        # add-back should likewise always be the magnitude (non-cash charge),
        # so use abs(raw).
        val = abs(raw)
        #logger.info(f"CF _bad_debt_period({start},{end}): raw={raw}, val={val}")
        return val

    cur_bad_debt = _bad_debt_period(current_start, current_end)
    pri_bad_debt = _bad_debt_period(prior_start,   prior_end)
    p2_bad_debt  = _bad_debt_period(prior2_start,  prior2_end) if prior2_start else None
    #logger.info(f"CF bad_debt results: cur={cur_bad_debt}, pri={pri_bad_debt}, p2={p2_bad_debt}")

    # Working capital changes
    # AR change for cash flow purposes requires separating two distinct allowance
    # movements that flow through account 1150 (Allowance for Doubtful Accounts):
    #
    #   1. Allowance BUILD — DR Bad Debt Expense / CR Allowance
    #      This is already captured as a non-cash add-back in cur_bad_debt above.
    #      The credit to 1150 must NOT appear here again or it double-counts.
    #
    #   2. Write-off execution — DR Allowance / CR Gross AR
    #      The debit to 1150 reduces the allowance (but not cash).
    #      The credit to 1100 reduces gross AR (also not cash).
    #      Both legs are non-cash; including only gross AR movement makes the
    #      write-off look like a cash collection.  We must add back the debit
    #      to the allowance to cancel that false inflow.
    #
    # Solution: add only the DEBIT activity on the allowance account (write-offs),
    # not the net change.  Debits to a credit-normal account are the raw debit sum.
    # Credits (allowance build) are already in the bad_debt add-back and must be
    # excluded here to avoid double-counting.
    #
    # Proof for a year with $8,700 write-off and $162,151 bad debt expense:
    #   gross_chg  = -(gross AR dropped by write-off, $8,700 CR)  = +8,700
    #   writeoff_addback = debit to allowance = +8,700
    #   net AR line = +8,700 − 8,700 = 0  ✓  (no cash moved on write-off)
    #   bad_debt add-back = +162,151  ✓  (non-cash expense, counted once above)
    ar_acct    = _find_acct("Accounts Receivable")
    ar_no      = ar_acct["account"] if ar_acct else None
    allow_acct = _find_acct("Allowance for Doubtful Accounts")
    allow_no   = allow_acct["account"] if allow_acct else None

    def _allowance_writeoffs(start: date, end: date) -> float:
        """
        Return the total DEBIT activity on the Allowance account for the period.
        Debits represent write-offs (DR Allowance / CR Gross AR) — a non-cash
        entry that reduces both gross AR and the allowance by equal amounts.
        Credits (from DR Bad Debt Expense / CR Allowance) are excluded because
        the bad_debt add-back line above already captures them.
        """
        if not allow_no:
            return 0.0
        stmt = text("""
            SELECT COALESCE(SUM(gl.debit), 0) AS total_debit
            FROM bookkeeper.general_ledger gl
            WHERE gl.account_number = :acct
              AND gl.entry_date BETWEEN :start AND :end
        """)
        row = db.execute(stmt, {"acct": allow_no, "start": start, "end": end}).fetchone()
        return float(row.total_debit) if row else 0.0

    def _ar_net_chg(start, end):
        gross_chg = -_acct_change(ar_no, start, end) if ar_no else 0.0
        # Add back only the write-off debits on the allowance; these are the
        # non-cash entries that made gross AR appear to shrink without cash flowing.
        writeoff  = _allowance_writeoffs(start, end)
        net = gross_chg - writeoff
        #logger.info(
        #    f"CF _ar_net_chg({start},{end}): gross_chg={gross_chg:.2f}, "
        #    f"writeoff_addback={writeoff:.2f}, net={net:.2f}"
        #)
        return net

    cur_ar_chg = _ar_net_chg(current_start, current_end)
    pri_ar_chg = _ar_net_chg(prior_start,   prior_end)
    p2_ar_chg  = _ar_net_chg(prior2_start,  prior2_end) if prior2_start else None

    # Inventory (1200): Debit-normal asset; increase = cash use (negative)
    inv_acct = _find_acct("Inventory")
    inv_no   = inv_acct["account"] if inv_acct else None
    cur_inv_chg = -_acct_change(inv_no, current_start, current_end) if inv_no else 0.0
    pri_inv_chg = -_acct_change(inv_no, prior_start,   prior_end)   if inv_no else 0.0
    p2_inv_chg  = -_acct_change(inv_no, prior2_start,  prior2_end)  if (inv_no and prior2_start) else None

    # Prepaid Expenses (1300): Debit-normal asset; increase = cash use (negative)
    prepaid_acct = _find_acct("Prepaid Expenses")
    prepaid_no   = prepaid_acct["account"] if prepaid_acct else None
    cur_prepaid_chg = -_acct_change(prepaid_no, current_start, current_end) if prepaid_no else 0.0
    pri_prepaid_chg = -_acct_change(prepaid_no, prior_start,   prior_end)   if prepaid_no else 0.0
    p2_prepaid_chg  = -_acct_change(prepaid_no, prior2_start,  prior2_end)  if (prepaid_no and prior2_start) else None

    # AP (2000): Credit-normal liability; increase = cash source (positive)
    ap_acct = _find_acct("Accounts Payable")
    ap_no   = ap_acct["account"] if ap_acct else None
    cur_ap_chg = _acct_change(ap_no, current_start, current_end) if ap_no else 0.0
    pri_ap_chg = _acct_change(ap_no, prior_start,   prior_end)   if ap_no else 0.0
    p2_ap_chg  = _acct_change(ap_no, prior2_start,  prior2_end)  if (ap_no and prior2_start) else None

    # Accrued Liabilities (2100): Credit-normal liability; increase = cash source (positive)
    accr_acct = _find_acct("Accrued Liabilities")
    accr_no   = accr_acct["account"] if accr_acct else None
    cur_accr_chg = _acct_change(accr_no, current_start, current_end) if accr_no else 0.0
    pri_accr_chg = _acct_change(accr_no, prior_start,   prior_end)   if accr_no else 0.0
    p2_accr_chg  = _acct_change(accr_no, prior2_start,  prior2_end)  if (accr_no and prior2_start) else None

    # Unearned Revenue (2300): Credit-normal liability; increase = cash source (positive)
    unearned_acct = _find_acct("Unearned Revenue")
    unearned_no   = unearned_acct["account"] if unearned_acct else None
    cur_unearned_chg = _acct_change(unearned_no, current_start, current_end) if unearned_no else 0.0
    pri_unearned_chg = _acct_change(unearned_no, prior_start,   prior_end)   if unearned_no else 0.0
    p2_unearned_chg  = _acct_change(unearned_no, prior2_start,  prior2_end)  if (unearned_no and prior2_start) else None

    cur_op_cash = (cur_net_income + cur_dep + cur_bad_debt
                   + cur_ar_chg + cur_inv_chg + cur_prepaid_chg
                   + cur_ap_chg + cur_accr_chg + cur_unearned_chg)
    pri_op_cash = (pri_net_income + pri_dep + pri_bad_debt
                   + pri_ar_chg + pri_inv_chg + pri_prepaid_chg
                   + pri_ap_chg + pri_accr_chg + pri_unearned_chg)
    p2_op_cash  = (
        p2_net_income + p2_dep + p2_bad_debt
        + p2_ar_chg + p2_inv_chg + p2_prepaid_chg
        + p2_ap_chg + p2_accr_chg + p2_unearned_chg
    ) if prior2_start else None

    # ── INVESTING ─────────────────────────────────────────────────────────────
    # PP&E purchases: Debit-normal asset; increase = cash outflow (negative)
    ppe_acct = _find_acct("Property, Plant & Equip")
    ppe_no   = ppe_acct["account"] if ppe_acct else None
    cur_capex = -_acct_change(ppe_no, current_start, current_end) if ppe_no else 0.0
    pri_capex = -_acct_change(ppe_no, prior_start,   prior_end)   if ppe_no else 0.0
    p2_capex  = -_acct_change(ppe_no, prior2_start,  prior2_end)  if (ppe_no and prior2_start) else None

    # Short-Term Investments (acct 1050): Debit-normal asset; increase = cash outflow (negative)
    sti_acct = _find_acct("Short-Term Investments")
    sti_no   = sti_acct["account"] if sti_acct else None
    cur_sti = -_acct_change(sti_no, current_start, current_end) if sti_no else 0.0
    pri_sti = -_acct_change(sti_no, prior_start,   prior_end)   if sti_no else 0.0
    p2_sti  = -_acct_change(sti_no, prior2_start,  prior2_end)  if (sti_no and prior2_start) else None

    cur_inv_total = cur_capex + cur_sti
    pri_inv_total = pri_capex + pri_sti
    p2_inv_total  = ((p2_capex or 0) + (p2_sti or 0)) if prior2_start else None

    # ── FINANCING ─────────────────────────────────────────────────────────────
    # Short-term debt: Credit-normal; net credit = proceeds (positive)
    cur_st_debt = _named_acct_range("Short-term Debt", current_start, current_end)
    pri_st_debt = _named_acct_range("Short-term Debt", prior_start,   prior_end)
    p2_st_debt  = _p2(_named_acct_range, "Short-term Debt", prior2_start, prior2_end)

    # Long-term debt: Credit-normal; net credit = proceeds (positive)
    cur_lt_debt = _named_acct_range("Long-term Debt", current_start, current_end)
    pri_lt_debt = _named_acct_range("Long-term Debt", prior_start,   prior_end)
    p2_lt_debt  = _p2(_named_acct_range, "Long-term Debt", prior2_start, prior2_end)

    # Common stock issuance: Credit-normal equity
    cur_stock = _named_acct_range("Common Stock", current_start, current_end)
    pri_stock = _named_acct_range("Common Stock", prior_start,   prior_end)
    p2_stock  = _p2(_named_acct_range, "Common Stock", prior2_start, prior2_end)

    # Treasury stock: Debit-normal contra equity → _named_acct_range returns positive debit net.
    # Cash outflow → negate.
    cur_treasury = -_named_acct_range("Treasury Stock", current_start, current_end)
    pri_treasury = -_named_acct_range("Treasury Stock", prior_start,   prior_end)
    p2_treasury  = -_p2(_named_acct_range, "Treasury Stock", prior2_start, prior2_end) if prior2_start else None

    # Dividends Paid: Debit-normal contra equity → cash outflow → negate.
    cur_div = -_named_acct_range("Dividends Paid", current_start, current_end)
    pri_div = -_named_acct_range("Dividends Paid", prior_start,   prior_end)
    p2_div  = -_p2(_named_acct_range, "Dividends Paid", prior2_start, prior2_end) if prior2_start else None

    cur_fin_total = cur_st_debt + cur_lt_debt + cur_stock + cur_treasury + (cur_div or 0)
    pri_fin_total = pri_st_debt + pri_lt_debt + pri_stock + pri_treasury + (pri_div or 0)
    p2_fin_total  = (
        (p2_st_debt or 0) + (p2_lt_debt or 0) + (p2_stock or 0) + (p2_treasury or 0) + (p2_div or 0)
    ) if prior2_start else None

    # ── CASH RECONCILIATION ───────────────────────────────────────────────────
    cur_net_cash = cur_op_cash + cur_inv_total + cur_fin_total
    pri_net_cash = pri_op_cash + pri_inv_total + pri_fin_total
    p2_net_cash  = (p2_op_cash + p2_inv_total + p2_fin_total) if prior2_start else None

    def _cash_bal(as_of: date) -> float:
        if not cash_acct_no:
            return 0.0
        return _acct_bal_as_of(cash_acct_no, as_of)

    cur_cash_beg = _cash_bal(current_start - timedelta(days=1))
    cur_cash_end = _cash_bal(current_end)
    pri_cash_beg = _cash_bal(prior_start - timedelta(days=1))
    pri_cash_end = _cash_bal(prior_end)
    p2_cash_beg  = _cash_bal(prior2_start - timedelta(days=1)) if prior2_start else None
    p2_cash_end  = _cash_bal(prior2_end)                       if prior2_start else None

    # Reconciling difference: (cash_end - cash_beg) is the authoritative cash
    # change per the GL.  cur_net_cash is the indirect-method sum of operating,
    # investing, and financing.  Any gap reflects GL transactions that affected
    # cash but are not captured by the indirect-method working capital lines
    # (e.g. direct cash adjustments, unclassified entries in the simulated data).
    cur_reconciling = (cur_cash_end - cur_cash_beg) - cur_net_cash
    pri_reconciling = (pri_cash_end - pri_cash_beg) - pri_net_cash
    p2_reconciling  = ((p2_cash_end - p2_cash_beg) - p2_net_cash) if prior2_start else None

    if abs(cur_reconciling) > 0.005:
        logger.warning(
            f"Cash flow reconciling difference year={current_end.year}: "
            f"{cur_reconciling:,.2f}. Indirect-method sum does not equal GL cash change."
        )
    if abs(pri_reconciling) > 0.005:
        logger.warning(
            f"Cash flow reconciling difference year={prior_end.year}: "
            f"{pri_reconciling:,.2f}. Indirect-method sum does not equal GL cash change."
        )

    def _row(desc, cur, pri, p2=None):
        return CashFlowRow(description=desc, current_period=cur, prior_period=pri, prior2_period=p2)

    return [
        _row("CASH FLOWS FROM OPERATING ACTIVITIES",  0,                0,                0 if prior2_start else None),
        _row("Net Income",                             cur_net_income,   pri_net_income,   p2_net_income),
        _row("Depreciation Expense",                   cur_dep,          pri_dep,          p2_dep),
        _row("Bad Debt Expense",                       cur_bad_debt,     pri_bad_debt,     p2_bad_debt),
        _row("Changes in Accounts Receivable",         cur_ar_chg,       pri_ar_chg,       p2_ar_chg),
        _row("Changes in Inventory",                   cur_inv_chg,      pri_inv_chg,      p2_inv_chg),
        _row("Changes in Prepaid Expenses",            cur_prepaid_chg,  pri_prepaid_chg,  p2_prepaid_chg),
        _row("Changes in Accounts Payable",            cur_ap_chg,       pri_ap_chg,       p2_ap_chg),
        _row("Changes in Accrued Liabilities",         cur_accr_chg,     pri_accr_chg,     p2_accr_chg),
        _row("Changes in Unearned Revenue",            cur_unearned_chg, pri_unearned_chg, p2_unearned_chg),
        _row("Net Cash from Operating Activities",     cur_op_cash,      pri_op_cash,      p2_op_cash),
        _row("CASH FLOWS FROM INVESTING ACTIVITIES",   0,                0,                0 if prior2_start else None),
        _row("Purchase of Short-Term Investments",     cur_sti,          pri_sti,          p2_sti),
        _row("Purchase of Property, Plant & Equip",    cur_capex,        pri_capex,        p2_capex),
        _row("Net Cash from Investing Activities",     cur_inv_total,    pri_inv_total,    p2_inv_total),
        _row("CASH FLOWS FROM FINANCING ACTIVITIES",   0,                0,                0 if prior2_start else None),
        _row("Proceeds from Short-term Debt",          cur_st_debt,      pri_st_debt,      p2_st_debt),
        _row("Proceeds from Long-term Debt",           cur_lt_debt,      pri_lt_debt,      p2_lt_debt),
        _row("Issuance of Common Stock",               cur_stock,        pri_stock,        p2_stock),
        _row("Purchase of Treasury Stock",             cur_treasury,     pri_treasury,     p2_treasury),
        _row("Dividends Paid",                         cur_div,          pri_div,          p2_div),
        _row("Net Cash from Financing Activities",     cur_fin_total,    pri_fin_total,    p2_fin_total),
        _row("Net Increase in Cash",                   cur_net_cash,     pri_net_cash,     p2_net_cash),
        _row("Cash at Beginning of Period",            cur_cash_beg,     pri_cash_beg,     p2_cash_beg),
        _row("Cash at End of Period",                  cur_cash_end,     pri_cash_end,     p2_cash_end),
    ]


# ── Statement of Stockholders' Equity ─────────────────────────────────────────

def _common_stock_balance(db: Session, coa: list[dict], as_of: date) -> float:
    """Cumulative balance of every Equity-category account EXCEPT
    Retained Earnings / Treasury Stock / Dividends Paid — the same
    'Normal equity (Common Stock, ...)' bucket build_balance_sheet
    uses, summed (rather than hardcoding one account number, so this
    stays correct if another normal-equity account is ever added).
    Shared by both build_stockholders_equity() (10-K) and
    build_stockholders_equity_quarterly() (10-Q)."""
    special_equity_nos = {RETAINED_EARNINGS_ACCT, TREASURY_STOCK_ACCT, DIVIDENDS_PAID_ACCT}
    bal = get_gl_balances_cumulative(db, as_of, exclude_account_numbers=list(special_equity_nos))
    return sum(
        bal.get(a["account"], 0.0) for a in coa
        if a["category"] in EQUITY_CATEGORIES and a["account"] not in special_equity_nos
    )


def _treasury_balance(db: Session, as_of: date) -> float:
    """Shared by both build_stockholders_equity() and
    build_stockholders_equity_quarterly()."""
    full = get_gl_balances_cumulative(db, as_of)
    return -full.get(TREASURY_STOCK_ACCT, 0.0)


def _equity_balance_row(db: Session, coa: list[dict], as_of: date) -> "StockholdersEquityRow":
    """
    Build one "Balance as of ..." row. Shared by build_stockholders_equity()
    (10-K, a continuous 3-year chain) and build_stockholders_equity_quarterly()
    (10-Q, four independent rollforward blocks) — the underlying point-in-
    time balance math is identical either way; only the surrounding row
    ORDER/grouping differs between the two callers.
    """
    common   = _common_stock_balance(db, coa, as_of)
    treasury = _treasury_balance(db, as_of)
    retained = _compute_retained_earnings(db, as_of)
    return StockholdersEquityRow(
        description=f"Balance as of {as_of.strftime('%B %d, %Y')}",
        is_balance_row=True,
        common_stock_amount=common,
        treasury_stock=treasury,
        accumulated_oci=0.0,
        retained_earnings=retained,
        total_equity=common + treasury + retained,
    )


def _equity_activity_rows(db: Session, coa: list[dict], period_start: date, period_end: date) -> list["StockholdersEquityRow"]:
    """
    Build the activity rows (Net income, Dividends, stock issuance/
    purchases) between two balance rows for one period. Shared by
    build_stockholders_equity() and build_stockholders_equity_quarterly() —
    see _equity_balance_row()'s docstring for why these are module-level
    rather than duplicated per-caller.
    """
    ni       = net_income_for_period(db, coa, period_start, period_end)
    div      = -_acct_range_by_name(db, "Dividends Paid", period_start, period_end)
    stock    = _acct_range_by_name(db, "Common Stock",    period_start, period_end)
    treasury = -_acct_range_by_name(db, "Treasury Stock", period_start, period_end)

    rows = [StockholdersEquityRow(
        description="Net income", retained_earnings=ni, total_equity=ni,
    )]
    if abs(div) > 0.005:
        rows.append(StockholdersEquityRow(
            description="Dividends declared", retained_earnings=div, total_equity=div,
        ))
    if abs(stock) > 0.005:
        rows.append(StockholdersEquityRow(
            description="Issuance of common stock",
            common_stock_amount=stock, total_equity=stock,
        ))
    if abs(treasury) > 0.005:
        rows.append(StockholdersEquityRow(
            description="Purchase of treasury stock",
            treasury_stock=treasury, total_equity=treasury,
        ))
    return rows


def build_stockholders_equity(
    db: Session,
    prior2_start: date, prior2_end: date,
    prior_start: date, prior_end: date,
    current_start: date, current_end: date,
) -> list[StockholdersEquityRow]:
    """
    Build a 3-year Statement of Stockholders' Equity rollforward for the
    10-K, matching the standard SEC layout (see equity-sample-amzn.xlsx /
    equity-grid-gem.docx): a "Balance as of ..." row, then each year's
    activity rows, repeated for every fiscal year covered — same 3-year
    span as the income statement/cash flow's prior2/prior/current columns.

    Business decisions (Time Flux LLC's stock is "without par value" and
    this app doesn't track stock-based compensation):
      - No Additional Paid-In Capital column at all — for no-par stock,
        GAAP records the full contributed-capital proceeds directly in
        Common Stock, so a separate APIC column would always be 0 and add
        nothing; it's folded into common_stock_amount instead.
      - accumulated_oci is always 0 for now — not tracked anywhere in this
        chart of accounts, but kept as a real column since it may be wired
        up later.
      - No share-count column — this app doesn't track share issuances
        over time and the table doesn't need it.

    Net Income, Dividends, and Common Stock / Treasury Stock activity all
    reuse the SAME shared helpers build_cash_flow() uses
    (net_income_for_period, _acct_range_by_name) rather than a third,
    independently-written computation of the same figures — see
    net_income_for_period's docstring for why that discipline matters.
    Each balance row's Retained Earnings / Common Stock / Treasury Stock
    values come from _compute_retained_earnings / cumulative GL balances,
    so a balance-to-balance change always equals the activity rows between
    them by construction — nothing here needs a separate reconciliation
    check the way the old tax_exp mismatch did.
    """
    coa = get_chart_of_accounts(db)

    beginning_date = prior2_start - timedelta(days=1)
    periods = [
        (prior2_end, (prior2_start,  prior2_end)),
        (prior_end,  (prior_start,   prior_end)),
        (current_end, (current_start, current_end)),
    ]

    rows: list[StockholdersEquityRow] = [_equity_balance_row(db, coa, beginning_date)]
    for as_of, (p_start, p_end) in periods:
        rows.extend(_equity_activity_rows(db, coa, p_start, p_end))
        rows.append(_equity_balance_row(db, coa, as_of))

    return rows


def build_stockholders_equity_quarterly(
    db: Session,
    quarter: int,
    quarter_start: date,      quarter_end: date,
    prior_quarter_start: date, prior_quarter_end: date,
    ytd_start: date,           ytd_end: date,
    prior_ytd_start: date,     prior_ytd_end: date,
) -> list[StockholdersEquityRow]:
    """
    Build the 10-Q's Statement of Stockholders' Equity — see
    equity-10-Q-periods.docx for the exact target layout. Unlike the 10-K's
    single CONTINUOUS 3-year chain (build_stockholders_equity, above,
    where each period's beginning balance IS the previous period's ending
    balance), a 10-Q shows either ONE rollforward (Q1) or FOUR INDEPENDENT
    rollforwards grouped into two labeled sections (Q2/Q3):

        Three Months Ended
            Balance as of <quarter_start - 1 day>     (current year)
            <activity>
            Balance as of <quarter_end>
            <blank row>
            Balance as of <prior_quarter_start - 1 day>  (prior year)
            <activity>
            Balance as of <prior_quarter_end>

        <Six|Nine> Months Ended        (Q2/Q3 ONLY — see below)
            Balance as of <ytd_start - 1 day>          (current year YTD;
                                                          == prior fiscal
                                                          year-end)
            <activity>
            Balance as of <ytd_end>
            <blank row>
            Balance as of <prior_ytd_start - 1 day>    (prior year YTD)
            <activity>
            Balance as of <prior_ytd_end>

    Q1 has NO second section at all. For Q1, ytd_start == quarter_start and
    ytd_end == quarter_end (the first quarter's YTD *is* the quarter), so
    the "YTD" block would be an exact duplicate of the "Three Months Ended"
    block above it, just mislabeled — there previously was no `quarter`
    parameter here at all, so this function always built both sections
    unconditionally and always hardcoded the second one as "Six Months
    Ended", regardless of which quarter was actually being requested.

    Each block's beginning balance is computed fresh (the day before that
    block's own period_start) rather than reused from a neighboring block —
    the current-quarter block and the current-YTD block both end on the
    same calendar date (quarter_end == ytd_end) but start from DIFFERENT
    dates, so they are NOT the same rollforward and must not share rows.

    section_label on the first row of each section drives the "Three
    Months Ended" / "Six Months Ended" / "Nine Months Ended" bold divider
    docx_service.py and StockholdersEquityTable.jsx render above that
    block — see StockholdersEquityRow.section_label.
    """
    YTD_SECTION_LABEL = {2: "Six Months Ended", 3: "Nine Months Ended"}

    coa = get_chart_of_accounts(db)

    def _block(period_start: date, period_end: date) -> list[StockholdersEquityRow]:
        beginning_date = period_start - timedelta(days=1)
        block_rows = [_equity_balance_row(db, coa, beginning_date)]
        block_rows.extend(_equity_activity_rows(db, coa, period_start, period_end))
        block_rows.append(_equity_balance_row(db, coa, period_end))
        return block_rows

    def _section_header(label: str) -> StockholdersEquityRow:
        # A dedicated row rather than a flag on the balance row below it —
        # same convention build_cash_flow() already uses for its own
        # "CASH FLOWS FROM OPERATING ACTIVITIES" section dividers (see
        # xbrl_tagger.py / docx_service.py's _CF_SECTION_DESCS set).
        # docx_service.py / StockholdersEquityTable.jsx should recognize
        # this row the same way: description in a known SECTION_LABELS set,
        # is_balance_row False, every numeric field left at its default.
        return StockholdersEquityRow(description=label)

    blank_row = StockholdersEquityRow(description="")

    rows: list[StockholdersEquityRow] = []

    rows.append(_section_header("Three Months Ended"))
    rows.extend(_block(quarter_start, quarter_end))
    rows.append(blank_row)
    rows.extend(_block(prior_quarter_start, prior_quarter_end))

    ytd_label = YTD_SECTION_LABEL.get(quarter)
    if ytd_label:
        rows.append(blank_row)
        rows.append(_section_header(ytd_label))
        rows.extend(_block(ytd_start, ytd_end))
        rows.append(blank_row)
        rows.extend(_block(prior_ytd_start, prior_ytd_end))

    return rows


# ── Balance sheet totals ──────────────────────────────────────────────────────

def balance_sheet_totals(rows: list) -> dict[str, float]:
    """
    Summarise a balance sheet row list into total assets, liabilities, equity,
    and a balance check.  Returns a dict the MD&A prompt builder can embed
    directly so the LLM always receives verified totals.

    Assets, liabilities, and equity values are already sign-corrected (positive
    means the account has value / the obligation exists).
    The accounting equation: total_assets == total_liabilities + total_equity
    """
    total_assets      = 0.0
    total_liabilities = 0.0
    total_equity      = 0.0

    for row in rows:
        cat = row.category
        val = row.current_period
        if cat in ASSET_CATEGORIES:
            total_assets      += val
        elif cat in LIABILITY_CATEGORIES:
            total_liabilities += val
        elif cat in EQUITY_CATEGORIES:
            total_equity      += val

    difference = total_assets - (total_liabilities + total_equity)
    balanced   = abs(difference) < 0.02   # $0.02 rounding tolerance

    #logger.info(
    #    f"balance_sheet_totals: assets={total_assets:.2f}, "
    #    f"liabilities={total_liabilities:.2f}, equity={total_equity:.2f}, "
    #    f"difference={difference:.2f}, balanced={balanced}"
    #)
    if not balanced:
        logger.warning(f"BALANCE SHEET DOES NOT BALANCE: difference={difference:.2f}")

    return {
        "total_assets":      round(total_assets,      2),
        "total_liabilities": round(total_liabilities, 2),
        "total_equity":      round(total_equity,      2),
        "difference":        round(difference,        2),
        "balanced":          balanced,
    }


# ── Public entry points ───────────────────────────────────────────────────────

def get_quarterly_statements(db: Session, year: int, quarter: int):
    """Get 10-Q financial statements for a given year and quarter."""
    q_start, q_end       = _quarter_dates(year,     quarter)
    prior_q_start, prior_q_end = _quarter_dates(year - 1, quarter)
    year_start           = date(year,     1, 1)
    prior_year_start     = date(year - 1, 1, 1)
    prior_year_end       = date(year - 1, 12, 31)

    balance_sheet = build_balance_sheet(
        db,
        current_start=year_start, current_end=q_end,
        prior_start=prior_year_start, prior_end=prior_year_end,
    )
    income_stmt = build_income_statement(
        db,
        current_start=q_start,        current_end=q_end,
        prior_start=prior_q_start,    prior_end=prior_q_end,
        ytd_start=year_start,          ytd_end=q_end,
        ytd_prior_start=prior_year_start, ytd_prior_end=prior_q_end,
    )
    cash_flow = build_cash_flow(
        db,
        current_start=year_start,      current_end=q_end,
        prior_start=prior_year_start,  prior_end=prior_q_end,
    )
    stockholders_equity = build_stockholders_equity_quarterly(
        db,
        quarter=quarter,
        quarter_start=q_start,             quarter_end=q_end,
        prior_quarter_start=prior_q_start, prior_quarter_end=prior_q_end,
        ytd_start=year_start,              ytd_end=q_end,
        prior_ytd_start=prior_year_start,  prior_ytd_end=prior_q_end,
    )

    q_labels  = {1: "March 31",    2: "June 30",    3: "September 30"}
    # period_label / prior_label always describe the QUARTER (three months),
    # not the YTD period.  The income statement component builds the YTD column
    # headers separately using qMonths[quarter] on the frontend.
    period_label   = f"Three Months Ended\n {q_labels[quarter]}, {year}"
    prior_label    = f"Three Months Ended\n {q_labels[quarter]}, {year - 1}"
    bs_prior_label = f"December 31, {year - 1}"

    dates = {
        "quarter_start":      q_start.isoformat(),
        "quarter_end":        q_end.isoformat(),
        "year_start":         year_start.isoformat(),
        "prior_quarter_start": prior_q_start.isoformat(),
        "prior_quarter_end":  prior_q_end.isoformat(),
        "prior_year_start":   prior_year_start.isoformat(),
        "prior_year_end":     prior_year_end.isoformat(),
    }

    balance_sheet_totals(balance_sheet)   # logs balance check
    return (balance_sheet, income_stmt, cash_flow, stockholders_equity,
            period_label, prior_label, bs_prior_label, dates)


def get_annual_statements(db: Session, year: int):
    """Get 10-K financial statements for a given year (three-year income & cash flow)."""
    curr_start,   curr_end   = _year_dates(year)
    prior_start,  prior_end  = _year_dates(year - 1)
    prior2_start, prior2_end = _year_dates(year - 2)

    balance_sheet = build_balance_sheet(db, curr_start, curr_end, prior_start, prior_end)
    income_stmt   = build_income_statement(
        db,
        curr_start,  curr_end,
        prior_start, prior_end,
        prior2_start=prior2_start, prior2_end=prior2_end,
    )
    cash_flow = build_cash_flow(
        db,
        curr_start,  curr_end,
        prior_start, prior_end,
        prior2_start=prior2_start, prior2_end=prior2_end,
    )
    stockholders_equity = build_stockholders_equity(
        db,
        prior2_start=prior2_start, prior2_end=prior2_end,
        prior_start=prior_start,   prior_end=prior_end,
        current_start=curr_start,  current_end=curr_end,
    )

    period_label = f"Year ended\n December 31, {year}"
    prior_label  = f"Year ended\n December 31, {year - 1}"
    prior2_label = f"Year ended\n December 31, {year - 2}"

    balance_sheet_totals(balance_sheet)   # logs balance check
    return balance_sheet, income_stmt, cash_flow, stockholders_equity, period_label, prior_label, prior2_label
