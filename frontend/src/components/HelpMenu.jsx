/**
 * HelpMenu.jsx
 * Dropdown Help menu with User Guide and About dialogs.
 * Rendered in Navbar.jsx and tested directly by the frontend test suite.
 */
import React, { useState } from "react";

const VERSION = "1.0.0";

export default function HelpMenu() {
  const [dialog, setDialog] = useState(null); // "guide" | "about" | null

  return (
    <div style={{ display: "inline-block", position: "relative" }}>
      {/* ── User Guide link ── */}
      <button
        onClick={() => setDialog("guide")}
        style={{ background: "none", border: "none", cursor: "pointer", fontFamily: "Arial" }}
      >
        User Guide
      </button>

      {/* ── About link ── */}
      <button
        onClick={() => setDialog("about")}
        style={{ background: "none", border: "none", cursor: "pointer", fontFamily: "Arial", marginLeft: 8 }}
      >
        About
      </button>

      {/* ── User Guide dialog ── */}
      {dialog === "guide" && (
        <div role="dialog" aria-modal="true" style={overlayStyle}>
          <div style={dialogStyle}>
            <h3 style={{ fontFamily: "Arial" }}>User Guide</h3>
            <p>
              This application generates quarterly (10-Q) and annual (10-K)
              financial reports from your general ledger data. Use the navigation
              links to prepare financial statements, review MD&amp;A narratives,
              and produce SEC-ready DOCX and EDGAR HTML output.
            </p>
            <button onClick={() => setDialog(null)} style={closeBtnStyle}>Close</button>
          </div>
        </div>
      )}

      {/* ── About dialog ── */}
      {dialog === "about" && (
        <div role="dialog" aria-modal="true" style={overlayStyle}>
          <div style={dialogStyle}>
            <h3 style={{ fontFamily: "Arial" }}>About</h3>
            <p>Disclosure Pilot AI — Financial Report Generator</p>
            <p>Version {VERSION}</p>
            <button onClick={() => setDialog(null)} style={closeBtnStyle}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}

const overlayStyle = {
  position: "fixed", inset: 0,
  background: "rgba(0,0,0,0.4)",
  display: "flex", alignItems: "center", justifyContent: "center",
  zIndex: 1000,
};

const dialogStyle = {
  background: "#fff",
  padding: "2rem",
  borderRadius: 8,
  maxWidth: 480,
  width: "90%",
  fontFamily: "Times New Roman",
};

const closeBtnStyle = {
  marginTop: "1rem",
  padding: "0.4rem 1rem",
  backgroundColor: "#E6E6E6",
  border: "1px solid #ccc",
  borderRadius: 5,
  cursor: "pointer",
  fontFamily: "Arial",
};
