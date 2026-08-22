import React from 'react'
import { useState, useEffect } from 'react'
import { tenQApi } from '../api/client'

export function Edgar10QPage() {
  const [reports, setReports] = useState([])
  const [selected, setSelected] = useState(null)
  const [listLoading, setListLoading] = useState(true)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null)

  useEffect(() => {
    tenQApi.getReportsList()
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
      const res = await tenQApi.generateEdgarHtml(selected)
      setStatus({ type: 'success', text: res.data.message })
    } catch (e) {
      setStatus({ type: 'error', text: e.response?.data?.detail || 'Conversion failed' })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page-container">
      <h2 className="page-title">Convert 10-Q to SEC EDGAR HTM(iXBRL)</h2>
      <div className="card">
        <div className="card-title">Select Report</div>
        <p style={{ marginBottom: '1rem', color: '#555' }}>
          Converts a previously generated Form 10-Q report into SEC EDGAR-compliant iXBRL format.
          Pick which period's report to convert below — the Form 10-Q DOCX report for that period must
          already exist (see Generate Form 10-Q Report).
        </p>

        {listLoading ? (
          <p style={{ color: '#555' }}>Loading available reports...</p>
        ) : reports.length === 0 ? (
          <p style={{ color: '#555' }}>
            No Form 10-Q reports found yet. Generate one first from the "Generate Form 10-Q Report" page.
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
                  name="report10q"
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
          Generate 10-Q EDGAR HTM(iXBRL)
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
