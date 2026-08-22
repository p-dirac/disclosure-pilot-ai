// BalanceSheetTable.jsx
//
// Props:
//   data        – balance_sheet array from API (List[BalanceSheetRow])
//   dates       – { quarter_end, prior_year_end, … } from API
//   periodLabel – fallback column header
//   priorLabel  – fallback prior column header
//   bsPriorLabel– balance sheet specific prior label
//
// Sign convention (from backend):
//   Assets (debit-normal)         → positive  → show as-is
//   Contra-Asset (Acc. Dep.)       → negative  → fmtNum shows (parens)
//   Liabilities (credit-normal)    → positive  → show as-is
//   Equity (credit-normal)         → positive  → show as-is
//   Treasury Stock (contra-equity) → negative  → fmtNum shows (parens)
//
// $ sign: first data row of each statement, and last grand total row only.

import React, { useMemo } from "react";

function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  const month = new Date(y, m - 1, 1).toLocaleString("en-US", { month: "long" });
  return `${month} ${d}, ${y}`;
}

const numFmt = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

function fmtNum(val, showDollar = false) {
  if (val === null || val === undefined || val === 0) return "—";
  const abs = numFmt.format(Math.abs(val));
  const prefix = showDollar ? "$" : "";
  return val < 0 ? `(${prefix}${abs})` : `${prefix}${abs}`;
}

const S = {
  tableWrap:  { background: "#F5F5F5", borderRadius: 4, padding: "16px 20px", marginBottom: 24, overflowX: "auto", fontFamily: "'Times New Roman', serif", fontSize: "1rem", color: "#000" },
  title:      { fontFamily: "Arial, sans-serif", fontSize: "1.25rem", fontWeight: "bold", marginBottom: 12, color: "#000" },
  table:      { width: "100%", borderCollapse: "collapse", minWidth: 480 },
  thLeft:     { textAlign: "left",  fontFamily: "Arial, sans-serif", fontSize: "0.875rem", fontWeight: "bold", padding: "4px 8px", borderBottom: "2px solid #000" },
  th:         { textAlign: "right", fontFamily: "Arial, sans-serif", fontSize: "0.875rem", fontWeight: "bold", padding: "4px 8px", borderBottom: "2px solid #000", whiteSpace: "normal", textWrap: "balance" },
  secHdr:     { fontFamily: "Arial, sans-serif", fontWeight: "bold", fontSize: "0.9rem", padding: "10px 8px 2px 8px" },
  label:      { padding: "3px 8px 3px 28px" },
  num:        { textAlign: "right", padding: "3px 8px", whiteSpace: "nowrap" },
  totCell:    { fontWeight: "bold", padding: "4px 8px", borderTop: "1px solid #000", fontFamily: "Arial, sans-serif", fontSize: "0.9rem" },
  totNum:     { textAlign: "right", padding: "4px 8px", borderTop: "1px solid #000", fontWeight: "bold", whiteSpace: "nowrap" },
  dblCell:    { borderTop: "3px double #000", fontWeight: "bold", padding: "4px 8px", fontFamily: "Arial, sans-serif", fontSize: "0.9rem" },
  dblNum:     { borderTop: "3px double #000", textAlign: "right", fontWeight: "bold", padding: "4px 8px", whiteSpace: "nowrap" },
};

export default function BalanceSheetTable({ data, dates, periodLabel, priorLabel, bsPriorLabel }) {
  const qEnd    = dates?.quarter_end || dates?.q_end;
  const colLabel = qEnd ? fmtDate(qEnd) : (periodLabel || "");
  const priorCol = dates?.prior_year_end
    ? `Year ended\n ${fmtDate(dates.prior_year_end)}`
    : (bsPriorLabel || priorLabel || "");

  const { assets, liabilities, equity } = useMemo(() => {
    const assets = [], liabilities = [], equity = [];
    (data || []).forEach(r => {
      if      (r.category === "Asset")     assets.push(r);
      else if (r.category === "Liability") liabilities.push(r);
      else if (r.category === "Equity")    equity.push(r);
    });
    return { assets, liabilities, equity };
  }, [data]);

  const sectionSum  = (rows, field) => rows.reduce((acc, r) => acc + (r[field] || 0), 0);
  const liabSum     = (rows, field) => Math.abs(rows.reduce((acc, r) => acc + (r[field] || 0), 0));

  const totLiabCur   = liabSum(liabilities, "current_period");
  const totEquityCur = sectionSum(equity,   "current_period");
  const totLiabPri   = liabSum(liabilities, "prior_period");
  const totEquityPri = sectionSum(equity,   "prior_period");

  // First data row index across ALL sections — gets the $ sign
  const firstAsset = assets[0];
  console.log("colLabel:", colLabel);
  return (
    <div style={S.tableWrap}>
      <div style={S.title}>Balance Sheets</div>
      <table style={S.table}>
        <thead>
          <tr>
            <th style={S.thLeft}></th>
            <th style={{ ...S.th, whiteSpace: 'pre-line' }}>{colLabel}</th>
            <th style={{ ...S.th, whiteSpace: 'pre-line' }}>{priorCol}</th>
          </tr>
        </thead>
        <tbody>
          {/* ASSETS */}
          <tr><td style={S.secHdr} colSpan={3}>ASSETS</td></tr>
          {assets.map((r, i) => (
            <tr key={r.account}>
              <td style={S.label}>{r.acct_name}</td>
              <td style={S.num}>{fmtNum(r.current_period, i === 0)}</td>
              <td style={S.num}>{fmtNum(r.prior_period,   i === 0)}</td>
            </tr>
          ))}
          <tr>
            <td style={S.totCell}>Total Assets</td>
            <td style={S.totNum}>{fmtNum(sectionSum(assets, "current_period"))}</td>
            <td style={S.totNum}>{fmtNum(sectionSum(assets, "prior_period"))}</td>
          </tr>

          {/* LIABILITIES */}
          <tr><td style={S.secHdr} colSpan={3}>LIABILITIES</td></tr>
          {liabilities.map((r, i) => (
            <tr key={r.account}>
              <td style={S.label}>{r.acct_name}</td>
              <td style={S.num}>{fmtNum(Math.abs(r.current_period), i === 0)}</td>
              <td style={S.num}>{fmtNum(Math.abs(r.prior_period),   i === 0)}</td>
            </tr>
          ))}
          <tr>
            <td style={S.totCell}>Total Liabilities</td>
            <td style={S.totNum}>{fmtNum(totLiabCur)}</td>
            <td style={S.totNum}>{fmtNum(totLiabPri)}</td>
          </tr>

          {/* EQUITY */}
          <tr><td style={S.secHdr} colSpan={3}>EQUITY</td></tr>
          {equity.map((r, i) => (
            <tr key={r.account}>
              <td style={S.label}>{r.acct_name}</td>
              <td style={S.num}>{fmtNum(r.current_period, i === 0)}</td>
              <td style={S.num}>{fmtNum(r.prior_period,   i === 0)}</td>
            </tr>
          ))}
          <tr>
            <td style={S.totCell}>Total Equity</td>
            <td style={S.totNum}>{fmtNum(totEquityCur)}</td>
            <td style={S.totNum}>{fmtNum(totEquityPri)}</td>
          </tr>

          {/* GRAND TOTAL — double underline, $ sign */}
          <tr>
            <td style={S.dblCell}>Total Liabilities + Equity</td>
            <td style={S.dblNum}>{fmtNum(totLiabCur + totEquityCur, true)}</td>
            <td style={S.dblNum}>{fmtNum(totLiabPri + totEquityPri, true)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
