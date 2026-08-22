import React from 'react'
import { useState, useEffect } from 'react'
import { tenKApi } from '../api/client'

//const maxYear = new Date().getFullYear()
const maxYear = 2025 // for dev data 2022-2025
const YEARS = Array.from({ length: 4 }, (_, i) => maxYear - i)

export function Report10KPage() {
  const [year, setYear] = useState(maxYear)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null)

  const generate = async () => {
    setLoading(true)
    setStatus({ type: 'loading', text: 'Generating Form 10-K report, merging item files...' })
    try {
      const res = await tenKApi.generateReport(year)
      setStatus({ type: 'success', text: res.data.message })
    } catch (e) {
      setStatus({ type: 'error', text: e.response?.data?.detail || 'Report generation failed' })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page-container">
      <h2 className="page-title">Generate Form 10-K Report</h2>
      <div className="card">
        <div className="card-title">Select Year</div>
        <p style={{ marginBottom: '1rem', color: '#555' }}>
          <strong>Caution:</strong> Ensure all Form 10-K item files have been completed before generating the final report.
        </p>
        <div className="controls-row">
          <div className="form-group">
            <label>Year</label>
            <select value={year} onChange={e => setYear(Number(e.target.value))}>
              {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
          <button className="btn btn-primary" onClick={generate} disabled={loading}>
            {loading && <span className="spinner" />}
            Generate Form 10-K Report
          </button>
        </div>
        {status && (
          <div className={`status-message status-${status.type}`} style={{ marginTop: '1rem' }}>
            {status.type === 'loading' && <span className="spinner" />}
            {status.text}
          </div>
        )}
      </div>
    </div>
  )
}

export function Edgar10KPage() {
  const [reports, setReports] = useState([])
  const [selected, setSelected] = useState(null)
  const [listLoading, setListLoading] = useState(true)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null)

  useEffect(() => {
    tenKApi.getReportsList()
      .then(res => {
        const filenames = res.data.filenames || []
        setReports(filenames)
        setSelected(filenames[0] || null)   // newest report, already sorted by the backend
      })
      .catch(() => setReports([]))
      .finally(() => setListLoading(false))
  }, [])

  const generate = async () => {
    if (!selected) return
    setLoading(true)
    setStatus({ type: 'loading', text: `Converting ${selected} to SEC EDGAR HTML...` })
    try {
      const res = await tenKApi.generateEdgarHtml(selected)
      setStatus({ type: 'success', text: res.data.message })
    } catch (e) {
      setStatus({ type: 'error', text: e.response?.data?.detail || 'Conversion failed' })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page-container">
      <h2 className="page-title">Convert Form 10-K to SEC EDGAR HTM(iXBRL)</h2>
      <div className="card">
        <div className="card-title">Select Report</div>
        <p style={{ marginBottom: '1rem', color: '#555' }}>
          Converts a previously generated Form 10-K report into SEC EDGAR-compliant iXBRL format.
          Pick which year's report to convert below — the Form 10-K DOCX report for that year must
          already exist (see Generate Form 10-K Report).
        </p>

        {listLoading ? (
          <p style={{ color: '#555' }}>Loading available reports...</p>
        ) : reports.length === 0 ? (
          <p style={{ color: '#555' }}>
            No Form 10-K reports found yet. Generate one first from the "Generate Form 10-K Report" page.
          </p>
        ) : (
          <div className="reports-file-list" style={{ marginBottom: '1rem' }}>
            {reports.map(name => (
              <label
                key={name}
                style={{
                  display: 'flex',
                  justifyContent: 'flex-start',
                  alignItems: 'center',
                  gap: '0.5rem',
                  width: 'fit-content',
                  padding: '0.25rem 0',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="radio"
                  name="report10k"
                  value={name}
                  checked={selected === name}
                  onChange={() => setSelected(name)}
                />
                <span style={{ whiteSpace: 'nowrap' }}>{name}</span>
              </label>
            ))}
          </div>
        )}

        <button className="btn btn-primary" onClick={generate} disabled={loading || !selected}>
          {loading && <span className="spinner" />}
          Generate 10-K EDGAR HTM(iXBRL)
        </button>
        {status && (
          <div className={`status-message status-${status.type}`} style={{ marginTop: '1rem' }}>
            {status.type === 'loading' && <span className="spinner" />}
            {status.text}
          </div>
        )}
      </div>
    </div>
  )
}
