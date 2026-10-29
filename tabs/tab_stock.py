"""Stock planning tab (under Sales insights): the safety stock, reorder point and order quantity for each country and
product, built from the forecast and from assumptions the planner can change. Every figure is tagged with where its
assumption came from (entered, estimated, derived or default), and the plan can be downloaded."""
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import engine_ingest as eg
import engine_inventory as ei
from tabs import charts, common, ui
from tabs.tab_analytics import SERIES

PREFIX = "sp_"
# (key, label, kind, help). kind: months, pct, money, units, growth
FIELDS = [("lead_time_months", "Lead time", "lead"), ("review_period_months", "Review period (months)", "num"),
          ("service_level", "Service level (%)", "pct"), ("holding_rate", "Holding cost (% of unit cost a year)", "pct"),
          ("order_cost", "Ordering cost (USD per order)", "num"), ("unit_cost", "Unit cost (USD per unit)", "num"),
          ("salvage", "Salvage value (USD per unit)", "num"), ("pack_size", "Pack size (units)", "num"),
          ("moq", "Minimum order (units)", "num"), ("storage_cap", "Storage cap (units per order)", "num"),
          ("budget_cap", "Budget cap (USD per order)", "num"), ("growth_override_pct", "Extra growth (% a year)", "num")]
OVERRIDE_COLUMNS = [("lead_time_months", "Lead time (months)", 1.0), ("review_period_months", "Review (months)", 1.0),
                    ("service_level", "Service level (%)", 100.0), ("holding_rate", "Holding (% a year)", 100.0),
                    ("order_cost", "Ordering cost (USD)", 1.0), ("pack_size", "Pack size", 1.0), ("moq", "Min order", 1.0)]


@st.cache_resource
def _caches():
    """The expensive, assumption-independent steps (forecasts and backtests) are kept between runs."""
    return {}


@st.cache_data
def _raw():
    return common.load_orders()


@st.cache_data(show_spinner=False)
def _plan(basis, entered_json, overrides_json):
    entered = json.loads(entered_json)
    overrides = {tuple(k.split("|")): v for k, v in json.loads(overrides_json).items()}
    return ei.build_plan(_raw(), common.base_config(), basis=basis, entered=entered, overrides=overrides, caches=_caches())


@st.cache_data(show_spinner=False)
def _plan_upload(basis, entered_json, overrides_json, clean, recon, label, did, lead_days):
    entered = json.loads(entered_json)
    overrides = {tuple(k.split("|")): v for k, v in json.loads(overrides_json).items()}
    return ei.build_plan(None, common.base_config(), basis=basis, entered=entered, overrides=overrides, caches=_caches(),
                         demand=(clean, recon), data_label=label, data_id=did, lead_days=lead_days)


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
    rejected = table[table["reason"].notna()]
    if len(rejected):
        with st.expander(f"Rows not used ({len(rejected)}): row number and reason"):
            ui.table(rejected[["row", "date", "country", "product", "quantity", "record_type", "reason"]].astype(str).rename(
                columns={"row": "Row in your file"}), hide_index=True)
    return {"clean": clean, "recon": recon, "label": f"Your data: {up.name} ({len(clean):,} rows used as demand)",
            "did": eg.data_id(table, basis), "lead_days": res["lead_days"], "basis": basis}


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
    return {k: v for k, v in out.items() if v is not None}


def _reset():
    for key, _, _ in FIELDS:
        st.session_state[PREFIX + key] = None
    st.session_state[PREFIX + "lead_unit"] = "months"
    st.session_state[PREFIX + "gen"] = st.session_state.get(PREFIX + "gen", 0) + 1


def _form(config):
    cfg = ei.inv(config)
    d = cfg["defaults"]
    st.caption("Leave a box empty to use the estimate from the data or the default; the Assumptions table below says which was used.")
    c = st.columns(4)
    with c[0]:
        a, b = st.columns([3, 2])
        a.number_input("Lead time", min_value=0.0, value=None, key=PREFIX + "lead_time_months", placeholder=f"default {d['lead_time_months']['value']:g}",
                       help="How long a supplier order takes to arrive. Not in the data: confirm it.")
        b.selectbox("Unit", ["months", "days"], key=PREFIX + "lead_unit")
        lt = st.session_state.get(PREFIX + "lead_time_months")
        if lt is not None and st.session_state.get(PREFIX + "lead_unit") == "days":
            st.caption(f"{lt:g} days = {ei.months_from_days(lt, config):.2f} months (30.4375 days a month)")
    c[1].number_input("Review period (months)", min_value=0.0, value=None, key=PREFIX + "review_period_months",
                      placeholder=f"default {d['review_period_months']['value']:g}", help="How often stock is checked. 0 means continuously.")
    c[2].number_input("Service level (%)", min_value=50.0, max_value=99.9, value=None, key=PREFIX + "service_level", placeholder="default 95",
                      help="Share of order cycles with no stockout. Left empty, it is derived from price, cost and salvage if a salvage value is entered.")
    c[3].number_input("Holding cost (% of unit cost a year)", min_value=0.0, value=None, key=PREFIX + "holding_rate", placeholder="default 20",
                      help="Cost of capital plus storage, insurance and obsolescence.")
    c = st.columns(4)
    c[0].number_input("Ordering cost (USD per order)", min_value=0.0, value=None, key=PREFIX + "order_cost", placeholder=f"placeholder {d['order_cost']['value']:g}",
                      help="Admin, freight and handling for one supplier order. A placeholder: must confirm.")
    c[1].number_input("Unit cost (USD per unit)", min_value=0.0, value=None, key=PREFIX + "unit_cost", placeholder="estimated from the data",
                      help="Left empty: median selling price less the assumed margin of the product line.")
    c[2].number_input("Salvage value (USD per unit)", min_value=0.0, value=None, key=PREFIX + "salvage", placeholder="none",
                      help="What a leftover unit sells for. With it, the service level is derived as Cu / (Cu + Co).")
    c[3].number_input("Extra growth (% a year)", value=None, key=PREFIX + "growth_override_pct", placeholder="0",
                      help="What-if only: added on top of the forecast. The forecast itself is not changed.")
    c = st.columns(4)
    c[0].number_input("Pack size (units)", min_value=0.0, value=None, key=PREFIX + "pack_size", placeholder="none", help="Orders must be a multiple of this.")
    c[1].number_input("Minimum order (units)", min_value=0.0, value=None, key=PREFIX + "moq", placeholder="none", help="Smallest order the supplier accepts.")
    c[2].number_input("Storage cap (units per order)", min_value=0.0, value=None, key=PREFIX + "storage_cap", placeholder="none")
    c[3].number_input("Budget cap (USD per order)", min_value=0.0, value=None, key=PREFIX + "budget_cap", placeholder="none")
    st.button("Reset all to the estimates and defaults", key=PREFIX + "reset", on_click=_reset, icon=":material/restart_alt:")


def _overrides_editor(config, keys):
    """Values for one country and product only. Blank cells use the global value. Returns {(country, product): {name: value}}."""
    lines, labels = config["analytics"]["product_lines"], config["analytics"]["regional_view"]["country_labels"]
    frame = pd.DataFrame({"Country": [labels.get(c, c) for c, _ in keys], "Product": [f"{p}: {lines.get(p, p)}" for _, p in keys],
                          **{label: pd.Series([None] * len(keys), dtype="float64") for _, label, _ in OVERRIDE_COLUMNS}})
    edited = st.data_editor(frame, hide_index=True, disabled=["Country", "Product"], key=f"{PREFIX}overrides_{st.session_state.get(PREFIX + 'gen', 0)}",
                            column_config={label: st.column_config.NumberColumn(label, min_value=0.0) for _, label, _ in OVERRIDE_COLUMNS})
    out = {}
    for i, key in enumerate(keys):
        vals = {name: float(edited.iloc[i][label]) / scale for name, label, scale in OVERRIDE_COLUMNS if pd.notna(edited.iloc[i][label])}
        if vals:
            out[key] = vals
    return out


def _pretty(plan, config):
    lines, labels = config["analytics"]["product_lines"], config["analytics"]["regional_view"]["country_labels"]
    t = plan["table"].copy()
    t["country"] = t["country"].map(lambda c: labels.get(c, c))
    t["product"] = t["product"].map(lambda p: f"{p}: {lines.get(p, p)}")
    t["stockout_risk_target"] = t["stockout_risk_target"] * 100
    return t.rename(columns={
        "country": "Country", "product": "Product", "forecast_next_12m": "Forecast, next 12 months (units)",
        "protection_months": "Protection interval (months)", "lead_time_demand": "Expected demand over it (units)",
        "sigma_over_interval": "Spread of that demand (units)", "safety_stock": "Safety stock (units)",
        "reorder_point": "Reorder point (units)", "order_quantity": "Order quantity (units)", "orders_per_year": "Orders per year",
        "average_inventory_units": "Average inventory (units)", "average_inventory_value": "Average inventory (USD)",
        "cash_per_order": "Cash per order (USD)", "turnover": "Inventory turns a year", "yearly_cost": "Ordering plus holding cost (USD a year)",
        "service_level": "Service level", "stockout_risk_target": "Stockout risk target (%)", "error_source": "Error measured from",
        "backtest_windows": "Own backtest windows", "months_of_history": "Months of history", "zero_months": "Zero months",
        "flags": "Flags", "notes": "Notes"})


def _money(v):
    return "n/a" if v is None or pd.isna(v) else f"{v:,.0f}"


def _metrics(plan, base):
    t = plan["table"]
    cols = st.columns(4)
    specs = [("Safety stock, all series (units)", "safety_stock", "{:,.0f}"), ("Average inventory (USD)", "average_inventory_value", "${:,.0f}"),
             ("Ordering plus holding cost (USD a year)", "yearly_cost", "${:,.0f}")]
    for col, (label, field, fmt) in zip(cols, specs):
        if not t[field].notna().any():
            col.metric(label, "n/a", help="No unit cost for these series, so costs are not calculated. Enter a unit cost above or include one in your file.")
            continue
        now = float(t[field].sum(skipna=True))
        delta = None
        if base is not None:
            diff = now - float(base["table"][field].sum(skipna=True))
            delta = f"{diff:+,.0f} against the defaults"
        col.metric(label, fmt.format(now), delta, delta_color="off")
    flagged = int((t["flags"].fillna("") != "").sum())
    cols[3].metric("Series with a flag", f"{flagged} of {len(t)}", help="A flag means an input is a default or placeholder, an error estimate was borrowed, or the history is short.")


def _downloads(plan, config):
    pretty = _pretty(plan, config)
    monthly = plan["monthly"]
    payload = {"data_source": plan["diagnostics"]["data_source"], "warning": plan["diagnostics"]["warning"],
               "run": {k: v for k, v in plan["diagnostics"].items() if k not in {"warning"}},
               "reconciliation": plan["recon"].to_dict("records"), "assumptions_log": plan["assumptions"].to_dict("records"),
               "plan": plan["table"].to_dict("records"), "plan_by_month": monthly.to_dict("records")}
    c = st.columns(4)
    c[0].download_button("Download the stock plan (CSV)", pretty.to_csv(index=False).encode("utf-8"), file_name="stock_plan.csv",
                         mime="text/csv", key=PREFIX + "dl_plan", icon=":material/download:")
    c[1].download_button("Download the forecast (CSV)", plan["forecast"].to_csv(index=False).encode("utf-8"), file_name="demand_forecast.csv",
                         mime="text/csv", key=PREFIX + "dl_fc", icon=":material/download:")
    c[2].download_button("Download the plan by month (CSV)", monthly.to_csv(index=False).encode("utf-8"), file_name="stock_plan_by_month.csv",
                         mime="text/csv", key=PREFIX + "dl_month", icon=":material/download:")
    c[3].download_button("Download everything with assumptions (JSON)", json.dumps(payload, indent=2, default=str).encode("utf-8"),
                         file_name="stock_plan_full.json", mime="application/json", key=PREFIX + "dl_json", icon=":material/download:")


def _drilldown(plan, config, keys):
    lines, labels = config["analytics"]["product_lines"], config["analytics"]["regional_view"]["country_labels"]
    key = st.selectbox("Look at one country and product", keys, key=PREFIX + "pick",
                       format_func=lambda k: f"{labels.get(k[0], k[0])} | Product {k[1]}: {lines.get(k[1], k[1])}")
    m = plan["monthly"]
    m = m[(m["country"] == key[0]) & (m["product"] == key[1])]
    fc = plan["forecast"]
    fc = fc[(fc["country"] == key[0]) & (fc["product"] == key[1])]
    if m.empty:
        st.warning("Not enough history to plan this series.")
        return
    hist = plan["history"][key]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=list(m["period"]) + list(m["period"])[::-1], y=list(m["interval_p95"]) + list(m["interval_p05"])[::-1],
                             fill="toself", fillcolor="rgba(122,92,192,.14)", line={"width": 0}, hoverinfo="skip",
                             name="Demand over the protection interval, 5% to 95%"))
    fig.add_trace(go.Scatter(x=hist.index, y=hist.values, name="Actual demand per month", mode="lines+markers",
                             line={"color": SERIES["actual"], "width": 2}, marker={"size": 7}))
    fig.add_trace(go.Scatter(x=fc["period"], y=fc["forecast"], name="Forecast per month", mode="lines+markers",
                             line={"color": SERIES["forecast"], "width": 3, "dash": "dash"}, marker={"size": 7}))
    fig.add_trace(go.Scatter(x=m["period"], y=m["interval_demand"], name="Expected demand over the protection interval", mode="lines",
                             line={"color": "#78716c", "width": 1.5, "dash": "dot"}))
    fig.add_trace(go.Scatter(x=m["period"], y=m["reorder_point"], name="Reorder point (stock level that triggers an order)", mode="lines",
                             line={"color": "#1c1917", "width": 3, "shape": "hv"}))
    fig.update_layout(hovermode="x unified", yaxis_title="Units", xaxis_title=None, legend={"y": -0.3})
    fig.update_yaxes(rangemode="tozero")
    with st.container(border=True):
        charts.chart_title(f"{labels.get(key[0], key[0])} | Product {key[1]}: {lines.get(key[1], key[1])}",
                           "Reorder point and the demand it has to cover, month by month")
        charts.show(fig, PREFIX + "chart", 400)
    row = plan["table"][(plan["table"]["country"] == key[0]) & (plan["table"]["product"] == key[1])].iloc[0]
    if row["flags"]:
        st.caption("Flags: " + row["flags"])
    if row["notes"]:
        st.caption(row["notes"])
    log = plan["assumptions"]
    log = log[(log["country"] == key[0]) & (log["product"] == key[1])]
    shown = pd.DataFrame({"Assumption": log["assumption"].map(ei.LABELS), "Value": log["value"], "Unit": log["unit"],
                          "Source": log["source"], "Why": log["note"]})
    with st.expander("Assumptions behind this series, each tagged entered, estimated, derived or default"):
        ui.table(shown, hide_index=True)


def render(config, state, user):
    cfg = ei.inv(config)
    ui.note("From forecast to stock: how much to hold, when to reorder and how much to order, for each country and product.",
            "The forecast is read from the Forecast explorer and never changed here. Safety stock comes from how wrong the forecast "
            "has been in a time-ordered backtest, not from a textbook formula. Change any assumption below and the plan re-runs.")
    uploading = st.session_state.get(PREFIX + "mode") == "upload"
    ui.banner("Illustrative: anything the data does not contain is an assumption, tagged as such." if uploading else
              "Illustrative: costs, lead times and service levels are assumptions until SAP data replaces them.",
              cfg["upload_warning"] if uploading else cfg["sample_warning"])
    src = _source(config)
    if src == "wait":
        return
    if src is None:
        basis_labels = cfg["demand_basis"]["labels"]
        basis = st.segmented_control("Demand to plan for", list(basis_labels), default=cfg["demand_basis"]["default"], key=PREFIX + "basis",
                                     format_func=lambda b: basis_labels[b]) or cfg["demand_basis"]["default"]
        st.caption(cfg["demand_basis"]["why_requests"] if basis == "requests" else
                   "Confirmed orders only: what actually shipped or was booked. It can lag true demand when stock runs short.")
        clean, _ = ei.demand_rows(_raw(), config, basis)
    else:
        basis, clean = src["basis"], src["clean"]
    keys = list(ei.demand_series(clean))
    with st.expander("Assumptions: change any value and the plan updates", expanded=True):
        _form(config)
    with st.expander("Different values for one country and product (empty cells use the values above)"):
        overrides = _overrides_editor(config, keys)
    entered = _entered(config)
    compare = st.toggle("Show the change against the default assumptions", key=PREFIX + "compare")
    ent_json = json.dumps(entered, sort_keys=True)
    over_json = json.dumps({"|".join(k): v for k, v in overrides.items()}, sort_keys=True)

    def run(e, o):
        if src is None:
            return _plan(basis, e, o)
        return _plan_upload(basis, e, o, src["clean"], src["recon"], src["label"], src["did"], src["lead_days"])
    with st.spinner("Planning..."):
        plan = run(ent_json, over_json)
        base = run("{}", "{}") if compare else None
    st.caption(f"Data: {plan['diagnostics']['data_source']}. Run {plan['diagnostics']['run_at'][:19]} UTC, seed {plan['diagnostics']['seed']}, "
               f"{plan['diagnostics']['simulations']:,} simulated paths.")
    _metrics(plan, base)
    _downloads(plan, config)
    ui.section("The plan", "One row per country and product.",
               tip="Safety stock = reorder point minus the expected demand over the protection interval (lead time plus review period). "
                   "Order quantity is the economic order quantity after pack size, minimum order and caps.")
    pretty = _pretty(plan, config)
    ui.table(pretty.drop(columns=["Notes"]), hide_index=True, column_config={
        c: st.column_config.NumberColumn(format="%.1f") for c in pretty.columns if c not in {"Country", "Product", "Error measured from", "Flags", "Notes"}})
    ui.section("What a higher or lower service level costs", "Safety stock and its holding cost at 90%, 95% and 99%, all series together.")
    tr = plan["tradeoff"].groupby("service_level", as_index=False)[["safety_stock", "reorder_point", "holding_cost_of_safety_stock"]].sum(min_count=1)
    tr["service_level"] = (tr["service_level"] * 100).round(1).astype(str) + "%"
    ui.table(tr.rename(columns={"service_level": "Service level", "safety_stock": "Safety stock (units)", "reorder_point": "Reorder point (units)",
                                "holding_cost_of_safety_stock": "Yearly holding cost of the safety stock (USD)"}),
             hide_index=True, column_config={"Safety stock (units)": st.column_config.NumberColumn(format="%.0f"),
                                             "Reorder point (units)": st.column_config.NumberColumn(format="%.0f"),
                                             "Yearly holding cost of the safety stock (USD)": st.column_config.NumberColumn(format="%.0f")})
    ui.section("One series up close")
    _drilldown(plan, config, keys)
    d = plan["diagnostics"]
    with st.expander("How reliable is this? Backtest, coverage and data accounting"):
        st.markdown("**Error model.** Every error comes from a time-ordered backtest: the model is refitted at each month using only earlier months "
                    "and its forecast over the protection interval is compared with what happened. No in-sample residuals are used anywhere.")
        for cal in d["calibration"]:
            if cal["checked"]:
                st.markdown(f"- Target service level {cal['alpha']:.0%}: the reorder point covered demand in **{cal['coverage_raw']:.0%}** of "
                            f"{cal['checked']} backtest checks" + (f", widened by a factor of {cal['k']:.2f} to {cal['coverage_calibrated']:.0%}." if cal["k"] > 1 else "."))
        st.markdown("- Error measured from: " + ", ".join(f"{k} ({v})" for k, v in d["error_sources"].items()) + ".")
        st.markdown("- Backtest windows overlap, so coverage figures are rough on 24 months of data.")
        recon = plan["recon"]
        st.markdown(f"**Where every input row went.** {int(recon['rows'].sum()):,} rows in = demand + excluded. Orders still waiting on master-data setup are never demand.")
        ui.table(recon.rename(columns={"category": "Category", "label": "Label", "rows": "Rows", "treatment": "Treatment"}), hide_index=True)
        st.caption(d["warning"])
