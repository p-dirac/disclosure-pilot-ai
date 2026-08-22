// StockholdersEquityTable.jsx
//
// Props:
//   rows – stockholders_equity array from API (List[StockholdersEquityRow])
//
// Matrix layout (see equity-grid-gem.docx): each row is either a
// "Balance as of ..." row (is_balance_row=true, bold, every column shown)
// or a single activity row (only the columns it affects are set; the rest
// render as "—"). Columns: Common Stock | Treasury Stock |
// Retained Earnings | AOCI | Total Equity — no share-count column, no
// separate APIC column (no-par stock records full proceeds in Common
// Stock instead, so APIC would always be 0 and add nothing).
//
// accumulated_oci is always 0 today (not tracked in this chart of
// accounts) but the column stays in the table since it may be wired up
// later.

import React from "react";

const numFmt = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

function fmtNum(val, showDollar = false) {
  if (val === null || val === undefined || val === 0) return "—";
  const abs    = numFmt.format(Math.abs(val));
  const prefix = showDollar ? "$" : "";
  return val < 0 ? `(${prefix}${abs})` : `${prefix}${abs}`;
}

const S = {
  tableWrap: { background: "#F5F5F5", borderRadius: 4, padding: "16px 20px", marginBottom: 24, overflowX: "auto", fontFamily: "'Times New Roman', serif", fontSize: "1rem", color: "#000" },
  title:     { fontFamily: "Arial, sans-serif", fontSize: "1.25rem", fontWeight: "bold", marginBottom: 12, color: "#000" },
  table:     { width: "100%", borderCollapse: "collapse", minWidth: 640 },
  thLeft:    { textAlign: "left",  fontFamily: "Arial, sans-serif", fontSize: "0.875rem", fontWeight: "bold", padding: "4px 8px", borderBottom: "2px solid #000" },
  th:        { textAlign: "right", fontFamily: "Arial, sans-serif", fontSize: "0.875rem", fontWeight: "bold", padding: "4px 8px", borderBottom: "2px solid #000", whiteSpace: "normal", minWidth: "90px" },
  label:     { padding: "3px 8px 3px 28px" },
  num:       { textAlign: "right", padding: "3px 8px", whiteSpace: "nowrap" },
  numTotal:  { textAlign: "right", padding: "3px 8px", whiteSpace: "nowrap", fontWeight: "bold" },
  balCell:   { borderTop: "1px solid #000", fontWeight: "bold", padding: "4px 8px", fontFamily: "Arial, sans-serif", fontSize: "0.9rem" },
  balNum:    { borderTop: "1px solid #000", textAlign: "right", fontWeight: "bold", padding: "4px 8px", whiteSpace: "nowrap" },
  dblCell:   { borderTop: "3px double #000", fontWeight: "bold", padding: "4px 8px", fontFamily: "Arial, sans-serif", fontSize: "0.9rem" },
  dblNum:    { borderTop: "3px double #000", textAlign: "right", fontWeight: "bold", padding: "4px 8px", whiteSpace: "nowrap" },
  sectionRow: { fontWeight: "bold", padding: "8px 8px 3px", fontFamily: "Arial, sans-serif", fontSize: "0.95rem" },
  blankRow:  { padding: "6px 8px" },
};

const COLUMNS = [
  { key: "common_stock_amount",       label: "Common\nStock" },
  { key: "treasury_stock",             label: "Treasury\nStock" },
  { key: "retained_earnings",          label: "Retained\nEarnings" },
  { key: "accumulated_oci",            label: "Accumulated\nOther Comprehensive\nIncome (Loss)" },
  { key: "total_equity",               label: "Total\nStockholders'\nEquity", isTotal: true },
];

// A 10-Q's equity statement has FOUR independent rollforward blocks (see
// build_stockholders_equity_quarterly() in financial_service.py), grouped
// under two section labels — these rows carry only a `description`, no
// numeric data, so they need their own bold-label rendering rather than
// falling through to the plain activity-row path (which would otherwise
// print five "—" placeholders next to the label). A 10-K's continuous
// 3-year chain never produces either of these row kinds.
const SECTION_LABEL_RE = /^(?:Three|Six|Nine) Months Ended$/;

export default function StockholdersEquityTable({ rows }) {
  if (!rows || rows.length === 0) return null;

  // $ sign on the very first balance row and the very last balance row only
  const balanceIndices = rows.reduce((acc, r, i) => {
    if (r.is_balance_row) acc.push(i);
    return acc;
  }, []);
  const firstBalanceIdx = balanceIndices[0];
  const lastBalanceIdx  = balanceIndices[balanceIndices.length - 1];

  return (
    <div style={S.tableWrap}>
      <div style={S.title}>Statements of Stockholders' Equity</div>
      <table style={S.table}>
        <thead>
          <tr>
            <th style={S.thLeft}></th>
            {COLUMNS.map(col => (
              <th key={col.key} style={{ ...S.th, whiteSpace: "pre-line" }}>{col.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const showDollar = r.is_balance_row;

            if (SECTION_LABEL_RE.test(r.description)) {
              return (
                <tr key={i}>
                  <td colSpan={COLUMNS.length + 1} style={S.sectionRow}>{r.description}</td>
                </tr>
              );
            }

            if (!r.description && !r.is_balance_row) {
              // Blank separator row between independent rollforward
              // blocks (10-Q only) — genuinely empty, not five "—"s.
              return (
                <tr key={i}>
                  <td colSpan={COLUMNS.length + 1} style={S.blankRow}>&nbsp;</td>
                </tr>
              );
            }

            if (r.is_balance_row) {
              const isLast = i === lastBalanceIdx;
              const cellStyle = isLast ? S.dblCell : S.balCell;
              const numStyle  = isLast ? S.dblNum  : S.balNum;
              return (
                <tr key={i}>
                  <td style={cellStyle}>{r.description}</td>
                  {COLUMNS.map(col => (
                    <td key={col.key} style={numStyle}>{fmtNum(r[col.key], showDollar)}</td>
                  ))}
                </tr>
              );
            }

            return (
              <tr key={i}>
                <td style={S.label}>{r.description}</td>
                {COLUMNS.map(col => (
                  <td key={col.key} style={col.isTotal ? S.numTotal : S.num}>{fmtNum(r[col.key], showDollar)}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
