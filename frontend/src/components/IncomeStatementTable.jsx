import React from 'react'
/**
 * IncomeStatementTable.jsx
 *
 * Multi-step GAAP income statement:
 *   1. Revenue
 *   2. Cost of Goods Sold  → Gross Profit
 *   3. Operating Expenses  → Operating Income / (Loss)
 *   4. Other Income / (Expenses)  → Income Before Tax
 *   5. Income Tax Expense  → NET INCOME
 *
 * $ sign: first data row of the statement and Net Income row only.
 * () for negative values throughout.
 * Label column is wider (min-width: 320px) to accommodate longer descriptions.
 *
 * Props
 * ─────
 * data        – IncomeStatementRow[]  (from API; each row has acct_subtype)
 * periodLabel – string
 * priorLabel  – string
 * prior2Label – string | undefined   (10-K third column)
 * quarter     – number | undefined   (10-Q mode; triggers YTD columns)
 * dates       – object | undefined   (10-Q date dict)
 */

// ── Section membership ────────────────────────────────────────────────────────
const COGS_NAMES                    = new Set(['Cost of Goods Sold'])
const OPERATING_REVENUE_SUBTYPES    = new Set(['Product Revenue', 'Service Revenue'])
const OPERATING_EXPENSE_SUBTYPES    = new Set(['Operating Expense'])
const NON_OPERATING_REVENUE_SUBTYPES = new Set(['Non-Operating Revenue'])
const NON_OPERATING_EXPENSE_SUBTYPES = new Set(['Non-Operating Expense'])
const TAX_SUBTYPES                  = new Set(['Tax Expense'])

function classify(row) {
  // COGS breaks out separately from other operating expenses
  if (COGS_NAMES.has(row.acct_name))                        return 'cogs'
  const sub = row.acct_subtype || ''
  if (OPERATING_REVENUE_SUBTYPES.has(sub))                  return 'op_revenue'
  if (OPERATING_EXPENSE_SUBTYPES.has(sub))                  return 'op_expense'
  if (NON_OPERATING_REVENUE_SUBTYPES.has(sub))              return 'non_op_revenue'
  if (NON_OPERATING_EXPENSE_SUBTYPES.has(sub))              return 'non_op_expense'
  if (TAX_SUBTYPES.has(sub))                                return 'tax'
  if (row.category === 'Revenue')                           return 'op_revenue'
  if (row.category === 'Expense')                           return 'op_expense'
  return null
}

// ── Formatting helpers ────────────────────────────────────────────────────────
const numFmt = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
})

function fmtNum(v, showDollar = false) {
  if (v == null) return '—'
  const abs    = numFmt.format(Math.abs(v))
  const prefix = showDollar ? '$' : ''
  return v < 0 ? `(${prefix}${abs})` : `${prefix}${abs}`
}

const sumCol = (rows, col) => rows.reduce((acc, r) => acc + (r[col] ?? 0), 0)

// ── Inline styles (matches BalanceSheetTable / CashFlowTable) ────────────────
const S = {
  tableWrap: { background: '#F5F5F5', borderRadius: 4, padding: '16px 20px', marginBottom: 24, overflowX: 'auto', fontFamily: "'Times New Roman', serif", fontSize: '1rem', color: '#000' },
  title:     { fontFamily: 'Arial, sans-serif', fontSize: '1.25rem', fontWeight: 'bold', marginBottom: 12, color: '#000' },
  table:     { width: '100%', borderCollapse: 'collapse' },
  thLeft:    { textAlign: 'left',  fontFamily: 'Arial, sans-serif', fontSize: '0.875rem', fontWeight: 'bold', padding: '4px 8px', borderBottom: '2px solid #000' },
  th:        { textAlign: 'right', fontFamily: 'Arial, sans-serif', fontSize: '0.875rem', fontWeight: 'bold', padding: '4px 8px', borderBottom: '2px solid #000', whiteSpace: 'normal' },
  secHdr:    { fontFamily: 'Arial, sans-serif', fontWeight: 'bold', fontSize: '0.9rem', padding: '10px 8px 2px 8px' },
  label:     { padding: '3px 8px 3px 28px' },
  num:       { textAlign: 'right', padding: '3px 8px', whiteSpace: 'nowrap' },
  totCell:   { fontWeight: 'bold', padding: '4px 8px', borderTop: '1px solid #000', fontFamily: 'Arial, sans-serif', fontSize: '0.9rem' },
  totNum:    { textAlign: 'right', padding: '4px 8px', borderTop: '1px solid #000', fontWeight: 'bold', whiteSpace: 'nowrap' },
  dblCell:   { borderTop: '3px double #000', fontWeight: 'bold', padding: '4px 8px', fontFamily: 'Arial, sans-serif', fontSize: '0.9rem' },
  dblNum:    { borderTop: '3px double #000', textAlign: 'right', fontWeight: 'bold', padding: '4px 8px', whiteSpace: 'nowrap' },
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionHeader({ label, colSpan }) {
  return (
    <tr>
      <td colSpan={colSpan} style={S.secHdr}>{label}</td>
    </tr>
  )
}

function DataRow({ label, cur, pri, pri2, ytdCur, ytdPri, hasYtd, hasPri2, isExpense, showDollar = false }) {
  // Expenses stored as positive magnitudes; display negated (in parens)
  const shown = (v) => {
    if (v == null) return null
    return isExpense ? -Math.abs(v) : v
  }
  const display = (v, dollar) => fmtNum(shown(v), dollar)

  return (
    <tr>
      <td style={S.label}>{label}</td>
      <td style={S.num}>{display(cur,    showDollar)}</td>
      <td style={S.num}>{display(pri,    showDollar)}</td>
      {hasPri2 && <td style={S.num}>{display(pri2, showDollar)}</td>}
      {hasYtd && <td style={S.num}>{display(ytdCur, showDollar)}</td>}
      {hasYtd && <td style={S.num}>{display(ytdPri, showDollar)}</td>}
    </tr>
  )
}

// variant: 'subtotal' (single underline) | 'double' (double underline — Net Income)
function TotalRow({ label, cur, pri, pri2, ytdCur, ytdPri, hasYtd, hasPri2, variant = 'subtotal', showDollar = false }) {
  const cellStyle = variant === 'double' ? S.dblCell : S.totCell
  const numStyle  = variant === 'double' ? S.dblNum  : S.totNum
  return (
    <tr>
      <td style={cellStyle}>{label}</td>
      <td style={numStyle}>{fmtNum(cur,    showDollar)}</td>
      <td style={numStyle}>{fmtNum(pri,    showDollar)}</td>
      {hasPri2 && <td style={numStyle}>{fmtNum(pri2, showDollar)}</td>}
      {hasYtd && <td style={numStyle}>{fmtNum(ytdCur, showDollar)}</td>}
      {hasYtd && <td style={numStyle}>{fmtNum(ytdPri, showDollar)}</td>}
    </tr>
  )
}

function Spacer({ colSpan }) {
  return <tr><td colSpan={colSpan} style={{ height: '0.5rem' }} /></tr>
}

// ── Main component ────────────────────────────────────────────────────────────
export default function IncomeStatementTable({
  data = [],
  periodLabel,
  priorLabel,
  prior2Label,
  quarter,
  dates,
}) {
  const hasYtd  = quarter != null && quarter !== 1
  const hasPri2 = !!prior2Label

  // Partition rows into sections
  const opRevRows    = data.filter(r => classify(r) === 'op_revenue')
  const cogsRows     = data.filter(r => classify(r) === 'cogs')
  const opExpRows    = data.filter(r => classify(r) === 'op_expense')
  const nonOpRevRows = data.filter(r => classify(r) === 'non_op_revenue')
  const nonOpExpRows = data.filter(r => classify(r) === 'non_op_expense')
  const taxRows      = data.filter(r => classify(r) === 'tax')

  // ── Column totals ─────────────────────────────────────────────────────────
  const sum = (rows, col) => sumCol(rows, col)

  // Revenue
  const totalRevCur  = sum(opRevRows, 'current_period')
  const totalRevPri  = sum(opRevRows, 'prior_period')
  const totalRevPri2 = hasPri2 ? sum(opRevRows, 'prior2_period') : null
  const totalRevYtdC = hasYtd  ? sum(opRevRows, 'ytd_current')   : null
  const totalRevYtdP = hasYtd  ? sum(opRevRows, 'ytd_prior')     : null

  // COGS (positive magnitudes)
  const totalCogsCur  = sum(cogsRows, 'current_period')
  const totalCogsPri  = sum(cogsRows, 'prior_period')
  const totalCogsPri2 = hasPri2 ? sum(cogsRows, 'prior2_period') : null
  const totalCogsYtdC = hasYtd  ? sum(cogsRows, 'ytd_current')   : null
  const totalCogsYtdP = hasYtd  ? sum(cogsRows, 'ytd_prior')     : null

  // Gross Profit = Revenue - COGS
  const grossProfitCur  = totalRevCur  - totalCogsCur
  const grossProfitPri  = totalRevPri  - totalCogsPri
  const grossProfitPri2 = hasPri2 ? (totalRevPri2 - totalCogsPri2) : null
  const grossProfitYtdC = hasYtd  ? (totalRevYtdC - totalCogsYtdC) : null
  const grossProfitYtdP = hasYtd  ? (totalRevYtdP - totalCogsYtdP) : null

  // Operating Expenses (excl. COGS)
  const totalExpCur  = sum(opExpRows, 'current_period')
  const totalExpPri  = sum(opExpRows, 'prior_period')
  const totalExpPri2 = hasPri2 ? sum(opExpRows, 'prior2_period') : null
  const totalExpYtdC = hasYtd  ? sum(opExpRows, 'ytd_current')   : null
  const totalExpYtdP = hasYtd  ? sum(opExpRows, 'ytd_prior')     : null

  // Operating Income = Gross Profit - Operating Expenses
  const opIncCur  = grossProfitCur  - totalExpCur
  const opIncPri  = grossProfitPri  - totalExpPri
  const opIncPri2 = hasPri2 ? (grossProfitPri2 - totalExpPri2) : null
  const opIncYtdC = hasYtd  ? (grossProfitYtdC - totalExpYtdC) : null
  const opIncYtdP = hasYtd  ? (grossProfitYtdP - totalExpYtdP) : null

  // Other income / expense
  const totNonOpRevCur  = sum(nonOpRevRows, 'current_period')
  const totNonOpRevPri  = sum(nonOpRevRows, 'prior_period')
  const totNonOpRevPri2 = hasPri2 ? sum(nonOpRevRows, 'prior2_period') : null
  const totNonOpRevYtdC = hasYtd  ? sum(nonOpRevRows, 'ytd_current')   : null
  const totNonOpRevYtdP = hasYtd  ? sum(nonOpRevRows, 'ytd_prior')     : null

  const totNonOpExpCur  = sum(nonOpExpRows, 'current_period')
  const totNonOpExpPri  = sum(nonOpExpRows, 'prior_period')
  const totNonOpExpPri2 = hasPri2 ? sum(nonOpExpRows, 'prior2_period') : null
  const totNonOpExpYtdC = hasYtd  ? sum(nonOpExpRows, 'ytd_current')   : null
  const totNonOpExpYtdP = hasYtd  ? sum(nonOpExpRows, 'ytd_prior')     : null

  const netOtherCur  = totNonOpRevCur  - totNonOpExpCur
  const netOtherPri  = totNonOpRevPri  - totNonOpExpPri
  const netOtherPri2 = hasPri2 ? (totNonOpRevPri2 - totNonOpExpPri2) : null
  const netOtherYtdC = hasYtd  ? (totNonOpRevYtdC - totNonOpExpYtdC) : null
  const netOtherYtdP = hasYtd  ? (totNonOpRevYtdP - totNonOpExpYtdP) : null

  // Income Before Tax
  const preTaxCur  = opIncCur  + netOtherCur
  const preTaxPri  = opIncPri  + netOtherPri
  const preTaxPri2 = hasPri2 ? (opIncPri2 + netOtherPri2) : null
  const preTaxYtdC = hasYtd  ? (opIncYtdC + netOtherYtdC) : null
  const preTaxYtdP = hasYtd  ? (opIncYtdP + netOtherYtdP) : null

  // Tax
  const taxCur  = sum(taxRows, 'current_period')
  const taxPri  = sum(taxRows, 'prior_period')
  const taxPri2 = hasPri2 ? sum(taxRows, 'prior2_period') : null
  const taxYtdC = hasYtd  ? sum(taxRows, 'ytd_current')   : null
  const taxYtdP = hasYtd  ? sum(taxRows, 'ytd_prior')     : null

  // Net Income
  const netIncCur  = preTaxCur  - taxCur
  const netIncPri  = preTaxPri  - taxPri
  const netIncPri2 = hasPri2 ? (preTaxPri2 - taxPri2) : null
  const netIncYtdC = hasYtd  ? (preTaxYtdC - taxYtdC) : null
  const netIncYtdP = hasYtd  ? (preTaxYtdP - taxYtdP) : null

  let colCount = 3
  if (hasPri2) colCount++
  if (hasYtd)  colCount += 2

  const qLabels  = { 1: 'March 31', 2: 'June 30', 3: 'September 30' }
  const qMonths  = { 1: 'Three Months', 2: 'Six Months', 3: 'Nine Months' }
  const ytdYear  = dates?.year_start ? parseInt(dates.year_start.slice(0, 4), 10) : ''
  const ytdPrYr  = ytdYear ? ytdYear - 1 : ''
  const ytdEnd   = quarter ? qLabels[quarter]  : ''
  const ytdMo    = quarter ? qMonths[quarter] : ''

  const sharedProps = { hasYtd, hasPri2 }

  return (
    <div style={S.tableWrap}>
      <div style={S.title}>Statements of Income</div>
      <table style={S.table}>
          <colgroup>
            <col style={{ minWidth: '320px' }} />
            <col />
            <col />
            {hasPri2 && <col />}
            {hasYtd && <><col /><col /></>}
          </colgroup>
          <thead>
            <tr>
              <th style={S.thLeft}></th>
              <th style={{ ...S.th, whiteSpace: 'pre-line' }}>{periodLabel}</th>
              <th style={{ ...S.th, whiteSpace: 'pre-line' }}>{priorLabel}</th>
              {hasPri2 && <th style={{ ...S.th, whiteSpace: 'pre-line' }}>{prior2Label}</th>}
              {hasYtd && (
                <>
                  <th style={S.th}>{ytdMo} Ended {ytdEnd}, {ytdYear}</th>
                  <th style={S.th}>{ytdMo} Ended {ytdEnd}, {ytdPrYr}</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>

            {/* ── SECTION 1: Revenue ── */}
            <SectionHeader label="REVENUE" colSpan={colCount} />
            {opRevRows.map((r, i) => (
              <DataRow
                key={r.account}
                label={r.acct_name}
                cur={r.current_period}   pri={r.prior_period}
                pri2={r.prior2_period}   ytdCur={r.ytd_current}  ytdPri={r.ytd_prior}
                isExpense={false}
                showDollar={i === 0}
                {...sharedProps}
              />
            ))}
            <TotalRow
              label="Total Revenue"
              cur={totalRevCur}   pri={totalRevPri}
              pri2={totalRevPri2} ytdCur={totalRevYtdC} ytdPri={totalRevYtdP}
              {...sharedProps}
            />

            <Spacer colSpan={colCount} />

            {/* ── SECTION 2: Cost of Goods Sold → Gross Profit ── */}
            {cogsRows.length > 0 && (
              <>
                <SectionHeader label="COST OF GOODS SOLD" colSpan={colCount} />
                {cogsRows.map(r => (
                  <DataRow
                    key={r.account}
                    label={r.acct_name}
                    cur={r.current_period}   pri={r.prior_period}
                    pri2={r.prior2_period}   ytdCur={r.ytd_current}  ytdPri={r.ytd_prior}
                    isExpense={true}
                    {...sharedProps}
                  />
                ))}
                <Spacer colSpan={colCount} />
                <TotalRow
                  label="Gross Profit"
                  cur={grossProfitCur}   pri={grossProfitPri}
                  pri2={grossProfitPri2} ytdCur={grossProfitYtdC} ytdPri={grossProfitYtdP}
                  variant="subtotal"
                  {...sharedProps}
                />
                <Spacer colSpan={colCount} />
              </>
            )}

            {/* ── SECTION 3: Operating Expenses ──
                Shown as positive magnitudes, no parens — standard
                presentation for this section (matches the backend
                report's docx_service.py, which uses _fmt_abs here too).
                isExpense={false} so DataRow doesn't force-negate; the
                Total row below uses the raw positive totalExpCur/etc
                rather than the negated -totalExpCur used elsewhere in
                this file, for the same reason. opIncCur and friends
                still subtract totalExpCur (the real positive sum), so
                this is a display-only change. */}
            <SectionHeader label="OPERATING EXPENSES" colSpan={colCount} />
            {opExpRows.map(r => (
              <DataRow
                key={r.account}
                label={r.acct_name}
                cur={r.current_period}   pri={r.prior_period}
                pri2={r.prior2_period}   ytdCur={r.ytd_current}  ytdPri={r.ytd_prior}
                isExpense={false}
                {...sharedProps}
              />
            ))}
            <TotalRow
              label="Total Operating Expenses"
              cur={totalExpCur}   pri={totalExpPri}
              pri2={hasPri2 ? totalExpPri2 : null}
              ytdCur={hasYtd ? totalExpYtdC : null}
              ytdPri={hasYtd ? totalExpYtdP : null}
              {...sharedProps}
            />

            <Spacer colSpan={colCount} />

            <TotalRow
              label="Operating Income / (Loss)"
              cur={opIncCur}   pri={opIncPri}
              pri2={opIncPri2} ytdCur={opIncYtdC} ytdPri={opIncYtdP}
              variant="subtotal"
              {...sharedProps}
            />

            <Spacer colSpan={colCount} />

            {/* ── SECTION 4: Other Income / (Expenses) ── */}
            {(nonOpRevRows.length > 0 || nonOpExpRows.length > 0) && (
              <>
                <SectionHeader label="OTHER INCOME / (EXPENSES)" colSpan={colCount} />
                {nonOpRevRows.map(r => (
                  <DataRow
                    key={r.account}
                    label={r.acct_name}
                    cur={r.current_period}   pri={r.prior_period}
                    pri2={r.prior2_period}   ytdCur={r.ytd_current}  ytdPri={r.ytd_prior}
                    isExpense={false}
                    {...sharedProps}
                  />
                ))}
                {nonOpExpRows.map(r => (
                  <DataRow
                    key={r.account}
                    label={r.acct_name}
                    cur={r.current_period}   pri={r.prior_period}
                    pri2={r.prior2_period}   ytdCur={r.ytd_current}  ytdPri={r.ytd_prior}
                    isExpense={true}
                    {...sharedProps}
                  />
                ))}
                <TotalRow
                  label="Total Other Income / (Expenses)"
                  cur={netOtherCur}   pri={netOtherPri}
                  pri2={netOtherPri2} ytdCur={netOtherYtdC} ytdPri={netOtherYtdP}
                  {...sharedProps}
                />
                <Spacer colSpan={colCount} />
              </>
            )}

            {/* ── Income Before Tax ── */}
            <TotalRow
              label="Income Before Income Tax"
              cur={preTaxCur}   pri={preTaxPri}
              pri2={preTaxPri2} ytdCur={preTaxYtdC} ytdPri={preTaxYtdP}
              variant="subtotal"
              {...sharedProps}
            />

            <Spacer colSpan={colCount} />

            {/* ── SECTION 5: Income Tax ──
                No parens (isExpense={false}) -- a standalone deduction
                line, not netted against anything else in this section,
                same convention as Operating Expenses. Label is hardcoded
                to "Income Tax" rather than r.acct_name, since the chart
                of accounts still literally has "Income Tax Expense" --
                mirrors docx_service.py's _income_stmt_label() on the
                backend report, so both surfaces show the same wording
                without touching the CoA data itself. */}
            {taxRows.length > 0 && (
              <>
                {taxRows.map(r => (
                  <DataRow
                    key={r.account}
                    label="Income tax"
                    cur={r.current_period}   pri={r.prior_period}
                    pri2={r.prior2_period}   ytdCur={r.ytd_current}  ytdPri={r.ytd_prior}
                    isExpense={false}
                    {...sharedProps}
                  />
                ))}
                <Spacer colSpan={colCount} />
              </>
            )}

            {/* ── NET INCOME — $ sign here ── */}
            <TotalRow
              label="NET INCOME / (LOSS)"
              cur={netIncCur}   pri={netIncPri}
              pri2={netIncPri2} ytdCur={netIncYtdC} ytdPri={netIncYtdP}
              variant="double"
              showDollar={true}
              {...sharedProps}
            />

          </tbody>
        </table>
    </div>
  )
}
