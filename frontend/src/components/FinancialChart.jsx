import React from 'react'
/**
 * FinancialChart.jsx
 *
 * Contains FinancialChart — bar chart summary of income statement data.
 *
 */
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

const _amountFormatter = new Intl.NumberFormat('en-US', {
  style: 'decimal',
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
})

function fmt(value) {
  if (value === null || value === undefined) return ''
  return _amountFormatter.format(value)
}

export default function FinancialChart({ incomeStatement, periodLabel, priorLabel }) {
  if (!incomeStatement || incomeStatement.length === 0) return null

  const REVENUE_CATS = ['Revenue', 'Income', 'Product Revenue']
  const EXPENSE_CATS = ['Expense', 'Cost of Goods Sold', 'Supplies Expense', 'Salaries and Wages', 
    'Lease Expense', 'Marketing Expense', 'R&D Expense', 'Depreciation Expense',
     'Accounting Expense', 'Interest Expense', 'Income Tax Expense']

  const revenue = incomeStatement
    .filter(r => REVENUE_CATS.includes(r.category))
    .reduce((s, r) => s + r.current_period, 0)
  const revenuePrior = incomeStatement
    .filter(r => REVENUE_CATS.includes(r.category))
    .reduce((s, r) => s + r.prior_period, 0)
  // Expenses are already positive from the backend
  const expenses = incomeStatement
    .filter(r => EXPENSE_CATS.includes(r.category))
    .reduce((s, r) => s + r.current_period, 0)
  const expensesPrior = incomeStatement
    .filter(r => EXPENSE_CATS.includes(r.category))
    .reduce((s, r) => s + r.prior_period, 0)

  const chartData = [
    { name: 'Revenue',    current: revenue,            prior: revenuePrior },
    { name: 'Expenses',   current: expenses,           prior: expensesPrior },
    { name: 'Net Income', current: revenue - expenses, prior: revenuePrior - expensesPrior },
  ]

  return (
    <div className="financial-table-container" style={{ padding: '1rem' }}>
      <div className="financial-table-title">Financial Summary Chart</div>
      <div style={{ height: 280, padding: '1rem' }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 10, right: 30, left: 20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" tick={{ fontFamily: 'Arial', fontSize: 12 }} />
            <YAxis tickFormatter={v => `$${(v / 1000).toFixed(0)}K`} tick={{ fontSize: 11 }} />
            <Tooltip formatter={(v) => fmt(v)} />
            <Legend />
            <Bar dataKey="current" name={periodLabel} fill="#1a6a8a" />
            <Bar dataKey="prior"   name={priorLabel}  fill="#8ab4c8" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
