import React from 'react'
import { useState } from 'react'
import { tenQApi } from '../api/client'

//const maxYear = new Date().getFullYear()
const maxYear = 2025 // for dev data 2022-2025
const YEARS = Array.from({ length: 4 }, (_, i) => maxYear - i)

const QUARTERS = [
  { value: 1, label: 'Q1 (Jan–Mar)' },
  { value: 2, label: 'Q2 (Apr–Jun)' },
  { value: 3, label: 'Q3 (Jul–Sep)' },
]

export default function Report10QPage() {
  const [year, setYear] = useState(maxYear)
  const [quarter, setQuarter] = useState(1)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null)

  const generate = async () => {
    setLoading(true)
    setStatus({ type: 'loading', text: 'Generating Form 10-Q report, merging item files...' })
    try {
      const res = await tenQApi.generateReport(year, quarter)
      setStatus({ type: 'success', text: res.data.message })
    } catch (e) {
      setStatus({ type: 'error', text: e.response?.data?.detail || 'Report generation failed' })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page-container">
      <h2 className="page-title">Generate Form 10-Q Report</h2>
      <div className="card">
        <div className="card-title">Select Period</div>
        <p style={{ marginBottom: '1rem', color: '#555' }}>
          <strong>Caution:</strong> Ensure all Form 10-Q item files have been completed before generating the final report.
        </p>
        <div className="controls-row">
          <div className="form-group">
            <label htmlFor="year-select">Year</label>
            <select id="year-select" value={year} onChange={e => setYear(Number(e.target.value))}>
              {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label htmlFor="quarter-select">Quarter</label>
            <select id="quarter-select" value={quarter} onChange={e => setQuarter(Number(e.target.value))}>
              {QUARTERS.map(q => <option key={q.value} value={q.value}>{q.label}</option>)}
            </select>
          </div>
          <button className="btn btn-primary" onClick={generate} disabled={loading}>
            {loading && <span className="spinner" />}
            Generate Form 10-Q Report
          </button>
        </div>
        {status && (
          <div className={`status-message status-${status.type}`}>
            {status.type === 'loading' && <span className="spinner" />}
            {status.text}
          </div>
        )}
      </div>
    </div>
  )
}
