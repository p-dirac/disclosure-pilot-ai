/**
 * Frontend test suite
 * Uses Vitest + React Testing Library
 *
 * Install deps (if not already present):
 *   npm install -D vitest @vitest/ui jsdom
 *                  @testing-library/react @testing-library/jest-dom
 *                  @testing-library/user-event
 *
 * Add to vite.config.js:
 *   test: { environment: "jsdom", globals: true,
 *            setupFiles: "./src/test/setup.js" }
 *
 * Create src/test/setup.js:
 *   import "@testing-library/jest-dom";
 *
 * Run with:  npx vitest run
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

// ── Mock react-router-dom so components that use <Link> / useNavigate work ──
vi.mock("react-router-dom", () => ({
  BrowserRouter:  ({ children }) => <div>{children}</div>,
  Link:           ({ children, to }) => <a href={to}>{children}</a>,
  useNavigate:    () => vi.fn(),
  useLocation:    () => ({ pathname: "/" }),
  Routes:         ({ children }) => <div>{children}</div>,
  Route:          ({ element }) => element,
  NavLink:        ({ children, to }) => <a href={to}>{children}</a>,
}));

// ── Mock useAuth hook ───────────────────────────────────────────────────────
// `login` is a shared vi.fn() so LoginPage tests can control its resolved/
// rejected value per test (mockResolvedValueOnce / mockRejectedValueOnce),
// the same way api.post is controlled for other pages.
const mockLogin = vi.fn();
vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { email: "test@example.com" }, token: "fake-token", login: mockLogin, logout: vi.fn() }),
}));

// ── Mock axios (used by api/client.js) ───────────────────────────────────────
// LoginPage and other pages use axios via ../api/client, not global fetch.
// We mock the axios module so all api.post/get calls are intercepted.
vi.mock("../api/client", () => {
  const mockApi = {
    post: vi.fn(),
    get:  vi.fn(),
  };
  // Mock tenQApi and tenKApi named exports used by Prep/Report pages -
  // kept in sync with the real ../api/client.js export shape. Both were
  // missing getNotesList/getNotes/saveNotes/getReportsList entirely (added
  // when Notes generation and the EDGAR-HTML file picker were wired up),
  // and generateEdgar didn't match the real generateEdgarHtml method name.
  const mockTenQApi = {
    getFinancials:     vi.fn(),
    saveFinancials:    vi.fn(),
    getMDA:            vi.fn(),
    saveMDA:           vi.fn(),
    getNotesList:      vi.fn(),
    getNotes:          vi.fn(),
    saveNotes:         vi.fn(),
    generateReport:    vi.fn(),
    getReportsList:    vi.fn(),
    generateEdgarHtml: vi.fn(),
  };
  const mockTenKApi = {
    getFinancials:     vi.fn(),
    saveFinancials:    vi.fn(),
    getMDA:            vi.fn(),
    saveMDA:           vi.fn(),
    getNotesList:      vi.fn(),
    getNotes:          vi.fn(),
    saveNotes:         vi.fn(),
    generateReport:    vi.fn(),
    getReportsList:    vi.fn(),
    generateEdgarHtml: vi.fn(),
  };
  // authApi - used directly by LoginPage for signup/reset (login itself goes
  // through useAuth().login, mocked separately above, not through authApi).
  const mockAuthApi = {
    register:      vi.fn(),
    resetPassword: vi.fn(),
  };
  return { default: mockApi, tenQApi: mockTenQApi, tenKApi: mockTenKApi, authApi: mockAuthApi };
});

import api, { tenQApi, tenKApi, authApi } from "../api/client";

// mockFetch shim — resets all mocks between tests via beforeEach
const mockFetch = {
  mockReset: () => {
    api.post.mockReset();
    api.get.mockReset();
    mockLogin.mockReset();
    Object.values(tenQApi).forEach(fn => fn.mockReset?.());
    Object.values(tenKApi).forEach(fn => fn.mockReset?.());
    Object.values(authApi).forEach(fn => fn.mockReset?.());
    // getNotesList/getReportsList are called unconditionally in a
    // useEffect on mount by Prep10QPage/Prep10KPage/Edgar10QPage - every
    // test that renders one of those pages hits this, even tests that
    // never mention notes/reports, so they need a persistent default
    // resolved value or those pages crash with "Cannot read properties
    // of undefined (reading 'then')". mockReset() above wipes any default
    // set at mock-creation time, so it has to be reapplied here, after
    // the reset, every time. A test that needs a specific notes/reports
    // list can still override this with mockResolvedValueOnce(...), which
    // takes priority over this persistent mockResolvedValue(...) default.
    tenQApi.getNotesList.mockResolvedValue({ data: { notes: [] } });
    tenQApi.getReportsList.mockResolvedValue({ data: { filenames: [] } });
    tenKApi.getNotesList.mockResolvedValue({ data: { notes: [] } });
  }
};

function mockFetchOk(body) {
  api.post.mockResolvedValueOnce({ data: body });
  api.get.mockResolvedValueOnce({ data: body });
  Object.values(tenQApi).forEach(fn => fn.mockResolvedValueOnce?.({ data: body }));
  Object.values(tenKApi).forEach(fn => fn.mockResolvedValueOnce?.({ data: body }));
}

function mockFetchError(status = 401) {
  const err = Object.assign(new Error("Unauthorized"), {
    response: { status, data: { detail: "Unauthorized" } },
  });
  api.post.mockRejectedValueOnce(err);
  api.get.mockRejectedValueOnce(err);
  Object.values(tenQApi).forEach(fn => fn.mockRejectedValueOnce?.(err));
  Object.values(tenKApi).forEach(fn => fn.mockRejectedValueOnce?.(err));
}

// ── Sample data fixtures ─────────────────────────────────────────────────────

const BALANCE_SHEET_ROWS = [
  { account: 1000, acct_name: "Cash and Equivalents",    category: "Asset",     current_period: 500000,  prior_period: 400000  },
  { account: 1100, acct_name: "Accounts Receivable",     category: "Asset",     current_period: 120000,  prior_period: 100000  },
  { account: 2000, acct_name: "Accounts Payable",        category: "Liability", current_period: -80000,  prior_period: -60000  },
  { account: 3000, acct_name: "Common Stock",            category: "Equity",    current_period: -200000, prior_period: -200000 },
  { account: 3100, acct_name: "Retained Earnings",       category: "Equity",    current_period: -290000, prior_period: -190000 },
  { account: 3200, acct_name: "Treasury Stock",          category: "Equity",    current_period: 50000,   prior_period: 50000   },
];

const INCOME_STMT_ROWS = [
  { account: 4000, acct_name: "Product Revenue", category: "Revenue", current_period: 200000, prior_period: 150000, ytd_current: 200000, ytd_prior: 150000 },
  { account: 5000, acct_name: "Cost of Goods Sold", category: "Expense", current_period: -80000, prior_period: -60000, ytd_current: -80000, ytd_prior: -60000 },
];

const CASH_FLOW_ROWS = [
  { description: "CASH FLOWS FROM OPERATING ACTIVITIES", current_period: 0,      prior_period: 0 },
  { description: "Net Income",                           current_period: 120000,  prior_period: 90000 },
  { description: "Depreciation Expense",                 current_period: 5000,    prior_period: 4000 },
  { description: "Changes in Accounts Receivable",       current_period: -20000,  prior_period: -10000 },
  { description: "Net Cash from Operating Activities",   current_period: 105000,  prior_period: 84000 },
  { description: "CASH FLOWS FROM INVESTING ACTIVITIES", current_period: 0,      prior_period: 0 },
  { description: "Net Cash from Investing Activities",   current_period: 0,      prior_period: 0 },
  { description: "CASH FLOWS FROM FINANCING ACTIVITIES", current_period: 0,      prior_period: 0 },
  { description: "Dividends Paid",                       current_period: -25000,  prior_period: -20000 },
  { description: "Net Cash from Financing Activities",   current_period: -25000,  prior_period: -20000 },
  { description: "Net Increase in Cash",                 current_period: 80000,   prior_period: 64000 },
  { description: "Cash at Beginning of Period",          current_period: 420000,  prior_period: 336000 },
  { description: "Cash at End of Period",                current_period: 500000,  prior_period: 400000 },
];

const DATES = {
  quarter_start:      "2024-01-01",
  quarter_end:        "2024-03-31",
  year_start:         "2024-01-01",
  prior_quarter_start:"2023-01-01",
  prior_quarter_end:  "2023-03-31",
  prior_year_start:   "2023-01-01",
  prior_year_end:     "2023-12-31",
};

const FINANCIALS_RESPONSE = {
  balance_sheet:    BALANCE_SHEET_ROWS,
  income_statement: INCOME_STMT_ROWS,
  cash_flow:        CASH_FLOW_ROWS,
  period_label:     "Three Months Ended\n March 31, 2024",
  prior_label:      "Three Months Ended\n March 31, 2023",
  bs_prior_label:   "Year ended\n December 31, 2023",
  dates:            DATES,
};


// ════════════════════════════════════════════════════════════════════════════
// BalanceSheetTable
// ════════════════════════════════════════════════════════════════════════════

import BalanceSheetTable from "../components/BalanceSheetTable";

describe("BalanceSheetTable", () => {
  it("renders the Balance Sheets title", () => {
    render(<BalanceSheetTable data={BALANCE_SHEET_ROWS} dates={DATES}
              periodLabel="" priorLabel="" bsPriorLabel="Year ended December 31, 2023" />);
    expect(screen.getByText("Balance Sheets")).toBeInTheDocument();
  });

  it("shows current period column header from dates.quarter_end", () => {
    render(<BalanceSheetTable data={BALANCE_SHEET_ROWS} dates={DATES}
              periodLabel="" priorLabel="" bsPriorLabel="" />);
    expect(screen.getByText(/March 31, 2024/)).toBeInTheDocument();
  });

  it("shows prior period column header as Year ended Dec 31", () => {
    render(<BalanceSheetTable data={BALANCE_SHEET_ROWS} dates={DATES}
              periodLabel="" priorLabel="" bsPriorLabel="Year ended December 31, 2023" />);
    expect(screen.getByText(/Year Ended December 31, 2023/i)).toBeInTheDocument();
  });

  it("renders ASSETS, LIABILITIES, EQUITY section headers", () => {
    render(<BalanceSheetTable data={BALANCE_SHEET_ROWS} dates={DATES}
              periodLabel="" priorLabel="" bsPriorLabel="" />);
    expect(screen.getByText("ASSETS")).toBeInTheDocument();
    expect(screen.getByText("LIABILITIES")).toBeInTheDocument();
    expect(screen.getByText("EQUITY")).toBeInTheDocument();
  });

  it("renders account names", () => {
    render(<BalanceSheetTable data={BALANCE_SHEET_ROWS} dates={DATES}
              periodLabel="" priorLabel="" bsPriorLabel="" />);
    expect(screen.getByText("Cash and Equivalents")).toBeInTheDocument();
    expect(screen.getByText("Retained Earnings")).toBeInTheDocument();
  });

  it("renders Total Assets row", () => {
    render(<BalanceSheetTable data={BALANCE_SHEET_ROWS} dates={DATES}
              periodLabel="" priorLabel="" bsPriorLabel="" />);
    expect(screen.getByText("Total Assets")).toBeInTheDocument();
  });

  it("renders Total Liabilities + Equity row", () => {
    render(<BalanceSheetTable data={BALANCE_SHEET_ROWS} dates={DATES}
              periodLabel="" priorLabel="" bsPriorLabel="" />);
    expect(screen.getByText("Total Liabilities + Equity")).toBeInTheDocument();
  });

  it("renders without crashing when data is empty", () => {
    render(<BalanceSheetTable data={[]} dates={DATES}
              periodLabel="" priorLabel="" bsPriorLabel="" />);
    expect(screen.getByText("Balance Sheets")).toBeInTheDocument();
  });

  it("falls back to bsPriorLabel when dates.prior_year_end is absent", () => {
    // bsPriorLabel must be {}-wrapped, not a plain-quoted JSX attribute -
    // JSX string-literal attributes ("...") don't process escape sequences
    // the way JS string literals do, so a plain-quoted "...\n..." attribute
    // passes the literal two characters "\" + "n" through untouched,
    // while screen.getByText("...\n...") below (a real JS string literal,
    // since it's inside a function call) gets an actual newline - the two
    // never matched.
    // Use regex /.../ where \s+ matches one or more whitespace
    render(<BalanceSheetTable data={BALANCE_SHEET_ROWS} dates={{}}
              periodLabel="" priorLabel="Q label"
              bsPriorLabel={"Year ended\n December 31, 2023"} />);
    expect(screen.getByText(/Year ended\s+December 31, 2023/)).toBeInTheDocument();
  });

  it("shows dashes for zero values", () => {
    const zeroData = [{ account: 9999, acct_name: "Zero Acct",
                        category: "Asset", current_period: 0, prior_period: 0 }];
    render(<BalanceSheetTable data={zeroData} dates={DATES}
              periodLabel="" priorLabel="" bsPriorLabel="" />);
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
  });
});


// ════════════════════════════════════════════════════════════════════════════
// Navigation bar
// ════════════════════════════════════════════════════════════════════════════

// Import your NavBar component — adjust path to match your project
import NavBar from "../components/Navbar";

describe("NavBar", () => {
  it("renders Home link", () => {
    render(<NavBar />);
    expect(screen.getByText("Home")).toBeInTheDocument();
  });

  it("renders Quarterly 10-Q link", () => {
    render(<NavBar />);
    expect(screen.getByText(/10-Q/i)).toBeInTheDocument();
  });

  it("renders Yearly 10-K link", () => {
    render(<NavBar />);
    expect(screen.getByText(/10-K/i)).toBeInTheDocument();
  });

  it("renders Help link", () => {
    render(<NavBar />);
    expect(screen.getByText("Help")).toBeInTheDocument();
  });
});


// ════════════════════════════════════════════════════════════════════════════
// Login / Auth form
// ════════════════════════════════════════════════════════════════════════════

// LoginForm.jsx was retired in favor of LoginPage.jsx (rendered by
// AuthRoute in App.jsx when there's no authenticated user). LoginPage
// calls useAuth().login() for sign-in and authApi.register/resetPassword
// directly for the other two modes, rather than taking an onLogin prop.
import LoginPage from "../pages/LoginPage";

describe("LoginPage", () => {
  beforeEach(() => mockFetch.mockReset());

  it("renders email and password fields", () => {
    render(<LoginPage />);
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("renders login button", () => {
    render(<LoginPage />);
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  it("shows toggle to sign-up mode", async () => {
    render(<LoginPage />);
    const toggle = screen.getByText(/sign.?up|create account|register/i);
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: /sign.?up|register|create/i }))
      .toBeInTheDocument();
  });

  it("shows toggle to reset password mode", async () => {
    // LoginPage's link reads "Forgot password?" rather than "Reset Password".
    render(<LoginPage />);
    const toggle = screen.getByText(/forgot password/i);
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: /reset|send/i }))
      .toBeInTheDocument();
  });

  it("calls useAuth().login with email and password on submit", async () => {
    // Navigation to /home on success is handled by AuthRoute reacting to
    // the auth context's `user`, not by LoginPage itself - so this checks
    // that login() was invoked correctly rather than watching a redirect.
    mockLogin.mockResolvedValueOnce({});
    render(<LoginPage />);
    await userEvent.type(screen.getByLabelText(/email/i), "user@example.com");
    await userEvent.type(screen.getByLabelText(/password/i), "Pass1!");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() =>
      expect(mockLogin).toHaveBeenCalledWith("user@example.com", "Pass1!")
    );
  });

  it("shows error message on failed login", async () => {
    mockLogin.mockRejectedValueOnce(
      Object.assign(new Error("Unauthorized"), {
        response: { status: 401, data: { detail: "Unauthorized" } },
      })
    );
    render(<LoginPage />);
    await userEvent.type(screen.getByLabelText(/email/i), "bad@example.com");
    await userEvent.type(screen.getByLabelText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() =>
      expect(screen.getByText(/invalid|incorrect|unauthorized|error/i))
        .toBeInTheDocument()
    );
  });

  it("does not submit with empty fields", async () => {
    render(<LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(mockLogin).not.toHaveBeenCalled();
  });
});


// ════════════════════════════════════════════════════════════════════════════
// Prep 10-Q page
// ════════════════════════════════════════════════════════════════════════════

import Prep10QPage from "../pages/Prep10QPage";

describe("Prep10QPage", () => {
  beforeEach(() => mockFetch.mockReset());

  it("renders year and quarter dropdowns", () => {
    render(<Prep10QPage />);
    expect(screen.getByLabelText(/year/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/quarter/i)).toBeInTheDocument();
  });

  it("year dropdown contains current and 4 prior years", () => {
    render(<Prep10QPage />);
    const select = screen.getByLabelText(/year/i);
    const options = Array.from(select.querySelectorAll("option")).map(o => o.value);
    expect(options.length).toBe(4); // current year + 3 prior years
  });

  it("quarter dropdown only shows Q1, Q2, Q3", () => {
    render(<Prep10QPage />);
    const select = screen.getByLabelText(/quarter/i);
    const options = Array.from(select.querySelectorAll("option")).map(o => o.text);
    expect(options.some(o => o.includes("Q1"))).toBe(true);
    expect(options.some(o => o.includes("Q2"))).toBe(true);
    expect(options.some(o => o.includes("Q3"))).toBe(true);
    expect(options.some(o => o.includes("Q4"))).toBe(false);
  });

  it("renders Save Financials button", async () => {
    // Save/MD&A/Generate buttons appear only after financials are loaded
    tenQApi.getFinancials.mockResolvedValueOnce({ data: FINANCIALS_RESPONSE });
    render(<Prep10QPage />);
    await userEvent.click(screen.getByRole("button", { name: /load/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /save financials/i })).toBeInTheDocument()
    );
  });

  it("renders Save MD&A button", async () => {
    // "Submit MD&A" only appears after getMDA sets the mda state.
    // Load financials first, then generate MD&A.
    tenQApi.getFinancials.mockResolvedValueOnce({ data: FINANCIALS_RESPONSE });
    tenQApi.getMDA.mockResolvedValueOnce({ data: { narrative: "Test narrative." } });
    render(<Prep10QPage />);
    await userEvent.click(screen.getByRole("button", { name: /load financial/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /generate.*md&a|generate mda/i })).toBeInTheDocument()
    );
    await userEvent.click(screen.getByRole("button", { name: /generate.*md&a|generate mda/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /submit md&a/i })).toBeInTheDocument()
    );
  });

  it("renders Generate Notes button after MD&A loads", async () => {
    // Prep10QPage has no "Generate Report" button — that is on Report10QPage.
    // After financials + MD&A load, "Generate Notes" button becomes visible.
    tenQApi.getFinancials.mockResolvedValueOnce({ data: FINANCIALS_RESPONSE });
    tenQApi.getMDA.mockResolvedValueOnce({ data: { narrative: "Test narrative." } });
    render(<Prep10QPage />);
    await userEvent.click(screen.getByRole("button", { name: /load financial/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /generate.*md&a|generate mda/i })).toBeInTheDocument()
    );
    await userEvent.click(screen.getByRole("button", { name: /generate.*md&a|generate mda/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /generate notes/i })).toBeInTheDocument()
    );
  });

  it("fetches financials when year/quarter selected", async () => {
    tenQApi.getFinancials.mockResolvedValueOnce({ data: FINANCIALS_RESPONSE });
    render(<Prep10QPage />);
    const yearSel = screen.getByLabelText(/year/i);
    const qSel    = screen.getByLabelText(/quarter/i);
    await userEvent.selectOptions(yearSel, "2024");
    await userEvent.selectOptions(qSel, "1");
    await userEvent.click(
      screen.getByRole("button", { name: /load financial statements/i })
    );
    await waitFor(() => expect(tenQApi.getFinancials).toHaveBeenCalledWith(2024, 1));
  });

  it("shows acknowledgement after Save Financials", async () => {
    tenQApi.getFinancials.mockResolvedValueOnce({ data: FINANCIALS_RESPONSE });
    tenQApi.saveFinancials.mockResolvedValueOnce({ data: { message: "Financial statements saved to item010.docx" } });
    render(<Prep10QPage />);
    await userEvent.selectOptions(screen.getByLabelText(/year/i), "2024");
    await userEvent.selectOptions(screen.getByLabelText(/quarter/i), "1");
    await userEvent.click(
      screen.getByRole("button", { name: /load financial statements/i })
    );
    await waitFor(() => expect(tenQApi.getFinancials).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole("button", { name: /save financials/i }));
    await waitFor(() =>
      expect(screen.getByText(/saved|item010/i)).toBeInTheDocument()
    );
  });
});


// ════════════════════════════════════════════════════════════════════════════
// 10-Q Report page
// ════════════════════════════════════════════════════════════════════════════

import Report10QPage from "../pages/Report10QPage";

describe("Report10QPage", () => {
  beforeEach(() => mockFetch.mockReset());

  it("renders year and quarter dropdowns", () => {
    render(<Report10QPage />);
    expect(screen.getByLabelText(/year/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/quarter/i)).toBeInTheDocument();
  });

  it("renders Generate Report button", () => {
    render(<Report10QPage />);
    expect(screen.getByRole("button", { name: /generate.*report/i }))
      .toBeInTheDocument();
  });

  it("shows acknowledgement after report generation", async () => {
    mockFetchOk({ success: true, message: "10-Q report generated: sec-10q.docx" });
    render(<Report10QPage />);
    await userEvent.click(screen.getByRole("button", { name: /generate.*report/i }));
    await waitFor(() =>
      expect(screen.getByText(/generated|sec-10q/i)).toBeInTheDocument()
    );
  });
});


// ════════════════════════════════════════════════════════════════════════════
// 10-Q to EDGAR HTML page
// ════════════════════════════════════════════════════════════════════════════

import { Edgar10QPage } from "../pages/Edgar10QPage";

describe("Edgar10QPage", () => {
  beforeEach(() => mockFetch.mockReset());

  it("renders Generate EDGAR HTML button", () => {
    render(<Edgar10QPage />);
    expect(screen.getByRole("button", { name: /generate.*html|edgar/i }))
      .toBeInTheDocument();
  });

  it("shows acknowledgement after HTML generation", async () => {
    mockFetchOk({ success: true, message: "EDGAR HTML generated: sec-10q.htm" });
    render(<Edgar10QPage />);
    await userEvent.click(screen.getByRole("button", { name: /generate.*html|edgar/i }));
    await waitFor(() =>
      expect(screen.getByText(/generated|sec-10q\.htm/i)).toBeInTheDocument()
    );
  });
});


// ════════════════════════════════════════════════════════════════════════════
// Prep 10-K page
// ════════════════════════════════════════════════════════════════════════════

import Prep10KPage from "../pages/Prep10KPage";

describe("Prep10KPage", () => {
  beforeEach(() => mockFetch.mockReset());

  it("renders year dropdown only (no quarter)", () => {
    render(<Prep10KPage />);
    expect(screen.getByLabelText(/year/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/quarter/i)).not.toBeInTheDocument();
  });

  it("year dropdown contains current and 4 prior years", () => {
    render(<Prep10KPage />);
    const select = screen.getByLabelText(/year/i);
    const options = Array.from(select.querySelectorAll("option"));
    expect(options.length).toBe(4); // current year + 3 prior years
  });

  it("renders Save MD&A button", async () => {
    // "Submit MD&A" button only appears after getMDA populates the mda state.
    tenKApi.getFinancials.mockResolvedValueOnce({ data: FINANCIALS_RESPONSE });
    tenKApi.getMDA.mockResolvedValueOnce({ data: { narrative: "Test narrative." } });
    render(<Prep10KPage />);
    await userEvent.click(screen.getByRole("button", { name: /load financial/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /generate.*md&a|generate mda/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /generate.*md&a|generate mda/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /submit md&a/i })).toBeInTheDocument()
    );
  });

  it("renders Generate Notes button after financials load", async () => {
    // Prep10KPage has no "Generate Report" button — that lives on Report10KPage.
    // After financials + MD&A load, a "Generate Notes" button becomes available.
    tenKApi.getFinancials.mockResolvedValueOnce({ data: FINANCIALS_RESPONSE });
    tenKApi.getMDA.mockResolvedValueOnce({ data: { narrative: "Test narrative." } });
    render(<Prep10KPage />);
    await userEvent.click(screen.getByRole("button", { name: /load financial/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /generate.*md&a|generate mda/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /generate.*md&a|generate mda/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /generate notes/i })).toBeInTheDocument()
    );
  });
});


// ════════════════════════════════════════════════════════════════════════════
// Home page
// ════════════════════════════════════════════════════════════════════════════

import HomePage from "../pages/HomePage";

describe("HomePage", () => {
  it("renders welcome message", () => {
    render(<HomePage />);
    expect(
      screen.getByText(/generate.*quarterly.*yearly.*financial.*reports/i)
    ).toBeInTheDocument();
  });
});


// ════════════════════════════════════════════════════════════════════════════
// Help dialog — User Guide and About
// ════════════════════════════════════════════════════════════════════════════

import HelpMenu from "../components/HelpMenu";

describe("HelpMenu", () => {
  it("renders User Guide link", () => {
    render(<HelpMenu />);
    expect(screen.getByText(/user guide/i)).toBeInTheDocument();
  });

  it("renders About link", () => {
    render(<HelpMenu />);
    expect(screen.getByText(/about/i)).toBeInTheDocument();
  });

  it("User Guide dialog opens on click", async () => {
    render(<HelpMenu />);
    await userEvent.click(screen.getByText(/user guide/i));
    expect(screen.getByText(/financial reports|general ledger|sec-ready/i)).toBeInTheDocument();
  });

  it("About dialog shows version number", async () => {
    render(<HelpMenu />);
    await userEvent.click(screen.getByText(/about/i));
    expect(screen.getByText(/version|\d+\.\d+/i)).toBeInTheDocument();
  });
});
