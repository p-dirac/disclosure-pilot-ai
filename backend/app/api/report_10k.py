"""10-K report API endpoints."""
import os
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import get_current_user
from app.models.user import AppUser
from app.schemas.schemas import (
    AnnualRequest, AnnualMDARequest, AnnualMDAGenerateRequest,
    AnnualNotesGenerateRequest, AnnualNotesRequest,
    SaveAnnualFinancialsRequest,
    AckResponse, MDAResponse, NotesResponse, NotesListResponse,
    SelectedNoteInfo, FinancialStatements
)
from app.services.financial_service import get_annual_statements, get_loans
from app.services.docx_service import (
    create_10k_item081, create_10k_item070, create_10k_item082,
    create_10k_final_report, create_10k_edgar_html,
    fill_cover_page
)
from app.services.xbrl_tagger import tag_filing
from app.services.notes_config import get_notes_config
from app.agents.notes_registry import get_selected_notes
import tomllib
from pathlib import Path
from app.agents.mda_agent import generate_annual_mda
from app.agents.notes_agent import generate_annual_notes
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Matches "sec-10k-2025.docx" -> year=2025
_REPORT_FILENAME_RE = re.compile(r"^sec-10k-(\d{4})\.docx$")


class ReportsListResponse(BaseModel):
    filenames: List[str]


class GenerateEdgarHtmlRequest(BaseModel):
    filename: str




@router.post("/financials", response_model=FinancialStatements)
async def get_annual_financials(
    req: AnnualRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Get 10-K financial statements for preview."""
    balance_sheet, income_stmt, cash_flow, stockholders_equity, period_label, prior_label, prior2_label = \
        get_annual_statements(db, req.year)

    return FinancialStatements(
        balance_sheet=balance_sheet,
        income_statement=income_stmt,
        cash_flow=cash_flow,
        stockholders_equity=stockholders_equity,
        period_label=period_label,
        prior_label=prior_label,
        prior2_label=prior2_label,
    )


@router.post("/mda", response_model=MDAResponse)
async def get_annual_mda(
    req: AnnualMDAGenerateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Generate 10-K MD&A narrative via LangGraph/Ollama."""
    balance_sheet, income_stmt, cash_flow, _stockholders_equity, period_label, prior_label, prior2_label = \
        get_annual_statements(db, req.year)

    narrative, compliance_notes, validation_warnings = await generate_annual_mda(
        balance_sheet, income_stmt, cash_flow,
        req.year, period_label, prior_label,
        mda_context=req.mda_context.model_dump() if req.mda_context else None,
    )
    return MDAResponse(
        narrative=narrative,
        year=req.year,
        compliance_notes=compliance_notes,
        validation_warnings=validation_warnings,
    )


@router.post("/save-financials", response_model=AckResponse)
async def save_annual_financials(
    req: SaveAnnualFinancialsRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Save 10-K financial statements to item080.docx."""
    balance_sheet, income_stmt, cash_flow, stockholders_equity, period_label, prior_label, prior2_label = \
        get_annual_statements(db, req.year)

    path = create_10k_item081(
        balance_sheet, income_stmt, cash_flow, stockholders_equity,
        period_label, prior_label, prior2_label, req.year
    )

    return AckResponse(
        success=True,
        message=f"Financial statements saved to {os.path.basename(path)}",
        file_path=path,
    )


@router.post("/save-mda", response_model=AckResponse)
async def save_annual_mda(
    req: AnnualMDARequest,
    current_user: AppUser = Depends(get_current_user),
):
    """Save 10-K MD&A narrative to item070.docx."""
    path = create_10k_item070(req.narrative)
    return AckResponse(
        success=True,
        message=f"MD&A saved to {os.path.basename(path)}",
        file_path=path,
    )


@router.get("/notes-list", response_model=NotesListResponse)
async def get_annual_notes_list(
    current_user: AppUser = Depends(get_current_user),
):
    """
    Read-only list of the notes that WILL be generated for a 10-K, per
    note_list_10k.toml — number and title only. This drives the frontend's
    "Notes to be Included" display; it takes no year/quarter parameter
    because note selection isn't per-filing state, it's config (the same
    lineup applies to whichever year the user later generates notes for).
    """
    selected = get_selected_notes("10-K")
    return NotesListResponse(
        notes=[SelectedNoteInfo(number=n.number, title=n.title) for n in selected]
    )


@router.post("/notes", response_model=NotesResponse)
async def get_annual_notes(
    req: AnnualNotesGenerateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Generate 10-K Notes to Financial Statements narrative via LangGraph/Ollama."""
    balance_sheet, income_stmt, cash_flow, _stockholders_equity, period_label, prior_label, prior2_label = \
        get_annual_statements(db, req.year)

    # Feeds Note 7's real 5-year maturity schedule and weighted-average-rate
    # breakdown (see notes_agent.py's compute_note_info_node). If this table
    # is empty/not yet populated for this deployment, generate_annual_notes
    # gracefully falls back to the short-term/long-term split only.
    loan_rows = get_loans(db)

    narrative, compliance_notes, validation_warnings = await generate_annual_notes(
        balance_sheet, income_stmt, cash_flow,
        req.year, period_label, prior_label,
        notes_context=req.notes_context.model_dump() if req.notes_context else None,
        loan_rows=loan_rows,
    )
    return NotesResponse(
        narrative=narrative,
        year=req.year,
        compliance_notes=compliance_notes,
        validation_warnings=validation_warnings,
    )


@router.post("/save-notes", response_model=AckResponse)
async def save_annual_notes(
    req: AnnualNotesRequest,
    current_user: AppUser = Depends(get_current_user),
):
    """Save 10-K Notes to Financial Statements to item082-Notes-to-Financial-Statements.docx."""
    path = create_10k_item082(req.narrative)
    return AckResponse(
        success=True,
        message=f"Notes saved to {os.path.basename(path)}",
        file_path=path,
    )


@router.post("/generate-report", response_model=AckResponse)
async def generate_10k_report(
    req: AnnualRequest,
    current_user: AppUser = Depends(get_current_user),
):
    """Generate the final 10-K report by merging all item files."""
    path = create_10k_final_report(req.year)
    return AckResponse(
        success=True,
        message=f"10-K report generated: {os.path.basename(path)}",
        file_path=path,
    )


@router.get("/reports-list", response_model=ReportsListResponse)
async def list_10k_reports(
    current_user: AppUser = Depends(get_current_user),
):
    """
    List existing sec-10k-{year}.docx files in REPORTS_DIR, most recent
    year first. Drives the Edgar 10-K page's file picker — the EDGAR HTML
    step depends on one of these already existing (via /generate-report).
    """
    if not os.path.isdir(settings.REPORTS_DIR):
        return ReportsListResponse(filenames=[])

    matches = []
    for name in os.listdir(settings.REPORTS_DIR):
        m = _REPORT_FILENAME_RE.match(name)
        if m:
            matches.append((int(m.group(1)), name))
    matches.sort(key=lambda t: t[0], reverse=True)
    return ReportsListResponse(filenames=[name for _year, name in matches])


@router.post("/generate-edgar-html", response_model=AckResponse)
async def generate_10k_edgar_html(
    req: GenerateEdgarHtmlRequest,
    current_user: AppUser = Depends(get_current_user),
):
    """
    Convert a specific sec-10k-{year}.docx to SEC EDGAR HTML format.

    The year is parsed directly from the selected filename (e.g.
    "sec-10k-2025.docx" -> 2025) rather than read from a shared
    toml — with multiple years' reports now coexisting as
    separate files, a single meta file can't unambiguously describe all of
    them, and the year/period boundaries are pure date arithmetic anyway
    (period_start/end = the calendar year itself; prior_start/end = the
    year before), so there's nothing a lookup would add here.
    """
    m = _REPORT_FILENAME_RE.match(req.filename)
    if not m:
        raise HTTPException(
            status_code=400,
            detail=f'Expected a filename like "sec-10k-YYYY.docx", got "{req.filename}"',
        )
    year = int(m.group(1))

    docx_path = os.path.join(settings.REPORTS_DIR, req.filename)
    if not os.path.exists(docx_path):
        raise HTTPException(status_code=404, detail=f"{req.filename} not found. Generate the report first.")

    period_start = f"{year}-01-01"
    period_end   = f"{year}-12-31"
    prior_start  = f"{year - 1}-01-01"
    prior_end    = f"{year - 1}-12-31"

    html_filename  = f"sec-10k-{year}.html"
    xhtml_filename = f"sec-10k-{year}.htm"
    xhtml_path = os.path.join(settings.REPORTS_DIR, xhtml_filename)

    # Render item001-10-k-cover.docx from its docxtpl placeholder template
    # using business_info.toml BEFORE it gets merged/converted below —
    # skipping this step is what left literal "{{ ... }}" placeholders in
    # the cover page (and broke iXBRL tagging, since e.g.
    # dei:EntityIncorporationStateCountryCode had nothing valid to match
    # against and dei:SecurityExchangeName failed Arelle's enumeration
    # check on the literal string "{{ exchange }}").
    fill_cover_page(period_end)

    html_path = create_10k_edgar_html(html_filename)

    try:
        # Read company identity from business_info.toml
        biz_path = os.path.join(settings.DATA_USER_INPUT_DIR, "business_info.toml")
        with open(biz_path, "rb") as f:
            biz = tomllib.load(f)
        # add iXBRL(inline XBRL) tags to html to create xhtml file
        tag_filing(
            html_path    = html_path,
            output_path  = xhtml_path,
            form_type    = "10-K",
            entity_name  = biz.get("company_name", ""),
            ticker       = biz.get("ticker", "ZZZZ"),
            cik          = biz.get("cik", "0000000000"),
            period_end   = period_end,
            period_start = period_start,
            prior_end    = prior_end,
            prior_start  = prior_start,
            currency     = "USD",
            taxonomy_year= 2026,
            notes        = get_notes_config(year, period_start, period_end),
        )
        message = f"EDGAR HTM generated: {html_filename} + iXBRL -> {xhtml_filename}"
        path    = xhtml_path
    except FileNotFoundError as e:
        # business_info.toml missing — return plain HTML without XBRL tagging
        logging.warning(f"iXBRL tagging skipped: {e}")
        message = f"EDGAR HTM generated (but no iXBRL): {html_filename}"
        path    = html_path

    return AckResponse(
        success=True,
        message=message,
        file_path=path,
    )
