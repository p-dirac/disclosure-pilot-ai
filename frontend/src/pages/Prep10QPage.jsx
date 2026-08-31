import React from 'react'
import { useState, useEffect } from 'react'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { Table, TableRow, TableHeader, TableCell } from '@tiptap/extension-table'
import { marked } from 'marked'
import { tenQApi } from '../api/client'
import BalanceSheetTable from '../components/BalanceSheetTable'
import IncomeStatementTable from '../components/IncomeStatementTable'
import CashFlowTable from '../components/CashFlowTable'
import StockholdersEquityTable from '../components/StockholdersEquityTable'

//const maxYear = new Date().getFullYear()
const maxYear = 2025 // for dev data 2022-2025
// YEARS range could be extended, if data exists.
// If user selects a year without 2 previous years of data,
// unexpected results will occur! 
// For data 2022 to 2025, user may select 2024 or 2025
const YEARS = Array.from({ length: 2 }, (_, i) => maxYear - i)
const QUARTERS = [
  { value: 1, label: 'Q1 (Jan–Mar)' },
  { value: 2, label: 'Q2 (Apr–Jun)' },
  { value: 3, label: 'Q3 (Jul–Sep)' },
]

function StatusMsg({ msg }) {
  if (!msg) return null
  const cls =
    msg.type === 'error'   ? 'status-error'   :
    msg.type === 'loading' ? 'status-loading' : 'status-success'
  return (
    <div className={`status-message ${cls}`}>
      {msg.type === 'loading' && <span className="spinner" />}
      {msg.text}
    </div>
  )
}

/** Shared rich-text toolbar used by both the MD&A and Notes editors. */
function EditorToolbar({ editor }) {
  if (!editor) return null
  return (
    <div className="mda-editor-toolbar">
      <button
        type="button"
        className={`toolbar-btn${editor.isActive('bold') ? ' is-active' : ''}`}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().toggleBold().run() }}
        title="Bold"
      >B</button>
      <button
        type="button"
        className={`toolbar-btn${editor.isActive('italic') ? ' is-active' : ''}`}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().toggleItalic().run() }}
        title="Italic"
      ><em>I</em></button>
      <button
        type="button"
        className={`toolbar-btn${editor.isActive('heading', { level: 2 }) ? ' is-active' : ''}`}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().toggleHeading({ level: 2 }).run() }}
        title="Heading"
      >H2</button>
      <button
        type="button"
        className={`toolbar-btn${editor.isActive('heading', { level: 3 }) ? ' is-active' : ''}`}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().toggleHeading({ level: 3 }).run() }}
        title="Sub-heading"
      >H3</button>
      <button
        type="button"
        className={`toolbar-btn${editor.isActive('bulletList') ? ' is-active' : ''}`}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().toggleBulletList().run() }}
        title="Bullet list"
      >• List</button>
      <button
        type="button"
        className={`toolbar-btn${editor.isActive('orderedList') ? ' is-active' : ''}`}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().toggleOrderedList().run() }}
        title="Numbered list"
      >1. List</button>
      <span className="toolbar-sep" />
      <button
        type="button"
        className="toolbar-btn"
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() }}
        title="Insert table"
      >⊞ Table</button>
      <button
        type="button"
        className="toolbar-btn"
        disabled={!editor.can().addRowBefore()}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().addRowBefore().run() }}
        title="Add row above"
      >+Row↑</button>
      <button
        type="button"
        className="toolbar-btn"
        disabled={!editor.can().addRowAfter()}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().addRowAfter().run() }}
        title="Add row below"
      >+Row↓</button>
      <button
        type="button"
        className="toolbar-btn"
        disabled={!editor.can().deleteRow()}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().deleteRow().run() }}
        title="Delete row"
      >-Row</button>
      <button
        type="button"
        className="toolbar-btn"
        disabled={!editor.can().addColumnBefore()}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().addColumnBefore().run() }}
        title="Add column left"
      >+Col←</button>
      <button
        type="button"
        className="toolbar-btn"
        disabled={!editor.can().addColumnAfter()}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().addColumnAfter().run() }}
        title="Add column right"
      >+Col→</button>
      <button
        type="button"
        className="toolbar-btn"
        disabled={!editor.can().deleteColumn()}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().deleteColumn().run() }}
        title="Delete column"
      >-Col</button>
      <button
        type="button"
        className="toolbar-btn"
        disabled={!editor.can().deleteTable()}
        onMouseDown={e => { e.preventDefault(); editor.chain().focus().deleteTable().run() }}
        title="Delete table"
      >⊟ Table</button>
    </div>
  )
}

export default function Prep10QPage() {
  const [year,    setYear]    = useState(maxYear)
  const [quarter, setQuarter] = useState(1)
  const [financials, setFinancials] = useState(null)

  // MD&A state — HTML string kept in sync with TipTap editor via onUpdate
  const [mda, setMda] = useState('')

  // Notes state — HTML string kept in sync with its own TipTap editor
  const [notes, setNotes] = useState('')

  // ── Notes list — READ-ONLY, no user-editable parameters ─────────────────
  // Deliberately no form state here: which notes get generated, in what
  // order/number, is entirely config-driven — now per-quarter, via that
  // quarter's own note_list_10q-q{quarter}.toml (see env-10q-123.docx) —
  // and can change independently of this frontend, so there is nothing for
  // the user to set beyond which quarter they're looking at. This is just
  // a preview of what generate-notes will produce. Mirrors Prep10KPage.jsx's
  // identical block, calling the 10-Q's own /notes-list endpoint.
  const [notesList, setNotesList] = useState([])

  useEffect(() => {
    tenQApi.getNotesList(quarter)
      .then(res => setNotesList(res.data.notes))
      .catch(() => setNotesList([]))
  }, [quarter])

  // ── TipTap editor instances ──────────────────────────────────────────────
  const mdaEditor = useEditor({
    extensions: [
      StarterKit,
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
    ],
    content: '',
    onUpdate: ({ editor }) => setMda(editor.getHTML()),
  })

  const notesEditor = useEditor({
    extensions: [
      StarterKit,
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
    ],
    content: '',
    onUpdate: ({ editor }) => setNotes(editor.getHTML()),
  })

  // ── MD&A context form state ──────────────────────────────────────────────
  const [mdaContext, setMdaContext] = useState({
    company_strategic_initiatives: 'investing in R&D, expanding into new markets',
    company_risk_factors:          'increased competition, changes in market conditions',
    company_accounting_estimates:  'income tax allowances, useful life of assets',
  })
  const setCtx = (field, val) => setMdaContext(p => ({ ...p, [field]: val }))

  // ── Loading / status maps ─────────────────────────────────────────────────
  const [loading, setLoading] = useState({})
  const [status,  setStatus]  = useState({})
  const setLoad = (key, val) => setLoading(p => ({ ...p, [key]: val }))
  const setMsg  = (key, msg) => setStatus(p =>  ({ ...p, [key]: msg }))

  // ── Handlers ──────────────────────────────────────────────────────────────

  const loadFinancials = async () => {
    setLoad('fin', true)
    setMsg('fin', { type: 'loading', text: 'Loading financial statements...' })
    try {
      const res = await tenQApi.getFinancials(year, quarter)
      setFinancials(res.data)
      setMsg('fin', null)
    } catch (e) {
      setMsg('fin', { type: 'error', text: e.response?.data?.detail || 'Failed to load financials' })
    } finally {
      setLoad('fin', false)
    }
  }

  const saveFinancials = async () => {
    setLoad('saveFin', true)
    setMsg('saveFin', { type: 'loading', text: 'Saving financial statements...' })
    try {
      const res = await tenQApi.saveFinancials(year, quarter)
      setMsg('saveFin', { type: 'success', text: res.data.message })
    } catch (e) {
      setMsg('saveFin', { type: 'error', text: e.response?.data?.detail || 'Save failed' })
    } finally {
      setLoad('saveFin', false)
    }
  }

  const loadMDA = async () => {
    setLoad('mda', true)
    setMsg('mda', { type: 'loading', text: 'Generating MD&A via AI (this may take a few minutes)...' })
    try {
      const res = await tenQApi.getMDA(year, quarter, mdaContext)
      const html = marked.parse(res.data.narrative)
      mdaEditor.commands.setContent(html)
      setMda(html)
      setMsg('mda', null)
    } catch (e) {
      setMsg('mda', { type: 'error', text: e.response?.data?.detail || 'MD&A generation failed' })
    } finally {
      setLoad('mda', false)
    }
  }

  const saveMDA = async () => {
    setLoad('saveMda', true)
    setMsg('saveMda', { type: 'loading', text: 'Saving MD&A...' })
    try {
      const res = await tenQApi.saveMDA(year, quarter, mda)
      setMsg('saveMda', { type: 'success', text: res.data.message })
    } catch (e) {
      setMsg('saveMda', { type: 'error', text: e.response?.data?.detail || 'Save MD&A failed' })
    } finally {
      setLoad('saveMda', false)
    }
  }

  const loadNotes = async () => {
    setLoad('notes', true)
    setMsg('notes', { type: 'loading', text: 'Generating Notes to Financial Statements via AI (this may take a few minutes)...' })
    try {
      const res = await tenQApi.getNotes(year, quarter)
      const html = marked.parse(res.data.narrative)
      notesEditor.commands.setContent(html)
      setNotes(html)
      setMsg('notes', null)
    } catch (e) {
      setMsg('notes', { type: 'error', text: e.response?.data?.detail || 'Notes generation failed' })
    } finally {
      setLoad('notes', false)
    }
  }

  const saveNotes = async () => {
    setLoad('saveNotes', true)
    setMsg('saveNotes', { type: 'loading', text: 'Saving Notes to Financial Statements...' })
    try {
      const res = await tenQApi.saveNotes(year, quarter, notes)
      setMsg('saveNotes', { type: 'success', text: res.data.message })
    } catch (e) {
      setMsg('saveNotes', { type: 'error', text: e.response?.data?.detail || 'Save Notes failed' })
    } finally {
      setLoad('saveNotes', false)
    }
  }

  const generateReport = async () => {
    setLoad('report', true)
    setMsg('report', { type: 'loading', text: 'Generating 10-Q report...' })
    try {
      const res = await tenQApi.generateReport(year, quarter)
      setMsg('report', { type: 'success', text: res.data.message })
    } catch (e) {
      setMsg('report', { type: 'error', text: e.response?.data?.detail || 'Report generation failed' })
    } finally {
      setLoad('report', false)
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="page-container">
      <style>{`
        .mda-editor table {
          border-collapse: collapse;
          width: 100%;
          margin: 0.75rem 0;
        }
        .mda-editor table td,
        .mda-editor table th {
          border: 1px solid #ccc;
          padding: 0.4rem 0.6rem;
          background-color: #F5F5F5;
          text-align: left;
        }
        .mda-editor table th {
          font-family: Arial, sans-serif;
          font-weight: bold;
        }
        /* Notes tables (notes_agent.py's segment/debt/lease tables) are
           always label-in-column-0, dollar-amounts-after - same shape as
           the docx-side fix (_right_align_notes_table_columns in
           docx_service.py). Scoped to .notes-editor specifically, not
           .mda-editor generally, so a table a user manually inserts into
           MD&A prose via the toolbar isn't force-aligned the same way. */
        .notes-editor table td:not(:first-child),
        .notes-editor table th:not(:first-child) {
          text-align: right;
        }
      `}</style>
      <h2 className="page-title">Prepare 10-Q Quarterly Report</h2>

      {/* ── Period selector ── */}
      <div className="card">
        <div className="card-title">Select Period</div>
        <div className="controls-row">
          <div className="form-group">
            <label htmlFor="year-select">Year</label>
            <select
              id="year-select"
              value={year}
              onChange={e => setYear(Number(e.target.value))}
            >
              {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label htmlFor="quarter-select">Quarter</label>
            <select
              id="quarter-select"
              value={quarter}
              onChange={e => setQuarter(Number(e.target.value))}
            >
              {QUARTERS.map(q => (
                <option key={q.value} value={q.value}>{q.label}</option>
              ))}
            </select>
          </div>
          <button
            className="btn btn-primary"
            onClick={loadFinancials}
            disabled={loading.fin}
          >
            {loading.fin && <span className="spinner" />}
            Load Financial Statements
          </button>
        </div>
        <StatusMsg msg={status.fin} />
      </div>

      {/* ── Financial statements ── */}
      {financials && (
        <>
          <BalanceSheetTable
            data={financials.balance_sheet}
            dates={financials.dates}
            periodLabel={financials.period_label}
            priorLabel={financials.prior_label}
          />
          <IncomeStatementTable
            data={financials.income_statement}
            quarter={quarter}
            dates={financials.dates}
            periodLabel={financials.period_label}
            priorLabel={financials.prior_label}
          />
          <CashFlowTable
            rows={financials.cash_flow}
            quarter={quarter}
            dates={financials.dates}
            periodLabel={financials.period_label}
            priorLabel={financials.prior_label}
          />
          {financials.stockholders_equity && (
            <StockholdersEquityTable
              rows={financials.stockholders_equity}
            />
          )}
          <div className="btn-row">
            <button
              className="btn btn-primary"
              onClick={saveFinancials}
              disabled={!financials || loading.saveFin}
            >
              {loading.saveFin && <span className="spinner" />}
              Save Financials
            </button>
          </div>
          <StatusMsg msg={status.saveFin} />
        </>
      )}

      {/* ── MD&A context form — shown once financials are loaded ── */}
      {financials && (
        <>
          <div className="card" style={{ marginTop: '1.5rem' }}>
            <div className="card-title">MD&A Factors</div>
            <div className="form-grid">
              <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                <label>Strategic Initiatives</label>
                <textarea
                  value={mdaContext.company_strategic_initiatives}
                  onChange={e => setCtx('company_strategic_initiatives', e.target.value)}
                  rows={2}
                />
              </div>
              <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                <label>Risk Factors</label>
                <textarea
                  value={mdaContext.company_risk_factors}
                  onChange={e => setCtx('company_risk_factors', e.target.value)}
                  rows={2}
                />
              </div>
              <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                <label>Critical Accounting Estimates</label>
                <textarea
                  value={mdaContext.company_accounting_estimates}
                  onChange={e => setCtx('company_accounting_estimates', e.target.value)}
                  rows={2}
                />
              </div>
            </div>
          </div>
          <div className="btn-row">
            <button
              className="btn btn-secondary"
              onClick={loadMDA}
              disabled={!financials || loading.mda}
            >
              {loading.mda && <span className="spinner" />}
              Generate MD&amp;A
            </button>
          </div>
        </>
      )}

      {/* ── MD&A editor ── */}
      {(mda || loading.mda) && (
        <div className="card" style={{ marginTop: '1.5rem' }}>
          <div className="card-title">Management's Discussion and Analysis (MD&A)</div>
          <StatusMsg msg={status.mda} />
          {mda && (
            <>
              <p style={{ marginBottom: '0.5rem', color: '#555', fontSize: '0.9rem' }}>
                Review and edit the narrative below before saving.
              </p>
              <EditorToolbar editor={mdaEditor} />
              <EditorContent editor={mdaEditor} className="mda-editor" />
            </>
          )}
          <div className="btn-row">
            <button
              className="btn btn-primary"
              onClick={saveMDA}
              disabled={!mda || loading.saveMda}
            >
              {loading.saveMda && <span className="spinner" />}
              Submit MD&amp;A
            </button>
          </div>
          <StatusMsg msg={status.saveMda} />

          {/* ── Notes to be included — read-only, config-driven ── */}
          <div className="card" style={{ marginTop: '1rem' }}>
            <div className="card-title">Notes to be Included</div>
            {notesList.length > 0 ? (
              <ol className="notes-to-include-list" style={{ margin: '0.5rem 0 0', paddingLeft: '1.5rem' }}>
                {notesList.map(n => (
                  <li key={n.number}>{n.title}</li>
                ))}
              </ol>
            ) : (
              <p style={{ color: '#555', fontSize: '0.9rem', marginTop: '0.5rem' }}>
                No notes are currently selected — check note_list_10q.toml.
              </p>
            )}
          </div>

          {/* ── Generate Notes button — shown once MD&A is available ── */}
          <div className="btn-row" style={{ marginTop: '1rem' }}>
            <button
              className="btn btn-secondary"
              onClick={loadNotes}
              disabled={loading.notes}
            >
              {loading.notes && <span className="spinner" />}
              Generate Notes
            </button>
          </div>
        </div>
      )}

      {/* ── Notes to Financial Statements editor ── */}
      {(notes || loading.notes) && (
        <div className="card" style={{ marginTop: '1.5rem' }}>
          <div className="card-title">Notes to Financial Statements</div>
          <StatusMsg msg={status.notes} />
          {notes && (
            <>
              <p style={{ marginBottom: '0.5rem', color: '#555', fontSize: '0.9rem' }}>
                Review and edit the notes below before saving. 
              </p>
              <EditorToolbar editor={notesEditor} />
              <EditorContent editor={notesEditor} className="mda-editor notes-editor" />
            </>
          )}
          <div className="btn-row">
            <button
              className="btn btn-primary"
              onClick={saveNotes}
              disabled={!notes || loading.saveNotes}
            >
              {loading.saveNotes && <span className="spinner" />}
              Submit Notes
            </button>
          </div>
          <StatusMsg msg={status.saveNotes} />
        </div>
      )}
    </div>
  )
}
