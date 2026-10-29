"""Stock planning tab (under Sales insights): the safety stock, reorder point and order quantity for each country and
product, built from the forecast and from assumptions the planner can change. Every figure is tagged with where its
assumption came from (entered, estimated, derived or default), and the plan can be downloaded."""
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import engine_actions as eact
import engine_advice as ead
import engine_ingest as eg
import engine_inventory as ei
import engine_replay as er
from tabs import charts, common, ui
from tabs.tab_analytics import OTHERS, SERIES, SLOTS

PREFIX = "sp_"
# (key, label, kind, help). kind: months, pct, money, units, growth
FIELDS = [("lead_time_months", "Lead time", "lead"), ("review_period_months", "Review period (months)", "num"),
          ("service_level", "Service level (%)", "pct"), ("holding_rate", "Holding cost (% of unit cost a year)", "pct"),
          ("order_cost", "Ordering cost (USD per order)", "num"), ("unit_cost", "Unit cost (USD per unit)", "num"),
          ("salvage", "Salvage value (USD per unit)", "num"), ("pack_size", "Pack size (units)", "num"),
          ("moq", "Minimum order (units)", "num"), ("storage_cap", "Storage cap (units per order)", "num"),
          ("budget_cap", "Budget cap (USD per order)", "num"), ("growth_override_pct", "Extra growth (% a year)", "num")]
# name, column title, factor between what is shown and what the engine stores (percentages are shown as %)
GRID = [("lead_time_months", "Lead time (months)", 1.0), ("review_period_months", "Check stock every (months)", 1.0),
        ("service_level", "Safety level (%)", 100.0), ("holding_rate", "Holding cost (% of price a year)", 100.0),
        ("order_cost", "Cost per order (USD)", 1.0), ("unit_cost", "Purchase price per unit (USD)", 1.0),
        ("salvage", "Resale of a leftover unit (USD)", 1.0), ("pack_size", "Order in multiples of (units)", 1.0),
        ("moq", "Smallest order (units)", 1.0), ("storage_cap", "Largest order that fits (units)", 1.0),
        ("budget_cap", "Most cash for one order (USD)", 1.0), ("freight_per_unit", "Shipping per unit (USD)", 1.0),
        ("freight_per_order", "Shipping per order (USD)", 1.0), ("free_shipping_above", "Free shipping when an order is worth (USD)", 1.0)]
GUESSED = [("lead_time_months", "lead time"), ("order_cost", "cost per order"), ("holding_rate", "holding cost")]


@st.cache_resource
def _caches():
    """The expensive, assumption-independent steps (forecasts and backtests) are kept between runs."""
    return {}


@st.cache_data
def _raw():
    return common.load_orders()


@st.cache_data(show_spinner=False)
def _plan(basis, entered_json, overrides_json, only_json="", breaks_json="{}", dtype=""):
    entered = json.loads(entered_json)
    overrides = {tuple(k.split("|")): v for k, v in json.loads(overrides_json).items()}
    only = [tuple(k) for k in json.loads(only_json)] if only_json else None
    breaks = {tuple(k.split("|")): [tuple(x) for x in v] for k, v in json.loads(breaks_json).items()}
    return ei.build_plan(_raw(), common.base_config(), basis=basis, entered=entered, overrides=overrides, caches=_caches(), only=only,
                         price_breaks=breaks, discount_type=dtype or None)


@st.cache_data(show_spinner=False)
def _plan_upload(basis, entered_json, overrides_json, clean, recon, label, did, lead_days, only_json="", breaks_json="{}", dtype=""):
    entered = json.loads(entered_json)
    overrides = {tuple(k.split("|")): v for k, v in json.loads(overrides_json).items()}
    only = [tuple(k) for k in json.loads(only_json)] if only_json else None
    breaks = {tuple(k.split("|")): [tuple(x) for x in v] for k, v in json.loads(breaks_json).items()}
    return ei.build_plan(None, common.base_config(), basis=basis, entered=entered, overrides=overrides, caches=_caches(),
                         demand=(clean, recon), data_label=label, data_id=did, lead_days=lead_days, only=only, price_breaks=breaks, discount_type=dtype or None)


@st.cache_data(show_spinner=False)
def _replay_cached(clean, payload, did, basis, breaks_json="{}", dtype=""):
    resolved = {tuple(k.split("|")): a for k, a in json.loads(payload).items()}
    breaks = {tuple(k.split("|")): [tuple(x) for x in v] for k, v in json.loads(breaks_json).items()}
    return er.run_replay(clean, common.base_config(), resolved, caches=_caches(), data_id=did, basis=basis, price_breaks=breaks, discount_type=dtype or None)


@st.cache_data(show_spinner=False)
def _read(data, name):
    return eg.read_upload(data, name)


def _template_buttons():
    folder = common.ROOT / "data"
    c = st.columns(2)
    c[0].download_button("Download the Excel template", (folder / "stock_planning_template.xlsx").read_bytes(), file_name="stock_planning_template.xlsx",
                         mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=PREFIX + "tpl_xlsx", icon=":material/download:")
    c[1].download_button("Download the CSV template", (folder / "stock_planning_template.csv").read_bytes(), file_name="stock_planning_template.csv",
                         mime="text/csv", key=PREFIX + "tpl_csv", icon=":material/download:")


def _source(config):
    """The data the plan runs on. Returns None for the sample data, "wait" when the upload is not ready to use, or a dict
    (clean, recon, label, did, lead_days, basis) for an uploaded file that passed the checks."""
    cfg = ei.inv(config)["ingest"]
    mode = st.segmented_control("Data to plan from", ["sample", "upload"], default="sample", key=PREFIX + "mode",
                                format_func=lambda m: "Sample data" if m == "sample" else "Upload my own data") or "sample"
    if mode == "sample":
        return None
    st.caption("Fill in the template (the five required columns are enough to start), then upload it. Other layouts work too: "
               "you match the columns yourself in step 1.")
    _template_buttons()
    up = st.file_uploader("Upload a .csv or .xlsx file", type=["csv", "xlsx"], key=PREFIX + "file")
    if up is None:
        st.info("No file yet. Until you upload one, switch back to Sample data to see the plan.")
        return "wait"
    data = up.getvalue()
    try:
        df = _read(data, up.name)
    except Exception as exc:                                                         # a corrupt or unsupported file is shown, not hidden
        st.error(f"Could not read {up.name}: {exc}")
        return "wait"
    fid = f"{up.name}-{len(data)}"
    cols = [c for c in df.columns if c != "_row"]
    suggested = eg.suggest_mapping(cols, config)
    with st.expander("Step 1: match your columns to ours", expanded=True):
        st.caption("Required columns are marked *. A column left as (not mapped) is simply not used.")
        mapping, grid = {}, st.columns(4)
        for i, canon in enumerate(cfg["required"] + cfg["optional"]):
            options = [eg.NOT_MAPPED] + cols
            default = options.index(suggested[canon]) if suggested[canon] in options else 0
            pick = grid[i % 4].selectbox(canon + (" *" if canon in cfg["required"] else ""), options, index=default, key=f"{PREFIX}map_{canon}_{fid}")
            mapping[canon] = None if pick == eg.NOT_MAPPED else pick
    value_map = {}
    if mapping.get("record_type"):
        labels = cfg["record_type_labels"]
        back = {v: k for k, v in labels.items()}
        found = df[mapping["record_type"]].dropna().astype(str).unique().tolist()[:50]
        guess = eg.suggest_record_types(found, config)
        with st.expander("Step 2: say what each record type means", expanded=True):
            st.caption("Purchase requests and customer orders are demand. Master-data setup requests are never demand. Anything left as "
                       "(not mapped) is rejected with that reason, not guessed.")
            frame = pd.DataFrame({"In your file": found, "Counts as": [labels.get(guess[v], eg.NOT_MAPPED) for v in found]})
            edited = st.data_editor(frame, hide_index=True, disabled=["In your file"], key=f"{PREFIX}types_{fid}_{mapping['record_type']}",
                                    column_config={"Counts as": st.column_config.SelectboxColumn("Counts as", options=[eg.NOT_MAPPED] + list(labels.values()), required=True)})
            value_map = {str(a): back.get(b) for a, b in zip(edited["In your file"], edited["Counts as"])}
    drop = st.checkbox("Exclude exact duplicate rows", key=f"{PREFIX}dup_{fid}", help="Off by default: copies are counted and flagged, never removed silently.")
    res = eg.prepare(df, mapping, value_map, config, drop_duplicates=drop)
    if res["blocking"]:
        for msg in res["blocking"]:
            st.error(msg)
        if res["issues"]:
            ui.table(pd.DataFrame(res["issues"], columns=["Level", "Check", "What we found", "Count"]), hide_index=True)
        return "wait"
    table = res["table"]
    bases = eg.available_bases(table, config)
    if not bases:
        st.error("The file has no purchase requests or customer orders after mapping, so there is no demand to plan for.")
        return "wait"
    names = {b: cfg["record_type_labels"][t] + "s" for b, t in cfg["basis_type"].items()}
    basis = st.segmented_control("Demand to plan for", bases, default="requests" if "requests" in bases else bases[0], key=f"{PREFIX}ubasis_{fid}",
                                 format_func=lambda b: names[b]) or bases[0]
    st.caption(ei.inv(config)["demand_basis"]["why_requests"] if basis == "requests" else
               "Customer orders only: what was actually booked. It can lag true demand when stock runs short.")
    recon = eg.reconcile(table, basis, config)
    clean = eg.demand_frame(table, basis, config)
    m = st.columns(4)
    m[0].metric("Rows in your file", f"{len(table):,}")
    m[1].metric("Used as demand", f"{len(clean):,}")
    m[2].metric("Excluded (setup or other)", f"{int(recon.loc[recon['treatment'].str.startswith('Excluded'), 'rows'].sum()):,}")
    m[3].metric("Rejected", f"{int(recon.loc[recon['treatment'].str.startswith('Rejected'), 'rows'].sum()):,}")
    if res["issues"]:
        with st.expander(f"Checks on your data ({len(res['issues'])})", expanded=any(i[0] == "warning" for i in res["issues"])):
            ui.table(pd.DataFrame(res["issues"], columns=["Level", "Check", "What we found", "Count"]), hide_index=True)
    dups = table[table["duplicate_of"].notna()]
    if len(dups):
        with st.expander(f"Exact duplicate rows ({len(dups)}): see them before you decide"):
            st.caption("Each row below matches the earlier row named in 'Copy of row' in every column of your file (the first copy is always kept). "
                       + ("They are excluded now." if drop else "They are counted as demand now. Tick 'Exclude exact duplicate rows' above to leave them out."))
            ui.table(dups.assign(duplicate_of=dups["duplicate_of"].astype("Int64"))[["row", "duplicate_of", "date", "country", "product", "quantity", "record_type"]].astype(str).rename(
                columns={"row": "Row in your file", "duplicate_of": "Copy of row"}), hide_index=True)
    rejected = table[table["reason"].notna() & table["duplicate_of"].isna()]
    if len(rejected):
        with st.expander(f"Rows not used ({len(rejected)}): row number and reason"):
            ui.table(rejected[["row", "date", "country", "product", "quantity", "record_type", "reason"]].astype(str).rename(
                columns={"row": "Row in your file"}), hide_index=True)
    return {"clean": clean, "recon": recon, "label": f"Your data: {up.name} ({len(clean):,} rows used as demand)",
            "did": eg.data_id(table, basis), "lead_days": res["lead_days"], "basis": basis, "stock": eg.current_stock(table),
            "breaks": eg.read_price_breaks(data, up.name), "flags": eg.stockout_flags(table)}


def _entered(config):
    """Global assumption values from the form. Blank stays None so the estimate or default is used and tagged as such."""
    cfg, out = ei.inv(config), {}
    for key, _, kind in FIELDS:
        raw = st.session_state.get(PREFIX + key)
        if kind == "lead":
            if raw is not None:
                out[key] = raw / cfg["days_per_month"] if st.session_state.get(PREFIX + "lead_unit") == "days" else raw
            continue
        out[key] = None if raw is None else (raw / 100.0 if kind == "pct" else raw)
    care = st.session_state.get(PREFIX + "care") or "normal"
    if out.get("service_level") is None and care != "normal":                       # the plain choice, unless an exact % was typed
        out["service_level"] = cfg["care_levels"][care]["service_level"]
    return {k: v for k, v in out.items() if v is not None}


def _reset():
    for key, _, _ in FIELDS:
        st.session_state[PREFIX + key] = None
    st.session_state[PREFIX + "lead_unit"] = "months"
    st.session_state[PREFIX + "care"] = "normal"
    st.session_state[PREFIX + "scope_c"] = "All"
    st.session_state[PREFIX + "scope_p"] = "All"
    st.session_state[PREFIX + "gen"] = st.session_state.get(PREFIX + "gen", 0) + 1


def _scope_split(keys, country, product, entered):
    """Which rows the answers apply to. Returns (answers for every row, answers for chosen rows only {key: {...}}): when the choice covers
    every row the answers are global; otherwise only the chosen rows get them and the rest keep the standard guesses."""
    chosen = [k for k in keys if country in ("All", k[0]) and product in ("All", k[1])]
    if len(chosen) == len(keys):
        return entered, {}
    return {}, {k: dict(entered) for k in chosen}


def _form_basic(config, keys):
    """The four questions everyone can answer. Empty means: use the standard guess, and mark it as a guess."""
    cfg = ei.inv(config)
    clab, plab = _names(config)
    s = st.columns(3)
    s[0].selectbox("These answers apply to this country", ["All"] + sorted({k[0] for k in keys}), key=PREFIX + "scope_c",
                   format_func=lambda x: "All countries" if x == "All" else clab.get(x, x))
    s[1].selectbox("and this product", ["All"] + sorted({k[1] for k in keys}), key=PREFIX + "scope_p",
                   format_func=lambda x: "All products" if x == "All" else f"{x}: {plab.get(x, x)}")
    n = len(_scope_split(keys, st.session_state.get(PREFIX + "scope_c", "All"), st.session_state.get(PREFIX + "scope_p", "All"), {"x": 1})[1]) or len(keys)
    s[2].caption(f"Applies to {n} of {len(keys)} rows. The other rows keep the standard guesses until you type in the tabs below.")
    d, care = cfg["defaults"], cfg["care_levels"]
    c = st.columns(4)
    with c[0]:
        a, b = st.columns([3, 2])
        a.number_input("How long does a supplier order take to arrive?", min_value=0.0, value=None, key=PREFIX + "lead_time_months",
                       placeholder=f"guess: {d['lead_time_months']['value']:g}", help="From placing the order to having the stock. This is not in your data, so please confirm it.")
        b.selectbox("In", ["months", "days"], key=PREFIX + "lead_unit")
        lt = st.session_state.get(PREFIX + "lead_time_months")
        if lt is not None and st.session_state.get(PREFIX + "lead_unit") == "days":
            st.caption(f"{lt:g} days = {ei.months_from_days(lt, config):.2f} months")
    c[1].segmented_control("How careful should we be about running out?", list(care), default="normal", key=PREFIX + "care",
                           format_func=lambda k: care[k]["label"], help="Careful keeps more spare stock and runs short less often. "
                           + "; ".join(f"{v['label']}: {v['plain']}" for v in care.values()))
    c[2].number_input("What does it cost to keep stock? (% of its price a year)", min_value=0.0, value=None, key=PREFIX + "holding_rate",
                      placeholder="guess: 20", help="Money tied up, storage, insurance and items going out of date.")
    c[3].number_input("What does one supplier order cost? (USD)", min_value=0.0, value=None, key=PREFIX + "order_cost",
                      placeholder=f"guess: {d['order_cost']['value']:g}", help="Paperwork, freight and handling for one order. A placeholder: please confirm.")
    st.caption("Not sure? Leave a box empty. We use a standard guess and mark it as a guess in the plan.")
    st.button("Start again with the standard guesses", key=PREFIX + "reset", on_click=_reset, icon=":material/restart_alt:")


def _form_more(config):
    d = ei.inv(config)["defaults"]
    c = st.columns(4)
    c[0].number_input("How often is stock checked? (months)", min_value=0.0, value=None, key=PREFIX + "review_period_months",
                      placeholder=f"guess: {d['review_period_months']['value']:g}", help="0 means all the time.")
    c[1].number_input("Exact safety level (%)", min_value=50.0, max_value=99.9, value=None, key=PREFIX + "service_level", placeholder="from the choice above",
                      help="Overrides Relaxed, Normal or Careful. With a salvage value entered and nothing here, it is worked out from costs.")
    c[2].number_input("What one unit costs you (USD)", min_value=0.0, value=None, key=PREFIX + "unit_cost", placeholder="estimated from the data",
                      help="Left empty: the typical selling price less the assumed margin of the product line.")
    c[3].number_input("Leftover unit resale value (USD)", min_value=0.0, value=None, key=PREFIX + "salvage", placeholder="none")
    c = st.columns(4)
    c[0].number_input("Orders must be multiples of (units)", min_value=0.0, value=None, key=PREFIX + "pack_size", placeholder="none")
    c[1].number_input("Smallest order allowed (units)", min_value=0.0, value=None, key=PREFIX + "moq", placeholder="none")
    c[2].number_input("Largest order that fits in store (units)", min_value=0.0, value=None, key=PREFIX + "storage_cap", placeholder="none")
    c[3].number_input("Most cash for one order (USD)", min_value=0.0, value=None, key=PREFIX + "budget_cap", placeholder="none")
    st.number_input("What if demand grows faster? Extra growth (% a year)", value=None, key=PREFIX + "growth_override_pct", placeholder="0",
                    help="A what-if: added on top of the forecast. The forecast itself is not changed.")


def _grid_frame(base, rows, config):
    """One row per country for a product, each cell the value that row currently uses (an estimate, a guess or what was typed)."""
    clab = _names(config)[0]
    out = []
    for key in rows:
        a = base[key]
        row = {"Country": clab.get(key[0], key[0])}
        for name, label, scale in GRID:
            v = a[name]["value"]
            row[label] = None if v is None else float(v) * scale
        guesses = [text for name, text in GUESSED if a[name]["source"] == "default"]
        row["Still a guess"] = ", ".join(guesses) or "none"
        out.append(row)
    return pd.DataFrame(out)


def _changes(frame, edited, rows):
    """The cells the user changed, as {(country, product): {name: value}} in the engine's units. A cell cleared to empty is ignored."""
    out = {}
    for i, key in enumerate(rows):
        for name, label, scale in GRID:
            old, new = frame.iloc[i][label], edited.iloc[i][label]
            if pd.isna(new) or (not pd.isna(old) and abs(float(new) - float(old)) < 1e-9):
                continue
            out.setdefault(key, {})[name] = float(new) / scale
    return out


def _grids(config, keys, clean, lead_days, entered, scope_over):
    """The numbers for each product and country: one tab per product, one editable row per country. Returns the changes."""
    plab = _names(config)[1]
    base = ei.baseline_assumptions(config, clean, entered, lead_days, scope_over)
    products = sorted({p for _, p in keys})
    gen = st.session_state.get(PREFIX + "gen", 0)
    changes = {}
    for tab, product in zip(st.tabs([f"{p}: {plab.get(p, p)}" for p in products]), products):
        rows = [k for k in keys if k[1] == product]
        frame = _grid_frame(base, rows, config)
        with tab:
            edited = st.data_editor(frame, hide_index=True, disabled=["Country", "Still a guess"], key=f"{PREFIX}grid_{product}_{gen}",
                                    column_config={label: st.column_config.NumberColumn(label, min_value=0.0, max_value=99.9 if name == "service_level" else None)
                                                   for name, label, _ in GRID})
            changes.update(_changes(frame, edited, rows))
    return changes

def _headsup(flags, notes):
    """One short line in plain words about what to double-check for a row."""
    flags, notes = flags or "", notes or ""
    guessed = [name for text, name in (("Lead time is a default", "supplier lead time"), ("Ordering cost is a placeholder", "cost per order"),
                                       ("Holding rate is a default", "cost to keep stock")) if text in flags]
    bits = []
    if guessed:
        bits.append("guessed " + ", ".join(guessed))
    if "borrowed" in flags or "normal fallback" in flags:
        bits.append("little history to judge forecast misses")
    if "Under 12 months" in flags:
        bits.append("short history")
    if "No forecast demand" in flags:
        bits.append("no demand expected")
    if "counted as zero" in flags:
        bits.append("gaps in the data")
    if "No unit cost" in notes:
        bits.append("needs a unit cost to say how much to order")
    return "OK" if not bits else "Check: " + "; ".join(bits)


def _pretty(plan, config):
    """Every number, with its full name. Used for the CSV and the 'show every number' view."""
    lines, labels = config["analytics"]["product_lines"], config["analytics"]["regional_view"]["country_labels"]
    t = plan["table"].copy()
    t["country"] = t["country"].map(lambda c: labels.get(c, c))
    t["product"] = t["product"].map(lambda p: f"{p}: {lines.get(p, p)}")
    t["stockout_risk_target"] = t["stockout_risk_target"] * 100
    t = t.drop(columns=["econ"], errors="ignore")
    return t.rename(columns={
        "country": "Country", "product": "Product", "forecast_next_12m": "Forecast, next 12 months (units)",
        "price_used": "Price paid per unit (USD)", "freight_unit_cost_year": "Shipping per unit cost (USD a year)",
        "savings_vs_plain_order": "Saving from the order size chosen (USD a year)", "holding_rate": "Holding rate",
        "protection_months": "Months to cover (lead time plus check interval)", "lead_time_demand": "Expected demand over those months (units)",
        "sigma_over_interval": "Spread of that demand (units)", "safety_stock": "Spare stock (units)",
        "reorder_point": "Reorder when stock falls to (units)", "order_quantity": "Order this many (units)", "orders_per_year": "Orders a year",
        "average_inventory_units": "Average stock held (units)", "average_inventory_value": "Average stock held (USD)",
        "cash_per_order": "Cash per order (USD)", "turnover": "Times stock turns over a year", "yearly_cost": "Ordering plus holding cost (USD a year)",
        "service_level": "Safety level", "stockout_risk_target": "Chance of running short, target (%)", "error_source": "Forecast misses measured from",
        "backtest_windows": "Own test windows", "months_of_history": "Months of history", "zero_months": "Months with no demand",
        "flags": "Flags", "notes": "Notes"})


def _simple(plan, config):
    p = _pretty(plan, config)
    return pd.DataFrame({"Country": p["Country"], "Product": p["Product"],
                         "Reorder when stock falls to (units)": p["Reorder when stock falls to (units)"],
                         "Order this many (units)": p["Order this many (units)"], "Spare stock (units)": p["Spare stock (units)"],
                         "Orders a year": p["Orders a year"], "Heads-up": [_headsup(f, n) for f, n in zip(p["Flags"], p["Notes"])]})


def _downloads(plan, config):
    pretty = _pretty(plan, config)
    monthly = plan["monthly"]
    payload = {"data_source": plan["diagnostics"]["data_source"], "warning": plan["diagnostics"]["warning"],
               "run": {k: v for k, v in plan["diagnostics"].items() if k not in {"warning"}},
               "reconciliation": plan["recon"].to_dict("records"), "assumptions_log": plan["assumptions"].to_dict("records"),
               "plan": plan["table"].to_dict("records"), "plan_by_month": monthly.to_dict("records")}
    c = st.columns(4)
    c[0].download_button("Plan (CSV)", pretty.to_csv(index=False).encode("utf-8"), file_name="stock_plan.csv",
                         mime="text/csv", key=PREFIX + "dl_plan", icon=":material/download:")
    c[1].download_button("Forecast (CSV)", plan["forecast"].to_csv(index=False).encode("utf-8"), file_name="demand_forecast.csv",
                         mime="text/csv", key=PREFIX + "dl_fc", icon=":material/download:")
    c[2].download_button("Month by month (CSV)", monthly.to_csv(index=False).encode("utf-8"), file_name="stock_plan_by_month.csv",
                         mime="text/csv", key=PREFIX + "dl_month", icon=":material/download:")
    c[3].download_button("Everything, with the numbers behind it (JSON)", json.dumps(payload, indent=2, default=str).encode("utf-8"),
                         file_name="stock_plan_full.json", mime="application/json", key=PREFIX + "dl_json", icon=":material/download:")


# ----------------------------------------------------------------------------- the dashboard at the top
def _names(config):
    return config["analytics"]["regional_view"]["country_labels"], config["analytics"]["product_lines"]


def _in(df, selset):
    """Rows of df whose (country, product) is in selset."""
    return df[pd.Series(list(zip(df["country"], df["product"])), index=df.index).isin(selset)]


def _filters(keys, config):
    clab, plab = _names(config)
    c = st.columns(4)
    country = c[0].selectbox("Country", ["All"] + sorted({k[0] for k in keys}), key=PREFIX + "f_country",
                             format_func=lambda x: "All countries" if x == "All" else clab.get(x, x))
    product = c[1].selectbox("Product", ["All"] + sorted({k[1] for k in keys}), key=PREFIX + "f_product",
                             format_func=lambda x: "All products" if x == "All" else f"{x}: {plab.get(x, x)}")
    ahead = c[2].segmented_control("Look ahead", [6, 12], default=12, key=PREFIX + "f_ahead", format_func=lambda n: f"{n} months") or 12
    past = c[3].segmented_control("Past shown", ["last12", "all"], default="last12", key=PREFIX + "f_past",
                                  format_func=lambda v: "Last 12 months" if v == "last12" else "All history") or "last12"
    return [k for k in keys if (country == "All" or k[0] == country) and (product == "All" or k[1] == product)], ahead, past


def _tile(label, value, spark_values=None, colour=None, delta_pct=None, sub=""):
    delta = charts.delta_chip(delta_pct, " vs starting numbers") if delta_pct is not None else ""
    charts.card(label, value, delta, charts.spark(spark_values, colour or charts.BRAND) if spark_values is not None and len(spark_values) > 1 else "", sub)


def _pct(now, then):
    return None if not then else (now - then) / abs(then) * 100.0


def _tiles(plan, base, sel, t):
    m = _in(plan["monthly"], set(sel))
    fc = _in(plan["forecast"], set(sel))
    bt = None if base is None else _in(base["table"], set(sel))
    spare, money, cost = t["safety_stock"].sum(), t["average_inventory_value"], t["yearly_cost"]
    have_money = money.notna().any()
    c = st.columns(4)
    with c[0]:
        _tile("Spare stock to hold (units)", f"{spare:,.0f}", m.groupby("period")["safety_stock"].sum().to_numpy(), charts.BRAND,
              None if bt is None else _pct(spare, bt["safety_stock"].sum()), "Cushion for surprise rushes")
    with c[1]:
        _tile("Money tied up in stock", f"${charts.compact(money.sum())}" if have_money else "needs a unit cost", None, None,
              None if bt is None or not have_money else _pct(money.sum(), bt["average_inventory_value"].sum()), "Average value on the shelf")
    with c[2]:
        _tile("Yearly cost of ordering and holding", f"${charts.compact(cost.sum())}" if cost.notna().any() else "needs a unit cost", None, None,
              None if bt is None or not cost.notna().any() else _pct(cost.sum(), bt["yearly_cost"].sum()), f"Purchases on top: ${charts.compact(t['purchase_cost_year'].sum())}" if t["purchase_cost_year"].notna().any() else "Orders plus keeping the stock")
    with c[3]:
        _tile("Expected demand, next 12 months (units)", f"{t['forecast_next_12m'].sum():,.0f}", fc.groupby("period")["forecast"].sum().to_numpy()[:12],
              SERIES["forecast"], None, "From the forecast")


def _aggregate(plan, sel):
    s = set(sel)
    hist = None
    for k in sel:
        h = plan["history"][k]
        hist = h if hist is None else hist.add(h, fill_value=0.0)
    fc = _in(plan["forecast"], s).groupby("period")["forecast"].sum()
    m = _in(plan["monthly"], s).groupby("period")[["interval_demand", "reorder_point", "safety_stock"]].sum()
    return hist, fc, m


def _main_chart(plan, sel, config, ahead, past, t):
    clab, plab = _names(config)
    hist, fc, m = _aggregate(plan, sel)
    if hist is None or fc.empty:
        st.warning("Not enough history to plan this one.")
        return
    months = float(t["protection_months"].iloc[0])
    if past == "last12":
        hist = hist.iloc[-12:]
    fc, m = fc.iloc[:ahead], m.iloc[:ahead]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=m.index, y=m["interval_demand"], name=f"Demand over the next {months:g} months, which the stock must cover", mode="lines",
                             line={"color": charts.NEUTRAL, "width": 2, "dash": "dot", "shape": "hv"}))
    fig.add_trace(go.Scatter(x=m.index, y=m["reorder_point"], name="Reorder when stock falls to this level (covers that demand plus the cushion)", mode="lines", fill="tonexty",
                             fillcolor="rgba(0,112,242,.14)", line={"color": "#1c1917", "width": 3, "shape": "hv"}))
    fig.add_trace(go.Scatter(x=[None], y=[None], name="Shaded gap = spare stock, the cushion for surprises", mode="lines", line={"color": "rgba(0,112,242,.35)", "width": 8}))
    fig.add_trace(go.Scatter(x=hist.index, y=hist.values, name="Demand that happened each month", mode="lines+markers",
                             line={"color": SERIES["actual"], "width": 2}, marker={"size": 7}))
    fig.add_trace(go.Scatter(x=fc.index, y=fc.values, name="Forecast demand for each single month", mode="lines+markers",
                             line={"color": SERIES["forecast"], "width": 3, "dash": "dash"}, marker={"size": 7}))
    fig.update_layout(hovermode="x unified", yaxis_title="Units", xaxis_title=None, hoverlabel={"namelength": -1},
                      legend={"orientation": "v", "y": -0.12, "yanchor": "top", "x": 0})            # one entry per line, so no name is cut off
    fig.update_yaxes(rangemode="tozero", hoverformat=",.0f")
    if len(sel) == 1:
        title = f"{clab.get(sel[0][0], sel[0][0])} | Product {sel[0][1]}: {plab.get(sel[0][1], sel[0][1])}"
    else:
        title = f"{len(sel)} products added up"
    with st.container(border=True):
        charts.chart_title(title, f"Demand per month, and the stock level at which to reorder. Next {ahead} months.")
        charts.show(fig, PREFIX + "chart", 540)
    st.caption(f"Why is the dotted line higher than the forecast? The forecast shows **one month** of demand. The dotted line adds up **{months:g} months** of it, "
               f"because that is how long you wait for the next delivery, so it is about {months:g} times the monthly forecast. "
               f"Why is the black line higher still? It is a **stock level**: it has to cover "
               f"everything you will sell while waiting for the next delivery (**{months:g} months**: supplier lead time plus time until the next check), "
               f"plus the shaded cushion for surprises."
               + ("" if len(sel) == 1 else " With several products, the reorder levels are simply added up."))
    if len(sel) == 1:
        row = t.iloc[0]
        st.caption(_headsup(row["flags"], row["notes"]))
        log = plan["assumptions"]
        log = log[(log["country"] == sel[0][0]) & (log["product"] == sel[0][1])]
        source = {"entered": "you typed it", "estimated": "worked out from the data", "derived": "worked out from your other numbers", "default": "a standard guess"}
        shown = pd.DataFrame({"Number": log["assumption"].map(ei.LABELS), "Value": log["value"], "Unit": log["unit"],
                              "Where it came from": log["source"].map(source), "Note": log["note"]})
        with st.expander("Where each number behind this one came from"):
            ui.table(shown, hide_index=True)


def _card(title, sub, fig, key, height=300):
    fig.update_layout(hoverlabel={"namelength": -1})                                       # full names in the hover box
    with st.container(border=True):
        charts.chart_title(title, sub)
        charts.show(fig, key, height)


def _fit(fig, bottom=100):
    """Keep everything inside the card: room under the plot for a one-or-two-line legend, a little air at the top and right so
    labels are not cut off, and tick labels sized by the axes themselves."""
    fig.update_layout(margin={"l": 8, "r": 28, "t": 28, "b": bottom}, legend={"orientation": "h", "y": -0.2, "yanchor": "top", "x": 0, "font": {"size": 12}},
                      hoverlabel={"namelength": -1})


def _order_section(plan, sel, config, run_one):
    """Sliders for one product and country that move the order size, the cost curve and the stock over a year together.
    Nothing here changes the plan: the slider values only drive the two charts and the four numbers beside them."""
    clab, plab = _names(config)
    ui.section("How the order size is chosen", "Pick a product and country, then drag the sliders to see what each factor does. This does not change your plan.")
    key = st.selectbox("Product and country", sel, key=PREFIX + "one",
                       format_func=lambda k: f"{clab.get(k[0], k[0])} | Product {k[1]}: {plab.get(k[1], k[1])}")
    t = plan["table"]
    base = t[(t["country"] == key[0]) & (t["product"] == key[1])].iloc[0].to_dict()
    title = f"{clab.get(key[0], key[0])} | Product {key[1]}: {plab.get(key[1], key[1])}"
    if base.get("order_quantity") is None or pd.isna(base.get("order_quantity")) or base["order_quantity"] <= 0:
        with st.container(border=True):
            charts.chart_title(title, "Order size and stock over a year")
            st.info("This needs a unit cost to work out how much to order. Add a purchase price in the grid above, or include one in your file.")
        return

    log = plan["assumptions"]
    log = log[(log["country"] == key[0]) & (log["product"] == key[1]) & (log["assumption"] == "demand_change_pct")]
    change0 = float(log["value"].iloc[0] or 0.0) if len(log) else 0.0
    care_levels = ei.inv(config)["care_levels"]
    care0 = min(care_levels, key=lambda c: abs(care_levels[c]["service_level"] - base["service_level"]))
    C0, K0, h0 = float(base["unit_cost"]), float(base["order_cost"]), float(base["holding_rate"]) * 100.0
    econ0 = base["econ"] if isinstance(base.get("econ"), dict) else {}
    fpu0, fpo0 = float(econ0.get("fpu", 0.0)), float(econ0.get("fpo", 0.0))
    L0, R0 = float(base["lead_time_months"]), float(base["review_period_months"])
    gen = st.session_state.get(PREFIX + "slgen", 0)
    sk = lambda name: f"{PREFIX}sl_{name}_{key[0]}{key[1]}_{gen}_{round(C0, 2)}_{round(K0, 2)}_{round(L0, 2)}"       # noqa: E731
    price_step = max(0.01, round(C0 / 100.0, 2))
    order_step = max(1.0, round(K0 / 50.0))

    left, right = st.columns([1, 2])
    with left:
        st.markdown("**Play with the numbers**")
        s_demand = st.slider("More or less demand than the plan expects (%)", -50, 100, 0, 5, key=sk("demand"))
        s_lead = st.slider("Supplier lead time (months)", 0.0, max(6.0, 2 * L0), float(L0), 0.25, key=sk("lead"))
        s_review = st.slider("Check stock every (months)", 0.0, max(3.0, 2 * R0), float(R0), 0.25, key=sk("review"))
        s_order = st.slider("Cost of placing one order (USD)", 0.0, float(max(1000.0, 4 * K0)), float(K0), float(order_step), key=sk("order"))
        s_price = st.slider("Purchase price per unit (USD)", float(max(0.01, round(C0 * 0.1, 2))), float(round(C0 * 3, 2)), float(round(C0, 2)),
                            float(price_step), key=sk("price"))
        s_hold = st.slider("Cost of keeping stock (% of price a year)", 0, max(60, int(round(h0)) + 1), int(round(h0)), 1, key=sk("hold"))
        s_fpu = st.slider("Shipping per unit (USD)", 0.0, float(max(round(C0 * 0.5, 2), fpu0 * 2, 1.0)), float(fpu0), float(max(0.01, round(C0 / 200.0, 2))), key=sk("fpu"))
        s_fpo = st.slider("Shipping per order (USD)", 0.0, float(max(1000.0, 4 * fpo0)), float(fpo0), float(order_step), key=sk("fpo"))
        s_care = st.segmented_control("How careful about running out", list(care_levels), default=care0, key=sk("care"),
                                      format_func=lambda c: care_levels[c]["label"]) or care0
        st.button("Back to the plan's numbers", key=PREFIX + "slreset", icon=":material/restart_alt:",
                  on_click=lambda: st.session_state.__setitem__(PREFIX + "slgen", gen + 1))

    changed = {}
    if s_demand != 0:
        changed["demand_change_pct"] = ((1 + change0 / 100.0) * (1 + s_demand / 100.0) - 1) * 100.0
    if abs(s_lead - L0) > 1e-9:
        changed["lead_time_months"] = s_lead
    if abs(s_review - R0) > 1e-9:
        changed["review_period_months"] = s_review
    if abs(s_order - K0) > 1e-9:
        changed["order_cost"] = s_order
    if abs(s_price - round(C0, 2)) > 1e-9:
        changed["unit_cost"] = s_price
    if s_hold != int(round(h0)):
        changed["holding_rate"] = s_hold / 100.0
    if abs(s_fpu - fpu0) > 1e-9:
        changed["freight_per_unit"] = s_fpu
    if abs(s_fpo - fpo0) > 1e-9:
        changed["freight_per_order"] = s_fpo
    if s_care != care0:
        changed["service_level"] = care_levels[s_care]["service_level"]
    res = run_one(key, changed) if changed else plan
    rt = res["table"]
    row = rt[(rt["country"] == key[0]) & (rt["product"] == key[1])].iloc[0].to_dict()
    month = _in(res["monthly"], {key})

    with left:
        def num(label, field, fmt):
            delta = None if not changed else f"{row[field] - base[field]:+,.0f} vs plan"
            st.metric(label, fmt.format(row[field]), delta, delta_color="off")
        c = st.columns(2)
        with c[0]:
            num("Order amount (units)", "order_quantity", "{:,.0f}")
            num("Spare stock (units)", "safety_stock", "{:,.0f}")
        with c[1]:
            num("Reorder level (units)", "reorder_point", "{:,.0f}")
            num("Total yearly cost (USD)", "total_cost_year", "${:,.0f}")
        st.caption("Total yearly cost = purchases + ordering + keeping stock." if changed else "Showing your plan. Move a slider to try a change.")

    with right:
        curve = ei.cost_curve(row)
        e_now = row.get("econ") if isinstance(row.get("econ"), dict) else {}
        steps = bool(e_now.get("breaks")) or e_now.get("free_above") is not None
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=curve["order_quantity"], y=curve["ordering"], name="Placing orders", mode="lines",
                                 line={"color": "#c26f1e", "width": 2, "dash": "dot"}, hovertemplate="%{y:$,.0f} a year<extra>Placing orders (falls as orders get bigger)</extra>"))
        fig.add_trace(go.Scatter(x=curve["order_quantity"], y=curve["holding"], name="Keeping stock", mode="lines",
                                 line={"color": charts.NEUTRAL, "width": 2, "dash": "dot"}, hovertemplate="%{y:$,.0f} a year<extra>Keeping stock (rises as orders get bigger)</extra>"))
        ycol = "total" if steps else "ordering_plus_holding"
        fig.add_trace(go.Scatter(x=curve["order_quantity"], y=curve[ycol], name="Everything, including purchases and shipping" if steps else "Both together", mode="lines",
                                 line={"color": charts.BRAND, "width": 4}, hovertemplate="%{y:$,.0f} a year<extra>" + ("Everything" if steps else "Both together") + "</extra>"))
        used = curve[curve["order_quantity"] == row["order_quantity"]].iloc[0]
        fig.add_trace(go.Scatter(x=[used["order_quantity"]], y=[used[ycol]], name="Order amount used", mode="markers",
                                 marker={"size": 16, "color": "#1c1917", "symbol": "diamond"}, hoverinfo="skip"))
        fig.add_annotation(x=used["order_quantity"], y=used[ycol], ax=0, ay=-58, showarrow=True, arrowcolor="#1c1917",
                           text=f"<b>Best order: {row['order_quantity']:,.0f} units</b><br>{used[ycol]:,.0f} USD a year",
                           bgcolor="rgba(255,255,255,.9)", bordercolor="#1c1917", borderwidth=1, font={"size": 13})
        fig.update_xaxes(title_text="Units per order", tickformat=",.0f", range=[float(curve["order_quantity"].min()), float(curve["order_quantity"].max())])
        fig.update_yaxes(title_text="Yearly cost, including purchases (USD)" if steps else "Yearly cost that depends on order size (USD)", tickformat="$,.0f",
                         rangemode="normal" if steps else "tozero")
        fig.update_layout(hovermode="x unified")
        _fit(fig, 90)
        _card("What each order size costs", ("With a discount or free shipping, the price paid changes with the order size, so purchases are included: the curve steps down where a break is reached. " if steps else
                                             f"The lowest point is the best order amount. Purchases ({row['purchase_cost_year']:,.0f} USD a year) are the same at every size, so they are left out.") +
              (f" {row['notes']}" if steps and row.get("savings_vs_plain_order") else ""),
              fig, PREFIX + "c_u", 520)

    path, start = month["forecast"].to_numpy(), month["period"].iloc[0]
    args = (path, row["order_quantity"], row["reorder_point"], row["safety_stock"], row["lead_time_months"], row["review_period_months"], config)
    expected = ei.inventory_profile(*args, start=start)
    high_scale = float(row["demand_ratio_at_service"])
    high = ei.inventory_profile(*args, demand_scale=high_scale, start=start)
    pct = max(high_scale - 1, 0)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=expected["date"], y=expected["on_hand"], name="Stock on the shelf (demand as forecast)", mode="lines",
                             line={"color": SERIES["actual"], "width": 3}))
    fig.add_trace(go.Scatter(x=expected["date"], y=expected["position"], name="Stock plus orders on the way", mode="lines",
                             line={"color": charts.NEUTRAL, "width": 1.5, "dash": "dot"}))
    fig.add_trace(go.Scatter(x=high["date"], y=high["on_hand"], name=f"Stock if demand runs high ({pct:.0%} above forecast)", mode="lines",
                             line={"color": "#c26f1e", "width": 2, "dash": "dash"}))
    fig.add_hline(y=row["reorder_point"], line={"color": "#1c1917", "width": 2}, annotation_text="Reorder level", annotation_position="top left")
    fig.add_hline(y=row["safety_stock"], line={"color": charts.NEUTRAL, "width": 1.5, "dash": "dash"}, annotation_text="Spare stock", annotation_position="bottom left")
    fig.update_xaxes(tickformat="%b %Y", dtick="M2", range=[expected["date"].min(), expected["date"].max()])
    fig.update_yaxes(title_text="Units", rangemode="tozero", hoverformat=",.0f")
    fig.update_layout(hovermode="x unified")
    _fit(fig, 100)
    _card(title, f"Each drop is sales, each jump is a delivery of {row['order_quantity']:,.0f} units, placed when stock plus orders on the way falls to the reorder level.",
          fig, PREFIX + "c_saw", 500)
    short = float(high["shortage"].sum())
    st.caption("At the forecast, stock never runs out. " + (f"At the high end the plan is built for ({pct:.0%} above the forecast all year, a tough test), stock is pushed to its limit: "
                                                          f"about {short:,.0f} units short over the year, because orders arrive in whole lots and stock is only checked every so often."
                                                          if short > 0.5 else f"Even at the high end the plan is built for ({pct:.0%} above the forecast all year), the spare stock covers it."))


def _side_charts(plan, sel, t, config, hidden_cost):
    clab, plab = _names(config)
    palette = SLOTS + OTHERS                                  # products A, B and C keep their colours from the Overview tab
    prod_colour = {p: palette[i % len(palette)] for i, p in enumerate(sorted({k[1] for k in plan["history"]}))}
    country_colour = SLOTS + [charts.BRAND, charts.NEUTRAL]    # the chart template's own order, as used for countries elsewhere
    t = t.dropna(subset=["reorder_point"])

    spare = go.Figure()
    for i, c in enumerate(sorted(t["country"].unique())):
        d = t[t["country"] == c].sort_values("product")
        spare.add_trace(go.Bar(y=[f"{p}: {plab.get(p, p)}" for p in d["product"]], x=d["safety_stock"], orientation="h", name=clab.get(c, c),
                               marker_color=country_colour[i % len(country_colour)], hovertemplate="%{y}<br>%{x:,.0f} units<extra>" + clab.get(c, c) + "</extra>"))
    spare.update_layout(barmode="stack", xaxis_title="Spare stock (units)", yaxis={"autorange": "reversed"}, legend={"y": -0.3})

    care_levels = ei.inv(config)["care_levels"]
    names = {round(v["service_level"], 2): v["label"] for v in care_levels.values()}
    tr = _in(plan["tradeoff"], set(sel)).groupby("service_level", as_index=False)[["safety_stock", "holding_cost_of_safety_stock"]].sum(min_count=1)
    tr["label"] = tr["service_level"].map(lambda s: names.get(round(s, 2), f"{s:.0%}"))
    care = go.Figure(go.Bar(x=tr["label"], y=tr["safety_stock"], name="Spare stock (units)", marker_color=charts.BRAND,
                            hovertemplate="%{x}<br>%{y:,.0f} units<extra></extra>"))
    if tr["holding_cost_of_safety_stock"].notna().any():
        care.add_trace(go.Scatter(x=tr["label"], y=tr["holding_cost_of_safety_stock"], name="Yearly cost of keeping it (USD)", mode="lines+markers",
                                  yaxis="y2", line={"color": "#c26f1e", "width": 3}, marker={"size": 8}))
        care.update_layout(yaxis2={"overlaying": "y", "side": "right", "showgrid": False, "rangemode": "tozero", "title": {"text": "USD a year"}})
    care.update_layout(yaxis_title="Units", legend={"y": -0.35})

    cols = st.columns(2)
    with cols[0]:
        _card("Spare stock by product", "How much cushion each product needs, split by country", spare, PREFIX + "c_spare")
    with cols[1]:
        _card("What being more careful costs", "Spare stock and its yearly cost at each setting", care, PREFIX + "c_care")

    if hidden_cost:
        with st.container(border=True):
            charts.chart_title("Money tied up in stock", "Needs a unit cost")
            st.info("There is no unit cost for these products, so money figures are not worked out. Add one in the grid, or include it in your file.")
    else:
        pv = t.pivot_table(index="country", columns="product", values="average_inventory_value", aggfunc="sum")
        heat = go.Figure(go.Heatmap(z=pv.to_numpy(), x=[f"{p}: {plab.get(p, p)}" for p in pv.columns], y=[clab.get(c, c) for c in pv.index],
                                    colorscale=charts.RAMP, text=pv.to_numpy(), texttemplate="$%{z:,.0f}", hovertemplate="%{y}, %{x}<br>$%{z:,.0f}<extra></extra>",
                                    showscale=False))
        heat.update_layout(yaxis={"autorange": "reversed"})
        share = t.groupby("product")["average_inventory_value"].sum()
        top = share.idxmax()
        _card("Money tied up in stock", f"Average value on the shelf. {plab.get(top, top)} is {share.max() / share.sum():.0%} of it: each unit is worth more.",
              heat, PREFIX + "c_money", 340)

    if hidden_cost or not t["days_of_stock"].notna().any():
        with st.container(border=True):
            charts.chart_title("Which products tie up the most money for the longest", "Needs a unit cost")
            st.info("There is no unit cost for these products, so money tied up and days of stock are not worked out. Add one in the grid, or include it in your file.")
        return
    d = t.dropna(subset=["days_of_stock", "average_inventory_value"])
    hold = d["holding_cost_year"].fillna(0).clip(lower=0)
    top = float(hold.max() or 1.0)
    bub = go.Figure()
    for p in sorted(d["product"].unique()):
        r = d[d["product"] == p]
        size = 14 + 56 * (r["holding_cost_year"].fillna(0).clip(lower=0) / top) ** 0.5
        bub.add_trace(go.Scatter(x=r["days_of_stock"], y=r["average_inventory_value"], mode="markers+text", name=f"{p}: {plab.get(p, p)}",
                                 text=[clab.get(c, c)[:3].upper() for c in r["country"]], textposition="top center",
                                 marker={"size": size, "color": prod_colour[p], "opacity": 0.75, "line": {"width": 1, "color": "#ffffff"}},
                                 customdata=r[["country", "average_inventory_units", "holding_cost_year"]].to_numpy(),
                                 hovertemplate="%{customdata[0]}, " + p + "<br>%{x:,.0f} days of stock on the shelf<br>%{y:$,.0f} tied up"
                                               "<br>%{customdata[1]:,.0f} units on average<br>%{customdata[2]:$,.0f} a year to keep it<extra></extra>"))
    typical = float(d["days_of_stock"].median())
    bub.add_vline(x=typical, line={"color": charts.NEUTRAL, "width": 1.5, "dash": "dot"}, annotation_text=f"typical: {typical:,.0f} days", annotation_position="top")
    bub.update_xaxes(title_text="Days of stock on the shelf (average). Further right = sits longer before it sells", rangemode="tozero")
    bub.update_yaxes(title_text="Money tied up (USD)", tickformat="$,.0f", rangemode="tozero")
    bub.update_xaxes(range=[0, float(d["days_of_stock"].max()) * 1.15])
    bub.update_yaxes(range=[0, float(d["average_inventory_value"].max()) * 1.2])                    # headroom so the top bubbles and labels are not clipped
    bub.update_layout(margin={"l": 8, "r": 28, "t": 56, "b": 8}, legend={"y": -0.18}, hoverlabel={"namelength": -1})
    _card("Which products tie up the most money for the longest",
          "Right = stock sits longer before it sells. Higher = more money tied up. Bigger bubble = more it costs a year to keep. Top right needs attention.",
          bub, PREFIX + "c_bubble", 580)

def _breaks_from_rows(rows, keys):
    """Price breaks typed in the table -> {(country, product): [(from quantity, price per unit), ...]}. A row for 'All' countries applies to every
    country of that product unless that country has rows of its own. Incomplete or non-positive rows are ignored."""
    out = {}
    if rows is None or len(rows) == 0:
        return out
    ok = rows.dropna(subset=["Product", "From quantity", "Price per unit"])
    ok = ok[(ok["Price per unit"] > 0) & (ok["From quantity"] >= 0)]
    for k in keys:
        mine = ok[ok["Product"] == k[1]]
        use = mine[mine["Country"] == k[0]]
        use = use if len(use) else mine[mine["Country"] == "All"]
        if len(use):
            out[k] = sorted((float(q), float(p)) for q, p in zip(use["From quantity"], use["Price per unit"]))
    return out


def _discounts_ui(config, keys, file_breaks, did):
    """Quantity discounts: a table of price breaks (typed here or read from the upload's Price breaks sheet) and how the lower price applies."""
    cfg = ei.inv(config)
    types = cfg["discount"]["types"]
    st.caption("A price break says: from this many units per order, the price per unit is lower. Leave the table empty for no discounts. "
               "Shipping is set in the product tabs above (shipping per unit, per order, and the order value that earns free shipping).")
    dtype = st.selectbox("How the lower price applies", list(types), index=list(types).index(cfg["discount"]["default"]), key=PREFIX + "dtype",
                         format_func=lambda t: types[t])
    cols = ["Country", "Product", "From quantity", "Price per unit"]
    seed = pd.DataFrame([{"Country": "All" if c == "ALL" else c, "Product": p, "From quantity": q, "Price per unit": pr}
                         for (c, p), lst in sorted(file_breaks.items()) for q, pr in lst], columns=cols)
    seed = seed.astype({"Country": "object", "Product": "object", "From quantity": "float64", "Price per unit": "float64"})
    countries, products = ["All"] + sorted({k[0] for k in keys}), sorted({k[1] for k in keys})
    edited = st.data_editor(seed, num_rows="dynamic", hide_index=True, key=f"{PREFIX}breaks_{did}", column_config={
        "Country": st.column_config.SelectboxColumn("Country", options=countries, required=True),
        "Product": st.column_config.SelectboxColumn("Product", options=products, required=True),
        "From quantity": st.column_config.NumberColumn("From quantity (units per order)", min_value=0.0),
        "Price per unit": st.column_config.NumberColumn("Price per unit (USD)", min_value=0.0)})
    return _breaks_from_rows(edited, keys), dtype


def _stockout_step(config, clean0, file_flags, did):
    """Months that may have been stockouts, for the user to confirm. Nothing is changed unless confirmed. Returns the confirmed repairs as
    [(country, product, month, expected demand)]."""
    so = ei.inv(config)["stockout"]
    cands = ei.stockout_candidates(clean0, config, extra=file_flags)
    with st.expander(f"Possible stockouts in your history ({len(cands)})", expanded=bool(len(cands))):
        st.caption(so["note"])
        if cands.empty:
            st.caption("No month looks like a stockout, so nothing is adjusted.")
            return []
        clab, plab = _names(config)
        gen, mode = st.session_state.get(PREFIX + "so_gen", 0), st.session_state.get(PREFIX + "so_mode", "default")

        def setmode(m):
            st.session_state[PREFIX + "so_mode"] = m
            st.session_state[PREFIX + "so_gen"] = gen + 1
        b = st.columns(4)
        b[0].button("Confirm all", key=PREFIX + "so_all", on_click=lambda: setmode("all"))
        b[1].button("Clear all", key=PREFIX + "so_none", on_click=lambda: setmode("none"))
        confirm = [True if mode == "all" else False if mode == "none" else k == "marked in your file" for k in cands["kind"]]
        frame = pd.DataFrame({"Confirm": confirm, "Country": cands["country"].map(lambda c: clab.get(c, c)),
                              "Product": cands["product"].map(lambda p: f"{p}: {plab.get(p, p)}"), "Month": cands["month"].dt.strftime("%b %Y"),
                              "Sold": cands["actual"].round(0), "Expected": cands["expected"].round(0), "What it looks like": cands["kind"]})
        edited = st.data_editor(frame, hide_index=True, disabled=[c for c in frame.columns if c != "Confirm"], key=f"{PREFIX}so_{did}_{gen}")
        picked = cands[edited["Confirm"].to_numpy(dtype=bool)]
        repairs = [(r.country, r.product, r.month, float(r.expected)) for r in picked.itertuples()]
        if repairs:
            added = float((picked["expected"] - picked["actual"]).sum())
            st.caption(f"{len(repairs)} month(s) will be replaced by the expected demand, adding about {added:,.0f} units to the history. "
                       "Everything below (forecast, plan, replay) uses the adjusted history.")
        else:
            st.caption("Nothing confirmed, so the history is used exactly as it is.")
        return repairs


def _actions_section(plan, config, file_stock, did):
    """Today's actions: enter the stock you have, and see for each product whether to order now, order soon, wait, or hold off."""
    clab, plab = _names(config)
    a = ei.inv(config)["actions"]
    ui.section("What to do today", "Enter the stock you have right now. For each product and country, this says whether to order, wait or hold off.")
    t = plan["table"].dropna(subset=["reorder_point"]).reset_index(drop=True)
    example = bool(st.session_state.get(PREFIX + "stock_example"))
    gen = st.session_state.get(PREFIX + "stock_gen", 0)
    if example:
        base = eact.example_stock(plan["table"], config)
        origin = "example"
    elif len(file_stock):
        base, origin = file_stock, "file"
    else:
        base, origin = pd.DataFrame(columns=["country", "product", "on_hand", "on_order"]), "blank"
    merged = t[["country", "product"]].merge(base, on=["country", "product"], how="left")
    frame = pd.DataFrame({"Country": merged["country"].map(lambda c: clab.get(c, c)), "Product": merged["product"].map(lambda p: f"{p}: {plab.get(p, p)}"),
                          "On hand now (units)": merged["on_hand"].astype("float64"), "On order, not yet received (units)": merged["on_order"].astype("float64")})
    b = st.columns([1, 1, 3])
    b[0].button("Fill with example figures", key=PREFIX + "ex_on", icon=":material/science:",
                on_click=lambda: (st.session_state.__setitem__(PREFIX + "stock_example", True), st.session_state.__setitem__(PREFIX + "stock_gen", gen + 1)))
    b[1].button("Clear the figures", key=PREFIX + "ex_off", icon=":material/delete:",
                on_click=lambda: (st.session_state.__setitem__(PREFIX + "stock_example", False), st.session_state.__setitem__(PREFIX + "stock_gen", gen + 1)))
    if origin == "example":
        ui.banner("Example figures, not your real stock. Type over them or clear them.", "These numbers are made up, the same every time, only to show how this screen works.", tone="orange")
    elif origin == "file":
        st.caption("Latest stock figures from your uploaded file. You can type over them.")
    edited = st.data_editor(frame, hide_index=True, disabled=["Country", "Product"], key=f"{PREFIX}stock_{did}_{origin}_{gen}",
                            column_config={c: st.column_config.NumberColumn(c, min_value=0.0) for c in frame.columns[2:]})
    current = pd.DataFrame({"country": t["country"], "product": t["product"], "on_hand": edited.iloc[:, 2].to_numpy(dtype=float),
                            "on_order": edited.iloc[:, 3].to_numpy(dtype=float)})
    status = eact.status_table(plan["table"], current, config)
    s = eact.summary(status)
    if s["missing"] == len(status):
        st.info("Enter your stock above, or press 'Fill with example figures', to see what to do.")
        return
    c = st.columns(4)
    c[0].metric("Out of stock or order now", s["out"] + s["now"], help="Stock plus orders on the way is at or below the reorder level.")
    c[1].metric("Order soon", s["soon"], help="Within one order of reaching the reorder level.")
    c[2].metric("Cash for the orders to place", f"${s['order_cash']:,.0f}" if s["order_cash"] else "n/a", help="Cost of the suggested orders. Needs a unit cost.")
    c[3].metric("Cash in stock beyond what is needed", f"${s['excess_cash']:,.0f}" if s["excess_cash"] else "n/a", help="Stock above what the plan aims for.")
    shown = status[status["status"] != "Enter current stock"].copy()
    colour = {"Out of stock": charts.BAD, "Order now": charts.BAD, "Order soon": "#c26f1e", "Fine": charts.GOOD, "More than needed": charts.BRAND}
    shown["label"] = [f"{clab.get(c, c)} | {p}" for c, p in zip(shown["country"], shown["product"])]
    shown["pct"] = shown["position"] / shown["reorder_point"].where(shown["reorder_point"] > 0) * 100
    fig = go.Figure(go.Bar(y=shown["label"], x=shown["pct"], orientation="h", marker_color=[colour[x] for x in shown["status"]], text=shown["status"], textposition="auto",
                           hovertemplate="%{y}<br>%{x:,.0f}% of the reorder level<extra></extra>"))
    fig.add_vline(x=100, line={"color": "#1c1917", "width": 2}, annotation_text="Reorder level", annotation_position="top")
    fig.update_xaxes(title_text="Stock on hand plus on order, as % of the reorder level", ticksuffix="%", rangemode="tozero")
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(margin={"l": 8, "r": 28, "t": 40, "b": 8})
    _card("Where each product stands", "Left of the line means it is time to order. The bar colour is the status.", fig, PREFIX + "c_act", max(320, 26 * len(shown) + 120))
    pretty = pd.DataFrame({"Country": status["country"].map(lambda c: clab.get(c, c)), "Product": status["product"].map(lambda p: f"{p}: {plab.get(p, p)}"),
                           "What to do": status["status"], "On hand": status["on_hand"], "On order": status["on_order"], "Reorder level": status["reorder_point"],
                           "Days of cover": status["days_of_cover"], "Suggested order": status["suggested_order"], "Cost of that order (USD)": status["order_cash"],
                           "Notes": status["note"]})
    ui.table(pretty, hide_index=True, column_config={c: st.column_config.NumberColumn(format="%.0f") for c in pretty.columns if c not in {"Country", "Product", "What to do", "Notes"}})
    todo = pretty[status["suggested_order"].notna().to_numpy()]
    st.download_button("Orders to place (CSV)", todo.to_csv(index=False).encode("utf-8"), file_name="orders_to_place.csv", mime="text/csv",
                       key=PREFIX + "dl_orders", icon=":material/download:", disabled=todo.empty)


def _advice_section(plan, config, what_if):
    """What should we do: recommendations that change with what the company wants and how much risk it can accept."""
    a = ei.inv(config)["advice"]
    ui.section("What should we do?", "Tell us what matters and how much risk you can accept. The advice below changes with your answers, "
                                     "and every number comes from planning the affected products again with the change.")
    c = st.columns(2)
    goal = c[0].segmented_control("What matters most right now?", list(a["goals"]), default=a["default_goal"], key=PREFIX + "goal",
                                  format_func=lambda g: a["goals"][g]["label"]) or a["default_goal"]
    risk = c[1].segmented_control("How much chance of running short can you accept?", list(a["risk_levels"]), default=a["default_risk"], key=PREFIX + "risk",
                                  format_func=lambda r: a["risk_levels"][r]["label"]) or a["default_risk"]
    st.caption(f"For a company that wants to {a['goals'][goal]['plain']}, where {a['risk_levels'][risk]['plain']}:")
    with st.spinner("Trying the options..."):
        recs = ead.recommend(plan, what_if, config, goal, risk)
    if not recs:
        st.info("Nothing stands out for these choices. The plan already fits what you asked for.")
        return
    badge = {"cost": "green", "cash": "green", "service": "blue", "confidence": "gray", "effort": "gray", "risk": "orange"}
    for i, r in enumerate(recs, 1):
        with st.container(border=True):
            st.markdown(f"**{i}. {r['title']}**  " + " ".join(f":{badge[t]}-badge[{ead.TAG_LABELS[t]}]" for t in r["tags"]))
            st.markdown(r["why"])
            for line in r["effects"]:
                st.markdown(f"- {line}")
            st.markdown(f"**What to do:** {r['action']}")


def _outlook(plan, sel, config):
    """Demand over the last 12 months against the next 12, by product (or by country when one product is chosen)."""
    clab, plab = _names(config)
    t = _in(plan["table"], set(sel)).dropna(subset=["reorder_point"])
    one_product = len({k[1] for k in sel}) == 1
    rows = []
    for _, r in t.iterrows():
        last12 = float(plan["history"][(r["country"], r["product"])].iloc[-12:].sum())
        rows.append({"group": clab.get(r["country"], r["country"]) if one_product else f"{r['product']}: {plab.get(r['product'], r['product'])}",
                     "last": last12, "next": float(r["forecast_next_12m"])})
    g = pd.DataFrame(rows).groupby("group", sort=False)[["last", "next"]].sum()
    fig = go.Figure()
    fig.add_trace(go.Bar(x=g.index, y=g["last"], name="Last 12 months", marker_color=charts.NEUTRAL, hovertemplate="%{x}<br>%{y:,.0f} units<extra>Last 12 months</extra>"))
    fig.add_trace(go.Bar(x=g.index, y=g["next"], name="Next 12 months (forecast)", marker_color=charts.BRAND, hovertemplate="%{x}<br>%{y:,.0f} units<extra>Next 12 months</extra>",
                         text=[f"{(n - l) / l * 100:+.1f}%" if l else "" for l, n in zip(g["last"], g["next"])], textposition="outside"))
    fig.update_layout(barmode="group", yaxis_title="Units")
    fig.update_yaxes(rangemode="tozero", hoverformat=",.0f")
    _fit(fig, 70)
    total_last, total_next = g["last"].sum(), g["next"].sum()
    sub = f"Total demand is expected to {'grow' if total_next >= total_last else 'fall'} {abs(total_next - total_last) / max(total_last, 1) * 100:.1f}%, from {total_last:,.0f} to {total_next:,.0f} units."
    _card("Where demand is heading", sub, fig, PREFIX + "c_outlook", 360)


def _plan_section(plan, config):
    """The plan in one line per product, with the numbers behind it and the downloads."""
    cfg = ei.inv(config)
    ui.section("The plan, product by product")
    care = cfg["care_levels"][st.session_state.get(PREFIX + "care") or "normal"]
    t = plan["table"]
    st.markdown(f"With the **{care['label'].lower()}** setting you {care['plain']}. To get there, keep about "
                f"**{t['safety_stock'].sum():,.0f} units** of spare stock across all {len(t)} products, and reorder each one when its stock "
                f"falls to the level in the table.")
    st.caption(f"Based on: {plan['diagnostics']['data_source']}.")
    simple = _simple(plan, config)
    ui.table(simple, hide_index=True, column_config={
        c: st.column_config.NumberColumn(format="%.0f" if c != "Orders a year" else "%.1f") for c in simple.columns if c not in {"Country", "Product", "Heads-up"}})
    if st.toggle("Show every number", key=PREFIX + "all"):
        pretty = _pretty(plan, config)
        ui.table(pretty.drop(columns=["Notes"]), hide_index=True, column_config={
            c: st.column_config.NumberColumn(format="%.1f") for c in pretty.columns if c not in {"Country", "Product", "Forecast misses measured from", "Flags", "Notes"}})
    st.markdown("**Download**")
    _downloads(plan, config)


POLICY_COLOUR = {"plan": charts.BRAND, "flat": charts.NEUTRAL, "moving": "#c26f1e"}


def _clear(c):
    return {"better": "clearly better", "worse": "clearly worse", "unclear": "no clear difference"}[c]


def _verdict_lines(out, config):
    """Plain sentences about the replay. Nothing is called an improvement unless the range across products excludes zero."""
    labels = ei.inv(config)["replay"]["policy_labels"]
    s = out["summary"].set_index("policy")
    lines, tally = [], {"fill": [], "cost": []}
    for p in ("flat", "moving"):
        v = {x["metric"]: x for x in out["verdict"] if x["versus"] == p}
        f, m, c = v["fill_rate"], v["avg_stock_value"], v["total_cost"]
        lines.append(f"**Against {labels[p].lower()}:** the plan met {s.loc['plan', 'fill_rate']:.1%} of demand against {s.loc[p, 'fill_rate']:.1%} ({_clear(f['clear'])}). "
                     f"It held {abs(m['point']):,.0f} USD {'more' if m['point'] > 0 else 'less'} stock on average ({_clear(m['clear'])}) and cost "
                     f"{abs(c['point']):,.0f} USD {'more' if c['point'] > 0 else 'less'} a year to order and hold ({_clear(c['clear'])}).")
        tally["fill"].append(f["clear"])
        tally["cost"].append(c["clear"])
    if all(x == "worse" for x in tally["cost"]) and any(x in {"better", "unclear"} for x in tally["fill"]):
        bottom = ("**What this says:** the plan keeps products available, but it does so by holding more stock than the simple rules. If cost or cash matters more, "
                  "a lower safety level suits better (try Relaxed or Normal and run the replay again).")
    elif all(x == "better" for x in tally["cost"]) and all(x in {"better", "unclear"} for x in tally["fill"]):
        bottom = "**What this says:** the plan met demand at least as well as the simple rules and cost less, and the ranges support it."
    else:
        bottom = "**What this says:** the result is mixed. Where it says no clear difference, the sample is too small to tell the rules apart."
    return lines, bottom


def _replay_section(config, replay_fn):
    r = ei.inv(config)["replay"]
    ui.section("What would have happened last year?", "The plan is rebuilt month by month using only what was known at the time, then run against the demand that really followed. "
                                                      "Two simpler rules run on the same demand, for comparison.")
    if not st.session_state.get(PREFIX + "replay_on"):
        st.markdown("The replay follows stock day by day for every product and country. It takes a few seconds, so it only runs when you ask.")
        st.button("Run the replay", key=PREFIX + "replay_btn", icon=":material/play_arrow:", on_click=lambda: st.session_state.__setitem__(PREFIX + "replay_on", True))
        return
    with st.spinner("Replaying the year..."):
        out = replay_fn()
    if not out["available"]:
        st.warning(out["reason"])
        return
    clab, plab = _names(config)
    s, ranges, labels = out["summary"].set_index("policy"), out["ranges"], r["policy_labels"]
    uploading = st.session_state.get(PREFIX + "mode") == "upload"
    st.caption(f"Replayed {out['months']} months for {len(out['keys'])} products, starting {out['first_month']:%b %Y}. "
               + ei.inv(config)["upload_warning" if uploading else "sample_warning"])
    lines, bottom = _verdict_lines(out, config)
    with st.container(border=True):
        for line in lines:
            st.markdown(line)
        st.markdown(bottom)

    def bars(metric, title, sub, fmt, key, pct=False):
        fig = go.Figure()
        for p in ("plan", "flat", "moving"):
            rg = ranges[(ranges["policy"] == p) & (ranges["metric"] == metric)].iloc[0]
            y = float(s.loc[p, metric])
            fig.add_trace(go.Bar(x=[r["bar_labels"][p]], y=[y], name=labels[p], marker_color=POLICY_COLOUR[p],
                                 error_y={"type": "data", "symmetric": False, "array": [max(rg["high"] - y, 0)], "arrayminus": [max(y - rg["low"], 0)], "color": "#1c1917"},
                                 hovertemplate="%{y:" + fmt + "}<extra>" + labels[p] + "</extra>"))
        fig.update_layout(showlegend=False, xaxis={"tickangle": 0, "tickfont": {"size": 12}})
        fig.update_yaxes(tickformat=fmt, rangemode="tozero")
        if pct:
            lo = min(float(ranges[(ranges["metric"] == metric)]["low"].min()), 0.95)
            fig.update_yaxes(range=[max(lo - 0.02, 0), 1.005], rangemode="normal")
        fig.update_layout(margin={"l": 8, "r": 12, "t": 28, "b": 8})
        _card(title, sub, fig, key, 330)
    cols = st.columns(3)
    with cols[0]:
        bars("fill_rate", "How much demand was met", "Higher is better. Bars show the range across products.", ".1%", PREFIX + "r_fill", True)
    with cols[1]:
        bars("avg_stock_value", "Average money tied up in stock", "Lower is better (USD).", "$,.0f", PREFIX + "r_stock")
    with cols[2]:
        bars("total_cost", "Cost of ordering and holding", "Lower is better (USD over the year).", "$,.0f", PREFIX + "r_cost")

    key = st.selectbox("Follow one product through the year", out["keys"], key=PREFIX + "r_pick",
                       format_func=lambda k: f"{clab.get(k[0], k[0])} | Product {k[1]}: {plab.get(k[1], k[1])}")
    tl = out["timeline"]
    tl = tl[(tl["country"] == key[0]) & (tl["product"] == key[1])]
    fig = go.Figure()
    for p in ("flat", "moving", "plan"):
        d = tl[tl["policy"] == p]
        fig.add_trace(go.Scatter(x=d["date"], y=d["on_hand"], name=labels[p], mode="lines", line={"color": POLICY_COLOUR[p], "width": 3 if p == "plan" else 1.8}))
        miss = d[d["short"] > 1e-6]
        if len(miss):
            fig.add_trace(go.Scatter(x=miss["date"], y=[0] * len(miss), mode="markers", showlegend=False, marker={"symbol": "x", "size": 9, "color": POLICY_COLOUR[p]},
                                     hovertemplate="%{x|%d %b %Y}: demand went unmet<extra>" + labels[p] + "</extra>"))
    fig.update_xaxes(tickformat="%b %Y", dtick="M2", range=[tl["date"].min(), tl["date"].max()])
    fig.update_yaxes(title_text="Units on the shelf", rangemode="tozero", hoverformat=",.0f")
    fig.update_layout(hovermode="x unified")
    _fit(fig, 90)
    _card(f"{clab.get(key[0], key[0])} | Product {key[1]}: {plab.get(key[1], key[1])}", "Stock on the shelf each week under each rule. An x marks a week where demand went unmet.", fig, PREFIX + "r_line", 440)

    per = out["per_series"]
    fr = per.pivot_table(index=["country", "product"], columns="policy", values="fill_rate").reset_index()
    ms = per.pivot_table(index=["country", "product"], columns="policy", values="months_short").reset_index()
    table = pd.DataFrame({"Country": fr["country"].map(lambda c: clab.get(c, c)), "Product": fr["product"].map(lambda p: f"{p}: {plab.get(p, p)}")})
    for p in ("plan", "flat", "moving"):
        table[f"Demand met: {labels[p]}"] = fr[p] * 100
    for p in ("plan", "flat", "moving"):
        table[f"Months short: {labels[p]}"] = ms[p]
    with st.expander("Product by product"):
        ui.table(table, hide_index=True, column_config={c: st.column_config.NumberColumn(format="%.1f") for c in table.columns if c not in {"Country", "Product"}})
    with st.expander("How the replay works, and what it cannot tell you"):
        for note in out["notes"]:
            st.markdown(f"- {note}")
        st.markdown(f"- The simple rules are: a flat average of the 12 months before the replay with no spare stock, and a moving average of the last {r['baseline_avg_months']} months "
                    f"plus a {r['baseline_buffer']:.0%} buffer. They use the same costs, lead times and ordering rules as the plan.")
        st.markdown("- Demand is spread evenly within each month. Orders arrive after the lead time with no delays, and unmet demand is lost, not backordered.")


def _trust_section(plan, config):
    d = plan["diagnostics"]
    with st.expander("Can I trust this?"):
        st.markdown("**How we checked.** For every past month, we pretended not to know what came next, made the forecast, and measured how far off it was. "
                    "The spare stock is sized from those misses, not from a rule of thumb.")
        for cal in d["calibration"]:
            if cal["checked"]:
                st.markdown(f"- In that test, the reorder level was enough **{cal['coverage_raw']:.0%}** of the time (aiming for {cal['alpha']:.0%})"
                            + (f". We added a safety margin to fix the shortfall (x{cal['k']:.2f})." if cal["k"] > 1 else "."))
        st.markdown("- Only about two years of history is available, so treat the exact numbers as rough.")
        st.markdown("- " + ", ".join(f"{v} product{'s' if v != 1 else ''}: {k}" for k, v in d["error_sources"].items()) + " (where the forecast-miss figures came from).")
        recon = plan["recon"]
        st.markdown(f"**Where every row of your data went.** {int(recon['rows'].sum()):,} rows in; each is counted as demand or set aside with a reason. "
                    "Orders still waiting on master-data setup are never demand.")
        ui.table(recon.rename(columns={"category": "Type", "label": "Label", "rows": "Rows", "treatment": "What happened to it"}), hide_index=True)
        st.caption(d["warning"])
        st.caption(f"Technical: run {d['run_at'][:19]} UTC, seed {d['seed']}, {d['simulations']:,} simulated paths, same seed gives the same plan.")


def _dashboard(plan, base, config, keys, run_one, what_if, replay_fn, actions_fn, tabs):
    overview, actions, replay, lab = tabs
    with overview:
        sel, ahead, past = _filters(keys, config)
        t = _in(plan["table"], set(sel)).dropna(subset=["reorder_point"]) if sel else plan["table"].iloc[0:0]
        if not sel:
            st.info("Nothing matches those choices.")
        elif t.empty:
            st.warning("Not enough history to plan these.")
        else:
            _tiles(plan, base, sel, t)
            _advice_section(plan, config, what_if)
            _outlook(plan, sel, config)
            _main_chart(plan, sel, config, ahead, past, t)
            _plan_section(plan, config)
            _side_charts(plan, sel, t, config, hidden_cost=not t["average_inventory_value"].notna().any())
            _trust_section(plan, config)
    with actions:
        actions_fn()
    with replay:
        _replay_section(config, replay_fn)
    with lab:
        st.caption("Try changes on one product and see what they do. Nothing here changes your plan.")
        _order_section(plan, keys, config, run_one)


GLOSSARY = [("Lead time", "How long a supplier order takes to arrive."),
            ("Reorder level", "When your stock falls to this number, place a new order."),
            ("Spare stock", "Extra stock kept on top of expected demand, so a surprise rush does not run you out."),
            ("Order amount", "How many units to order each time, balancing the cost of ordering against the cost of holding stock."),
            ("How careful", "Relaxed, Normal or Careful: how often you are willing to run short. Careful holds more spare stock.")]


def render(config, state, user):
    cfg = ei.inv(config)
    st.markdown("**How much stock should we keep, and when should we reorder?** Use the Setup tab to change the data or the numbers; every other tab updates. "
                "Anything you leave blank uses a standard guess, clearly marked.")
    uploading = st.session_state.get(PREFIX + "mode") == "upload"
    ui.banner("Illustrative: anything the data does not contain is a guess, marked as one." if uploading else
              "Illustrative: costs and lead times are guesses until real figures from SAP replace them.",
              cfg["upload_warning"] if uploading else cfg["sample_warning"])
    with st.expander("What do these words mean?"):
        for term, text in GLOSSARY:
            st.markdown(f"- **{term}:** {text}")

    t_over, t_act, t_rep, t_lab, t_setup = st.tabs(["Overview", "Today's actions", "Replay of the past year", "What-if lab", "Setup"])
    with t_setup:
        ui.section("Setup", "The data and the numbers the plan is built from. Change anything here and every other tab updates.")
        st.markdown("**Step 1: Which data?**")
        src = _source(config)
        if src == "wait":
            with t_over:
                st.info("Choose your data in the Setup tab to see the plan.")
            return
        if src is None:
            basis_labels = cfg["demand_basis"]["labels"]
            with st.expander("What counts as demand? (the standard choice is fine)"):
                basis = st.segmented_control("Count", list(basis_labels), default=cfg["demand_basis"]["default"], key=PREFIX + "basis",
                                             format_func=lambda b: basis_labels[b]) or cfg["demand_basis"]["default"]
                st.caption(cfg["demand_basis"]["why_requests"] if basis == "requests" else
                           "Confirmed orders only: what was actually booked. It can lag true demand when stock runs short.")
            clean0, recon0 = ei.demand_rows(_raw(), config, basis)
            did0, label0, lead_days, file_stock, file_breaks, file_flags = "sample", cfg["data_source_label"], None, pd.DataFrame(), {}, []
        else:
            basis, clean0, recon0 = src["basis"], src["clean"], src["recon"]
            did0, label0, lead_days, file_stock, file_breaks, file_flags = src["did"], src["label"], src["lead_days"], src["stock"], src["breaks"], src["flags"]
        repairs = _stockout_step(config, clean0, file_flags, did0)
        clean = ei.apply_repairs(clean0, repairs) if repairs else clean0
        recon = recon0
        if repairs:
            recon = pd.concat([recon0, pd.DataFrame([{"category": "adjusted", "label": f"Months replaced after you confirmed possible stockouts ({len(repairs)})", "rows": 0,
                                                       "treatment": "Demand, adjusted to the expected level"}])], ignore_index=True)
        pack = None if (src is None and not repairs) else {"clean": clean, "recon": recon, "label": label0 + (" (adjusted for confirmed stockouts)" if repairs else ""),
                                                           "did": did0 + (f"|r{abs(hash(tuple((c, p, str(m), round(x, 3)) for c, p, m, x in repairs))) % 10**9}" if repairs else ""),
                                                           "lead_days": lead_days}
        keys = list(ei.demand_series(clean))

        st.markdown("**Step 2: Numbers for each product and country**")
        st.caption("Each row below is one product in one country, so prices, lead times and costs can differ between them. The answers here start every row off; "
                   "then type over any cell to change just that row. \"Still a guess\" shows which numbers nobody has confirmed yet.")
        _form_basic(config, keys)
        with st.expander("More starting values (optional)"):
            _form_more(config)
        with st.expander("Quantity discounts and shipping (optional)"):
            breaks, dtype = _discounts_ui(config, keys, file_breaks, did0)
        entered, scope_over = _scope_split(keys, st.session_state.get(PREFIX + "scope_c", "All"), st.session_state.get(PREFIX + "scope_p", "All"), _entered(config))
        changes = _grids(config, keys, clean, lead_days, entered, scope_over)
        overrides = {k: {**scope_over.get(k, {}), **changes.get(k, {})} for k in set(scope_over) | set(changes)}
        compare = st.toggle("Show how this differs from the starting numbers", key=PREFIX + "compare")
        ent_json = json.dumps(entered, sort_keys=True)
        over_json = json.dumps({"|".join(k): v for k, v in overrides.items()}, sort_keys=True)

        breaks_json = json.dumps({"|".join(k): v for k, v in breaks.items()}, sort_keys=True)

        def exec_plan(e, o, only=""):
            if pack is None:
                return _plan(basis, e, o, only, breaks_json, dtype)
            return _plan_upload(basis, e, o, pack["clean"], pack["recon"], pack["label"], pack["did"], pack["lead_days"], only, breaks_json, dtype)

        def run(e, o):
            return exec_plan(e, o)
        with st.spinner("Working it out..."):
            plan = run(ent_json, over_json)
            base = run("{}", "{}") if compare else None

        def run_one(key, slider_values):
            merged = {**overrides, key: {**overrides.get(key, {}), **slider_values}}
            return exec_plan(ent_json, json.dumps({"|".join(k): v for k, v in merged.items()}, sort_keys=True), json.dumps([list(key)]))

        def what_if(per_key):
            merged = {k: {**overrides.get(k, {}), **v} for k, v in per_key.items()}
            return exec_plan(ent_json, json.dumps({"|".join(k): v for k, v in merged.items()}, sort_keys=True), json.dumps([list(k) for k in per_key]))

        def replay_fn():
            resolved = ei.baseline_assumptions(config, clean, entered, lead_days, overrides)
            payload = json.dumps({"|".join(k): a for k, a in resolved.items()})
            return _replay_cached(clean, payload, "sample" if pack is None else pack["did"], basis, breaks_json, dtype)

        def actions_fn():
            _actions_section(plan, config, file_stock, did0)
    _dashboard(plan, base, config, keys, run_one, what_if, replay_fn, actions_fn, (t_over, t_act, t_rep, t_lab))
