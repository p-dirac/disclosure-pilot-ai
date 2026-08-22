"""
Backend test suite.
Uses pytest + SQLite in-memory database (no Postgres required).
Run with:  pytest test_backend.py -v
"""
import os
import tempfile

import pytest
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

# ── Test environment overrides for app.core.config.Settings ─────────────────
# make_test_app() (further down) does `from app.main import app`, which pulls
# in app.core.config.settings via app.api.auth -> app.core.database ->
# app.core.config. Settings has 16 required fields with no defaults
# (OLLAMA_MODEL plus every WIN_*/DATA_10K_*/DATA_10Q_*/REPORTS_DIR path
# field) that are normally supplied by a real .env file - which doesn't
# exist in a bare/CI test run, so importing app.main used to raise
# "16 validation errors for Settings".
#
# These are now forced overrides (os.environ[key] = ...), not
# setdefault() - a setdefault() approach still let a REAL .env file's
# values win for fields that already had a class-level default (pydantic-
# settings' precedence is env vars > .env file > class default), which is
# exactly what broke DATABASE_URL here: config.py's default is a valid
# "postgresql://..." URL, but a real backend/.env has "DATABASE_URL=
# localhost" - just a bare hostname - which app.core.database's
# module-level `create_engine(db_url, ...)` (runs at IMPORT time, so it
# fires from importing app.main regardless of the SQLite session override
# these tests inject) can't parse, raising
# "Could not parse SQLAlchemy URL from string 'localhost'".
#
# None of these tests read/write the DATA_*/WIN_*/REPORTS_DIR filesystem
# paths or make a real DB/Ollama connection (DATABASE_URL below is never
# actually connected to - create_engine() only needs it to be a
# syntactically valid URL at import time; the tests' `db` fixture replaces
# it via dependency-injection). Forcing every field here makes this suite
# hermetic: identical results regardless of what's in any real .env.
_TEST_TMP_DIR = os.path.join(tempfile.gettempdir(), "disclosure-pilot-ai-tests")
for _key, _val in {
    # sqlite, not postgresql - app.core.database's module-level
    # create_engine(db_url, ...) needs the DBAPI driver importable at
    # IMPORT time even though it's never actually connected to (these
    # tests inject their own SQLite session via dependency override).
    # postgresql:// requires psycopg2 to be installed; sqlite3 is stdlib,
    # so this has zero extra dependencies regardless of what venv runs
    # these tests.
    "DATABASE_URL":        "sqlite:///:memory:",
    "OLLAMA_BASE_URL":     "http://127.0.0.1:11434",
    "OLLAMA_MODEL":        "test-model",
    "SECRET_KEY":          "test-secret-key",
    "ALGORITHM":           "HS256",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "480",
    "WIN_APPIO_DIR":       _TEST_TMP_DIR,
    "DATA_USER_INPUT_DIR": os.path.join(_TEST_TMP_DIR, "user-input"),
    "DATA_10K_DIR":        os.path.join(_TEST_TMP_DIR, "sec10K"),
    "DATA_10K_INTRO":      os.path.join(_TEST_TMP_DIR, "sec10K", "intro"),
    "DATA_10K_PART1":      os.path.join(_TEST_TMP_DIR, "sec10K", "part1"),
    "DATA_10K_PART2":      os.path.join(_TEST_TMP_DIR, "sec10K", "part2"),
    "DATA_10K_PART3":      os.path.join(_TEST_TMP_DIR, "sec10K", "part3"),
    "DATA_10K_PART4":      os.path.join(_TEST_TMP_DIR, "sec10K", "part4"),
    "DATA_10K_SIGS":       os.path.join(_TEST_TMP_DIR, "sec10K", "sigs"),
    # BASE_10Q is a PREFIX, not a directory - _q10_base(quarter) in
    # docx_service.py does `settings.BASE_10Q + str(quarter)`, then joins
    # DATA_10Q_* (short relative names, not full paths) onto that.
    "BASE_10Q":            os.path.join(_TEST_TMP_DIR, "sec10Q-q"),
    "DATA_10Q_INTRO":      "intro",
    "DATA_10Q_PART1":      "part1",
    "DATA_10Q_PART2":      "part2",
    "DATA_10Q_SIGS":       "sigs",
    "REPORTS_DIR":         os.path.join(_TEST_TMP_DIR, "reports"),
}.items():
    os.environ[_key] = _val

# ── SQLite in-memory engine with bookkeeper + applogins schemas ───────────────
# SQLite does not support schemas, so we use prefixed table names.
# StaticPool ensures every connection (including Session) shares the same
# single in-memory database — without it each new connection gets a fresh
# empty DB and the seeded tables are invisible.

@pytest.fixture(scope="session")
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.connect() as conn:
        # SQLite: emulate schemas by using prefixed table names and views
        conn.execute(text("PRAGMA journal_mode=WAL"))

        # ── bookkeeper tables ─────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE bookkeeper_chart_of_acct (
                account_number TEXT PRIMARY KEY,
                account_name   TEXT,
                account_type   TEXT,
                account_subtype TEXT,
                normal_balance  TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE bookkeeper_general_ledger (
                line_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_date     TEXT,
                account_number TEXT,
                debit          REAL DEFAULT 0,
                credit         REAL DEFAULT 0,
                currency       TEXT DEFAULT 'USD',
                description    TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE bookkeeper_depreciation (
                depreciation_id       TEXT PRIMARY KEY,
                asset_id              TEXT,
                fiscal_year           INTEGER,
                fiscal_period         INTEGER DEFAULT 12,
                period_depreciation   REAL DEFAULT 0,
                accumulated_thru_period REAL DEFAULT 0,
                currency              TEXT DEFAULT 'USD'
            )
        """))
        conn.execute(text("""
            CREATE TABLE bookkeeper_accounts_payable (
                ap_id        TEXT PRIMARY KEY,
                vendor_id    TEXT,
                invoice_date TEXT,
                due_date     TEXT,
                amount       REAL,
                status       TEXT,
                currency     TEXT DEFAULT 'USD'
            )
        """))
        conn.execute(text("""
            CREATE TABLE bookkeeper_accounts_receivable (
                ar_id        TEXT PRIMARY KEY,
                customer_id  TEXT,
                invoice_date TEXT,
                due_date     TEXT,
                amount       REAL,
                status       TEXT,
                currency     TEXT DEFAULT 'USD'
            )
        """))

        # ── applogins table ───────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE applogins_app_users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_active     INTEGER DEFAULT 1,
                password_date TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # ── Seed chart of accounts ────────────────────────────────────────
        # account_number, account_name, account_type, account_subtype, normal_balance
        # account_number must be strings (VARCHAR in real DB).
        # Dividends Paid MUST be "3300" to match DIVIDENDS_PAID_ACCT constant in financial_service.py.
        # Treasury Stock MUST be "3200" to match TREASURY_STOCK_ACCT constant.
        # Retained Earnings MUST be "3100" to match RETAINED_EARNINGS_ACCT constant.
        coa_rows = [
            ("1000", "Cash and Equivalents",    "Asset",     "Current Asset",     "Debit"),
            ("1100", "Accounts Receivable",     "Asset",     "Current Asset",     "Debit"),
            ("1150", "Allowance for Doubtful",  "Asset",     "Contra Asset",      "Credit"),
            ("1200", "Inventory",               "Asset",     "Current Asset",     "Debit"),
            ("1500", "Property, Plant & Equip", "Asset",     "Fixed Asset",       "Debit"),
            ("1600", "Accumulated Depreciation","Asset",     "Contra Asset",      "Credit"),
            ("2000", "Accounts Payable",        "Liability", "Current Liability", "Credit"),
            ("2100", "Accrued Liabilities",     "Liability", "Current Liability", "Credit"),
            ("2200", "Short-term Debt",         "Liability", "Current Liability", "Credit"),
            ("3000", "Common Stock",            "Equity",    "Equity",            "Credit"),
            ("3100", "Retained Earnings",       "Equity",    "Retained Earnings", "Credit"),
            ("3200", "Treasury Stock",          "Equity",    "Contra Equity",     "Debit"),
            ("3300", "Dividends Paid",          "Equity",    "Contra Equity",     "Debit"),
            ("4000", "Product Revenue",         "Revenue",   "Revenue",           "Credit"),
            ("4100", "Service Revenue",         "Revenue",   "Revenue",           "Credit"),
            ("5000", "Cost of Goods Sold",      "Expense",   "COGS",              "Debit"),
            ("6000", "Salaries and Wages",      "Expense",   "Operating Expense", "Debit"),
            ("6500", "Depreciation Expense",    "Expense",   "Operating Expense", "Debit"),
        ]
        for row in coa_rows:
            conn.execute(text(
                "INSERT INTO bookkeeper_chart_of_acct"
                " VALUES (:num,:name,:type,:sub,:nb)"
            ), {"num": row[0], "name": row[1], "type": row[2],
                "sub": row[3], "nb": row[4]})

        # ── Seed GL: opening RE balance (2022-12-31) ──────────────────────
        conn.execute(text("""
            INSERT INTO bookkeeper_general_ledger
            (entry_date, account_number, debit, credit, currency, description) VALUES
            ('2022-12-31','3100',0,15000000,'USD','Opening RE')
        """))

        # ── Seed GL: 2023 full-year activity ─────────────────────────────
        gl_2023 = [
            # Revenue
            ("GL001","2023-06-30",4000,"C",0,      600000,"Product sales","USD"),
            ("GL001","2023-06-30",1000,"D",600000, 0,     "Product sales","USD"),
            ("GL002","2023-06-30",4100,"C",0,      400000,"Service rev",  "USD"),
            ("GL002","2023-06-30",1000,"D",400000, 0,     "Service rev",  "USD"),
            # Expense
            ("GL003","2023-06-30",5000,"D",300000, 0,     "COGS",         "USD"),
            ("GL003","2023-06-30",2000,"C",0,      300000,"COGS",         "USD"),
            ("GL004","2023-06-30",6000,"D",200000, 0,     "Salaries",     "USD"),
            ("GL004","2023-06-30",1000,"C",0,      200000,"Salaries",     "USD"),
        ]
        for r in gl_2023:
            conn.execute(text(
                "INSERT INTO bookkeeper_general_ledger"
                " (entry_date, account_number, debit, credit, description, currency)"
                " VALUES (:date,:acct,:debit,:credit,:desc,:curr)"
            ), {"date": r[1], "acct": str(r[2]), "debit": r[4],
                "credit": r[5], "desc": r[6], "curr": r[7]})

        # ── Seed GL: 2024 Q1 activity ─────────────────────────────────────
        gl_2024_q1 = [
            ("GL010","2024-01-31",4000,"C",0,      200000,"Product Q1","USD"),
            ("GL010","2024-01-31",1000,"D",200000, 0,     "Product Q1","USD"),
            ("GL011","2024-01-31",4100,"C",0,      100000,"Service Q1","USD"),
            ("GL011","2024-01-31",1000,"D",100000, 0,     "Service Q1","USD"),
            ("GL012","2024-01-31",5000,"D",80000,  0,     "COGS Q1",  "USD"),
            ("GL012","2024-01-31",2000,"C",0,      80000, "COGS Q1",  "USD"),
            ("GL013","2024-01-31",6000,"D",50000,  0,     "Salary Q1","USD"),
            ("GL013","2024-01-31",1000,"C",0,      50000, "Salary Q1","USD"),
            # Dividends (account 3300 = DIVIDENDS_PAID_ACCT constant in financial_service.py)
            ("GL014","2024-03-31",3300,"D",25000,  0,     "Dividend", "USD"),
            ("GL014","2024-03-31",1000,"C",0,      25000, "Dividend", "USD"),
        ]
        for r in gl_2024_q1:
            conn.execute(text(
                "INSERT INTO bookkeeper_general_ledger"
                " (entry_date, account_number, debit, credit, description, currency)"
                " VALUES (:date,:acct,:debit,:credit,:desc,:curr)"
            ), {"date": r[1], "acct": str(r[2]), "debit": r[4],
                "credit": r[5], "desc": r[6], "curr": r[7]})

        conn.commit()
    return eng


@pytest.fixture
def db(engine):
    """Provide a Session that rewrites schema-qualified table names for SQLite."""
    with Session(engine) as session:
        # Monkey-patch execute to rewrite schema-qualified names
        original_execute = session.execute

        def patched_execute(stmt, params=None, **kwargs):
            from sqlalchemy.sql.elements import TextClause
            from sqlalchemy import text as sa_text
            if isinstance(stmt, TextClause):
                sql = stmt.text
            elif isinstance(stmt, str):
                sql = stmt
            elif hasattr(stmt, "text"):
                sql = stmt.text
            else:
                # Compile non-text statements (e.g. select()) to a SQL string
                sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
            sql = (sql
                   .replace("bookkeeper.chart_of_acct", "bookkeeper_chart_of_acct")
                   .replace("bookkeeper.general_ledger", "bookkeeper_general_ledger")
                   .replace("bookkeeper.depreciation",  "bookkeeper_depreciation")
                   .replace("bookkeeper.accounts_payable",    "bookkeeper_accounts_payable")
                   .replace("bookkeeper.accounts_receivable", "bookkeeper_accounts_receivable")
                   .replace("applogins.app_users",      "applogins_app_users"))
            # SQLite dialect fixes applied to every query:
            # 1. ILIKE  -> LIKE        (SQLite has no ILIKE)
            # 2. != ALL(:x) -> NOT IN  (SQLite has no ALL operator)
            # 3. Expand list params    (SQLite cannot bind a list as one param)
            import re

            sql = sql.replace("ILIKE", "LIKE")

            # Step 1: rewrite != ALL(:paramname) -> NOT IN (:paramname)
            # Use a lambda so the replacement is built from the match object,
            # never from a regex back-reference string (which corrupts to \x01).
            sql = re.sub(
                r"!=\s*ALL\(:([^)]+)\)",
                lambda m: "NOT IN (:" + m.group(1) + ")",
                sql,
            )
            # = ANY(:param) -> IN (:param)  (SQLite has no ANY operator)
            sql = re.sub(
                r"=\s*ANY\(:([^)]+)\)",
                lambda m: "IN (:" + m.group(1) + ")",
                sql,
            )

            # Step 2: expand any list/tuple param into individual placeholders
            if params and isinstance(params, dict):
                new_params = {}
                for key, val in list(params.items()):
                    if isinstance(val, (list, tuple)):
                        # Expand both NOT IN (:key) and IN (:key)
                        not_in_pat = re.compile(
                            r"NOT IN\s*\(:" + re.escape(key) + r"\)"
                        )
                        in_pat = re.compile(
                            r"(?<!NOT )IN\s*\(:" + re.escape(key) + r"\)"
                        )
                        if len(val) == 0:
                            sql = not_in_pat.sub("NOT IN (NULL)", sql)
                            sql = in_pat.sub("IN (NULL)", sql)
                        else:
                            ph = ", ".join(
                                ":" + key + "_" + str(i) for i in range(len(val))
                            )
                            sql = not_in_pat.sub("NOT IN (" + ph + ")", sql)
                            sql = in_pat.sub("IN (" + ph + ")", sql)
                            for i, item in enumerate(val):
                                new_params[key + "_" + str(i)] = item
                    else:
                        new_params[key] = val
                params = new_params
            return original_execute(text(sql), params, **kwargs)

        session.execute = patched_execute
        yield session


# ══════════════════════════════════════════════════════════════════════════════
# _quarter_dates
# ══════════════════════════════════════════════════════════════════════════════

from app.services.financial_service import _quarter_dates, _year_dates

class TestQuarterDates:
    def test_q1(self):
        start, end = _quarter_dates(2024, 1)
        assert start == date(2024, 1, 1)
        assert end   == date(2024, 3, 31)

    def test_q2(self):
        start, end = _quarter_dates(2024, 2)
        assert start == date(2024, 4, 1)
        assert end   == date(2024, 6, 30)

    def test_q3(self):
        start, end = _quarter_dates(2024, 3)
        assert start == date(2024, 7, 1)
        assert end   == date(2024, 9, 30)

    def test_leap_year_q1(self):
        start, end = _quarter_dates(2024, 1)
        assert end == date(2024, 3, 31)

    def test_year_dates(self):
        start, end = _year_dates(2023)
        assert start == date(2023, 1, 1)
        assert end   == date(2023, 12, 31)


# ══════════════════════════════════════════════════════════════════════════════
# get_chart_of_accounts
# ══════════════════════════════════════════════════════════════════════════════

from app.services.financial_service import get_chart_of_accounts

class TestGetChartOfAccounts:
    def test_returns_all_accounts(self, db):
        coa = get_chart_of_accounts(db)
        assert len(coa) == 18  # matches seed rows (18 accounts after adding 1150)

    def test_fields_present(self, db):
        coa = get_chart_of_accounts(db)
        first = coa[0]
        assert "account" in first
        assert "acct_name" in first
        assert "category" in first
        assert "acct_subtype" in first

    def test_ordered_by_acct_id(self, db):
        coa = get_chart_of_accounts(db)
        ids = [r["account"] for r in coa]
        # account is a string (VARCHAR); sort lexicographically matches numeric order
        # for zero-padded strings, but our accounts are 4-char strings like "1000"
        assert ids == sorted(ids, key=lambda x: int(x))

    def test_category_mapping(self, db):
        coa = get_chart_of_accounts(db)
        cash = next(r for r in coa if r["acct_name"] == "Cash and Equivalents")
        assert cash["category"] == "Asset"

    def test_revenue_accounts_present(self, db):
        coa = get_chart_of_accounts(db)
        rev = [r for r in coa if r["category"] == "Revenue"]
        assert len(rev) == 2


# ══════════════════════════════════════════════════════════════════════════════
# get_gl_balances_cumulative
# ══════════════════════════════════════════════════════════════════════════════

from app.services.financial_service import get_gl_balances_cumulative

class TestGlBalancesCumulative:
    def test_cash_balance_after_q1(self, db):
        bals = get_gl_balances_cumulative(db, date(2024, 3, 31))
        # Cash acct 1000: debits = 600k+400k+200k+100k = 1300k (2023)
        #                          + 200k+100k (2024 Q1 rev) = 300k
        #                 credits = 200k+50k+25k (2024 Q1 exp+div) = 275k
        #                 + 2023 credits = 200k (salaries)
        # Just verify it's a positive debit-normal balance
        assert bals.get("1000", 0) > 0

    def test_excludes_retained_earnings(self, db):
        # Exclude by account_number string (matches RETAINED_EARNINGS_ACCT constant)
        bals = get_gl_balances_cumulative(db, date(2024, 3, 31),
                                          exclude_account_numbers=["3100"])
        assert "3100" not in bals

    def test_excludes_dividends(self, db):
        # Exclude by account_number string (matches DIVIDENDS_PAID_ACCT constant)
        bals = get_gl_balances_cumulative(db, date(2024, 3, 31),
                                          exclude_account_numbers=["3300"])
        assert "3300" not in bals

    def test_no_future_entries(self, db):
        bals_q1 = get_gl_balances_cumulative(db, date(2024, 3, 31))
        bals_before = get_gl_balances_cumulative(db, date(2023, 12, 31))
        # 2024 Q1 revenue credited to acct 4000 shouldn't appear in 2023 totals
        assert bals_before.get("4000", 0) != bals_q1.get("4000", 0)


# ══════════════════════════════════════════════════════════════════════════════
# _compute_retained_earnings
# ══════════════════════════════════════════════════════════════════════════════

from app.services.financial_service import _compute_retained_earnings

class TestComputeRetainedEarnings:
    def test_includes_opening_balance(self, db):
        re = _compute_retained_earnings(db, date(2022, 12, 31))
        assert re == 15_000_000.0

    def test_adds_net_income(self, db):
        # 2023: revenue=1000000, expenses=500000, dividends=0
        re = _compute_retained_earnings(db, date(2023, 12, 31))
        assert re == pytest.approx(15_000_000 + 1_000_000 - 500_000)

    def test_subtracts_dividends(self, db):
        # 2024 Q1: revenue=300000, expenses=130000, dividends=25000
        re = _compute_retained_earnings(db, date(2024, 3, 31))
        expected = 15_000_000 + 1_000_000 - 500_000 + 300_000 - 130_000 - 25_000
        assert re == pytest.approx(expected)

    def test_prior_period_less_than_current(self, db):
        re_prior   = _compute_retained_earnings(db, date(2023, 12, 31))
        re_current = _compute_retained_earnings(db, date(2024, 3, 31))
        assert re_current > re_prior


# ══════════════════════════════════════════════════════════════════════════════
# build_balance_sheet
# ══════════════════════════════════════════════════════════════════════════════

from app.services.financial_service import build_balance_sheet

class TestBuildBalanceSheet:
    def test_returns_list_of_rows(self, db):
        rows = build_balance_sheet(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 12, 31),
        )
        assert isinstance(rows, list)
        assert len(rows) > 0

    def test_no_dividends_row(self, db):
        rows = build_balance_sheet(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 12, 31),
        )
        names = [r.acct_name for r in rows]
        assert "Dividends Paid" not in names

    def test_no_retained_earnings_double_count(self, db):
        rows = build_balance_sheet(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 12, 31),
        )
        re_rows = [r for r in rows if "retained earnings" in r.acct_name.lower()]
        assert len(re_rows) == 1

    def test_retained_earnings_value(self, db):
        rows = build_balance_sheet(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 12, 31),
        )
        re = next(r for r in rows if "retained earnings" in r.acct_name.lower())
        expected = _compute_retained_earnings(db, date(2024, 3, 31))
        assert re.current_period == pytest.approx(expected)

    def test_has_asset_liability_equity_rows(self, db):
        rows = build_balance_sheet(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 12, 31),
        )
        cats = {r.category for r in rows}
        assert "Asset" in cats
        assert "Liability" in cats
        assert "Equity" in cats


# ══════════════════════════════════════════════════════════════════════════════
# build_income_statement
# ══════════════════════════════════════════════════════════════════════════════

from app.services.financial_service import build_income_statement

class TestBuildIncomeStatement:
    def test_returns_revenue_and_expense_rows(self, db):
        rows = build_income_statement(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 3, 31),
        )
        cats = {r.category for r in rows}
        assert "Revenue" in cats
        assert "Expense" in cats

    def test_revenue_positive(self, db):
        rows = build_income_statement(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 3, 31),
        )
        rev_rows = [r for r in rows if r.category == "Revenue"]
        assert all(r.current_period >= 0 for r in rev_rows)

    def test_ytd_columns_populated(self, db):
        rows = build_income_statement(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 3, 31),
            ytd_start=date(2024, 1, 1), ytd_end=date(2024, 3, 31),
            ytd_prior_start=date(2023, 1, 1), ytd_prior_end=date(2023, 3, 31),
        )
        assert all(r.ytd_current is not None for r in rows)
        assert all(r.ytd_prior is not None for r in rows)

    def test_ytd_none_when_not_requested(self, db):
        rows = build_income_statement(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 3, 31),
        )
        assert all(r.ytd_current is None for r in rows)


# ══════════════════════════════════════════════════════════════════════════════
# build_cash_flow
# ══════════════════════════════════════════════════════════════════════════════

from app.services.financial_service import build_cash_flow

class TestBuildCashFlow:
    def _get_row(self, rows, description):
        return next((r for r in rows if r.description == description), None)

    def test_returns_all_sections(self, db):
        rows = build_cash_flow(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 3, 31),
        )
        descs = [r.description for r in rows]
        assert "CASH FLOWS FROM OPERATING ACTIVITIES" in descs
        assert "CASH FLOWS FROM INVESTING ACTIVITIES" in descs
        assert "CASH FLOWS FROM FINANCING ACTIVITIES" in descs
        assert "Net Increase in Cash"                 in descs
        assert "Cash at Beginning of Period"          in descs
        assert "Cash at End of Period"                in descs

    def test_net_income_positive(self, db):
        rows = build_cash_flow(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 3, 31),
        )
        ni = self._get_row(rows, "Net Income")
        assert ni is not None
        assert ni.current_period > 0  # revenue > expenses in seed data

    def test_dividends_negative(self, db):
        rows = build_cash_flow(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 3, 31),
        )
        div = self._get_row(rows, "Dividends Paid")
        assert div is not None
        assert div.current_period <= 0  # dividends are a cash outflow

    def test_cash_end_equals_begin_plus_net(self, db):
        rows = build_cash_flow(
            db,
            date(2024, 1, 1), date(2024, 3, 31),
            date(2023, 1, 1), date(2023, 3, 31),
        )
        beg = self._get_row(rows, "Cash at Beginning of Period").current_period
        end = self._get_row(rows, "Cash at End of Period").current_period
        net = self._get_row(rows, "Net Increase in Cash").current_period
        assert end == pytest.approx(beg + net)


# ══════════════════════════════════════════════════════════════════════════════
# get_quarterly_statements
# ══════════════════════════════════════════════════════════════════════════════

from app.services.financial_service import get_quarterly_statements

class TestGetQuarterlyStatements:
    def test_returns_eight_values(self, db):
        # balance_sheet, income_stmt, cash_flow, stockholders_equity,
        # period_label, prior_label, bs_prior_label, dates
        result = get_quarterly_statements(db, 2024, 1)
        assert len(result) == 8

    def test_period_label_format(self, db):
        *_, period_label, prior_label, bs_prior_label, dates = \
            get_quarterly_statements(db, 2024, 1)
        assert "March 31" in period_label
        assert "2024" in period_label

    def test_bs_prior_label_is_year_end(self, db):
        *_, period_label, prior_label, bs_prior_label, dates = \
            get_quarterly_statements(db, 2024, 1)
        assert "December 31" in bs_prior_label
        assert "2023" in bs_prior_label

    def test_prior_label_is_quarterly(self, db):
        *_, period_label, prior_label, bs_prior_label, dates = \
            get_quarterly_statements(db, 2024, 1)
        assert "March 31" in prior_label
        assert "2023" in prior_label

    def test_dates_dict_keys(self, db):
        *_, dates = get_quarterly_statements(db, 2024, 1)
        for key in ("quarter_start", "quarter_end", "year_start",
                    "prior_quarter_start", "prior_quarter_end",
                    "prior_year_start", "prior_year_end"):
            assert key in dates

    def test_invalid_quarter_raises(self, db):
        # Quarter 4 is not valid for 10-Q (annual only)
        # The API layer rejects it; service will still run but dates will be wrong.
        # Confirm Q1-Q3 all succeed without error.
        for q in (1, 2, 3):
            result = get_quarterly_statements(db, 2024, q)
            assert len(result) == 8


# ══════════════════════════════════════════════════════════════════════════════
# API endpoint tests (FastAPI TestClient)
# ══════════════════════════════════════════════════════════════════════════════

from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
import json

# We import the app and override the DB dependency + auth dependency.

def make_test_app(db_session):
    from app.main import app
    from app.core.database import get_db
    from app.api.auth import get_current_user
    from app.models.user import AppUser

    fake_user = AppUser(id=1, email="test@example.com",
                        is_active=True, password_hash="x")

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return TestClient(app)


class TestReport10QEndpoints:
    def test_financials_endpoint_200(self, db):
        client = make_test_app(db)
        resp = client.post("/api/10q/financials",
                           json={"year": 2024, "quarter": 1})
        assert resp.status_code == 200

    def test_financials_response_has_balance_sheet(self, db):
        client = make_test_app(db)
        resp = client.post("/api/10q/financials",
                           json={"year": 2024, "quarter": 1})
        data = resp.json()
        assert "balance_sheet" in data
        assert len(data["balance_sheet"]) > 0

    def test_financials_response_has_labels(self, db):
        client = make_test_app(db)
        resp = client.post("/api/10q/financials",
                           json={"year": 2024, "quarter": 1})
        data = resp.json()
        assert "period_label" in data
        assert "prior_label"  in data
        assert "bs_prior_label" in data

    def test_financials_invalid_quarter_rejected(self, db):
        client = make_test_app(db)
        resp = client.post("/api/10q/financials",
                           json={"year": 2024, "quarter": 4})
        assert resp.status_code == 400

    def test_financials_requires_auth(self, db):
        from app.main import app
        from app.core.database import get_db
        from app.api.auth import get_current_user
        # Remove BOTH overrides so the real auth dependency runs
        saved_db   = app.dependency_overrides.pop(get_db, None)
        saved_auth = app.dependency_overrides.pop(get_current_user, None)
        try:
            client = TestClient(app)
            resp = client.post("/api/10q/financials",
                               json={"year": 2024, "quarter": 1})
            assert resp.status_code in (401, 403)
        finally:
            # Restore overrides so other tests are not affected
            if saved_db is not None:
                app.dependency_overrides[get_db] = saved_db
            if saved_auth is not None:
                app.dependency_overrides[get_current_user] = saved_auth


# ══════════════════════════════════════════════════════════════════════════════
# Auth / user management tests
# ══════════════════════════════════════════════════════════════════════════════

from passlib.context import CryptContext

# Use sha256_crypt in tests — bcrypt triggers a passlib wrap-bug detection
# routine that hashes a 214-byte string internally, which newer bcrypt libs
# reject with "password cannot be longer than 72 bytes". sha256_crypt has no
# length limit and is faster in CI. The real app still uses bcrypt via
# app.core.security; this context is only for test-side hashing helpers.
pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")

class TestAuthService:
    def test_password_hash_and_verify(self):
        pw = "SecurePass123!"
        hashed = pwd_context.hash(pw)
        assert pwd_context.verify(pw, hashed)
        assert not pwd_context.verify("wrong", hashed)

    def test_create_user_in_db(self, db):
        hashed = pwd_context.hash("TestPass1!")
        db.execute(text(
            "INSERT INTO applogins_app_users (email, password_hash) VALUES (:e, :h)"
        ), {"e": "new@example.com", "h": hashed})
        db.commit()
        row = db.execute(text(
            "SELECT * FROM applogins_app_users WHERE email = 'new@example.com'"
        )).fetchone()
        assert row is not None
        assert pwd_context.verify("TestPass1!", row.password_hash)

    def test_duplicate_email_rejected(self, db):
        import sqlite3
        hashed = pwd_context.hash("Pass1!")
        db.execute(text(
            "INSERT INTO applogins_app_users (email, password_hash) VALUES (:e, :h)"
        ), {"e": "dup@example.com", "h": hashed})
        db.commit()
        with pytest.raises(Exception):
            db.execute(text(
                "INSERT INTO applogins_app_users (email, password_hash) VALUES (:e, :h)"
            ), {"e": "dup@example.com", "h": hashed})
            db.commit()

    def test_reset_password_updates_hash(self, db):
        old_hash = pwd_context.hash("OldPass1!")
        db.execute(text(
            "INSERT INTO applogins_app_users (email, password_hash) VALUES (:e, :h)"
        ), {"e": "reset@example.com", "h": old_hash})
        db.commit()
        new_hash = pwd_context.hash("NewPass2!")
        db.execute(text(
            "UPDATE applogins_app_users SET password_hash=:h WHERE email='reset@example.com'"
        ), {"h": new_hash})
        db.commit()
        row = db.execute(text(
            "SELECT password_hash FROM applogins_app_users WHERE email='reset@example.com'"
        )).fetchone()
        assert pwd_context.verify("NewPass2!", row.password_hash)
        assert not pwd_context.verify("OldPass1!", row.password_hash)
