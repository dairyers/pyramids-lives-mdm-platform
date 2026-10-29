"""Reading a user's own data for the stock plan: CSV or XLSX, column mapping, record-type mapping, validation and a
reconciliation in which every row is either demand or excluded with a reason. Nothing is dropped, rewritten or invented
silently: a row that cannot be used is kept in the table with the reason, and the same file and mapping always give the same
result. Column names, synonyms, record types and thresholds come from config analytics.inventory.ingest."""
import io
import re

import numpy as np
import pandas as pd

import engine_inventory as ei

NOT_MAPPED = "(not mapped)"
REJECT = {"date": "Rejected: date missing or not a real date",
          "quantity": "Rejected: quantity missing, not a number, or negative",
          "keys": "Rejected: country or product missing",
          "type": "Rejected: record type not mapped",
          "duplicate": "Rejected: exact duplicate row"}


def ing(config):
    return ei.inv(config)["ingest"]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def read_upload(data, filename):
    """The file as a DataFrame with its original columns plus `_row`, the row number as seen in Excel (header is row 1)."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        names = pd.ExcelFile(io.BytesIO(data)).sheet_names
        df = pd.read_excel(io.BytesIO(data), sheet_name="Data" if "Data" in names else names[0])
    elif name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data))
    else:
        raise ValueError("Upload a .csv or .xlsx file.")
    df.columns = [str(c).strip() for c in df.columns]
    df["_row"] = np.arange(len(df)) + 2
    keep = df.drop(columns="_row").notna().any(axis=1)
    return df[keep].reset_index(drop=True)


def suggest_mapping(columns, config):
    """{canonical column: file column or None}. A file column matches when its name, ignoring case, spaces and punctuation,
    equals the canonical name or a listed synonym. Each file column is used once, and the choice can be changed by the user."""
    cfg, taken, out = ing(config), set(), {}
    lookup = {}
    for c in columns:
        lookup.setdefault(_norm(c), c)
    for canon in cfg["required"] + cfg["optional"]:
        out[canon] = None
        for cand in [canon] + cfg["synonyms"].get(canon, []):
            col = lookup.get(_norm(cand))
            if col is not None and col not in taken:
                out[canon], _ = col, taken.add(col)
                break
    return out


def suggest_record_types(values, config):
    """{value in the file: canonical record type or None} from the synonym lists."""
    table = {_norm(v): cat for cat, vs in ing(config)["record_types"].items() for v in vs}
    return {str(v): table.get(_norm(v)) for v in values}


def prepare(df, mapping, value_map, config, drop_duplicates=False):
    """Standardises the file. `mapping` is {canonical: file column or None}, `value_map` {record-type value: category or None}.
    Returns a dict: table (every row, with `reason` empty when usable), issues (checks with counts), blocking (list of
    reasons the file cannot be used at all), lead_days (country, product, days) and the columns the plan does not use yet."""
    cfg = ing(config)
    out = {"table": pd.DataFrame(), "issues": [], "blocking": [], "lead_days": pd.DataFrame(columns=["country", "product", "days"])}
    missing = [c for c in cfg["required"] if not mapping.get(c)]
    if missing:
        out["blocking"].append("Map these required columns first: " + ", ".join(missing) + ".")
        return out
    t = pd.DataFrame({"row": df["_row"].to_numpy()})
    for canon, col in mapping.items():
        if col:
            t[canon] = df[col].to_numpy()
    t["date_parsed"] = pd.to_datetime(t["date"], errors="coerce")
    t["quantity_num"] = pd.to_numeric(t["quantity"], errors="coerce")
    for c in ("country", "product"):
        t[c] = t[c].astype("string").str.strip()
    t["category"] = t["record_type"].map(lambda v: value_map.get(str(v)) if pd.notna(v) else None)
    reason = pd.Series([None] * len(t), dtype=object)
    for name, bad in (("date", t["date_parsed"].isna()), ("quantity", t["quantity_num"].isna() | (t["quantity_num"] < 0)),
                      ("keys", t["country"].isna() | t["product"].isna() | (t["country"] == "") | (t["product"] == "")),
                      ("type", t["category"].isna())):
        reason = reason.where(reason.notna() | ~bad, REJECT[name])
    cols = [c for c in ["date", "country", "product", "quantity", "record_type"] + cfg["optional"] if c in t.columns]
    dup = t.duplicated(subset=cols, keep="first") & reason.isna()
    if drop_duplicates:
        reason = reason.where(reason.notna() | ~dup, REJECT["duplicate"])
    t["reason"] = reason
    t["YearMonth"] = t["date_parsed"].dt.to_period("M").dt.to_timestamp()
    for c in ["on_hand", "stockout_flag", "received_qty", "unit_cost", "price", "salvage", "order_cost", "pack_size", "moq"]:
        if c in t.columns:
            num = pd.to_numeric(t[c], errors="coerce")
            bad = int((num.isna() & t[c].notna()).sum())
            if bad:
                out["issues"].append(("warning", f"Column {c}", f"{bad} cell(s) are not numbers and were ignored.", bad))
            t[c] = num
    if "order_date" in t.columns and "receipt_date" in t.columns:
        od, rd = pd.to_datetime(t["order_date"], errors="coerce"), pd.to_datetime(t["receipt_date"], errors="coerce")
        days = (rd - od).dt.days
        okd = days.notna() & (days >= 0) & reason.isna()
        out["lead_days"] = pd.DataFrame({"country": t.loc[okd, "country"], "product": t.loc[okd, "product"], "days": days[okd].astype(float)})
        wrong = int((days < 0).sum())
        if wrong:
            out["issues"].append(("warning", "Lead time dates", f"{wrong} row(s) have a receipt date before the order date and were not used.", wrong))
    out["table"] = t
    used = t[t["reason"].isna()]
    for text, n in t["reason"].value_counts().items():
        out["issues"].append(("info", "Rows not used", f"{text} ({n} row{'s' if n != 1 else ''}).", int(n)))
    if drop_duplicates is False and int(dup.sum()):
        out["issues"].append(("warning", "Duplicates", f"{int(dup.sum())} row(s) are exact copies of an earlier row and are counted as demand. "
                                                     "Tick 'Exclude exact duplicate rows' to leave them out.", int(dup.sum())))
    if not len(used):
        out["blocking"].append("No usable rows: every row was rejected. See the reasons below.")
        return out
    setup = int((used["category"] == "mdm_setup").sum())
    if setup:
        out["issues"].append(("info", "MDM setup rows", f"{setup} row(s) are master-data setup requests. They are not demand and are reported separately.", setup))
    for c in cfg["unused_for_now"]:
        if c in t.columns and t[c].notna().any():
            out["issues"].append(("info", f"Column {c}", "Present, but not used by the plan yet (stockout adjustment is not built).", int(t[c].notna().sum())))
    demand = used[used["category"].isin(["purchase_request", "order"])]
    if len(demand):
        _series_checks(demand, cfg, out)
    return out


def _series_checks(demand, cfg, out):
    """Gaps, short history, outliers and mixed units, on the rows that count as demand. Reported, never repaired."""
    gaps, short, outliers = [], [], 0
    for (c, p), g in demand.groupby(["country", "product"]):
        m = g.groupby("YearMonth")["quantity_num"].sum()
        span = pd.date_range(m.index.min(), m.index.max(), freq="MS")
        missing = len(span) - len(m)
        if missing:
            gaps.append(f"{c}/{p} ({missing})")
        if len(span) < cfg["min_history_months"]:
            short.append(f"{c}/{p} ({len(span)} months)")
        q = g["quantity_num"]
        med = float(q.median())
        mad = float((q - med).abs().median())
        limit = med + cfg["outlier_mad_k"] * mad if mad > 0 else (cfg["outlier_mad_k"] * med if med > 0 else np.inf)
        outliers += int((q > limit).sum())
    if gaps:
        out["issues"].append(("warning", "Missing months", "Months with no rows inside a series are counted as zero demand: " + ", ".join(gaps[:8])
                              + (" ..." if len(gaps) > 8 else ""), len(gaps)))
    if short:
        out["issues"].append(("warning", "Short history", f"Under {cfg['min_history_months']} months, so a simpler forecast is used: " + ", ".join(short[:8])
                              + (" ..." if len(short) > 8 else ""), len(short)))
    if outliers:
        out["issues"].append(("warning", "Outliers", f"{outliers} quantity value(s) are far above their series' usual size. They are kept; check them.", outliers))
    mixed = []
    for p, g in demand[demand["quantity_num"] > 0].groupby("product"):
        lo, hi = g["quantity_num"].quantile(0.05), g["quantity_num"].quantile(0.95)
        if lo > 0 and hi / lo > cfg["mixed_units_ratio"]:
            mixed.append(str(p))
    if mixed:
        out["issues"].append(("warning", "Possible mixed units", "Quantities span more than a thousand-fold within: " + ", ".join(mixed)
                              + ". Check that one unit is used throughout.", len(mixed)))
    months = demand["YearMonth"].drop_duplicates().sort_values()
    if len(months):
        hole = len(pd.date_range(months.min(), months.max(), freq="MS")) - len(months)
        if hole:
            out["issues"].append(("warning", "Date gaps", f"{hole} month(s) between the first and last date have no rows for any series.", hole))


def reconcile(table, basis, config):
    """Where every row went, for the chosen demand basis. Always balances: rows in = rows in the table."""
    cfg = ing(config)
    labels = cfg["record_type_labels"]
    want = cfg["basis_type"][basis]
    rows = []
    rejected = table[table["reason"].notna()]
    for text, n in rejected["reason"].value_counts().items():
        rows.append({"category": "rejected", "label": text, "rows": int(n), "treatment": text})
    ok = table[table["reason"].isna()]
    for cat, n in ok["category"].value_counts().items():
        if cat == want:
            treat = "Demand"
        elif cat == "mdm_setup":
            treat = "Excluded: waiting on master-data setup, not demand"
        elif cat in cfg["basis_type"].values():
            treat = "Excluded: not in the chosen demand basis"
        else:
            treat = "Excluded: not demand"
        rows.append({"category": cat, "label": labels.get(cat, cat), "rows": int(n), "treatment": treat})
    recon = pd.DataFrame(rows, columns=["category", "label", "rows", "treatment"])
    assert int(recon["rows"].sum()) == len(table)
    return recon


def available_bases(table, config):
    cfg = ing(config)
    ok = table[table["reason"].isna()]
    return [b for b, cat in cfg["basis_type"].items() if (ok["category"] == cat).any()]


def demand_frame(table, basis, config):
    """The demand rows in the shape the stock plan reads: U_Entity, U_Country, U_ProductLine, Quantity, LineTotal_USD (price
    x quantity when the file has a price, otherwise empty), DocDate, YearMonth, and the optional cost columns."""
    want = ing(config)["basis_type"][basis]
    d = table[table["reason"].isna() & (table["category"] == want)]
    out = pd.DataFrame({"U_Entity": "UPLOAD", "U_Country": d["country"].astype(str).to_numpy(), "U_ProductLine": d["product"].astype(str).to_numpy(),
                        "Quantity": d["quantity_num"].astype(float).to_numpy(), "DocDate": d["date_parsed"].to_numpy(),
                        "YearMonth": d["YearMonth"].to_numpy()})
    out["LineTotal_USD"] = (d["price"] * d["quantity_num"]).to_numpy() if "price" in d.columns else np.nan
    for c in ei.FILE_COLUMNS:
        if c in d.columns and c != "price":
            out[c] = d[c].to_numpy()
    return out.reset_index(drop=True)


def data_id(table, basis):
    """A stable id for caching: the same rows and basis give the same id."""
    return f"upload:{basis}:{len(table)}:{int(pd.util.hash_pandas_object(table[['row', 'quantity_num', 'category']].astype(str), index=False).sum() % 10**12)}"
