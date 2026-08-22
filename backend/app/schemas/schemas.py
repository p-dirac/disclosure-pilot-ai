"""Pydantic schemas for request/response validation."""
from pydantic import BaseModel, EmailStr
from typing import Optional, Any
from datetime import date


# ─── Auth schemas ─────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class PasswordReset(BaseModel):
    email: EmailStr
    new_password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    email: Optional[str] = None


class UserOut(BaseModel):
    id: int
    email: str
    is_active: bool

    model_config = {"from_attributes": True}


# ─── Financial schemas ─────────────────────────────────────────────────────────

class AccountRow(BaseModel):
    account: int
    acct_name: str
    category: str
    balance: float


class BalanceSheetRow(BaseModel):
    account: int
    acct_name: str
    category: str
    current_period: float
    prior_period: float


class IncomeStatementRow(BaseModel):
    account: int
    acct_name: str
    category: str
    acct_subtype: Optional[str] = None
    current_period: float
    prior_period: float
    prior2_period: Optional[float] = None   # 10-K only: year - 2
    ytd_current: Optional[float] = None
    ytd_prior: Optional[float] = None


class CashFlowRow(BaseModel):
    description: str
    current_period: Optional[float] = None
    prior_period: Optional[float] = None
    prior2_period: Optional[float] = None   # 10-K only: year - 2


class StockholdersEquityRow(BaseModel):
    """
    One row of the Statement of Stockholders' Equity rollforward — either
    a "Balance as of ..." row (is_balance_row=True, every column populated)
    or a single activity row (only the columns that row affects are set;
    the rest are None, rendered as "—" on the frontend/docx).

    additional_paid_in_capital and accumulated_oci are always 0.0 — this
    app doesn't track APIC (no-par stock records full proceeds in
    common_stock_amount instead) or other comprehensive income at all.
    APIC has no column of its own in this table (it's folded entirely
    into common_stock_amount per business decision).
    """
    description: str
    is_balance_row: bool = False
    common_stock_amount:        Optional[float] = None
    treasury_stock:              Optional[float] = None
    accumulated_oci:             Optional[float] = None
    retained_earnings:           Optional[float] = None
    total_equity:                Optional[float] = None


class FinancialStatements(BaseModel):
    balance_sheet: list[BalanceSheetRow]
    income_statement: list[IncomeStatementRow]
    cash_flow: list[CashFlowRow]
    stockholders_equity: Optional[list[StockholdersEquityRow]] = None   # 10-K only, for now
    period_label: str
    prior_label: str
    prior2_label: Optional[str] = None      # 10-K only: year - 2 label
    bs_prior_label: Optional[str] = None     # balance sheet prior column header (prior year-end)
    dates: Optional[dict[str, str]] = None   # ISO date strings for frontend column headers


# ─── MD&A context schema ──────────────────────────────────────────────────────

class MDAContext(BaseModel):
    # These four fields are read from business_info.toml on the backend;
    # the frontend no longer collects them, so they default to "".
    company_name: Optional[str] = ""
    company_industry: Optional[str] = ""
    company_geo_focus: Optional[str] = ""
    company_description: Optional[str] = ""
    # These three are still collected from the frontend form.
    company_strategic_initiatives: str
    company_risk_factors: str
    company_accounting_estimates: str


# ─── Notes context schema ─────────────────────────────────────────────────────

class NotesContext(BaseModel):
    """Optional user-supplied context that enriches the Notes narrative.
    All fields are optional; notes_agent falls back to business_info.toml / defaults.

    As of this writing, NO frontend page populates this — the notes
    lineup itself is config-driven (note_list_10k.toml / note_list_10q.toml
    via app.agents.notes_registry) and can change independently of any
    frontend release, so the frontend intentionally exposes zero editable
    fields for Notes generation (see Prep10KPage.jsx's "Notes to be
    Included" list, which is read-only). This schema is kept for
    programmatic/API callers that want to override a value (e.g. a future
    admin tool), but every request from the shipped frontend sends
    notes_context = None and relies entirely on business_info.toml.
    """
    # Business profile — shared with MDAContext; re-used if already in the form
    company_name:        Optional[str] = None
    company_description: Optional[str] = None
    company_industry:    Optional[str] = None
    company_geo_focus:   Optional[str] = None
    # Notes-specific fields
    shares_outstanding:  Optional[str] = None   # e.g. "10,000,000"
    lease_payment_yr1:   Optional[str] = None   # e.g. "1200000" — future lease payment, year 1
    lease_payment_yr2:   Optional[str] = None
    lease_payment_yr3:   Optional[str] = None
    lease_payment_yr4:   Optional[str] = None
    lease_payment_yr5:   Optional[str] = None
    functional_currency: Optional[str] = None   # e.g. "U.S. dollar (USD)"
    reporting_currency:  Optional[str] = None   # e.g. "U.S. dollar (USD)"
    lease_description:   Optional[str] = None
    debt_description:    Optional[str] = None


# ─── 10-Q schemas ─────────────────────────────────────────────────────────────

class QuarterlyMDAGenerateRequest(BaseModel):
    year: int
    quarter: int
    mda_context: MDAContext


class QuarterlyNotesGenerateRequest(BaseModel):
    """POST /10q/notes — trigger AI generation of quarterly Notes."""
    year:          int
    quarter:       int
    notes_context: Optional[NotesContext] = None


class QuarterlyRequest(BaseModel):
    year: int
    quarter: int  # 1, 2, or 3


class MDARequest(BaseModel):
    year: int
    quarter: int
    narrative: str          # HTML string from TipTap editor (frontend)
    mda_context: Optional[MDAContext] = None


class QuarterlyNotesRequest(BaseModel):
    """POST /10q/save-notes — save the (possibly edited) Notes HTML to docx."""
    year:      int
    quarter:   int
    narrative: str          # HTML string from TipTap editor (frontend)


class SaveFinancialsRequest(BaseModel):
    year: int
    quarter: int


class GenerateReportRequest(BaseModel):
    year: int
    quarter: int


# ─── 10-K schemas ─────────────────────────────────────────────────────────────

class AnnualMDAGenerateRequest(BaseModel):
    year: int
    mda_context: MDAContext


class AnnualNotesGenerateRequest(BaseModel):
    """POST /10k/notes — trigger AI generation of annual Notes."""
    year:          int
    notes_context: Optional[NotesContext] = None


class AnnualRequest(BaseModel):
    year: int


class AnnualMDARequest(BaseModel):
    year: int
    narrative: str          # HTML string from TipTap editor (frontend)
    mda_context: Optional[MDAContext] = None


class AnnualNotesRequest(BaseModel):
    """POST /10k/save-notes — save the (possibly edited) Notes HTML to docx."""
    year:      int
    narrative: str          # HTML string from TipTap editor (frontend)


class SaveAnnualFinancialsRequest(BaseModel):
    year: int


# ─── Response schemas ──────────────────────────────────────────────────────────

class AckResponse(BaseModel):
    success: bool
    message: str
    file_path: Optional[str] = None


class MDAResponse(BaseModel):
    narrative: str
    year: int
    quarter: Optional[int] = None
    compliance_notes: Optional[list[str]] = None
    validation_warnings: Optional[list[str]] = None


class NotesResponse(BaseModel):
    """Response from POST /10q/notes and POST /10k/notes."""
    narrative:           str
    year:                int
    quarter:             Optional[int] = None   # None for 10-K
    compliance_notes:    list[str] = []
    validation_warnings: list[str] = []


class SelectedNoteInfo(BaseModel):
    """One entry in the read-only list of notes that will be generated —
    number and title only, no user-editable fields. The note lineup is
    driven entirely by note_list_10k.toml / note_list_10q.toml (see
    app.agents.notes_registry), never by frontend input, since which
    notes are selected — and their order/numbering — can change from
    filing to filing without any frontend change."""
    number: int
    title:  str


class NotesListResponse(BaseModel):
    """Response from GET /10k/notes-list and GET /10q/notes-list."""
    notes: list[SelectedNoteInfo]
