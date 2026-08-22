"""10-Q report API endpoints."""
import os
import re
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import get_current_user
from app.models.user import AppUser
from app.schemas.schemas import (
    QuarterlyRequest, QuarterlyMDAGenerateRequest,
    QuarterlyNotesGenerateRequest, QuarterlyNotesRequest,
    MDARequest, SaveFinancialsRequest,
    GenerateReportRequest, AckResponse, MDAResponse, NotesResponse,
    NotesListResponse, SelectedNoteInfo, FinancialStatements
)
from app.services.financial_service import get_quarterly_statements, get_loans
from app.services.docx_service import (
    create_10q_item010, create_10q_item011, create_10q_item020,
    create_10q_final_report, create_10q_edgar_html,
    fill_cover_page_10q
)
from app.services.xbrl_tagger import tag_filing
from app.services.notes_config import get_notes_config
from app.agents.notes_registry import get_selected_notes
from app.services.financial_service import _quarter_dates
import tomllib
from pathlib import Path
from datetime import date
from app.agents.mda_agent import generate_quarterly_mda
from app.agents.notes_agent import generate_quarterly_notes
from app.core.config import settings

logger = logging.getLogger()
router = APIRouter()

# Matches "sec-10q-2025-q3.docx" -> year=2025, quarter=3
_REPORT_FILENAME_RE = re.compile(r"^sec-10q-(\d{4})-q([123])\.docx$")


class ReportsListResponse(BaseModel):
    filenames: list[str]


class GenerateEdgarHtmlRequest(BaseModel):
    filename: str




@router.post("/financials", response_model=FinancialStatements)
async def get_quarterly_financials(
    req: QuarterlyRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Get 10-Q financial statements for preview."""
    if req.quarter not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="Quarter must be 1, 2, or 3")

    balance_sheet, income_stmt, cash_flow, stockholders_equity, \
        period_label, prior_label, bs_prior_label, dates = \
        get_quarterly_statements(db, req.year, req.quarter)

    return FinancialStatements(
        balance_sheet=balance_sheet,
        income_statement=income_stmt,
        cash_flow=cash_flow,
        stockholders_equity=stockholders_equity,
        period_label=period_label,
        prior_label=prior_label,
        bs_prior_label=bs_prior_label,
        dates=dates,
    )


@router.post("/mda", response_model=MDAResponse)
async def get_quarterly_mda(
    req: QuarterlyMDAGenerateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Generate 10-Q MD&A narrative via LangGraph/Ollama."""
    balance_sheet, income_stmt, cash_flow, _stockholders_equity, \
        period_label, prior_label, bs_prior_label, dates = \
        get_quarterly_statements(db, req.year, req.quarter)

    narrative, compliance_notes, validation_warnings = await generate_quarterly_mda(
        balance_sheet, income_stmt, cash_flow,
        req.year, req.quarter, period_label, prior_label,
        mda_context=req.mda_context.model_dump() if req.mda_context else None,
    )
    return MDAResponse(
        narrative=narrative,
        year=req.year,
        quarter=req.quarter,
        compliance_notes=compliance_notes,
        validation_warnings=validation_warnings,
    )


@router.post("/save-financials", response_model=AckResponse)
async def save_quarterly_financials(
    req: SaveFinancialsRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Save 10-Q financial statements to item010.docx."""
    balance_sheet, income_stmt, cash_flow, stockholders_equity, \
        period_label, prior_label, bs_prior_label, dates = \
        get_quarterly_statements(db, req.year, req.quarter)

    path = create_10q_item010(
        balance_sheet, income_stmt, cash_flow, stockholders_equity,
        period_label, prior_label, bs_prior_label, req.year, req.quarter
    )

    return AckResponse(
        success=True,
        message=f"Financial statements saved to {os.path.basename(path)}",
        file_path=path,
    )


@router.post("/save-mda", response_model=AckResponse)
async def save_quarterly_mda(
    req: MDARequest,
    current_user: AppUser = Depends(get_current_user),
):
    """Save 10-Q MD&A narrative to item020.docx."""
    path = create_10q_item020(req.narrative, req.quarter)
    return AckResponse(
        success=True,
        message=f"MD&A saved to {os.path.basename(path)}",
        file_path=path,
    )


@router.get("/notes-list", response_model=NotesListResponse)
async def get_quarterly_notes_list(
    quarter: int,
    current_user: AppUser = Depends(get_current_user),
):
    """
    Read-only list of the notes that WILL be generated for a 10-Q, per
    that quarter's own note_list_10q-q{quarter}.toml — number and title
    only. Mirrors report_10k.py's identical endpoint, except quarter is
    now REQUIRED (as a query param, e.g. GET /notes-list?quarter=3):
    each quarter has its own toml file (see env-10q-123.docx), so unlike
    the 10-K side, note selection here genuinely does vary per quarter.
    """
    if quarter not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="Quarter must be 1, 2, or 3")
    selected = get_selected_notes("10-Q", quarter)
    return NotesListResponse(
        notes=[SelectedNoteInfo(number=n.number, title=n.title) for n in selected]
    )


@router.post("/notes", response_model=NotesResponse)
async def get_quarterly_notes(
    req: QuarterlyNotesGenerateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Generate 10-Q Notes to Financial Statements narrative via LangGraph/Ollama."""
    if req.quarter not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="Quarter must be 1, 2, or 3")

    balance_sheet, income_stmt, cash_flow, _stockholders_equity, \
        period_label, prior_label, bs_prior_label, dates = \
        get_quarterly_statements(db, req.year, req.quarter)

    # Feeds Note 7's real 5-year maturity schedule and weighted-average-rate
    # breakdown (see notes_agent.py's compute_note_info_node) — same as the
    # 10-K /notes endpoint. Falls back to the short-term/long-term split
    # only if this table is empty for this deployment.
    loan_rows = get_loans(db)

    narrative, compliance_notes, validation_warnings = await generate_quarterly_notes(
        balance_sheet, income_stmt, cash_flow,
        req.year, req.quarter, period_label, prior_label,
        notes_context=req.notes_context.model_dump() if req.notes_context else None,
        loan_rows=loan_rows,
    )
    return NotesResponse(
        narrative=narrative,
        year=req.year,
        quarter=req.quarter,
        compliance_notes=compliance_notes,
        validation_warnings=validation_warnings,
    )


@router.post("/save-notes", response_model=AckResponse)
async def save_quarterly_notes(
    req: QuarterlyNotesRequest,
    current_user: AppUser = Depends(get_current_user),
):
    """Save 10-Q Notes to Financial Statements to item011-Notes-to-Financial-Statements.docx."""
    path = create_10q_item011(req.narrative, req.quarter)
    return AckResponse(
        success=True,
        message=f"Notes saved to {os.path.basename(path)}",
        file_path=path,
    )


@router.post("/generate-report", response_model=AckResponse)
async def generate_10q_report(
    req: GenerateReportRequest,
    current_user: AppUser = Depends(get_current_user),
):
    """Generate the final 10-Q report by merging all item files."""
    q_start, q_end = _quarter_dates(req.year, req.quarter)
    fill_cover_page_10q(q_end.isoformat(), req.quarter)

    path = create_10q_final_report(req.year, req.quarter)
    logger.info(f"REPORTS path: {path}")
    return AckResponse(
        success=True,
        message=f"10-Q report generated: {os.path.basename(path)}",
        file_path=path,
    )


@router.get("/reports-list", response_model=ReportsListResponse)
async def list_10q_reports(
    current_user: AppUser = Depends(get_current_user),
):
    """
    List existing sec-10q-{year}-q{quarter}.docx files in REPORTS_DIR,
    most recent (year, then quarter) first. Drives the Edgar 10-Q page's
    file picker — the EDGAR HTML step depends on one of these already
    existing (via /generate-report).
    """
    if not os.path.isdir(settings.REPORTS_DIR):
        return ReportsListResponse(filenames=[])

    matches = []
    for name in os.listdir(settings.REPORTS_DIR):
        m = _REPORT_FILENAME_RE.match(name)
        if m:
            matches.append((int(m.group(1)), int(m.group(2)), name))
    matches.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return ReportsListResponse(filenames=[name for _y, _q, name in matches])


@router.post("/generate-edgar-html", response_model=AckResponse)
async def generate_10q_edgar_html(
    req: GenerateEdgarHtmlRequest,
    current_user: AppUser = Depends(get_current_user),
):
    """
    Convert a specific sec-10q-{year}-q{quarter}.docx to SEC EDGAR HTML
    format.

    Year and quarter are parsed directly from the selected filename (e.g.
    "sec-10q-2025-q3.docx" -> year=2025, quarter=3) rather than read from
    a shared filing_meta.toml — with multiple periods' reports now
    coexisting as separate files, a single meta file can't unambiguously
    describe all of them. All period boundaries (quarter start/end, prior
    quarter, YTD start/end, prior YTD, prior fiscal year-end) are re-derived
    here with the exact same _quarter_dates()-based arithmetic
    get_quarterly_statements() itself uses, so this stays consistent with
    the Prep page's own date math without needing a DB round-trip (the
    dates are pure calendar arithmetic — no financial data is needed here).
    """
    m = _REPORT_FILENAME_RE.match(req.filename)
    if not m:
        raise HTTPException(
            status_code=400,
            detail=f'Expected a filename like "sec-10q-YYYY-qN.docx", got "{req.filename}"',
        )
    year, quarter = int(m.group(1)), int(m.group(2))

    docx_path = os.path.join(settings.REPORTS_DIR, req.filename)
    if not os.path.exists(docx_path):
        raise HTTPException(status_code=404, detail=f"{req.filename} not found. Generate the report first.")

    q_start, q_end             = _quarter_dates(year, quarter)
    prior_q_start, prior_q_end = _quarter_dates(year - 1, quarter)
    year_start       = date(year, 1, 1)
    prior_year_start = date(year - 1, 1, 1)
    prior_year_end   = date(year - 1, 12, 31)

    period_start = year_start.isoformat()          # YTD start
    period_end   = q_end.isoformat()
    prior_start  = prior_year_start.isoformat()
    prior_end    = prior_q_end.isoformat()

    html_filename  = f"sec-10q-{year}-q{quarter}.html"
    xhtml_filename = f"sec-10q-{year}-q{quarter}.htm"
    xhtml_path = os.path.join(settings.REPORTS_DIR, xhtml_filename)

    # Render item001-10-q-cover.docx from its docxtpl placeholder template
    # using business_info.toml BEFORE it gets converted below — same
    # reason as the 10-K endpoint: skipping this step leaves literal
    # "{{ ... }}" placeholders in the cover page and breaks iXBRL tagging
    # of the DEI cover-page facts. Must be the 10-Q-specific renderer —
    # fill_cover_page() (10-K) writes item001-10-k-cover.docx into
    # DATA_10K_INTRO and never touches this quarter's cover at all, which
    # was the actual bug: every quarter's cover showed the same stale date
    # because this endpoint was calling the wrong function.
    fill_cover_page_10q(period_end, quarter)

    # Build the EDGAR HTML with real inline styles (matches the 10-K's
    # create_10k_edgar_html) instead of the legacy bare/unstyled
    # convert_to_edgar_html() path.
    html_path = create_10q_edgar_html(html_filename, quarter)

    try:
        # Read company identity from business_info.toml
        biz_path = os.path.join(settings.DATA_USER_INPUT_DIR, "business_info.toml")
        with open(biz_path, "rb") as f:
            biz = tomllib.load(f)
        # add iXBRL(inline XBRL) tags to html to create xhtml file
        tag_filing(
            html_path    = html_path,
            output_path  = xhtml_path,
            form_type    = "10-Q",
            entity_name  = biz.get("company_name", ""),
            ticker       = biz.get("ticker", "ZZZZ"),
            cik          = biz.get("cik", "0000000000"),
            period_end   = period_end,
            period_start = period_start,
            prior_end    = prior_end,
            prior_start  = prior_start,
            currency     = "USD",
            taxonomy_year= 2026,
            # Per-table period overrides for the Income Statement's
            # quarter-only columns and the Balance Sheet's prior-fiscal-
            # year-end column — see xbrl_tagger.tag_filing()'s docstring.
            quarter_start       = q_start.isoformat(),
            prior_quarter_start = prior_q_start.isoformat(),
            bs_prior_end        = prior_year_end.isoformat(),
            notes        = get_notes_config(
                year, period_start, period_end,
                report_type="10-Q", quarter=quarter,
            ),
        )
        message = f"EDGAR HTM generated: {html_filename} + iXBRL -> {xhtml_filename}"
        path    = xhtml_path
    except FileNotFoundError as e:
        logging.getLogger(__name__).warning(f"iXBRL tagging skipped: {e}")
        message = f"EDGAR HTM generated (but no iXBRL): {html_filename}"
        path    = html_path

    return AckResponse(
        success=True,
        message=message,
        file_path=path,
    )
