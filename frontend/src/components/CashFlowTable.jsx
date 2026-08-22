// CashFlowTable.jsx
//
// Props:
//   rows        – cash_flow array from API (List[CashFlowRow])
//   quarter     – 1 | 2 | 3  (10-Q only; omit or pass null for 10-K)
//   periodLabel – current-year column header
//   priorLabel  – prior-year column header
//   prior2Label – two-years-back column header (10-K only)
//   dates       – dates dict from API (10-Q only)
//
// $ sign: Net Income row (first data row) and Cash at End of Period (last row).
// Reconciling Difference row is suppressed — not shown to users.

import React from "react";

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
  const abs    = numFmt.format(Math.abs(val));
  const prefix = showDollar ? "$" : "";
  return val < 0 ? `(${prefix}${abs})` : `${prefix}${abs}`;
}

const YTD_DURATION = { 1: "Three Months", 2: "Six Months", 3: "Nine Months" };

const SECTION_DESCS = new Set([
  "CASH FLOWS FROM OPERATING ACTIVITIES",
  "CASH FLOWS FROM INVESTING ACTIVITIES",
  "CASH FLOWS FROM FINANCING ACTIVITIES",
]);

const SUBTOTAL_DESCS = new Set([
  "Net Cash from Operating Activities",
  "Net Cash from Investing Activities",
  "Net Cash from Financing Activities",
]);

// Rows to suppress entirely — reconciling difference is an internal diagnostic
const HIDDEN_DESCS = new Set([
  "Reconciling Difference",
]);

const S = {
  tableWrap: { background: "#F5F5F5", borderRadius: 4, padding: "16px 20px", marginBottom: 24, overflowX: "auto", fontFamily: "'Times New Roman', serif", fontSize: "1rem", color: "#000" },
  title:     { fontFamily: "Arial, sans-serif", fontSize: "1.25rem", fontWeight: "bold", marginBottom: 12, color: "#000" },
  table:     { width: "100%", borderCollapse: "collapse", minWidth: 480 },
  thLeft:    { textAlign: "left",  fontFamily: "Arial, sans-serif", fontSize: "0.875rem", fontWeight: "bold", padding: "4px 8px", borderBottom: "2px solid #000" },
  th:        { textAlign: "right", fontFamily: "Arial, sans-serif", fontSize: "0.875rem", fontWeight: "bold", padding: "4px 8px", borderBottom: "2px solid #000", whiteSpace: "normal", minWidth: "90px" },
  secHdr:    { fontFamily: "Arial, sans-serif", fontWeight: "bold", fontSize: "0.9rem", padding: "10px 8px 2px 8px" },
  label:     { padding: "3px 8px 3px 28px" },
  num:       { textAlign: "right", padding: "3px 8px", whiteSpace: "nowrap" },
  totCell:   { fontWeight: "bold", padding: "4px 8px", borderTop: "1px solid #000", fontFamily: "Arial, sans-serif", fontSize: "0.9rem" },
  totNum:    { textAlign: "right", padding: "4px 8px", borderTop: "1px solid #000", fontWeight: "bold", whiteSpace: "nowrap" },
  dblCell:   { borderTop: "3px double #000", fontWeight: "bold", padding: "4px 8px", fontFamily: "Arial, sans-serif", fontSize: "0.9rem" },
  dblNum:    { borderTop: "3px double #000", textAlign: "right", fontWeight: "bold", padding: "4px 8px", whiteSpace: "nowrap" },
};

export default function CashFlowTable({ rows, quarter, dates, periodLabel, priorLabel, prior2Label }) {
  const is10K    = !quarter;
  const duration = YTD_DURATION[quarter] || "Period";

  function dateOnly(label) {
    return (label || "").replace(/^.+Ended\s+/i, "");
  }

  const curLabel = is10K
    ? (periodLabel || "")
    : (dates?.q_end
        ? `${duration} Ended\n ${fmtDate(dates.q_end)}`
        : `${duration} Ended\n ${dateOnly(periodLabel)}`);
  const priLabel = is10K
    ? (priorLabel || "")
    : (dates?.prior_q_end
        ? `${duration} Ended\n ${fmtDate(dates.prior_q_end)}`
        : `${duration} Ended\n ${dateOnly(priorLabel)}`);

  const showPrior2 = is10K && !!prior2Label;
  const cols = showPrior2 ? 4 : 3;

  // $ on Net Income (first data row) and Cash at End of Period (last row)
  const DOLLAR_DESCS = new Set(["Net Income", "Cash at End of Period"]);

  return (
    <div style={S.tableWrap}>
      <div style={S.title}>Statements of Cash Flows</div>
      <table style={S.table}>
        <thead>
          <tr>
            <th style={S.thLeft}></th>
            <th style={{ ...S.th, whiteSpace: 'pre-line' }}>{curLabel}</th>
            <th style={{ ...S.th, whiteSpace: 'pre-line' }}>{priLabel}</th>
            {showPrior2 && <th style={{ ...S.th, whiteSpace: 'pre-line' }}>{prior2Label}</th>}
          </tr>
        </thead>
        <tbody>
          {(rows || []).map((r, i) => {
            const desc = r.description || "";

            // Suppress reconciling difference — not shown to users
            if (HIDDEN_DESCS.has(desc)) return null;

            const showDollar = DOLLAR_DESCS.has(desc);

            // Section header
            if (SECTION_DESCS.has(desc)) {
              return (
                <tr key={i}>
                  <td style={S.secHdr} colSpan={cols}>{desc}</td>
                </tr>
              );
            }

            // Subtotal rows — single underline
            if (SUBTOTAL_DESCS.has(desc)) {
              return (
                <tr key={i}>
                  <td style={S.totCell}>{desc}</td>
                  <td style={S.totNum}>{fmtNum(r.current_period, showDollar)}</td>
                  <td style={S.totNum}>{fmtNum(r.prior_period,   showDollar)}</td>
                  {showPrior2 && <td style={S.totNum}>{fmtNum(r.prior2_period, showDollar)}</td>}
                </tr>
              );
            }

            // Net Increase / Cash at End — double underline
            if (desc === "Net Increase in Cash" || desc === "Cash at End of Period") {
              return (
                <tr key={i}>
                  <td style={S.dblCell}>{desc}</td>
                  <td style={S.dblNum}>{fmtNum(r.current_period, showDollar)}</td>
                  <td style={S.dblNum}>{fmtNum(r.prior_period,   showDollar)}</td>
                  {showPrior2 && <td style={S.dblNum}>{fmtNum(r.prior2_period, showDollar)}</td>}
                </tr>
              );
            }

            // Regular detail row
            return (
              <tr key={i}>
                <td style={S.label}>{desc}</td>
                <td style={S.num}>{fmtNum(r.current_period, showDollar)}</td>
                <td style={S.num}>{fmtNum(r.prior_period,   showDollar)}</td>
                {showPrior2 && <td style={S.num}>{fmtNum(r.prior2_period, showDollar)}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
