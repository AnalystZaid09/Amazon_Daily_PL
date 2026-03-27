# Daliy P&L
import streamlit as st
import pandas as pd
import numpy as np
import io
import os
import xlsxwriter

st.set_page_config(page_title="Amazon Daily-P&L", page_icon="📊", layout="wide")

st.title("Amazon Daily-P&L")
st.markdown(
    "Upload your Amazon transaction CSV and Purchase Master (PM) Excel file to analyze profits. "
    "This build removes all datetime parsing — every date column is treated as plain text. "
    "Styled Excel export preserves SKU formatting and exports the currently filtered view."
)

# ----------------- Helpers -----------------

def clean_numeric(series):
    """Robustly convert a series of strings (with currency, commas, parentheses) to floats."""
    s = series.astype(str).fillna("0").str.strip()
    s = s.replace({'': '0', 'nan': '0', 'NaN': '0', 'N/A': '0', 'n/a': '0', '-': '0'})
    
    # Standardize all dash variants to a standard hyphen
    s = s.str.replace('–', '-', regex=False).str.replace('—', '-', regex=False)
    
    # Identify negative values (start with hyphen or wrapped in parentheses)
    is_paren = s.str.startswith('(') & s.str.endswith(')')
    is_hyphen = s.str.contains('-', regex=False)
    
    # Strip everything except digits and dots
    s_clean = s.str.replace(r'[^\d\.]', '', regex=True)
    s_clean = s_clean.replace('', '0')
    
    nums = pd.to_numeric(s_clean, errors='coerce').fillna(0)
    
    # Apply negative sign if it was originally there
    res = np.where(is_paren | is_hyphen, -nums, nums)
    return pd.Series(res, index=series.index)


def clean_sku_val(x):
    if pd.isna(x):
        return ""
    # Convert to string and strip all forms of whitespace
    s = str(x).strip()
    # Remove non-printable characters
    s = "".join(ch for ch in s if ord(ch) > 31)
    
    # Handle numeric SKUs that might have been read as floats (e.g., "123.0")
    try:
        # Check if it's a numeric string
        if s.replace('.', '', 1).isdigit():
            f = float(s)
            if f.is_integer():
                s = str(int(f))
            else:
                s = str(f)
    except Exception:
        pass
        
    # Standardize formatting
    return s.upper().strip()


def find_col_by_names(df_cols, candidates):
    cols_lower = {c.lower().strip(): c for c in df_cols}
    for cand in candidates:
        if cand.lower().strip() in cols_lower:
            return cols_lower[cand.lower().strip()]
    return None


def compute_financials(df):
    df = df.copy()
    for col in ["Sales Proceed", "Transferred Price", "Our Cost", "Support Amount"]:
        if col not in df.columns:
            df[col] = np.nan
    df["Sales Proceed"] = clean_numeric(df["Sales Proceed"])
    df["Transferred Price"] = clean_numeric(df["Transferred Price"])
    df["Our Cost"] = clean_numeric(df["Our Cost"])
    df["Support Amount"] = clean_numeric(df["Support Amount"])
    
    if "Quantity" in df.columns:
        df["Quantity"] = clean_numeric(df["Quantity"])
    else:
        df["Quantity"] = 1.0
    
    df["Quantity"] = df["Quantity"].replace(0, 1).fillna(1)

    df["Amazon Total Fees"] = df["Sales Proceed"] - df["Transferred Price"]
    df["Amazon Fees in %"] = np.where(df["Sales Proceed"] != 0, (df["Amazon Total Fees"] / df["Sales Proceed"]) * 100, 0)
    df["Amazon Fees in %"] = df["Amazon Fees in %"].round(2)

    df["Our Cost As Per Qty"] = df["Our Cost"] * df["Quantity"]

    df["Profit"] = df["Transferred Price"] - df["Our Cost As Per Qty"]
    # Standard Margin: Profit / Sales Proceed
    df["Profit In Percentage"] = np.where(df["Sales Proceed"] != 0, (df["Profit"] * 100) / df["Sales Proceed"], 0)
    df["Profit In Percentage"] = df["Profit In Percentage"].round(2)

    df["With BackEnd Price"] = df["Our Cost"] - df["Support Amount"]
    df["With Support Purchase As Per Qty"] = df["With BackEnd Price"] * df["Quantity"]
    df["Profit With Support"] = df["Transferred Price"] - df["With Support Purchase As Per Qty"]
    # Standard Margin With Support: Profit With Support / Sales Proceed
    df["Profit In Percentage With Support"] = np.where(df["Sales Proceed"] != 0,
                                                     (df["Profit With Support"] * 100) / df["Sales Proceed"],
                                                     0)
    df["Profit In Percentage With Support"] = df["Profit In Percentage With Support"].round(2)

    df["3% On Transferred Price"] = (df["Transferred Price"] * 0.03).round(2)
    df["After 3% Profit"] = df["Profit With Support"] - df["3% On Transferred Price"]
    df["After 3% Percentage"] = np.where(df["With Support Purchase As Per Qty"] > 0,
                                         ((df["After 3% Profit"] / df["With Support Purchase As Per Qty"]) * 100),
                                         np.nan)
    df["After 3% Percentage"] = df["After 3% Percentage"].round(0)
    
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    return df


# ---------- Excel Styling Helpers ----------

def _xl_col_letter(n):
    s = ''
    while n >= 0:
        s = chr(n % 26 + 65) + s
        n = n // 26 - 1
    return s

def create_styled_workbook_bytes(df: pd.DataFrame, header_hex="#0B5394", currency_symbol='₹', include_summary=True):
    num_cols = ["Sales Proceed", "Transferred Price", "Profit", "Profit With Support", 
                "After 3% Profit", "Our Cost As Per Qty", "Support Amount", 
                "With Support Purchase As Per Qty", "Quantity", "Qty"]
    for col in num_cols:
        if col in df_write.columns:
            df_write[col] = clean_numeric(df_write[col])

    sku_cols = [c for c in df_write.columns if c.lower().strip() in ["sku", "asin", "sku id"] or 'sku' in c.lower()]            
    text_cols = sku_cols + [c for c in df_write.columns if any(x in c.lower() for x in ['order id', 'order_id', 'item id', 'settlement', 'description', 'ordered on', 'date', 'name', 'member', 'brand', 'manager'])]

    profit_cols = [c for c in [
        "Profit",
        "Profit In Percentage",
        "Profit With Support",
        "Profit In Percentage With Support",
        "After 3% Profit",
        "After 3% Percentage"
    ] if c in df_write.columns]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book

        sheet_name = "Data"
        df_write.to_excel(writer, index=False, sheet_name=sheet_name, startrow=0, header=True)
        worksheet = writer.sheets[sheet_name]

        header_format = workbook.add_format({
            'bold': True, 'text_wrap': True, 'valign': 'vcenter',
            'fg_color': header_hex, 'font_color': '#FFFFFF', 'border': 1
        })
        currency_fmt = workbook.add_format({'num_format': f'"{currency_symbol}"#,##0.00', 'border': 1})
        integer_fmt = workbook.add_format({'num_format': '0', 'border': 1})
        pct_fmt = workbook.add_format({'num_format': '0.00"%";-0.00"%"', 'border': 1})
        default_fmt = workbook.add_format({'border': 1})
        sku_fmt = workbook.add_format({'num_format': '@', 'border': 1})

        for col_num, col_name in enumerate(df_write.columns):
            worksheet.write(0, col_num, col_name, header_format)
            try:
                max_len = max(
                    df_write[col_name].astype(str).map(len).max() if df_write[col_name].size > 0 else 0,
                    len(str(col_name))
                ) + 2
            except Exception:
                max_len = len(str(col_name)) + 2
            max_len = min(max_len, 60)
            worksheet.set_column(col_num, col_num, max_len)

        worksheet.freeze_panes(1, 0)
        worksheet.autofilter(0, 0, len(df_write), len(df_write.columns) - 1)

        for col_idx, col_name in enumerate(df_write.columns):
            series = df_write[col_name]
            if col_name in sku_cols:
                worksheet.set_column(col_idx, col_idx, None, sku_fmt)
            elif pd.api.types.is_integer_dtype(series) or (pd.api.types.is_float_dtype(series) and all(series.dropna().apply(float).apply(float.is_integer)) if series.dropna().size>0 else False):
                worksheet.set_column(col_idx, col_idx, None, integer_fmt)
            elif pd.api.types.is_float_dtype(series) or pd.api.types.is_numeric_dtype(series):
                lname = col_name.lower()
                # Handle specific names for pivot support
                if any(k in lname for k in ['sales', 'profit', 'cost', 'price', 'fees', 'amount', 'transferred', 'after 3%']):
                    worksheet.set_column(col_idx, col_idx, None, currency_fmt)
                elif 'percentage' in lname or '%' in col_name or 'pct' in lname:
                    worksheet.set_column(col_idx, col_idx, None, pct_fmt)
                else:
                    worksheet.set_column(col_idx, col_idx, None, default_fmt)
            else:
                worksheet.set_column(col_idx, col_idx, None, default_fmt)

        pos_format = workbook.add_format({'bg_color': '#C6EFCE', 'font_color': '#006100'})
        neg_format = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})
        for pcol in profit_cols:
            try:
                col_idx = list(df_write.columns).index(pcol)
                col_letter = _xl_col_letter(col_idx)
                first_row = 2
                last_row = len(df_write) + 1
                cell_range = f'{col_letter}{first_row}:{col_letter}{last_row}'
                worksheet.conditional_format(cell_range, {'type': 'cell', 'criteria': '>=', 'value': 0, 'format': pos_format})
                worksheet.conditional_format(cell_range, {'type': 'cell', 'criteria': '<',  'value': 0, 'format': neg_format})
            except Exception:
                pass

        if include_summary:
            summary_name = "Summary"
            total_rows = len(df_write)
            total_sales = df_write.get('Sales Proceed', pd.Series([])).sum(skipna=True) if 'Sales Proceed' in df_write.columns else 0
            total_profit = df_write.get('Profit', pd.Series([])).sum(skipna=True) if 'Profit' in df_write.columns else 0
            total_after3 = df_write.get('After 3% Profit', pd.Series([])).sum(skipna=True) if 'After 3% Profit' in df_write.columns else 0
            total_qty = int(df_write.get('Quantity', pd.Series([0])).sum(skipna=True)) if 'Quantity' in df_write.columns else 0
            unique_skus = df_write.get('SKU', pd.Series([])).nunique() if 'SKU' in df_write.columns else 0
            
            # New summary metrics
            # Standard Margin: (Profit / Sales)
            profit_in_percentage = (total_profit * 100 / total_sales) if total_sales != 0 else 0
            total_support_amount = df_write.get('Support Amount', pd.Series([])).sum(skipna=True) if 'Support Amount' in df_write.columns else 0
            total_with_support_purchase = df_write.get('With Support Purchase As Per Qty', pd.Series([])).sum(skipna=True) if 'With Support Purchase As Per Qty' in df_write.columns else 0
            total_profit_with_support = df_write.get('Profit With Support', pd.Series([])).sum(skipna=True) if 'Profit With Support' in df_write.columns else 0
            # Standard Margin With Support: (Profit With Support / Sales)
            profit_in_pct_with_support = (total_profit_with_support * 100 / total_sales) if total_sales != 0 else 0

            if 'Product Name' in df_write.columns and 'Profit' in df_write.columns:
                agg_cols = {
                    'Quantity': 'sum', 
                    'Sales Proceed': 'sum', 
                    'Profit': 'sum',
                    'Support Amount': 'sum',
                    'With Support Purchase As Per Qty': 'sum',
                    'Profit With Support': 'sum',
                    'After 3% Profit': 'sum',
                    'Our Cost As Per Qty': 'sum'
                }
                # filter agg_cols to only those in df_write
                agg_map = {k: v for k, v in agg_cols.items() if k in df_write.columns}
                
                top_prod_raw = df_write.groupby('Product Name', dropna=True).agg(agg_map).reset_index()
                
                # Recalculate percentages after aggregation (Margin on Sales)
                if 'Profit' in top_prod_raw.columns and 'Sales Proceed' in top_prod_raw.columns:
                    top_prod_raw['Profit In Percentage'] = np.where(
                        top_prod_raw['Sales Proceed'] != 0,
                        (top_prod_raw['Profit'] * 100) / top_prod_raw['Sales Proceed'],
                        0
                    )
                
                if 'Profit With Support' in top_prod_raw.columns and 'Sales Proceed' in top_prod_raw.columns:
                    top_prod_raw['Profit In Percentage With Support'] = np.where(
                        top_prod_raw['Sales Proceed'] != 0,
                        (top_prod_raw['Profit With Support'] * 100) / top_prod_raw['Sales Proceed'],
                        0
                    )
                
                # Define final column order for the table
                final_top_cols = [
                    'Product Name', 'Quantity', 'Sales Proceed', 'Profit', 
                    'Profit In Percentage', 'Support Amount', 'With Support Purchase As Per Qty', 
                    'Profit With Support', 'Profit In Percentage With Support', 'After 3% Profit'
                ]
                # Ensure columns exist in top_prod_raw
                available_top_cols = [c for c in final_top_cols if c in top_prod_raw.columns]
                top_products = top_prod_raw[available_top_cols].round(2).sort_values('Profit', ascending=False).head(100)
            else:
                top_products = pd.DataFrame()

            summary_ws = workbook.add_worksheet(summary_name)

            kv = [
                ("Total Rows", total_rows),
                ("Total Sales", total_sales),
                ("Total Profit", total_profit),
                ("Profit In Percentage", round(profit_in_percentage, 2)),
                ("Support Amount", total_support_amount),
                ("With Support Purchase As Per Qty", total_with_support_purchase),
                ("Profit With Support", total_profit_with_support),
                ("Profit In Percentage With Support", round(profit_in_pct_with_support, 2)),
                ("After 3% Profit (Sum)", total_after3),
                ("Total Quantity", total_qty),
                ("Unique SKUs", unique_skus)
            ]
            r = 0
            label_fmt = workbook.add_format({'bold': True})
            pct_value_fmt = workbook.add_format({'num_format': '0.00"%"', 'border': 1})
            for label, value in kv:
                summary_ws.write(r, 0, label, label_fmt)
                if isinstance(value, (int, np.integer)):
                    summary_ws.write(r, 1, int(value))
                elif 'Percentage' in label:
                    try:
                        summary_ws.write(r, 1, float(value), pct_value_fmt)
                    except Exception:
                        summary_ws.write(r, 1, value)
                else:
                    try:
                        summary_ws.write(r, 1, float(value), currency_fmt)
                    except Exception:
                        summary_ws.write(r, 1, value)
                r += 1

            r += 1
            if not top_products.empty:
                for cidx, cname in enumerate(top_products.columns):
                    summary_ws.write(r, cidx, cname, header_format)
                    try:
                        max_len = max(top_products[cname].astype(str).map(len).max(), len(cname)) + 2
                    except Exception:
                        max_len = len(cname) + 2
                    summary_ws.set_column(cidx, cidx, min(max_len, 60))
                for ridx, row in top_products.iterrows():
                    for cidx, cname in enumerate(top_products.columns):
                        val = row[cname]
                        if pd.api.types.is_number(val):
                            lname = cname.lower()
                            if any(x in lname for x in ['sales', 'profit', 'amount', 'purchase']):
                                summary_ws.write(r + 1 + ridx, cidx, val, currency_fmt)
                            elif 'percentage' in lname:
                                summary_ws.write(r + 1 + ridx, cidx, val, pct_value_fmt)
                            else:
                                summary_ws.write(r + 1 + ridx, cidx, val)
                        else:
                            summary_ws.write(r + 1 + ridx, cidx, val)
            else:
                summary_ws.write(r, 0, "No Product Name / Profit columns to show top product table.", default_fmt)

    output.seek(0)
    return output.read()


# ----------------- Uploads / Options (CENTER, not sidebar) -----------------
st.markdown("---")
st.header("Upload Files & Options")

# File uploaders in 2 columns
col1, col2 = st.columns(2)

with col1:
    transaction_file = st.file_uploader("Upload Transaction File (CSV/Excel)", type=['csv', 'xlsx', 'xls'])

with col2:
    pm_file = st.file_uploader("Upload Purchase Master (PM.xlsx)", type=['xlsx', 'xls'])

# Options row
opt_col1, opt_col2 = st.columns(2)

with opt_col1:
    skip_rows = st.number_input(
        "Rows to skip",
        min_value=0,
        max_value=200,
        value=11,
        help="Number of header rows to skip in the transaction file"
    )

with opt_col2:
    enable_excel_export = st.checkbox("Enable Excel export (styled)", value=True)


# ----------------- Main processing -----------------
if transaction_file and pm_file:
    try:
        # Read transaction file (CSV or Excel)
        if str(transaction_file.name).lower().endswith('.csv'):
            df = pd.read_csv(transaction_file, skiprows=skip_rows, dtype=str)
        else:
            df = pd.read_excel(transaction_file, skiprows=skip_rows, dtype=str)
        st.success(f"Loaded transaction file with {len(df)} rows")

        pm = pd.read_excel(pm_file, sheet_name=0, dtype=str)
        pm.columns = [str(col).strip() for col in pm.columns]
        st.success(f"Loaded Product Master with {len(pm)} rows (sheet 0)")

        # detect SKU columns
        df_cols_lower = [c.lower().strip() for c in df.columns]
        possible_sku_names = ['sku', 'seller sku', 'asin', 'product sku', 'item sku', 'sku id']
        sku_col_df = None
        for name in possible_sku_names:
            if name in df_cols_lower:
                sku_col_df = df.columns[df_cols_lower.index(name)]
                break
        if sku_col_df is None:
            for c in df.columns:
                if 'sku' in str(c).lower():
                    sku_col_df = c
                    break
        if sku_col_df is None:
            raise ValueError("Couldn't detect SKU column in transaction CSV. Expected column like 'Sku' or 'Seller SKU'.")

        # PM SKU detection
        possible_pm_sku_names = ['Amazon Sku Name', 'EasycomSKU', 'Vendor SKU Codes', 'sku', 'seller sku', 'amazon sku', 'product sku', 'sku id']
        sku_col_pm = find_col_by_names(pm.columns, possible_pm_sku_names)
        if sku_col_pm is None and len(pm.columns) >= 3:
            sku_col_pm = pm.columns[2]
        if sku_col_pm is None:
            raise ValueError("Couldn't detect SKU column in PM file. Ensure SKU is present in PM.")

        # normalize SKUs
        df[sku_col_df] = df[sku_col_df].apply(clean_sku_val)
        pm[sku_col_pm] = pm[sku_col_pm].apply(clean_sku_val)
        
        # Remove blank SKUs
        df = df[df[sku_col_df] != ""].copy()
        pm = pm[pm[sku_col_pm] != ""].copy()

        # detect PM columns
        purchase_member_col = find_col_by_names(pm.columns, ['purchase member name', 'purchase member', 'member'])
        brand_col           = find_col_by_names(pm.columns, ['brand', 'brand name', 'brandname'])
        brand_manager_col   = find_col_by_names(pm.columns, ['brand manager', 'brandmanager', 'manager', 'mgr', 'brand mgr', 'brand-manager'])
        product_name_col    = find_col_by_names(pm.columns, ['product name', 'item name', 'name', 'title'])
        our_cost_col        = find_col_by_names(pm.columns, ['cp', 'our cost', 'cost', 'unit cost', 'purchase price'])
        support_amount_col  = find_col_by_names(pm.columns, ['additional support', 'support amount', 'support', 'support price'])
        asin_col            = find_col_by_names(pm.columns, ['asin', 'amazon asin', 'product asin'])
        
        # fallbacks by index (only if name-based detection failed and indices are likely safe)
        try:
            # We avoid aggressive fallbacks for Purchase Member and Product Name 
            # as they often conflict with newer column requirements like Brand/Manager.
            pass
        except Exception:
            pass

        # Prepare PM details for merging by SKU
        pm_detail_cols_to_map = [purchase_member_col, brand_col, brand_manager_col, product_name_col, our_cost_col, support_amount_col, asin_col]
        pm_detail_cols_available = [c for c in pm_detail_cols_to_map if c is not None]
        
        # Deduplicate PM rows by (SKU, ASIN) pairs to match the original row count behavior exactly (1726 rows).
        # If ASIN is not detected, we fall back to SKU-only deduplication.
        dup_subset = [sku_col_pm]
        if asin_col:
            dup_subset.append(asin_col)
        pm_details_df = pm[[sku_col_pm] + pm_detail_cols_available].dropna(subset=[sku_col_pm]).drop_duplicates(subset=dup_subset)
        
        # Clean numeric cols in details map
        if our_cost_col is not None:
            pm_details_df[our_cost_col] = pm_details_df[our_cost_col].astype(str).str.replace(",", "", regex=False)
            pm_details_df[our_cost_col] = pd.to_numeric(pm_details_df[our_cost_col], errors='coerce')
        if support_amount_col is not None:
            pm_details_df[support_amount_col] = pm_details_df[support_amount_col].astype(str).str.replace(",", "", regex=False)
            pm_details_df[support_amount_col] = pd.to_numeric(pm_details_df[support_amount_col], errors='coerce')

        # filter only orders
        if 'type' not in [c.lower() for c in df.columns]:
            df_order = df.copy()
        else:
            type_col = None
            for c in df.columns:
                if c.lower().strip() == 'type':
                    type_col = c
                    break
            df_order = df[df[type_col].astype(str).str.lower().str.strip() == 'order'].copy()

        # detect sales/total/gst columns
        def detect_column(df_cols, candidates):
            cols_map = {c.lower().strip(): c for c in df_cols}
            for cand in candidates:
                if cand.lower().strip() in cols_map:
                    return cols_map[cand.lower().strip()]
            return None

        product_sales_col = detect_column(df_order.columns, ['product sales', 'product_sales', 'product_sales_amount', 'product price'])
        total_col = detect_column(df_order.columns, ['total', 'transfered price', 'tranfered price', 'total amount', 'amount'])
        gst_col = detect_column(df_order.columns, ['total sales tax liable(gst before adjusting tcs)', 'total sales tax liable', 'gst', 'tax'])

        if product_sales_col is None:
            for c in df_order.columns:
                if 'product' in c.lower() and 'sales' in c.lower():
                    product_sales_col = c
                    break
        if total_col is None:
            for c in df_order.columns:
                if 'total' == c.lower().strip() or 'amount' in c.lower() and 'total' in c.lower():
                    total_col = c
                    break

        if product_sales_col is None:
            raise ValueError("Couldn't detect 'product sales' column in transactions. Expected a column like 'product sales'.")

        df_order[product_sales_col] = df_order[product_sales_col].astype(str).str.replace(",", "", regex=False)
        df_order[product_sales_col] = pd.to_numeric(df_order[product_sales_col], errors='coerce')

        if gst_col is not None:
            df_order[gst_col] = df_order[gst_col].astype(str).str.replace(",", "", regex=False)
            df_order[gst_col] = pd.to_numeric(df_order[gst_col], errors='coerce')
        else:
            gst_col = 'GST_TEMP_ZERO'
            df_order[gst_col] = 0.0

        df_order = df_order[df_order[product_sales_col].fillna(0) != 0].copy()
        df_order = df_order.rename(columns={sku_col_df: 'SKU__'})
        df_order['SKU__'] = df_order['SKU__'].astype(str).apply(clean_sku_val)
        # Final filter for blank SKU__
        df_order = df_order[df_order['SKU__'] != ""].copy()

        # Directly map transactions to PM details via SKU
        merged = df_order.merge(pm_details_df, how='left', left_on='SKU__', right_on=sku_col_pm, suffixes=('', '_redundant'))

        # Check for missing SKUs (SKU not found in mapping at all)
        missing_skus = merged[merged[sku_col_pm].isna()]['SKU__'].unique() if sku_col_pm in merged.columns else []
        
        # rename columns to stable names safely
        cols_to_map = [
            (purchase_member_col, 'Purchase Member Name', "SKU/ASIN MISSING IN PM"),
            (brand_col,           'Brand',                "SKU/ASIN MISSING IN PM"),
            (brand_manager_col,   'Brand Manager',        "SKU/ASIN MISSING IN PM"),
            (product_name_col,    'Product Name',         "SKU/ASIN MISSING IN PM"),
            (asin_col,            'ASIN',                 "ASIN MISSING IN PM"),
            (our_cost_col,        'Our Cost',             0),
            (support_amount_col,  'Support Amount',       0)
        ]
        
        for old_name, new_name, fill_val in cols_to_map:
            # Skip if no source column was detected
            if old_name is None:
                if new_name not in merged.columns:
                    merged[new_name] = fill_val
                continue
                
            # Identify source in 'merged'. Handling suffixes from merge collisions.
            source_col = None
            if old_name + "_redundant" in merged.columns:
                source_col = old_name + "_redundant"
            elif old_name in merged.columns:
                source_col = old_name
            
            if source_col:
                # If we're mapping to a new name, copy the data.
                # Direct rename is problematic if multiple targets use the same source column.
                if source_col != new_name:
                    merged[new_name] = merged[source_col]
            
            # Ensure existence and fillna
            if new_name not in merged.columns:
                merged[new_name] = fill_val
            
            if isinstance(fill_val, (int, float)):
                merged[new_name] = pd.to_numeric(merged[new_name], errors='coerce').fillna(fill_val)
            else:
                merged[new_name] = merged[new_name].fillna(fill_val)
        if total_col in merged.columns:
            merged[total_col] = merged[total_col].astype(str).str.replace(",", "", regex=False)
            merged[total_col] = pd.to_numeric(merged[total_col], errors='coerce')
        else:
            merged[total_col] = np.nan

        # quantity
        qty_col = detect_column(merged.columns, ['quantity', 'qty', 'count'])
        if isinstance(qty_col, str):
            merged['Quantity'] = pd.to_numeric(merged[qty_col].astype(str).str.replace(",", "", regex=False), errors='coerce').fillna(1)
        elif 'quantity' in merged.columns:
            merged['Quantity'] = pd.to_numeric(merged['quantity'].astype(str).str.replace(",", "", regex=False), errors='coerce').fillna(1)
        else:
            merged['Quantity'] = 1

        merged['Sales Proceed'] = merged[product_sales_col].fillna(0) + merged[gst_col].fillna(0)
        merged = merged.rename(columns={total_col: 'Transferred Price'})

        # compute financials
        merged = compute_financials(merged)

        # Accuracy Matching User's Excel:
        # We create dedicated columns for pivot values to avoid confusion with main report metrics
        merged['pivot_sales'] = merged[product_sales_col].fillna(0)
        merged['pivot_tax'] = merged[gst_col].fillna(0)
        merged['pivot_qty'] = merged['Quantity'].fillna(1)
        merged['pivot_transferred'] = merged['Transferred Price'].fillna(0)

        # ----------------- Finalize final_df -----------------
        # Detect Order Id column (case-insensitive)
        order_id_col = find_col_by_names(merged.columns, ['order id', 'order_id', 'orderid', 'amazon order id', 'amazon-order-id'])
        
        rename_dict = {
            'Purchase Member Name': 'Purchase Member Name',
            'Product Name': 'Product Name', 
            'description': 'Description', 
            'Quantity': 'Quantity', 
            'SKU__': 'SKU'
        }
        if order_id_col:
            rename_dict[order_id_col] = 'Order Id'
            
        final_df = merged.rename(columns=rename_dict)

        ordered_on_col = find_col_by_names(final_df.columns, ['date/time','order date','ordered on','date'])
        if ordered_on_col:
            final_df = final_df.rename(columns={ordered_on_col: 'Ordered On'})

        order_item_id_col = find_col_by_names(final_df.columns, ['settlement id','order item id','order item','settlementid'])
        if order_item_id_col:
            final_df = final_df.rename(columns={order_item_id_col: 'ORDER ITEM ID'})

        final_columns = [
            "Ordered On", "ORDER ITEM ID", "Purchase Member Name", "Brand", "Brand Manager", "Order Id",
            "Product Name", "Description", "Quantity", "SKU", "ASIN", "Sales Proceed",
            "Amazon Total Fees", "Amazon Fees in %", "Transferred Price",
            "Our Cost", "Our Cost As Per Qty", "Profit", "Profit In Percentage",
            "Support Amount", "With BackEnd Price", "With Support Purchase As Per Qty",
            "Profit With Support", "Profit In Percentage With Support",
            "3% On Transferred Price", "After 3% Profit", "After 3% Percentage"
        ]
        
        # Deduplicate columns to avoid AttributeErrors when columns are accessed (e.g. during filter generation)
        # This can happen if PM file has columns that get renamed to names already present.
        final_df = final_df.loc[:, ~final_df.columns.duplicated()].copy()
        
        # Preserve pivot columns in the dataframe but keep them out of final_columns for display
        pivot_internal = ["pivot_sales", "pivot_tax", "pivot_qty", "pivot_transferred"]
        available_cols = [c for c in (final_columns + pivot_internal) if c in final_df.columns]
        final_df = final_df[available_cols].copy()
        final_df.replace([np.inf, -np.inf], np.nan, inplace=True)

        # ----------------- Mapping Validation UI -----------------
        if len(missing_skus) > 0:
            st.warning(f"⚠️ {len(missing_skus)} SKUs found in transactions are MISSING from Purchase Master. These will have 0 cost/profit.")
            with st.expander("View Missing SKUs"):
                st.write("The following SKUs need to be added to your Purchase Master file:")
                st.dataframe(pd.DataFrame({'Missing SKU': missing_skus}), use_container_width=True)
        else:
            st.success("✅ All transaction SKUs matched with Purchase Master.")

        # Store original name for download buttons
        orig_name = getattr(transaction_file, "name", "transactions.csv")

        # ----------------- Filters, Charts & Export of FILTERED data -----------------
        st.markdown("---")
        st.header("Interactive Filters & Charts")

        with st.expander("Filters"):
            col1, col2 = st.columns(2)
            with col1:
                product_values = ["All"] + sorted(final_df['Product Name'].dropna().unique().tolist()) if 'Product Name' in final_df.columns else ['All']
                selected_product = st.selectbox("Filter by Product", product_values)
            with col2:
                member_values = ["All"] + sorted(final_df['Purchase Member Name'].dropna().unique().tolist()) if 'Purchase Member Name' in final_df.columns else ['All']
                selected_member = st.selectbox("Filter by Member", member_values)

        filtered = final_df.copy()
        if selected_product != 'All':
            filtered = filtered[filtered['Product Name'] == selected_product]
        if selected_member != 'All':
            filtered = filtered[filtered['Purchase Member Name'] == selected_member]

        st.subheader("Summary Metrics (Filtered)")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Rows", len(filtered))
        with col2:
            total_sales = filtered['Sales Proceed'].sum() if 'Sales Proceed' in filtered.columns else 0
            st.metric("Total Sales", f"₹{total_sales:,.2f}")
        with col3:
            total_profit = filtered['Profit'].sum() if 'Profit' in filtered.columns else 0
            st.metric("Total Profit", f"₹{total_profit:,.2f}")
        with col4:
            cost_sum = filtered['Our Cost As Per Qty'].sum() if 'Our Cost As Per Qty' in filtered.columns else 0
            avg_margin = (total_profit / cost_sum * 100) if cost_sum != 0 else 0
            st.metric("Avg Profit Margin", f"{avg_margin:.2f}%")

        col5, col6, col7, col8 = st.columns(4)
        with col5:
            amazon_fees_sum = filtered['Amazon Total Fees'].sum() if 'Amazon Total Fees' in filtered.columns else 0
            st.metric("Amazon Fees", f"₹{amazon_fees_sum:,.2f}")
        with col6:
            after3_sum = filtered['After 3% Profit'].sum() if 'After 3% Profit' in filtered.columns else 0
            st.metric("After 3% Profit", f"₹{after3_sum:,.2f}")
        with col7:
            qty_sum = int(filtered['Quantity'].sum()) if 'Quantity' in filtered.columns else 0
            st.metric("Total Quantity", f"{qty_sum}")
        with col8:
            unique_skus = filtered['SKU'].nunique() if 'SKU' in filtered.columns else 0
            st.metric("Unique SKUs", f"{unique_skus}")


        st.markdown("---")
        st.header("Pivot Table (Summary)")

        # Prepare for pivot table (using filtered data for UI reactivity)
        pivot_df = filtered.copy()

        # Grouping for Pivot Table
        # Rows: sku, settlement-id, order-item-id, asin
        # Values: specific sums to match User's manual excel report
        
        pivot_rename = {
            'SKU': 'sku',
            'Order Id': 'Order Id',
            'ORDER ITEM ID': 'Order Item Id',
            'ASIN': 'asin',
            'pivot_qty': 'Qty',
            'pivot_sales': 'Sales Proceed',
            'pivot_tax': 'Amazon Total Fees',
            'pivot_transferred': 'Transferred Price'
        }

        # Value columns (Pivot-specific raw values + selected calculated columns)
        base_value_cols = ['pivot_qty', 'pivot_sales', 'pivot_tax', 'pivot_transferred']
        calculated_value_cols = [
            'Amazon Fees In %', 'Our Cost', 'Our Cost As Per Qty', 'Profit', 
            'Profit In Percentage', 'Support Amount', 'With BackEnd Price', 
            'With Support Purchase As Per Qty', 'Profit With Support', 
            'Profit In Percentage With Support', '3% On Tranfered Price', 
            'After 3% Profit', 'After 3% Percentage'
        ]
        
        available_values = [v for v in (base_value_cols + calculated_value_cols) if v in pivot_df.columns]
        
        # Index columns
        index_candidates = ['SKU', 'Brand', 'Brand Manager', 'Product Name', 'Order Id', 'ORDER ITEM ID', 'ASIN']
        available_index = [c for c in index_candidates if c in pivot_df.columns]

        if available_index and available_values:
            # Create pivot table with dropna=False to ensure missing ASINs/IDs are included
            pivot_table = pivot_df.groupby(available_index, dropna=False)[available_values].sum().reset_index()
            
            # Apply renaming to match the user's requested display labels
            pivot_table = pivot_table.rename(columns=pivot_rename)
            
            # Define final display order
            requested_order = ['sku', 'Brand', 'Brand Manager', 'Product Name', 'Order Id', 'Order Item Id', 'asin']
            requested_values = ['Qty', 'Sales Proceed', 'Amazon Total Fees', 'Amazon Fees in %', 'Transferred Price']
            
            final_index_cols = [c for c in requested_order if c in pivot_table.columns]
            final_val_cols = [c for c in requested_values if c in pivot_table.columns]
            other_cols = [c for c in pivot_table.columns if c not in final_index_cols and c not in final_val_cols]
            
            pivot_table = pivot_table[final_index_cols + final_val_cols + other_cols]

            st.dataframe(pivot_table, use_container_width=True, height=400)
            
            pivot_csv_bytes = pivot_table.to_csv(index=False).encode('utf-8')
            st.download_button("Download Pivot Table CSV", data=pivot_csv_bytes, file_name="pivot_summary.csv", mime='text/csv')

            if enable_excel_export:
                if st.button("Create styled Excel for Pivot Table"):
                    try:
                        # For pivot table, we disable the auto-summary sheet as the table itself is a summary
                        pivot_xlsx = create_styled_workbook_bytes(pivot_table, header_hex="#0B5394", currency_symbol='₹', include_summary=False)
                        st.download_button(
                            label="📥 Download Styled Pivot Excel (.xlsx)",
                            data=pivot_xlsx,
                            file_name="pivot_summary_styled.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                        st.success("Styled Pivot Excel ready.")
                    except Exception as e:
                        st.error(f"Failed to build styled pivot Excel: {e}")

        st.markdown("---")
        st.header("Processed Data (Filtered)")
        # Hide internal pivot helper columns from UI and CSV download
        pivot_internal = ["pivot_sales", "pivot_tax", "pivot_qty", "pivot_transferred"]
        display_raw = filtered.drop(columns=[c for c in pivot_internal if c in filtered.columns])
        st.dataframe(display_raw, use_container_width=True, height=400)

        csv_bytes = display_raw.to_csv(index=False).encode('utf-8')
        st.download_button("Download Filtered CSV", data=csv_bytes, file_name=f"{os.path.splitext(orig_name)[0]}_filtered.csv", mime='text/csv')

        if enable_excel_export:
            st.markdown("---")
            st.header("Export: Styled Excel (Summary + Data) — exports current FILTERED view")
            if st.button("Create styled Excel for filtered data (multi-sheet, formatted)"):
                try:
                    bytes_xlsx = create_styled_workbook_bytes(filtered, header_hex="#0B5394", currency_symbol='₹')
                    st.download_button(
                        label="📥 Download Styled Excel (.xlsx) — filtered",
                        data=bytes_xlsx,
                        file_name="amazon_profit_analysis_filtered_styled.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    st.success("Styled Excel ready — click the download button above.")
                except Exception as e:
                    st.error(f"Failed to build styled Excel: {e}")

        st.markdown("---")
        st.header("Profit by Product (Table)")
        if 'Product Name' in final_df.columns:
            agg_cols_ui = {
                'Quantity': 'sum', 
                'Sales Proceed': 'sum', 
                'Profit': 'sum',
                'Support Amount': 'sum',
                'With Support Purchase As Per Qty': 'sum',
                'Profit With Support': 'sum',
                'After 3% Profit': 'sum',
                'Our Cost As Per Qty': 'sum'
            }
            agg_map_ui = {k: v for k, v in agg_cols_ui.items() if k in final_df.columns}
            
            prod_table_raw = final_df.groupby('Product Name').agg(agg_map_ui).reset_index()
            
            if 'Profit' in prod_table_raw.columns and 'Sales Proceed' in prod_table_raw.columns:
                prod_table_raw['Profit In Percentage'] = np.where(
                    prod_table_raw['Sales Proceed'] != 0,
                    (prod_table_raw['Profit'] * 100) / prod_table_raw['Sales Proceed'],
                    0
                )
            
            if 'Profit With Support' in prod_table_raw.columns and 'Sales Proceed' in prod_table_raw.columns:
                prod_table_raw['Profit In Percentage With Support'] = np.where(
                    prod_table_raw['Sales Proceed'] != 0,
                    (prod_table_raw['Profit With Support'] * 100) / prod_table_raw['Sales Proceed'],
                    0
                )
            
            final_ui_cols = [
                'Product Name', 'Quantity', 'Sales Proceed', 'Profit', 
                'Profit In Percentage', 'Support Amount', 'With Support Purchase As Per Qty', 
                'Profit With Support', 'Profit In Percentage With Support', 'After 3% Profit'
            ]
            available_ui_cols = [c for c in final_ui_cols if c in prod_table_raw.columns]
            profit_by_product_table = prod_table_raw[available_ui_cols].round(2).sort_values('Profit', ascending=False)
            st.dataframe(profit_by_product_table, use_container_width=True)

    except Exception as e:
        st.error(f"Error processing files: {e}")
        st.exception(e)
else:
    st.info("Please upload both files above to begin analysis.")
    with st.expander("Instructions"):
        st.markdown("""
        ### How to use this application:
        1. **Upload Transaction CSV**: Your Amazon unified transaction report  
           - The app will skip the first N rows by default (configurable)  
        2. **Upload Purchase Master (PM.xlsx)**: Excel file with product information  
        3. Export filtered results to CSV/Excel.
        """)
