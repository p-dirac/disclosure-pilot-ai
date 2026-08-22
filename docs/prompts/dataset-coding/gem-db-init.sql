-- 1. Base Tables (No Dependencies)
CREATE TABLE bookkeeper.chart_of_acct (
    account_number VARCHAR(10) NOT NULL PRIMARY KEY,
    account_name VARCHAR(100) NOT NULL,
    account_type VARCHAR(30) NOT NULL CHECK (account_type IN ('Asset', 'Liability', 'Equity', 'Revenue', 'Expense')),
    account_subtype VARCHAR(30) NOT NULL,
    normal_balance VARCHAR(6) NOT NULL CHECK (normal_balance IN ('Debit', 'Credit'))
);

CREATE TABLE bookkeeper.vendors (
    vendor_id VARCHAR(15) NOT NULL PRIMARY KEY,
    vendor_name VARCHAR(50) NOT NULL
);

CREATE TABLE bookkeeper.customers (
    customer_id VARCHAR(15) NOT NULL PRIMARY KEY,
    customer_name VARCHAR(50) NOT NULL
);

-- 2. Core Transaction Ledger
CREATE TABLE bookkeeper.journal_entries (
    id BIGSERIAL PRIMARY KEY,
    entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
    description TEXT NOT NULL,
    status VARCHAR(15) NOT NULL DEFAULT 'DRAFT', 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bookkeeper.journal_lines (
    id BIGSERIAL PRIMARY KEY,
    journal_entry_id BIGINT NOT NULL REFERENCES bookkeeper.journal_entries(id) ON DELETE CASCADE,
    account_number VARCHAR(10) NOT NULL REFERENCES bookkeeper.chart_of_acct(account_number), -- Added FK
    debit NUMERIC(15, 4) NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit NUMERIC(15, 4) NOT NULL DEFAULT 0 CHECK (credit >= 0),
    currency VARCHAR(3) DEFAULT 'USD' NOT NULL,
    CONSTRAINT chk_debit_credit_exclusivity CHECK (
        (debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)
    )
);

-- Indexing for performance
CREATE INDEX idx_journal_lines_entry ON bookkeeper.journal_lines(journal_entry_id);
CREATE INDEX idx_journal_lines_account ON bookkeeper.journal_lines(account_number);
CREATE INDEX idx_journal_entries_date_posted ON bookkeeper.journal_entries(entry_date) 
WHERE status = 'POSTED';
CREATE INDEX idx_ap_vendor ON bookkeeper.accounts_payable(vendor_id);
CREATE INDEX idx_ar_customer ON bookkeeper.accounts_receivable(customer_id);
CREATE INDEX idx_depreciation_asset ON bookkeeper.depreciation(asset_id);

-- 3. Validation Logic (Statement-level optimization)
-- 1. The Validation Function (Reverted to row-by-row identification)
CREATE OR REPLACE FUNCTION bookkeeper.enforce_journal_entry_balance()
RETURNS TRIGGER AS $$
DECLARE
    v_journal_entry_id BIGINT;
    v_balance_check NUMERIC(15, 4);
BEGIN
    -- Identify the journal entry being modified (handles inserts, updates, and deletes)
    v_journal_entry_id := COALESCE(NEW.journal_entry_id, OLD.journal_entry_id);

    -- Calculate the net balance (Debits minus Credits) for this specific journal entry
    SELECT COALESCE(SUM(debit) - SUM(credit), 0)
    INTO v_balance_check
    FROM bookkeeper.journal_lines
    WHERE journal_entry_id = v_journal_entry_id;

    -- If the balance does not equal 0, halt the commit and throw an error
    IF v_balance_check != 0 THEN
        RAISE EXCEPTION 'Transaction ID % is unbalanced by %. Total Debits must equal Total Credits.', 
            v_journal_entry_id, v_balance_check;
    END IF;

    RETURN NULL; 
END;
$$ LANGUAGE plpgsql;

-- 2. The Deferrable Constraint Trigger (Strictly FOR EACH ROW)
CREATE CONSTRAINT TRIGGER trigger_enforce_balance
AFTER INSERT OR UPDATE OR DELETE
ON bookkeeper.journal_lines
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION bookkeeper.enforce_journal_entry_balance();

-- 4. View 
CREATE VIEW bookkeeper.general_ledger AS
SELECT 
    l.id AS line_id,
    e.entry_date,
    l.account_number,
    l.debit,
    l.credit,
	l.currency,
    e.description
FROM bookkeeper.journal_lines l
JOIN bookkeeper.journal_entries e ON l.journal_entry_id = e.id
WHERE e.status = 'POSTED';

-- 5. Sub-ledgers & Operations
CREATE TABLE bookkeeper.accounts_payable (
    ap_id VARCHAR(15) NOT NULL PRIMARY KEY,
    vendor_id VARCHAR(15) NOT NULL REFERENCES bookkeeper.vendors(vendor_id),
    invoice_date DATE NOT NULL,
    due_date DATE NOT NULL,
    amount NUMERIC(15, 4) NOT NULL,
    status VARCHAR(15) NOT NULL DEFAULT 'UNPAID' 
        CHECK (status IN ('UNPAID', 'PAID', 'VOIDED')),
    currency VARCHAR(3) DEFAULT 'USD' NOT NULL,
    
    -- The paper trail linking straight to the General Ledger
    recognition_entry_id BIGINT NOT NULL REFERENCES bookkeeper.journal_entries(id),
    settlement_entry_id BIGINT REFERENCES bookkeeper.journal_entries(id),
    
    -- Integrity Check: If status is PAID, we MUST have a settlement journal entry
    CONSTRAINT chk_ap_payment_trail CHECK (
        (status = 'PAID' AND settlement_entry_id IS NOT NULL) OR
        (status != 'PAID' AND settlement_entry_id IS NULL)
    )
);

CREATE TABLE bookkeeper.accounts_receivable (
    ar_id VARCHAR(15) NOT NULL PRIMARY KEY,
    customer_id VARCHAR(15) NOT NULL REFERENCES bookkeeper.customers(customer_id),
    invoice_date DATE NOT NULL,
    due_date DATE NOT NULL,
    amount NUMERIC(15, 4) NOT NULL,
    status VARCHAR(15) NOT NULL DEFAULT 'UNPAID' 
        CHECK (status IN ('UNPAID', 'PAID', 'VOIDED', 'BAD_DEBT')),
    currency VARCHAR(3) DEFAULT 'USD' NOT NULL,
    
    -- The paper trail linking straight to the General Ledger
    recognition_entry_id BIGINT NOT NULL REFERENCES bookkeeper.journal_entries(id),
    settlement_entry_id BIGINT REFERENCES bookkeeper.journal_entries(id),
	writeoff_entry_id BIGINT REFERENCES bookkeeper.journal_entries(id),
    
    -- Integrity Check: If status is PAID, we MUST have a settlement journal entry
    CONSTRAINT chk_ar_payment_trail CHECK (
    (status = 'PAID' AND settlement_entry_id IS NOT NULL AND writeoff_entry_id IS NULL) OR
    (status = 'BAD_DEBT' AND writeoff_entry_id IS NOT NULL AND settlement_entry_id IS NULL) OR
    (status IN ('UNPAID', 'VOIDED') AND settlement_entry_id IS NULL AND writeoff_entry_id IS NULL)

);

CREATE TABLE bookkeeper.assets (
    asset_id VARCHAR(15) NOT NULL PRIMARY KEY,
    asset_name VARCHAR(100) NOT NULL, 
    purchase_date DATE NOT NULL,
    cost NUMERIC(15, 4) NOT NULL,     -- Standardized numeric precision
    service_years INTEGER NOT NULL,
    dept VARCHAR(15),
    currency VARCHAR(3) DEFAULT 'USD' NOT NULL,
	recognition_entry_id BIGINT REFERENCES bookkeeper.journal_entries(id)
);

CREATE TABLE bookkeeper.loans (
	loan_id VARCHAR(15) NOT NULL PRIMARY KEY,
	asset_id VARCHAR(15) NULL,
	principal NUMERIC(15, 4) NOT NULL,
	rate NUMERIC(15, 4) NOT NULL,
	term_months INTEGER NOT NULL, 
	start_date DATE NOT NULL, 
	monthly_payment NUMERIC(15, 4) NOT NULL
 );

CREATE TABLE bookkeeper.depreciation (
    depreciation_id VARCHAR(15) NOT NULL PRIMARY KEY,
    asset_id VARCHAR(15) NOT NULL REFERENCES bookkeeper.assets(asset_id), 
    fiscal_year INTEGER NOT NULL,
    fiscal_period INTEGER NOT NULL, -- 1 through 12
    period_depreciation NUMERIC(15, 4) NOT NULL,
    accumulated_thru_period NUMERIC(15, 4) NOT NULL,
    currency VARCHAR(3) DEFAULT 'USD' NOT NULL,
    
    -- Crucial for financial integrity: prevents duplicate entries for the same month
    CONSTRAINT unique_asset_period_performance UNIQUE (asset_id, fiscal_year, fiscal_period),
    -- Business rule: periods must map to standard calendar/fiscal months
    CONSTRAINT valid_period CHECK (fiscal_period BETWEEN 1 AND 12)
);
