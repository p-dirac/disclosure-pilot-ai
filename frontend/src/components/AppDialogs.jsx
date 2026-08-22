import React from 'react'



export function UserGuideDialog({ onClose }) {
  return (
    <div style={overlayStyle}>
      <div style={dialogStyle}>
        <h2 style={{ fontFamily: "Arial, sans-serif", fontSize: "1.0rem", marginBottom: "0.5rem" }}>
          User Guide
        </h2>
        <div style={scrollContentStyle}>
        <p style={{ fontFamily: "Times New Roman, serif", fontSize: "1rem", lineHeight: "1.3", marginBottom: "0.5rem" }}>
          This application generates professional quarterly (10-Q) and annual (10-K) 
          financial reports from your general ledger data. Use the <strong>10-Q</strong> menu 
          to prepare and export quarterly reports, and the <strong>10-K</strong> menu for 
          annual reports. 
        </p>
        <p style={{ fontFamily: "Times New Roman, serif", fontSize: "1rem", lineHeight: "1.3", marginBottom: "0.5rem" }}>
          Each menu has three steps: 
        </p>
        <ul style={{ paddingLeft: "1.25rem", marginTop: "0.5rem", marginBottom: "0.5rem" }}>
          <li><strong>Prep</strong> to preview financial statements, and edit
          the MD&amp;A and Notes narratives
          </li> 
          <li><strong>Report</strong> to compile the final document 
          </li>
          <li><strong>EDGAR</strong> to export SEC-compliant HTML
          </li>
        </ul>
        <p style={{ fontFamily: "Times New Roman, serif", fontSize: "1rem", lineHeight: "1.3", marginBottom: "0.5rem" }}>
          <strong>Caution:</strong> After finishing the Prep step, pause to ensure that all 10-K or 10-Q docx files
          are completed and approved, before proceeding to the Report/EDGAR steps.
        </p>
        <p style={{ fontFamily: "Times New Roman, serif", fontSize: "1rem", lineHeight: "1.3", marginBottom: "0.5rem" }}>
          After creating the EDGAR report file, open the Arelle app
          to validate and view the report with hightlighted iXBRL.
        </p>
        </div>
        <button onClick={onClose} style={closeButtonStyle}>Close</button>
      </div>
    </div>
  );
}

export function AboutDialog({ onClose }) {
  return (
    <div style={overlayStyle}>
      <div style={{ ...dialogStyle, maxWidth: '400px' }}>
        <h2 style={{ fontFamily: "Arial, sans-serif", fontSize: "1.0rem", marginBottom: "0.5rem" }}>
          About
        </h2>
        <p style={{ fontFamily: "Times New Roman, serif", fontSize: "1rem", lineHeight: "1.3", marginBottom: "0.5rem" }}>
          Disclosure Pilot AI v1.0.0
        </p>
        <button onClick={onClose} style={closeButtonStyle}>Close</button>
      </div>
    </div>
  );
}

const overlayStyle = {
  position: "fixed",
  inset: 0,
  backgroundColor: "rgba(0,0,0,0.4)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 2000,
};

const dialogStyle = {
  backgroundColor: "#F3EFE0",
  borderRadius: "5px",
  padding: "0.5rem",
  maxWidth: "580px",
  width: "90%",
  maxHeight: "80vh",
  display: "flex",
  flexDirection: "column",
  boxShadow: "0 8px 32px rgba(0,0,0,0.2)",
};

const scrollContentStyle = {
  overflowY: "scroll",
  paddingRight: "0.5rem",
};

const closeButtonStyle = {
  marginTop: "0.5rem",
  padding: "0.3rem 0.3rem",
  fontFamily: "Arial, sans-serif",
  fontSize: "1rem",
  backgroundColor: "#E6E6E6",
  border: "none",
  borderRadius: "5px",
  cursor: "pointer",
};