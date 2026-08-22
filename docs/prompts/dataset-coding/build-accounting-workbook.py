import pandas as pd, random, math
from datetime import date, datetime, timedelta
import xlsxwriter
from dateutil.relativedelta import relativedelta

random.seed(42)

coa = pd.read_csv('coa.csv')
assets_df = pd.read_csv('pre-pop-assets.csv')
customers_df = pd.read_csv('pre-pop-cust.csv')
vendors_df = pd.read_csv('pre-pop-vend.csv')

acct_map = dict(zip(coa['account_name'], coa['account_number']))

journal_entries = []
journal_lines = []
accounts_payable = []
accounts_receivable = []
depreciation_records = []
loans = []

je_id = 1; jl_id = 1; ar_seq = 1; ap_seq = 1; loan_seq = 1

def add_je(entry_date, description):
    global je_id
    journal_entries.append({'id': je_id, 'entry_date': entry_date, 'description': description, 'status': 'POSTED', 'created_at': datetime.now()})
    je_id += 1
    return je_id - 1

def add_jl(je_id, account_name, debit=0, credit=0):
    global jl_id
    journal_lines.append({'id': jl_id, 'journal_entry_id': je_id, 'account_number': acct_map[account_name], 'debit': round(debit,4), 'credit': round(credit,4), 'currency': 'USD'})
    jl_id += 1

def add_months(d, months):
    return d + relativedelta(months=months)

# Phase 1
je = add_je(date(2022,1,1), 'Initial capitalization')
add_jl(je, 'Cash and Equivalents', debit=25000000)
add_jl(je, 'Common Stock', credit=25000000)

je = add_je(date(2022,1,1), 'Initial inventory purchase')
add_jl(je, 'Inventory', debit=2000000)
add_jl(je, 'Cash and Equivalents', credit=2000000)

# Phase 2
assets_df['recognition_entry_id'] = None
for idx, asset in assets_df.iterrows():
    purchase_date = pd.to_datetime(asset['purchase_date']).date()
    cost = asset['cost']; sy = asset['service_years']
    je = add_je(purchase_date, f"Asset purchase {asset['asset_id']}")
    add_jl(je, 'Property, Plant & Equip', debit=cost)
    if sy > 8:
        add_jl(je, 'Long-term Debt', credit=cost); rate=0.10; term=72; debt_acct='Long-term Debt'
    elif sy >= 7:
        add_jl(je, 'Long-term Debt', credit=cost); rate=0.08; term=60; debt_acct='Long-term Debt'
    elif sy >= 5:
        add_jl(je, 'Short-term Debt', credit=cost); rate=0.04; term=12; debt_acct='Short-term Debt'
    else:
        add_jl(je, 'Cash and Equivalents', credit=cost); rate=None
    assets_df.at[idx, 'recognition_entry_id'] = je
    if rate:
        r = rate/12; n = term
        payment = round(cost * r * (1+r)**n / ((1+r)**n - 1), 2)
        loan_id = f"LOAN{loan_seq:04d}"; loan_seq += 1
        loans.append({'loan_id': loan_id, 'asset_id': asset['asset_id'], 'principal': cost, 'rate': rate, 'term_months': term, 'start_date': purchase_date, 'monthly_payment': payment})
        balance = cost
        for m in range(1, term+1):
            pay_date = add_months(purchase_date, m)
            if pay_date > date(2025,12,31): break
            interest = round(balance * r, 2)
            principal_pay = round(payment - interest, 2)
            balance = round(balance - principal_pay, 2)
            je_p = add_je(pay_date, f'Loan payment {loan_id}')
            add_jl(je_p, 'Interest Expense', debit=interest)
            add_jl(je_p, debt_acct, debit=principal_pay)
            add_jl(je_p, 'Cash and Equivalents', credit=payment)

for yr in [2022,2023,2024,2025]:
    je = add_je(date(yr,12,31), 'Year-end common stock issuance')
    add_jl(je, 'Cash and Equivalents', debit=1000000)
    add_jl(je, 'Common Stock', credit=1000000)
    je = add_je(date(yr,7,1), 'Legal fees H1')
    add_jl(je, 'Legal & Professional Fees', debit=100000)
    add_jl(je, 'Cash and Equivalents', credit=100000)
    je = add_je(date(yr,12,1), 'Legal fees H2')
    add_jl(je, 'Legal & Professional Fees', debit=400000)
    add_jl(je, 'Cash and Equivalents', credit=400000)

def get_balance(account_name, up_to_date):
    acct = acct_map[account_name]
    debits = sum(jl['debit'] for jl in journal_lines if jl['account_number']==acct and next((je['entry_date'] for je in journal_entries if je['id']==jl['journal_entry_id']), date.max) <= up_to_date)
    credits = sum(jl['credit'] for jl in journal_lines if jl['account_number']==acct and next((je['entry_date'] for je in journal_entries if je['id']==jl['journal_entry_id']), date.max) <= up_to_date)
    normal = coa.loc[coa['account_name']==account_name, 'normal_balance'].values[0]
    return debits - credits if normal=='Debit' else credits - debits

short_term_investments = []
current = date(2022,1,1)
end = date(2025,12,31)

while current <= end:
    year = current.year; month = current.month
    month_start = date(year, month, 1)
    month_end = (month_start + relativedelta(months=1) - timedelta(days=1))
    
    je = add_je(month_start, 'Monthly lease')
    add_jl(je, 'Lease Expense', debit=100000)
    add_jl(je, 'Cash and Equivalents', credit=100000)
    
    je = add_je(month_start, 'Consulting revenue')
    add_jl(je, 'Cash and Equivalents', debit=125000)
    add_jl(je, 'Consulting', credit=125000)
    
    quarter = (month-1)//3 + 1
    if year==2022: ns = 5 if quarter<=2 else 4
    elif year==2023: ns = 3 if quarter<=2 else 5
    elif year==2024: ns = 6 if quarter<=2 else 7
    else: ns = 9 if quarter<=2 else 10
    
    monthly_hardware_revenue = 0
    monthly_software_recognized = 0
    
    for i in range(ns):
        day = random.randint(1, 28)
        sale_date = date(year, month, day)
        customer = customers_df.sample(1).iloc[0]
        cust_id = customer['customer_id']
        
        je_h = add_je(sale_date, f'Hardware sale to {cust_id}')
        add_jl(je_h, 'Accounts Receivable', debit=525000)
        add_jl(je_h, 'Hardware Sales', credit=525000)
        monthly_hardware_revenue += 525000
        ar_id = f'AR{ar_seq:06d}'; ar_seq+=1
        ar_rec = {'ar_id': ar_id, 'customer_id': cust_id, 'invoice_date': sale_date, 'due_date': sale_date+timedelta(days=60), 'amount': 525000, 'status': 'UNPAID', 'currency':'USD', 'recognition_entry_id': je_h, 'settlement_entry_id': None, 'writeoff_entry_id': None}
        accounts_receivable.append(ar_rec)
        
        je_cogs = add_je(sale_date, 'COGS for hardware')
        add_jl(je_cogs, 'Cost of Goods Sold', debit=210000)
        add_jl(je_cogs, 'Inventory', credit=210000)
        
        collection_date = sale_date+timedelta(days=60)
        if collection_date <= end:
            je_coll = add_je(collection_date, 'Hardware cash collection')
            add_jl(je_coll, 'Cash and Equivalents', debit=525000)
            add_jl(je_coll, 'Accounts Receivable', credit=525000)
            ar_rec['status']='PAID'; ar_rec['settlement_entry_id']=je_coll
        
        je_s = add_je(sale_date, f'Software sale to {cust_id}')
        add_jl(je_s, 'Accounts Receivable', debit=475000)
        add_jl(je_s, 'Software Sales', credit=237500)
        add_jl(je_s, 'Unearned Revenue', credit=237500)
        monthly_software_recognized += 237500
        ar_id2 = f'AR{ar_seq:06d}'; ar_seq+=1
        ar_rec2 = {'ar_id': ar_id2, 'customer_id': cust_id, 'invoice_date': sale_date, 'due_date': sale_date+timedelta(days=60), 'amount': 475000, 'status': 'UNPAID', 'currency':'USD', 'recognition_entry_id': je_s, 'settlement_entry_id': None, 'writeoff_entry_id': None}
        accounts_receivable.append(ar_rec2)
        
        coll60 = sale_date+timedelta(days=60)
        if coll60 <= end:
            je_60 = add_je(coll60, 'Software 50% collection')
            add_jl(je_60, 'Cash and Equivalents', debit=237500)
            add_jl(je_60, 'Accounts Receivable', credit=237500)
        
        for mth in range(1,13):
            amort_date = add_months(sale_date, mth)
            if amort_date > end: break
            amt = 19791.67 if mth<12 else 19791.63
            je_amort = add_je(amort_date, 'Software revenue amortization')
            add_jl(je_amort, 'Unearned Revenue', debit=amt)
            add_jl(je_amort, 'Software Sales', credit=amt)
            if amort_date.month == month and amort_date.year == year:
                monthly_software_recognized += amt
            je_cash = add_je(amort_date, 'Software monthly collection')
            add_jl(je_cash, 'Cash and Equivalents', debit=amt)
            add_jl(je_cash, 'Accounts Receivable', credit=amt)
        if add_months(sale_date,12) <= end:
            ar_rec2['status']='PAID'; ar_rec2['settlement_entry_id']=je_cash
    
    ci = get_balance('Inventory', month_end)
    hs = monthly_hardware_revenue
    mi = 0.8 * hs
    if ci < mi:
        diff = mi - ci
        je_inv = add_je(month_end, 'Inventory replenishment')
        add_jl(je_inv, 'Inventory', debit=diff)
        add_jl(je_inv, 'Cash and Equivalents', credit=diff)
    
    sales_total = monthly_hardware_revenue + monthly_software_recognized + 125000
    sal = round(0.15 * sales_total)
    je_sal = add_je(month_end, 'Monthly salaries')
    add_jl(je_sal, 'Salaries and Wages', debit=sal)
    add_jl(je_sal, 'Cash and Equivalents', credit=sal)
    
    for _, asset in assets_df.iterrows():
        pd_date = pd.to_datetime(asset['purchase_date']).date()
        if pd_date <= month_end:
            cost = asset['cost']; sy = asset['service_years']
            period_dep = round(cost/(sy*12),2)
            prev_acc = sum(d['period_depreciation'] for d in depreciation_records if d['asset_id']==asset['asset_id'])
            accum = prev_acc + period_dep
            dep_id = f"DEP{asset['asset_id']}_{year}_{month}"
            depreciation_records.append({'depreciation_id': dep_id, 'asset_id': asset['asset_id'], 'fiscal_year': year, 'fiscal_period': month, 'period_depreciation': period_dep, 'accumulated_thru_period': accum, 'currency':'USD'})
            je_d = add_je(month_end, f'Depreciation {asset["asset_id"]}')
            add_jl(je_d, 'Depreciation Expense', debit=period_dep)
            add_jl(je_d, 'Accumulated Depreciation', credit=period_dep)
    
    if month in [3,6,9,12]:
        q_end = month_end
        rev = 0
        for jl in journal_lines:
            je = next(e for e in journal_entries if e['id']==jl['journal_entry_id'])
            if q_end - relativedelta(months=3) < je['entry_date'] <= q_end:
                if jl['account_number'] in [acct_map['Hardware Sales'], acct_map['Software Sales'], acct_map['Consulting']]:
                    rev += jl['credit']
        for exp_name, pct in [('R&D Expense',0.10), ('Marketing Expense',0.10), ('Supplies Expense',0.01)]:
            amt = round(rev * pct)
            vendor = vendors_df.sample(1).iloc[0]
            je_ap = add_je(q_end, f'{exp_name} accrual')
            add_jl(je_ap, exp_name, debit=amt)
            add_jl(je_ap, 'Accounts Payable', credit=amt)
            ap_id = f'AP{ap_seq:06d}'; ap_seq+=1
            ap_rec = {'ap_id': ap_id, 'vendor_id': vendor['vendor_id'], 'invoice_date': q_end, 'due_date': q_end+timedelta(days=60), 'amount': amt, 'status': 'UNPAID', 'currency':'USD', 'recognition_entry_id': je_ap, 'settlement_entry_id': None}
            accounts_payable.append(ap_rec)
            pay_date = q_end+timedelta(days=60)
            if pay_date <= end:
                je_pay = add_je(pay_date, f'{exp_name} payment')
                add_jl(je_pay, 'Accounts Payable', debit=amt)
                add_jl(je_pay, 'Cash and Equivalents', credit=amt)
                ap_rec['status']='PAID'; ap_rec['settlement_entry_id']=je_pay
        
        amt = round(rev * 0.01)
        vendor = vendors_df.sample(1).iloc[0]
        je_ap = add_je(q_end, 'Prepaid accrual')
        add_jl(je_ap, 'Prepaid Expenses', debit=amt)
        add_jl(je_ap, 'Accounts Payable', credit=amt)
        ap_id = f'AP{ap_seq:06d}'; ap_seq+=1
        accounts_payable.append({'ap_id': ap_id, 'vendor_id': vendor['vendor_id'], 'invoice_date': q_end, 'due_date': q_end+timedelta(days=60), 'amount': amt, 'status': 'UNPAID', 'currency':'USD', 'recognition_entry_id': je_ap, 'settlement_entry_id': None})
        
        bonus = round(rev * 0.01)
        je_bonus = add_je(q_end, 'R&D bonus accrual')
        add_jl(je_bonus, 'R&D Expense', debit=bonus)
        add_jl(je_bonus, 'Accrued Liabilities', credit=bonus)
        pay_bonus = q_end+timedelta(days=60)
        if pay_bonus <= end:
            je_bpay = add_je(pay_bonus, 'R&D bonus payment')
            add_jl(je_bpay, 'Accrued Liabilities', debit=bonus)
            add_jl(je_bpay, 'Cash and Equivalents', credit=bonus)
        
        gross_ar = sum(jl['debit']-jl['credit'] for jl in journal_lines if jl['account_number']==acct_map['Accounts Receivable'] and next(e for e in journal_entries if e['id']==jl['journal_entry_id'])['entry_date'] <= q_end)
        gross_ar = max(gross_ar,0)
        target = round(0.02 * gross_ar)
        allowance = sum(jl['credit']-jl['debit'] for jl in journal_lines if jl['account_number']==acct_map['Allowance for Doubtful Accounts'] and next(e for e in journal_entries if e['id']==jl['journal_entry_id'])['entry_date'] <= q_end)
        adj = target - allowance
        if abs(adj) > 0.5:
            je_bd = add_je(q_end, 'Bad debt adjustment')
            if adj>0:
                add_jl(je_bd, 'Bad Debt Expense', debit=adj)
                add_jl(je_bd, 'Allowance for Doubtful Accounts', credit=adj)
            else:
                add_jl(je_bd, 'Allowance for Doubtful Accounts', debit=-adj)
                add_jl(je_bd, 'Bad Debt Expense', credit=-adj)
        
        if month==9:
            bad_date = date(year,8,15)
            unpaid_ars = [ar for ar in accounts_receivable if ar['status']=='UNPAID' and ar['amount']>=8700]
            if unpaid_ars:
                ar = unpaid_ars[0]
                je_w = add_je(bad_date, 'Bad debt writeoff')
                add_jl(je_w, 'Allowance for Doubtful Accounts', debit=8700)
                add_jl(je_w, 'Accounts Receivable', credit=8700)
                ar['status']='BAD_DEBT'; ar['writeoff_entry_id']=je_w
        
        rev_q = 0; exp_q = 0
        for jl in journal_lines:
            je = next(e for e in journal_entries if e['id']==jl['journal_entry_id'])
            if q_end - relativedelta(months=3) < je['entry_date'] <= q_end:
                acct_type = coa.loc[coa['account_number']==jl['account_number'], 'account_type'].values[0]
                if acct_type=='Revenue': rev_q += jl['credit'] - jl['debit']
                elif acct_type=='Expense': exp_q += jl['debit'] - jl['credit']
        pre_tax = rev_q - exp_q
        tax = round(0.20*pre_tax) if pre_tax>0 else 0
        if tax>0:
            je_tax = add_je(q_end, 'Income tax')
            add_jl(je_tax, 'Income Tax Expense', debit=tax)
            add_jl(je_tax, 'Cash and Equivalents', credit=tax)
        net_income = pre_tax - tax
        
        if net_income > 2000000:
            invest_amt = round(0.5*(net_income-2000000))
            je_inv = add_je(q_end, 'Short-term investment')
            add_jl(je_inv, 'Short-Term Investments', debit=invest_amt)
            add_jl(je_inv, 'Cash and Equivalents', credit=invest_amt)
            short_term_investments.append({'purchase_date': q_end, 'principal': invest_amt})
        
        if net_income > 50000:
            div = round(0.05*net_income)
            je_div = add_je(q_end, 'Dividends')
            add_jl(je_div, 'Dividends Paid', debit=div)
            add_jl(je_div, 'Cash and Equivalents', credit=div)
            treas = round(0.05*net_income)
            je_treas = add_je(q_end, 'Treasury stock')
            add_jl(je_treas, 'Treasury Stock', debit=treas)
            add_jl(je_treas, 'Cash and Equivalents', credit=treas)
        
        for inv in short_term_investments[:]:
            maturity = inv['purchase_date'] + relativedelta(months=3)
            if q_end - relativedelta(months=3) < maturity <= q_end:
                principal = inv['principal']
                interest = round(principal * 0.04 * 0.25)
                je_int = add_je(q_end, 'ST investment interest')
                add_jl(je_int, 'Cash and Equivalents', debit=interest)
                add_jl(je_int, 'Interest Income', credit=interest)
                je_mat = add_je(q_end, 'ST investment maturity')
                add_jl(je_mat, 'Cash and Equivalents', debit=principal)
                add_jl(je_mat, 'Short-Term Investments', credit=principal)
                je_re = add_je(q_end, 'ST investment reinvest')
                add_jl(je_re, 'Short-Term Investments', debit=principal)
                add_jl(je_re, 'Cash and Equivalents', credit=principal)
    
    current = month_start + relativedelta(months=1)


# --- Create workbook ---
output_path = 'accounting_simulation_complete_output.xlsx'
wb = xlsxwriter.Workbook(output_path, {'nan_inf_to_errors': True})

def write_df(name, df, cols):
    ws = wb.add_worksheet(name[:31])
    df = df.reindex(columns=cols).fillna('')
    for c, col in enumerate(cols):
        ws.write(0, c, col)
    for r, row in enumerate(df.itertuples(index=False), 1):
        for c, val in enumerate(row):
            ws.write(r, c, str(val) if isinstance(val, (pd.Timestamp, datetime, date)) else val)

# Prepare dataframes
je_df = pd.DataFrame(journal_entries)
jl_df = pd.DataFrame(journal_lines)
ap_df = pd.DataFrame(accounts_payable)
ar_df = pd.DataFrame(accounts_receivable)
dep_df = pd.DataFrame(depreciation_records)
loans_df = pd.DataFrame(loans)

write_df('journal_entries', je_df, ['id','entry_date','description','status','created_at'])
write_df('journal_lines', jl_df, ['id','journal_entry_id','account_number','debit','credit','currency'])
write_df('accounts_payable', ap_df, ['ap_id','vendor_id','invoice_date','due_date','amount','status','currency','recognition_entry_id','settlement_entry_id'])
write_df('accounts_receivable', ar_df, ['ar_id','customer_id','invoice_date','due_date','amount','status','currency','recognition_entry_id','settlement_entry_id','writeoff_entry_id'])
write_df('depreciation', dep_df, ['depreciation_id','asset_id','fiscal_year','fiscal_period','period_depreciation','accumulated_thru_period','currency'])
write_df('loans', loans_df, ['loan_id','asset_id','principal','rate','term_months','start_date','monthly_payment'])
write_df('assets', assets_df, ['asset_id','asset_name','purchase_date','cost','service_years','dept','currency','recognition_entry_id'])
write_df('customers', customers_df, ['customer_id','customer_name'])
write_df('vendors', vendors_df, ['vendor_id','vendor_name'])
write_df('chart_of_acct', coa, ['account_number','account_name','account_type','account_subtype','normal_balance'])



# --- Add SEC 10-K statements ---
years = [2022,2023,2024,2025]

def year_total(acct_name, yr, is_debit=True):
    acct = acct_map[acct_name]
    start = date(yr,1,1); end = date(yr,12,31)
    total = 0
    for jl in journal_lines:
        if jl['account_number'] != acct: continue
        je = next(e for e in journal_entries if e['id']==jl['journal_entry_id'])
        if start <= je['entry_date'] <= end:
            total += jl['debit'] if is_debit else jl['credit']
    return total

# Income Statement
is_data = []
for yr in years:
    hw = year_total('Hardware Sales', yr, False)
    sw = year_total('Software Sales', yr, False)
    cons = year_total('Consulting', yr, False)
    rev = hw + sw + cons
    cogs = year_total('Cost of Goods Sold', yr, True)
    gross = rev - cogs
    rd = year_total('R&D Expense', yr, True)
    mkt = year_total('Marketing Expense', yr, True)
    ga = sum(year_total(a, yr, True) for a in ['Salaries and Wages','Lease Expense','Supplies Expense','Legal & Professional Fees','Depreciation Expense','Bad Debt Expense'])
    op_exp = rd + mkt + ga
    op_inc = gross - op_exp
    int_inc = year_total('Interest Income', yr, False)
    int_exp = year_total('Interest Expense', yr, True)
    other = int_inc - int_exp
    pretax = op_inc + other
    tax = year_total('Income Tax Expense', yr, True)
    net = pretax - tax
    is_data.append({'yr':yr,'hw':hw,'sw':sw,'cons':cons,'rev':rev,'cogs':cogs,'gross':gross,'rd':rd,'mkt':mkt,'ga':ga,'op_exp':op_exp,'op_inc':op_inc,'int_inc':int_inc,'int_exp':int_exp,'other':other,'pretax':pretax,'tax':tax,'net':net})

ws_is = wb.add_worksheet('income_statement_SEC')
hdr = ['', '2022','2023','2024','2025']
for c,h in enumerate(hdr): ws_is.write(0,c,h)
rows = [('Revenues:',None,True),('Hardware Sales','hw',False),('Software Sales','sw',False),('Consulting','cons',False),('Total revenues','rev',True),('',None,False),('Cost of goods sold','cogs',False),('Gross profit','gross',True),('',None,False),('Operating expenses:',None,True),('R&D','rd',False),('Sales & marketing','mkt',False),('G&A','ga',False),('Total operating','op_exp',True),('Operating income','op_inc',True),('',None,False),('Interest income','int_inc',False),('Interest expense','int_exp',False),('Other net','other',False),('Pretax income','pretax',True),('Income tax','tax',False),('Net income','net',True)]
for r,(lbl,key,bold) in enumerate(rows,1):
    fmt = wb.add_format({'bold':True}) if bold else None
    ws_is.write(r,0,lbl,fmt)
    if key:
        for i,yr in enumerate(years):
            val = next(d[key] for d in is_data if d['yr']==yr)
            ws_is.write(r,i+1,int(val),fmt)

# Balance Sheet
def bal(name, d):
    return get_balance(name, d)

bs_data = []
cum_net = cum_div = 0
for yr in years:
    end = date(yr,12,31)
    cash = bal('Cash and Equivalents', end)
    st = bal('Short-Term Investments', end)
    ar = bal('Accounts Receivable', end) - bal('Allowance for Doubtful Accounts', end)
    inv = bal('Inventory', end)
    prep = bal('Prepaid Expenses', end)
    ca = cash+st+ar+inv+prep
    ppe = bal('Property, Plant & Equip', end) - bal('Accumulated Depreciation', end)
    ta = ca + ppe
    ap = bal('Accounts Payable', end)
    acc = bal('Accrued Liabilities', end)
    std = bal('Short-term Debt', end)
    une = bal('Unearned Revenue', end)
    cl = ap+acc+std+une
    ltd = bal('Long-term Debt', end)
    tl = cl + ltd
    common = bal('Common Stock', end)
    net = next(d['net'] for d in is_data if d['yr']==yr)
    div = year_total('Dividends Paid', yr, True)
    cum_net += net; cum_div += div
    re = cum_net - cum_div
    treas = bal('Treasury Stock', end)
    te = common + re - treas
    bs_data.append({'yr':yr,'cash':cash,'st':st,'ar':ar,'inv':inv,'prep':prep,'ca':ca,'ppe':ppe,'ta':ta,'ap':ap,'acc':acc,'std':std,'une':une,'cl':cl,'ltd':ltd,'tl':tl,'common':common,'re':re,'treas':treas,'te':te})

ws_bs = wb.add_worksheet('balance_sheet_SEC')
for c,h in enumerate(hdr): ws_bs.write(0,c,h)
bs_rows = [('ASSETS',None,True),('Cash','cash',False),('ST investments','st',False),('AR net','ar',False),('Inventory','inv',False),('Prepaid','prep',False),('Total current','ca',True),('PPE net','ppe',False),('Total assets','ta',True),('',None,False),('LIABILITIES',None,True),('AP','ap',False),('Accrued','acc',False),('ST debt','std',False),('Unearned','une',False),('Total current liab','cl',True),('LT debt','ltd',False),('Total liabilities','tl',True),('',None,False),('EQUITY',None,True),('Common','common',False),('Retained','re',False),('Treasury','treas',False),('Total equity','te',True),('Total liabilities and equity','',False)]
for r,(lbl,key,bold) in enumerate(bs_rows,1):
    fmt = wb.add_format({'bold':True}) if bold else None
    ws_bs.write(r,0,lbl,fmt)
    if key:
        for i,yr in enumerate(years):
            d = next(x for x in bs_data if x['yr']==yr)
            val = d[key]
            if key=='treas': val = -val
            ws_bs.write(r,i+1,int(val),fmt)
    if lbl=='Total liabilities and equity':
        for i,yr in enumerate(years):
            d = next(x for x in bs_data if x['yr']==yr)
            ws_bs.write(r,i+1,int(d['tl']+d['te']),fmt)

# Cash Flow - full
cash_flow = {}
for yr in years:
    end = date(yr,12,31); prev = date(yr-1,12,31)
    net = next(d['net'] for d in is_data if d['yr']==yr)
    depr = year_total('Depreciation Expense', yr, True)
    bad = year_total('Bad Debt Expense', yr, True) - year_total('Bad Debt Expense', yr, False)
    ar_ch = (bal('Accounts Receivable', end)-bal('Allowance for Doubtful Accounts', end)) - (bal('Accounts Receivable', prev)-bal('Allowance for Doubtful Accounts', prev))
    inv_ch = bal('Inventory', end) - bal('Inventory', prev)
    prep_ch = bal('Prepaid Expenses', end) - bal('Prepaid Expenses', prev)
    ap_ch = bal('Accounts Payable', end) - bal('Accounts Payable', prev)
    acc_ch = bal('Accrued Liabilities', end) - bal('Accrued Liabilities', prev)
    une_ch = bal('Unearned Revenue', end) - bal('Unearned Revenue', prev)
    oper = net + depr + bad - ar_ch - inv_ch - prep_ch + ap_ch + acc_ch + une_ch
    ppe_ch = bal('Property, Plant & Equip', end) - bal('Property, Plant & Equip', prev)
    st_ch = bal('Short-Term Investments', end) - bal('Short-Term Investments', prev)
    invest = -ppe_ch - st_ch
    debt_ch = (bal('Long-term Debt', end)+bal('Short-term Debt', end)) - (bal('Long-term Debt', prev)+bal('Short-term Debt', prev))
    stock_ch = bal('Common Stock', end) - bal('Common Stock', prev)
    treas_ch = bal('Treasury Stock', end) - bal('Treasury Stock', prev)
    div_ch = bal('Dividends Paid', end) - bal('Dividends Paid', prev)
    financ = debt_ch + stock_ch - treas_ch - div_ch
    cash_ch = bal('Cash and Equivalents', end) - bal('Cash and Equivalents', prev)
    cash_flow[yr] = {'net':net,'depr':depr,'bad':bad,'ar':-ar_ch,'inv':-inv_ch,'prep':-prep_ch,'ap':ap_ch,'acc':acc_ch,'une':une_ch,'oper':oper,'ppe':-ppe_ch,'st':-st_ch,'invest':invest,'debt':debt_ch,'stock':stock_ch,'treas':-treas_ch,'div':-div_ch,'financ':financ,'cash_ch':cash_ch,'beg':bal('Cash and Equivalents', prev) if yr>2022 else 0,'end':bal('Cash and Equivalents', end)}

ws_cf = wb.add_worksheet('cash_flow_statement')
for c,h in enumerate(hdr): ws_cf.write(0,c,h)
cf_rows = [('Operating:',None,True),('Net income','net',False),('Depreciation','depr',False),('Bad debt','bad',False),('AR change','ar',False),('Inventory change','inv',False),('Prepaid change','prep',False),('AP change','ap',False),('Accrued change','acc',False),('Unearned change','une',False),('Net operating','oper',True),('',None,False),('Investing:',None,True),('PPE','ppe',False),('ST investments','st',False),('Net investing','invest',True),('',None,False),('Financing:',None,True),('Debt','debt',False),('Stock','stock',False),('Treasury','treas',False),('Dividends','div',False),('Net financing','financ',True),('',None,False),('Net change','cash_ch',True),('Beginning cash','beg',False),('Ending cash','end',True)]
for r,(lbl,key,bold) in enumerate(cf_rows,1):
    fmt = wb.add_format({'bold':True}) if bold else None
    ws_cf.write(r,0,lbl,fmt)
    if key:
        for i,yr in enumerate(years):
            ws_cf.write(r,i+1,int(cash_flow[yr][key]),fmt)

wb.close()
print(f"Workbook created: {output_path} with {len(journal_entries)} entries and 3 SEC tabs")

