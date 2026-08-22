import React from 'react'
 import { useState, useRef, useEffect } from "react";

const NAV_STYLES = {
  navbar: {
    display: "flex",
    alignItems: "center",
    backgroundColor: "#CBE6EF",
    padding: "0 2rem",
    height: "52px",
    fontFamily: "Arial, sans-serif",
    fontSize: "1rem",
    color: "#000000",
    boxShadow: "0 2px 4px rgba(0,0,0,0.12)",
    position: "sticky",
    top: 0,
    zIndex: 1000,
  },
  brand: {
    fontFamily: "Arial, sans-serif",
    fontWeight: "700",
    fontSize: "1.25rem",
    color: "#000000",
    marginRight: "2rem",
    textDecoration: "none",
    whiteSpace: "nowrap",
  },
  navList: {
    display: "flex",
    alignItems: "center",
    listStyle: "none",
    margin: 0,
    padding: 0,
    gap: "0.25rem",
    flex: 1,
  },
  navItem: {
    position: "relative",
  },
  navLink: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.3rem",
    padding: "0.4rem 0.85rem",
    borderRadius: "5px",
    color: "#000000",
    textDecoration: "none",
    fontFamily: "Arial, sans-serif",
    fontSize: "1rem",
    cursor: "pointer",
    background: "transparent",
    border: "none",
    whiteSpace: "nowrap",
    transition: "background-color 0.15s",
  },
  navLinkHover: {
    backgroundColor: "rgba(0,0,0,0.08)",
  },
  dropdownMenu: {
    position: "absolute",
    top: "calc(100% + 4px)",
    left: 0,
    backgroundColor: "#CBE6EF",
    border: "1px solid rgba(0,0,0,0.15)",
    borderRadius: "5px",
    boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
    minWidth: "150px",
    zIndex: 2000,
    overflow: "hidden",
  },
  dropdownItem: {
    display: "block",
    width: "100%",
    padding: "0.55rem 1rem",
    fontFamily: "Arial, sans-serif",
    fontSize: "1rem",
    color: "#000000",
    textDecoration: "none",
    background: "transparent",
    border: "none",
    textAlign: "left",
    cursor: "pointer",
    transition: "background-color 0.15s",
    whiteSpace: "nowrap",
  },
  dropdownItemHover: {
    backgroundColor: "rgba(0,0,0,0.08)",
  },
  chevron: {
    fontSize: "0.65rem",
    transition: "transform 0.2s",
  },
  chevronOpen: {
    transform: "rotate(180deg)",
  },
};


// ── Dropdown Component ────────────────────────────────────────────────
function NavDropdown({ label, items, onNavigate }) {
  const [open, setOpen] = useState(false);
  const [hoveredTrigger, setHoveredTrigger] = useState(false);
  const [hoveredItem, setHoveredItem] = useState(null);
  const ref = useRef(null);

  // Close when clicking outside
  useEffect(() => {
    function handleClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <li style={NAV_STYLES.navItem} ref={ref}>
      <button
        style={{
          ...NAV_STYLES.navLink,
          ...(hoveredTrigger || open ? NAV_STYLES.navLinkHover : {}),
        }}
        onMouseEnter={() => setHoveredTrigger(true)}
        onMouseLeave={() => setHoveredTrigger(false)}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="true"
        aria-expanded={open}
      >
        {label}
        <span
          style={{
            ...NAV_STYLES.chevron,
            ...(open ? NAV_STYLES.chevronOpen : {}),
          }}
        >
          ▼
        </span>
      </button>

      {open && (
        <div style={NAV_STYLES.dropdownMenu} role="menu">
          {items.map((item) => (
            <button
              key={item.key}
              role="menuitem"
              style={{
                ...NAV_STYLES.dropdownItem,
                ...(hoveredItem === item.key ? NAV_STYLES.dropdownItemHover : {}),
              }}
              onMouseEnter={() => setHoveredItem(item.key)}
              onMouseLeave={() => setHoveredItem(null)}
              onClick={() => {
				console.log("nav item clicked:", item.key);
                setOpen(false);
                onNavigate && onNavigate(item.key);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </li>
  );
}

// ── Simple Nav Link ───────────────────────────────────────────────────
function NavLinkItem({ label, navKey, onNavigate }) {
  const [hovered, setHovered] = useState(false);
  return (
    <li style={NAV_STYLES.navItem}>
      <button
        style={{
          ...NAV_STYLES.navLink,
          ...(hovered ? NAV_STYLES.navLinkHover : {}),
        }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onClick={() => onNavigate && onNavigate(navKey)}
      >
        {label}
      </button>
    </li>
  );
}

// ── Help Dropdown ─────────────────────────────────────────────────────
function HelpDropdown({ onNavigate }) {
  return (
    <NavDropdown
      label="Help"
      items={[
        { key: "user-guide", label: "User Guide" },
        { key: "about", label: "About" },
      ]}
      onNavigate={onNavigate}
    />
  );
}

// ── Main Navbar ───────────────────────────────────────────────────────
export default function Navbar({ onNavigate, activePage }) {
  const handleNavigate = onNavigate || ((key) => console.log("Navigate to:", key));

  return (
    <nav style={NAV_STYLES.navbar} aria-label="Main navigation">
      {/* Brand */}
      <a href="#home" style={NAV_STYLES.brand} onClick={(e) => { e.preventDefault(); handleNavigate("home"); }}>
        Disclosure Pilot AI
      </a>

      {/* Nav Items */}
      <ul style={NAV_STYLES.navList}>
        <NavLinkItem label="Home" navKey="home" onNavigate={handleNavigate} />

        {/* 10-Q Dropdown */}
        <NavDropdown
          label="10-Q"
          items={[
            { key: "10q-prep", label: "Prep" },
            { key: "10q-report", label: "Report" },
            { key: "10q-edgar", label: "EDGAR" },
          ]}
          onNavigate={handleNavigate}
        />

        {/* 10-K Dropdown */}
        <NavDropdown
          label="10-K"
          items={[
            { key: "10k-prep", label: "Prep" },
            { key: "10k-report", label: "Report" },
            { key: "10k-edgar", label: "EDGAR" },
          ]}
          onNavigate={handleNavigate}
        />

        {/* Help Dropdown */}
        <HelpDropdown onNavigate={handleNavigate} />
      </ul>
    </nav>
  );
}
