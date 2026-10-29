"""Inventory planning on top of the demand forecast: for every country x product it gives a safety stock, a reorder point
and an order quantity, from assumptions the user can change.

It never alters a forecast: it reads them through forecast_adapter(), which only calls engine_analytics. Every default,
threshold and the random seed come from config analytics.inventory. All time is held in months (days are converted with
days_per_month and the conversion is shown). Nothing is dropped silently: demand_rows() returns a reconciliation in which
every input row is either demand or excluded with a reason, and every assumption carries a source tag
(entered, estimated, derived or default)."""
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

import engine_analytics as ea

SOURCES = ("entered", "estimated", "derived", "default")
ASSUMPTIONS = ["lead_time_months", "review_period_months", "service_level", "holding_rate", "order_cost", "unit_cost",
               "price", "salvage", "pack_size", "moq", "storage_cap", "budget_cap", "growth_override_pct", "demand_change_pct",
               "freight_per_unit", "freight_per_order", "free_shipping_above"]
LABELS = {"lead_time_months": "Lead time", "review_period_months": "Review period", "service_level": "Service level",
          "holding_rate": "Holding rate", "order_cost": "Ordering cost", "unit_cost": "Unit cost", "price": "Selling price",
          "salvage": "Salvage value", "pack_size": "Pack size", "moq": "Minimum order quantity",
          "storage_cap": "Storage cap", "budget_cap": "Budget cap", "growth_override_pct": "Extra growth",
          "demand_change_pct": "Demand change vs forecast", "freight_per_unit": "Shipping per unit",
          "freight_per_order": "Shipping per order", "free_shipping_above": "Free shipping above"}
UNITS = {"lead_time_months": "months", "review_period_months": "months", "service_level": "share", "holding_rate": "per year",
         "order_cost": "USD per order", "unit_cost": "USD per unit", "price": "USD per unit", "salvage": "USD per unit",
         "pack_size": "units", "moq": "units", "storage_cap": "units", "budget_cap": "USD per order",
         "growth_override_pct": "% per year", "demand_change_pct": "% vs forecast", "freight_per_unit": "USD per unit",
         "freight_per_order": "USD per order", "free_shipping_above": "USD per order"}


def inv(config):
    return config["analytics"]["inventory"]


# ============================================================================ stage 1: which rows are demand
def demand_rows(raw_df, config, basis=None):
    """(demand rows with YearMonth, reconciliation). The basis picks which record categories count as demand. Confirmed
    orders still waiting on master-data setup are reported on their own and never counted as demand. The reconciliation
    always balances: rows in = rows used as demand + rows excluded (setup) + rows excluded (not demand)."""
    cfg = inv(config)
    basis = basis or cfg["demand_basis"]["default"]
    include = cfg["demand_basis"]["sets"][basis]
    clean = ea.clean_transaction_data(raw_df, config, include=include)
    if raw_df is None or raw_df.empty:
        return clean, pd.DataFrame(columns=["category", "label", "rows", "treatment"])
    cats = ea.order_categories(raw_df, config)
    labels = config["analytics"]["regional_view"]["request_type_labels"]
    setup = cfg["demand_basis"]["setup_category"]
    rows = []
    for cat, n in cats.value_counts().items():
        treatment = ("Demand" if cat in include else
                     "Excluded: waiting on master-data setup, not demand" if cat == setup else "Excluded: not demand")
        rows.append({"category": cat, "label": labels.get(cat, cat), "rows": int(n), "treatment": treatment})
    recon = pd.DataFrame(rows, columns=["category", "label", "rows", "treatment"])
    assert int(recon["rows"].sum()) == len(raw_df) and int(recon.loc[recon["treatment"] == "Demand", "rows"].sum()) == len(clean)
    return clean, recon


def demand_series(clean_df):
    """{(country, product): monthly quantity, entities summed}. Months with no rows inside a series' span count as zero
    (they are reported by plan_table as zero_months, not hidden)."""
    if clean_df is None or clean_df.empty:
        return {}
    last, out = clean_df["YearMonth"].max(), {}
    for key, g in clean_df.groupby(["U_Country", "U_ProductLine"]):
        s = g.groupby("YearMonth")["Quantity"].sum().astype(float)
        out[key] = s.reindex(pd.date_range(s.index.min(), last, freq="MS"), fill_value=0.0)
    return dict(sorted(out.items()))


# ============================================================================ stage 3: the forecast, read not changed
def forecast_adapter(series, clean_df, config, horizon, model=None, lookback=None, upto=None):
    """{(country, product): DataFrame[period, actual, fitted, forecast, method_used]} from the existing engine, with the
    same seasonal pattern, growth rate and wave count that engine_analytics derives from `clean_df` (up to `upto` when a
    backtest origin is being replayed)."""
    fc = config["analytics"]["forecast"]
    model, lookback = model or fc["default_model"], lookback or fc["lookback_months"]
    season = ea.seasonal_index(clean_df, config, upto=upto) if model in ea.NEEDS_SEASON else None
    growth = ea.pooled_growth(clean_df, config, upto=upto) if model in ea.NEEDS_GROWTH else None
    waves = ea.choose_waves(clean_df, config, upto=upto) if model in ea.NEEDS_GROWTH else None
    return {k: ea.forecast_series(s, config, horizon=horizon, model=model, lookback=lookback, season=season,
                                  growth=growth, waves=waves) for k, s in series.items()}


def interval_weights(length, horizon):
    """Weight of each of the next `horizon` months in an interval of `length` months: 1 for whole months, the fraction for
    the last part month, 0 after."""
    w = np.zeros(horizon)
    whole = min(int(math.floor(length + 1e-9)), horizon)
    w[:whole] = 1.0
    if whole < horizon:
        w[whole] = max(length - whole, 0.0)
    return w


def interval_sum(path, start, length):
    path = np.asarray(path, dtype=float)
    need = int(math.ceil(length - 1e-9))
    seg = path[start:start + max(need, 1)]
    return float((interval_weights(length, len(seg)) * seg).sum())


# ============================================================================ stage 4: error measured out of sample
def backtest_errors(series, clean_df, config, length, model=None, lookback=None):
    """Rolling-origin, expanding-window backtest, time-ordered with nothing learned from months after each origin. At every
    origin the engine is refitted on the months up to it, forecasts the next ceil(length) months, and the demand over the
    protection interval (length months, part months prorated) is compared with what happened: ratio = actual / forecast.
    The ratio of the interval total, not of one month, is what lead-time demand needs. Returns a DataFrame with one row per
    series and origin: country, product, origin, actual, forecast, ratio (NaN when the forecast is zero)."""
    bt, fc = inv(config)["backtest"], config["analytics"]["forecast"]
    model, lookback = model or fc["default_model"], lookback or fc["lookback_months"]
    cols = ["country", "product", "origin", "actual", "forecast", "ratio"]
    if not series:
        return pd.DataFrame(columns=cols)
    horizon = max(1, int(math.ceil(length - 1e-9)))
    w = interval_weights(length, horizon)
    first = min(s.index[0] for s in series.values())
    last = max(s.index[-1] for s in series.values())
    rows = []
    for origin in pd.date_range(first + pd.DateOffset(months=bt["min_train_months"] - 1), last - pd.DateOffset(months=horizon), freq="MS"):
        trains = {k: s.loc[:origin] for k, s in series.items() if len(s.loc[:origin]) >= bt["min_train_months"]
                  and len(s.loc[origin + pd.DateOffset(months=1):origin + pd.DateOffset(months=horizon)]) == horizon}
        if not trains:
            continue
        fcs = forecast_adapter(trains, clean_df, config, horizon, model, lookback, upto=origin)
        for k, f in fcs.items():
            path = f["forecast"].dropna().to_numpy()
            if len(path) < horizon:
                continue
            actual = float((w * series[k].loc[origin + pd.DateOffset(months=1):origin + pd.DateOffset(months=horizon)].to_numpy()).sum())
            fsum = float((w * path[:horizon]).sum())
            ratio = max(actual / fsum, bt["min_ratio"]) if fsum > 0 else float("nan")
            rows.append({"country": k[0], "product": k[1], "origin": origin, "actual": actual, "forecast": fsum, "ratio": ratio})
    return pd.DataFrame(rows, columns=cols)


def _bandwidth(logr, cfg):
    n = len(logr)
    sd = float(np.std(logr, ddof=1)) if n > 1 else 0.0
    h = 1.06 * sd * n ** -0.2
    return h if h > 0 else cfg["backtest"]["min_bandwidth"]


def _smoothed_log_draws(logr, n, rng, cfg):
    """Smoothed bootstrap in log space: resample the observed errors and add a little kernel noise, so a quantile is not
    simply the largest of a handful of windows."""
    return rng.choice(logr, n) + rng.normal(0.0, _bandwidth(logr, cfg), n)


def calibrate(errors, config, alpha, length):
    """Time-ordered coverage check. For each origin, the service-level quantile of the ratios from windows already finished
    by then is compared with the ratio that then happened. If coverage falls more than coverage_tolerance short of alpha,
    the error spread is inflated by the smallest factor k (up to max_inflation) that restores it. Returns a dict."""
    cfg = inv(config)
    bt, seed = cfg["backtest"], cfg["seed"]
    horizon = max(1, int(math.ceil(length - 1e-9)))
    out = {"alpha": alpha, "k": 1.0, "checked": 0, "coverage_raw": None, "coverage_calibrated": None, "reached": None}
    d = errors.dropna(subset=["ratio"]).copy()
    if d.empty:
        return out
    d["lr"] = np.log(d["ratio"])
    rows = []
    for i, origin in enumerate(sorted(d["origin"].unique())):
        known = d[d["origin"] + pd.DateOffset(months=horizon) <= origin]
        if len(known) < bt["min_prior_windows"]:
            continue
        pool = known["lr"].to_numpy()
        rng = np.random.default_rng([seed, i])
        q = float(np.quantile(_smoothed_log_draws(pool, bt["bootstrap_draws"], rng, cfg), alpha))
        med = float(np.median(pool))
        rows += [(lr, med, q) for lr in d.loc[d["origin"] == origin, "lr"]]
    if not rows:
        return out
    a = np.array(rows)

    def cover(k):
        return float(np.mean(a[:, 0] <= a[:, 1] + k * (a[:, 2] - a[:, 1])))
    k = 1.0
    target = alpha - bt["coverage_tolerance"]
    while cover(k) < target and k < bt["max_inflation"] - 1e-9:
        k = round(k + bt["inflation_step"], 4)
    out.update({"k": k, "checked": len(a), "coverage_raw": cover(1.0), "coverage_calibrated": cover(k), "reached": cover(k) >= target})
    return out


def error_source(errors, config, key):
    """The log-ratios used for one series and how they were found: its own windows when it has at least min_windows,
    otherwise borrowed from series of the same product, otherwise from all series, widened by borrow_widen and flagged.
    With too little error history anywhere, source is 'normal fallback' and logr is None."""
    bt = inv(config)["backtest"]
    d = errors.dropna(subset=["ratio"])
    own = np.log(d.loc[(d["country"] == key[0]) & (d["product"] == key[1]), "ratio"].to_numpy())
    if len(own) >= bt["min_windows"]:
        return {"logr": own, "source": "own windows", "windows": len(own), "widen": 1.0}
    same = np.log(d.loc[d["product"] == key[1], "ratio"].to_numpy())
    if len(same) >= bt["min_windows"]:
        return {"logr": same, "source": "borrowed from the same product", "windows": len(own), "widen": bt["borrow_widen"]}
    pooled = np.log(d["ratio"].to_numpy())
    if len(pooled) >= bt["min_windows"]:
        return {"logr": pooled, "source": "borrowed from all series", "windows": len(own), "widen": bt["borrow_widen"]}
    return {"logr": None, "source": "normal fallback", "windows": len(own), "widen": 1.0}


def ratio_draws(info, config, n, rng, k=1.0):
    """n draws of actual/forecast over the protection interval. Simulated in log space and exponentiated, so the result is
    right-skewed and never negative. The fallback, used only when there is too little error history anywhere, is a normal
    approximation with a default coefficient of variation, and is labelled as such."""
    if info["logr"] is None:
        return np.clip(rng.normal(1.0, inv(config)["backtest"]["fallback_cv"] * info["widen"], n), 0.0, None)
    logr = info["logr"]
    draws = _smoothed_log_draws(logr, n, rng, inv(config))
    med = float(np.median(logr))
    return np.exp(med + k * info["widen"] * (draws - med))


# ============================================================================ assumptions
FILE_COLUMNS = ("unit_cost", "price", "salvage", "order_cost", "pack_size", "moq", "freight_per_unit", "freight_per_order", "free_shipping_above")


def _estimates(clean_df, config, lead_days=None):
    """What the data can support, per series. Price: the median value per unit of the demand rows. Unit cost: that price less
    the assumed margin of the product line (config analytics.margin, itself an assumption). When an uploaded file carries
    its own unit_cost, price, salvage, order_cost, pack_size or moq column, the median of those values is used instead, and
    lead_days (receipt date less order date, per country and product) gives the lead time."""
    out = {}
    if clean_df is None or clean_df.empty:
        return out
    margin = config["analytics"]["margin"]["by_product"]
    dpm = inv(config)["days_per_month"]
    d = clean_df[clean_df["Quantity"] > 0]
    days = None if lead_days is None or lead_days.empty else lead_days.dropna(subset=["days"])
    for key, g in d.groupby(["U_Country", "U_ProductLine"]):
        est = {}
        if "LineTotal_USD" in g.columns and g["LineTotal_USD"].notna().any():
            price = float((g["LineTotal_USD"] / g["Quantity"]).median())
            est["price"] = (price, f"median of {len(g)} rows: {price:,.2f} USD per unit")
            if key[1] in margin:
                est["unit_cost"] = (price * (1 - margin[key[1]]), f"selling price less an assumed {margin[key[1]]:.0%} margin (assumption in the settings)")
        for name in FILE_COLUMNS:
            if name in g.columns and g[name].notna().any():
                vals = g[name].dropna()
                est[name] = (float(vals.median()), f"median of {len(vals)} values in your file")
        if days is not None:
            own = days.loc[(days["country"] == key[0]) & (days["product"] == key[1]), "days"]
            if len(own):
                med, lo, hi = float(own.median()), float(own.quantile(0.1)), float(own.quantile(0.9))
                est["lead_time_months"] = (med / dpm, f"median of {len(own)} supplier orders in your file: {med:.0f} days ({med / dpm:.2f} months). "
                                                       f"10th to 90th percentile {lo:.0f} to {hi:.0f} days; the spread is not used yet")
        if est:
            out[key] = est
    return out


def resolve_assumptions(config, key, entered=None, overrides=None, estimates=None):
    """Every assumption for one series as {name: {value, unit, source, note}}. Most specific wins: the series' own override,
    then the global entry, then an estimate the data supports, then the default. Blank (None) entries fall through. The
    service level is derived from price, cost and salvage (Cu / (Cu + Co)) when salvage is entered and no service level is."""
    cfg = inv(config)
    entered, overrides, estimates = entered or {}, overrides or {}, estimates or {}
    out = {}
    for name in ASSUMPTIONS:
        d = cfg["defaults"].get(name, {})
        value, source, note = None, "default", d.get("note", "")
        est = estimates.get(name)
        if overrides.get(name) is not None:
            value, source, note = overrides[name], "entered", "Entered for this country and product."
        elif entered.get(name) is not None:
            value, source, note = entered[name], "entered", "Entered for all series."
        elif est is not None:
            value, source, note = est[0], "estimated", "Estimated from the data: " + est[1]
        elif d.get("value") is not None:
            value = d["value"]
        out[name] = {"value": value, "unit": UNITS[name], "source": source, "note": note}
    if out["service_level"]["source"] == "default" and out["salvage"]["source"] == "entered" \
            and out["price"]["value"] is not None and out["unit_cost"]["value"] is not None:
        cu = out["price"]["value"] - out["unit_cost"]["value"]
        co = out["unit_cost"]["value"] - out["salvage"]["value"]
        if cu > 0 and co > 0:
            lo, hi = cfg["policy"]["service_level_bounds"]
            out["service_level"] = {"value": min(max(cu / (cu + co), lo), hi), "unit": "share", "source": "derived",
                                    "note": f"Cu / (Cu + Co) with underage {cu:,.2f} and overage {co:,.2f} USD per unit."}
    return out


def baseline_assumptions(config, clean_df, entered=None, lead_days=None, overrides=None):
    """The assumptions each country x product row starts with before any row-specific change: {(country, product): {name:
    {value, unit, source, note}}}. The screen shows these in an editable grid, so every row has its own visible values."""
    est = _estimates(clean_df, config, lead_days)
    return {k: resolve_assumptions(config, k, entered, (overrides or {}).get(k), est.get(k)) for k in demand_series(clean_df)}


def months_from_days(days, config):
    """Days to months, shown to the user as 'N days = M months'."""
    return days / inv(config)["days_per_month"]


# ============================================================================ stage 6: the policy
def eoq_orders(path, K, H, config):
    """Economic order quantity with annual demand D = 12 x the average forecast over the next order cycle, iterated until
    the cycle length is consistent (at most eoq_iterations rounds). Returns (Q, D, cycle_months). Q is not rounded and the
    number of orders per year is D / Q, also unrounded."""
    pol = inv(config)["policy"]
    path = np.asarray(path, dtype=float)
    avail = len(path)
    cycle = float(min(12, avail))
    q = d = 0.0
    for _ in range(pol["eoq_iterations"]):
        d = 12.0 * interval_sum(path, 0, cycle) / cycle
        if d <= 0 or K <= 0 or H <= 0:
            return 0.0, max(d, 0.0), float("inf")
        q_new = math.sqrt(2.0 * d * K / H)
        cycle = min(max(q_new / d * 12.0, 1e-3), float(avail))
        if q and abs(q_new - q) / q < pol["eoq_tolerance"]:
            q = q_new
            break
        q = q_new
    return q, d, q / d * 12.0


def constrain_quantity(q, D, K, H, pack=None, moq=None, storage_cap=None, budget_cap=None, unit_cost=None):
    """Order quantity after pack size, minimum order and caps. Pack size: both neighbouring multiples are compared on yearly
    ordering plus holding cost and the cheaper is taken. MOQ: if the result is below it, the smallest allowed quantity
    at or above the MOQ is used. Caps are applied last and reported as binding. Returns (Q, notes)."""
    notes = []

    def cost(x):
        return K * D / x + H * x / 2.0 if x > 0 else float("inf")
    if q <= 0:
        return 0.0, notes
    if pack and pack > 0:
        lo = math.floor(q / pack) * pack
        cands = [c for c in (lo, lo + pack) if c > 0]
        q2 = min(cands, key=cost)
        if abs(q2 - q) > 1e-9:
            notes.append(f"Pack size {pack:g}: rounded {q:,.1f} to {q2:,.0f}.")
        q = q2
    if moq and q < moq:
        q2 = math.ceil(moq / pack) * pack if pack and pack > 0 else moq
        notes.append(f"Minimum order {moq:g}: raised from {q:,.1f} to {q2:,.0f}.")
        q = q2
    if storage_cap is not None and q > storage_cap:
        q2 = math.floor(storage_cap / pack) * pack if pack and pack > 0 else storage_cap
        notes.append(f"Storage cap binds: {q:,.0f} cut to {q2:,.0f} units.")
        q = q2
    if budget_cap is not None and unit_cost and q * unit_cost > budget_cap:
        q2 = budget_cap / unit_cost
        q2 = math.floor(q2 / pack) * pack if pack and pack > 0 else q2
        notes.append(f"Budget cap binds: {q:,.0f} cut to {q2:,.0f} units.")
        q = q2
    return q, notes


def plan_series(key, path, info, a, config, rng, history=None, k=1.0, periods=None, breaks=None, dtype=None):
    """The plan for one country x product. `path` is the monthly forecast (planning horizon plus the protection interval),
    `info` the error source, `a` the resolved assumptions. Returns (row dict, monthly DataFrame, trade-off rows)."""
    cfg = inv(config)
    pol, n = cfg["policy"], cfg["simulations"]
    val = {name: a[name]["value"] for name in ASSUMPTIONS}
    L, R = float(val["lead_time_months"]), float(val["review_period_months"])
    P = L + R
    horizon = cfg["horizon_months"]
    need = int(math.ceil(P - 1e-9))
    path = np.asarray(path, dtype=float)
    growth = float(val["growth_override_pct"] or 0.0) / 100.0
    path = path * (1.0 + float(val["demand_change_pct"] or 0.0) / 100.0)                 # what-if: more or less demand than forecast
    path = path * (1.0 + growth) ** (np.arange(1, len(path) + 1) / 12.0)
    starts = max(min(horizon, len(path) - need + 1), 1)
    f_int = np.array([interval_sum(path, s, P) for s in range(starts)])
    alpha = float(val["service_level"])
    draws = ratio_draws(info, config, n, rng, k)
    q_alpha, mean_r = float(np.quantile(draws, alpha)), float(draws.mean())
    rop_m, mean_m = f_int * q_alpha, f_int * mean_r
    ss_m = np.clip(rop_m - mean_m, 0.0, None)
    ltd_mean, rop, ss = float(mean_m[0]), float(rop_m[0]), float(ss_m[0])
    sigma = float(np.std(f_int[0] * draws))
    C, H_rate = val["unit_cost"], float(val["holding_rate"])
    fpu, fpo = float(val["freight_per_unit"] or 0.0), float(val["freight_per_order"] or 0.0)
    free_above = val["free_shipping_above"]
    H = H_rate * (C + fpu) if C is not None else None             # holding is charged on the landed price: price plus shipping per unit
    K = float(val["order_cost"])
    Kt = K + fpo                                                   # one order: its cost plus its shipping
    notes, flags = [], []
    if info["source"] != "own windows":
        flags.append(f"Error {info['source']} ({info['windows']} own windows)")
    if a["lead_time_months"]["source"] == "default":
        flags.append("Lead time is a default")
    if a["service_level"]["source"] == "default":
        flags.append("Service level is a default")
    if a["order_cost"]["source"] == "default":
        flags.append("Ordering cost is a placeholder")
    if a["holding_rate"]["source"] == "default":
        flags.append("Holding rate is a default")
    q = d = cycle = q_star = econ = eco = saving = None
    orders = turnover = None
    if H is not None and H > 0:
        q_star, d, cycle = eoq_orders(path, Kt, H, config)
        econ = {"D": d, "K": K, "fpo": fpo, "fpu": fpu, "hrate": H_rate, "ss": ss, "base_price": float(C), "breaks": [(m, p) for m, p in (breaks or [])],
                "dtype": dtype or cfg["discount"]["default"], "free_above": free_above}
        if econ["breaks"] or free_above is not None:
            q, qn, saving = best_order(econ, q_star, val, config)
        else:
            q, qn = constrain_quantity(q_star, d, Kt, H, val["pack_size"], val["moq"], val["storage_cap"], val["budget_cap"], C)
        notes += qn
        if q and q > 0 and d:
            orders = d / q
            cycle = q / d * 12.0
            eco = order_economics(q, econ)
            if L > cycle:
                notes.append(f"Lead time ({L:g} months) is longer than the order cycle ({cycle:.1f} months): orders are placed against inventory position (on hand plus on order), so about {math.ceil(L / cycle)} orders are in transit at once.")
    else:
        notes.append("No unit cost for this series: order quantity and costs are not calculated, only safety stock and reorder point in units.")
    avg_inv = (q / 2.0 + ss) if q is not None else None
    if avg_inv and d:
        turnover = d / avg_inv
    if sum(path[:max(1, min(12, len(path)))]) <= 0:
        flags.append("No forecast demand")
    price_used = eco["price"] if eco else (float(C) if C is not None else None)
    landed = price_used + fpu if price_used is not None else None
    row = {"country": key[0], "product": key[1], "forecast_next_12m": float(path[:12].sum()),
           "protection_months": P, "lead_time_demand": ltd_mean, "sigma_over_interval": sigma,
           "safety_stock": ss, "reorder_point": rop, "order_quantity": q, "orders_per_year": orders,
           "average_inventory_units": avg_inv, "average_inventory_value": avg_inv * landed if avg_inv is not None and landed else None,
           "cash_per_order": q * landed if q is not None and landed else None, "turnover": turnover,
           "days_of_stock": 365.25 / turnover if turnover else None,
           "yearly_cost": (eco["ordering"] + eco["holding"]) if eco else None,
           "ordering_cost_year": eco["ordering"] if eco else None,
           "holding_cost_year": eco["holding"] if eco else None,
           "annual_demand": d, "eoq_unconstrained": q_star, "unit_cost": C, "order_cost": K, "holding_rate": H_rate,
           "holding_per_unit_year": H_rate * landed if landed else H,
           "purchase_cost_year": eco["purchases"] if eco else (d * C if d is not None and C is not None else None),
           "freight_unit_cost_year": eco["freight_unit"] if eco else None, "price_used": price_used, "savings_vs_plain_order": saving,
           "total_cost_year": eco["total"] if eco else None,
           "lead_time_months": L, "review_period_months": R, "demand_ratio_at_service": q_alpha, "demand_ratio_mean": mean_r,
           "service_level": alpha, "stockout_risk_target": 1.0 - alpha, "error_source": info["source"],
           "backtest_windows": info["windows"], "flags": "; ".join(flags), "notes": " ".join(notes), "econ": econ}
    periods = periods if periods is not None else pd.date_range(pd.Timestamp.today().normalize().replace(day=1), periods=len(path), freq="MS")
    q05, q95 = float(np.quantile(draws, 0.05)), float(np.quantile(draws, 0.95))
    months = pd.DataFrame({"country": key[0], "product": key[1], "period": list(periods)[:starts], "forecast": path[:starts],
                           "interval_demand": mean_m, "interval_p05": f_int * q05, "interval_p95": f_int * q95,
                           "safety_stock": ss_m, "reorder_point": rop_m})
    trade = []
    for beta in pol["service_levels_shown"]:
        qb = float(np.quantile(draws, beta))
        ssb = max(f_int[0] * (qb - mean_r), 0.0)
        trade.append({"country": key[0], "product": key[1], "service_level": beta, "safety_stock": ssb,
                      "reorder_point": f_int[0] * qb,
                      "holding_cost_of_safety_stock": ssb * H if H is not None else None})
    return row, months, trade


# ============================================================================ prices, shipping and discounts
def _edges(e):
    """Price tiers as [(from quantity, price per unit)], starting at 0 with the base price unless a break at 0 is given."""
    t = sorted((float(m), float(p)) for m, p in (e.get("breaks") or []) if m is not None and p is not None and float(p) > 0 and float(m) >= 0)
    if not t or t[0][0] > 0:
        return [(0.0, float(e["base_price"]))] + t
    return t


def price_at(Q, e):
    """The price per unit for an order of Q. With 'all_units' the whole order gets the price of the highest break it reaches; with
    'incremental' each unit is priced by the tier it falls in, so the result is the average price of the order."""
    edges = _edges(e)
    if len(edges) == 1:
        return edges[0][1]
    if e.get("dtype") == "incremental":
        total = 0.0
        for i, (lo, p) in enumerate(edges):
            hi = edges[i + 1][0] if i + 1 < len(edges) else float("inf")
            total += max(min(Q, hi) - lo, 0.0) * p
        return total / Q if Q > 0 else edges[0][1]
    price = edges[0][1]
    for lo, p in edges:
        if Q >= lo:
            price = p
    return price


def order_economics(Q, e):
    """Yearly cost of ordering Q at a time, with prices, shipping and spare stock. `e` holds D (units a year), K (cost per order),
    fpo and fpu (shipping per order and per unit), hrate (holding rate), ss (spare stock), base_price, breaks, dtype and free_above
    (shipping per order is waived for orders worth at least this). Returns purchases, shipping per unit, ordering (cost per order plus
    any shipping per order), holding (on the landed price) and the total."""
    if Q is None or Q <= 0:
        return None
    price = price_at(Q, e)
    ship = 0.0 if (e.get("free_above") is not None and Q * price >= e["free_above"]) else e["fpo"]
    purchases, freight_unit = e["D"] * price, e["D"] * e["fpu"]
    ordering = (e["K"] + ship) * e["D"] / Q
    holding = e["hrate"] * (price + e["fpu"]) * (Q / 2.0 + e["ss"])
    return {"price": price, "ship_per_order": ship, "purchases": purchases, "freight_unit": freight_unit, "ordering": ordering, "holding": holding,
            "ordering_plus_holding": ordering + holding, "total": purchases + freight_unit + ordering + holding}


def best_order(e, q_star, val, config):
    """The cheapest order size when prices step down with quantity or shipping is waived above a value. Every sensible size is tried (a fine
    grid, each price break, the best size inside each tier, and the size that reaches free shipping), respecting pack size, minimum order
    and the caps, and the one with the lowest total yearly cost is taken. Returns (Q, notes, saving against ordering the plain EOQ)."""
    D, base = e["D"], e["base_price"]
    pack, moq, scap, bcap = val["pack_size"], val["moq"], val["storage_cap"], val["budget_cap"]
    edges = _edges(e)
    cand = list(np.linspace(max(1.0, 0.2 * q_star), max(4.0 * q_star, 1.5 * edges[-1][0], 10.0), inv(config)["discount"]["grid_points"]))
    cand += [m for m, _ in edges if m > 0]
    for _, p in edges:
        hh = e["hrate"] * (p + e["fpu"])
        if hh > 0 and D > 0:
            cand.append(math.sqrt(2.0 * D * (e["K"] + e["fpo"]) / hh))
        if e.get("free_above") and p > 0:
            cand.append(e["free_above"] / p)
    variants = set()
    for q in cand:
        if q <= 0:
            continue
        for x in ([math.floor(q / pack) * pack, math.floor(q / pack) * pack + pack] if pack and pack > 0 else [q]):
            if x <= 0:
                continue
            if moq and x < moq:
                x = math.ceil(moq / pack) * pack if pack and pack > 0 else moq
            variants.add(round(float(x), 6))
    feasible = [x for x in variants if (scap is None or x <= scap + 1e-9) and (bcap is None or x * price_at(x, e) <= bcap + 1e-9)]
    plain, plain_notes = constrain_quantity(q_star, D, e["K"] + e["fpo"], e["hrate"] * (base + e["fpu"]), pack, moq, scap, bcap, base)
    if not feasible:
        return plain, plain_notes, None
    best = min(feasible, key=lambda x: order_economics(x, e)["total"])
    saving = None
    notes = []
    if plain and plain > 0:
        saving = order_economics(plain, e)["total"] - order_economics(best, e)["total"]
        price_b, ec = price_at(best, e), order_economics(best, e)
        if saving > 0.5 and abs(price_b - base) > 1e-9:
            notes.append(f"Order {best:,.0f} units to pay {price_b:,.2f} per unit instead of {base:,.2f}: saves about {saving:,.0f} USD a year against the plain order of {plain:,.0f}.")
        elif saving > 0.5 and e["fpo"] and ec["ship_per_order"] == 0:
            notes.append(f"Order {best:,.0f} units so the order is big enough for free shipping: saves about {saving:,.0f} USD a year against the plain order of {plain:,.0f}.")
        if abs(best - plain) > 1e-6 and saving <= 0.5:
            notes.append("The plain order size is already the cheapest allowed.")
    return best, notes, saving


# ============================================================================ the order size and its cost
def total_cost(D, K, H, C, Q, SS=0.0):
    """Yearly cost of ordering Q at a time: purchases D x C, ordering K x D / Q, holding H x (Q / 2 + SS), where H is the
    holding cost of one unit for a year and SS the spare stock that is always on the shelf. Returns a dict, or None when Q <= 0."""
    if Q is None or Q <= 0:
        return None
    purchases, ordering, holding = D * C, K * D / Q, H * (Q / 2.0 + SS)
    return {"purchases": purchases, "ordering": ordering, "holding": holding, "total": purchases + ordering + holding,
            "ordering_plus_holding": ordering + holding}


def cost_curve(row, points=70):
    """Yearly cost against the size of each order, for one plan row: the U shape that the order quantity sits at the bottom of. Prices,
    shipping and any quantity discount come from the row's own economics, so the curve steps where a price break is reached. Columns:
    order_quantity, ordering, holding, ordering_plus_holding, purchases, total. The grid always contains the chosen order quantity and the
    unconstrained EOQ, so both can be marked. Empty when the row has no cost inputs."""
    cols = ["order_quantity", "ordering", "holding", "ordering_plus_holding", "purchases", "total"]
    e = row.get("econ")
    need = ("order_quantity", "eoq_unconstrained")
    if not isinstance(e, dict) or any(row.get(k) is None or pd.isna(row.get(k)) for k in need) or row["order_quantity"] <= 0 or row["eoq_unconstrained"] <= 0:
        return pd.DataFrame(columns=cols)
    q, qs = float(row["order_quantity"]), float(row["eoq_unconstrained"])
    breaks = [m for m, _ in _edges(e) if m > 0]
    top = 3.0 * max(q, qs, *(1.3 * b for b in breaks)) if breaks else 3.0 * max(q, qs)
    grid = np.unique(np.concatenate([np.linspace(max(1.0, 0.2 * min(q, qs)), top, points), [q, qs], breaks, [b - 1e-6 for b in breaks if b > 1]]))
    rows = []
    for g in grid:
        eco = order_economics(float(g), e)
        rows.append({"order_quantity": g, "ordering": eco["ordering"], "holding": eco["holding"], "ordering_plus_holding": eco["ordering_plus_holding"],
                     "purchases": eco["purchases"] + eco["freight_unit"], "total": eco["total"]})
    return pd.DataFrame(rows)[cols]

def inventory_profile(path, q, rop, ss, lead_months, review_months, config, demand_scale=1.0, months=12, start=None):
    """A day-by-day picture of the stock under the plan, from the monthly forecast `path`. Start: one full order cycle, with stock
    at the reorder level plus one order. Each day demand is the month's forecast spread evenly (times demand_scale). Stock is
    checked every review_months (every day if 0): when on hand plus on order is at or below the reorder point, whole
    multiples of q are ordered, and they arrive after the lead time. Stock cannot go below zero; the unmet part is counted
    as shortage. Returns a DataFrame: date, on_hand, position (on hand plus on order), shortage (units short that day)."""
    dpm = inv(config)["days_per_month"]
    days = int(round(months * dpm))
    lead = max(int(round(lead_months * dpm)), 0)
    gap = max(int(round(review_months * dpm)), 1) if review_months and review_months > 0 else 1
    path = np.asarray(path, dtype=float)
    on_hand, pipeline, shortage = float(max(rop, ss) + q), [], 0.0
    out = []
    t0 = pd.Timestamp(start) if start is not None else pd.Timestamp.today().normalize().replace(day=1)
    for day in range(days):
        for arrive, qty in [p for p in pipeline if p[0] == day]:
            on_hand += qty
        pipeline = [p for p in pipeline if p[0] > day]
        month = min(int(day / dpm), len(path) - 1)
        need = max(path[month], 0.0) / dpm * demand_scale
        served = min(on_hand, need)
        on_hand -= served
        short = need - served
        position = on_hand + sum(qty for _, qty in pipeline)
        if day % gap == 0 and q > 0 and position <= rop:
            lots = math.ceil((rop - position) / q + 1e-9)
            pipeline.append((day + max(lead, 1), lots * q))
            position += lots * q
        out.append({"date": t0 + pd.Timedelta(days=day), "on_hand": on_hand, "position": position, "shortage": short})
    return pd.DataFrame(out)


# ============================================================================ possible stockouts in the history
def stockout_candidates(clean_df, config, extra=None):
    """Months where demand fell well below what the series' usual level and season predict, which may mean the product ran out
    rather than that nobody wanted it. For each country x product: the usual level is the median of the previous level_months months
    after removing the pooled seasonal pattern, and a month is flagged when it is under low_fraction of level x seasonal factor.
    `extra` is an optional list of (country, product, month) the user's own file marks as stockouts: they are always listed (kind
    'marked in your file'), with what was expected for them. Returns a DataFrame: country, product, month, actual, expected, ratio,
    kind ('no sales', 'low sales' or 'marked in your file'). Nothing is changed by this."""
    so = inv(config)["stockout"]
    cols = ["country", "product", "month", "actual", "expected", "ratio", "kind"]
    series = demand_series(clean_df)
    if not series:
        return pd.DataFrame(columns=cols)
    season = ea.seasonal_index(clean_df, config)
    marked = {(c, p, pd.Timestamp(m)) for c, p, m in (extra or [])}
    rows = []
    for key, s in series.items():
        v = s.to_numpy(dtype=float)
        fac = np.array([season[m.month] if season else 1.0 for m in s.index])
        des = v / np.where(fac > 0, fac, 1.0)
        for i in range(so["min_level_months"], len(v)):
            level = float(np.median(des[max(0, i - so["level_months"]):i]))
            expected = level * fac[i]
            flagged = (key[0], key[1], s.index[i]) in marked
            if expected > 0 and (v[i] < so["low_fraction"] * expected or flagged):
                kind = "marked in your file" if flagged else ("no sales" if v[i] <= 0 else "low sales")
                rows.append({"country": key[0], "product": key[1], "month": s.index[i], "actual": v[i], "expected": expected, "ratio": v[i] / expected, "kind": kind})
    return pd.DataFrame(rows, columns=cols)


def apply_repairs(clean_df, repairs):
    """A copy of the demand rows in which each confirmed month is replaced by what was expected. `repairs` is a list of (country, product,
    month, new_total). Rows already in that month are scaled so they add up to new_total (value scaled in step); a month with no rows
    gets one added row, marked 'ADJUSTED'. The original frame is never changed. With no repairs the same data comes back."""
    if not repairs or clean_df is None or clean_df.empty:
        return clean_df
    df = clean_df.copy()
    df["Quantity"] = df["Quantity"].astype(float)                    # repaired months are rarely whole numbers
    extra = []
    for country, product, month, target in repairs:
        month = pd.Timestamp(month)
        sel = (df["U_Country"] == country) & (df["U_ProductLine"] == product) & (df["YearMonth"] == month)
        have = float(df.loc[sel, "Quantity"].sum())
        if sel.any() and have > 0:
            factor = float(target) / have
            df.loc[sel, "Quantity"] = df.loc[sel, "Quantity"] * factor
            if "LineTotal_USD" in df:
                df.loc[sel, "LineTotal_USD"] = df.loc[sel, "LineTotal_USD"] * factor
        else:
            mine = df[(df["U_Country"] == country) & (df["U_ProductLine"] == product)]
            if mine.empty:
                continue
            row = mine.iloc[-1].copy()
            price = float((mine["LineTotal_USD"] / mine["Quantity"].where(mine["Quantity"] > 0)).median()) if "LineTotal_USD" in mine and (mine["Quantity"] > 0).any() else float("nan")
            row["Quantity"], row["DocDate"], row["YearMonth"] = float(target), month + pd.Timedelta(days=14), month
            if "LineTotal_USD" in row.index:
                row["LineTotal_USD"] = float(target) * price if not np.isnan(price) else np.nan
            for c in ("CardCode", "CardName"):
                if c in row.index:
                    row[c] = "ADJUSTED"
            extra.append(row)
    if extra:
        df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
    return df.sort_values(["YearMonth", "U_Country", "U_ProductLine"], kind="stable").reset_index(drop=True)

# ============================================================================ the whole plan
def build_plan(raw_df, config, basis=None, entered=None, overrides=None, horizon=None, model=None, lookback=None,
               simulations=None, seed=None, now=None, caches=None, demand=None, data_label=None, data_id="sample",
               lead_days=None, only=None, price_breaks=None, discount_type=None):
    """Runs every stage and returns a dict:
      table (one row per country x product), monthly (reorder point by month), tradeoff (90/95/99 % service),
      forecast (the monthly forecast used), assumptions (the log: scope, name, value, unit, source, note, timestamp),
      recon (row reconciliation), diagnostics (seed, runs, coverage, error sources, warnings, data source).
    `entered` holds global assumption values (None = blank), `overrides` {(country, product): {name: value}}.
    `caches` is an optional dict that stores the expensive steps between calls (the UI passes a cached lookup).
    `price_breaks` is {(country, product): [(from quantity, price per unit), ...]} and `discount_type` is "all_units" or "incremental".
    `only` is an optional list of (country, product) to plan; the others are skipped, and the numbers for those that are planned
    are identical to a full run (each series keeps its place in the random stream)."""
    cfg = inv(config)
    cfg_run = {**config, "analytics": {**config["analytics"], "inventory": {**cfg, "simulations": simulations or cfg["simulations"],
                                                                         "seed": cfg["seed"] if seed is None else seed}}}
    cfg = inv(cfg_run)
    basis = basis or cfg["demand_basis"]["default"]
    caches = caches if caches is not None else {}
    stamp = (now or pd.Timestamp.now(tz="UTC")).isoformat()
    if demand is not None:                                       # an uploaded file: (demand rows, reconciliation) already prepared
        clean, recon = demand
    else:
        key_demand = ("demand", basis)
        if key_demand not in caches:
            caches[key_demand] = demand_rows(raw_df, cfg_run, basis)
        clean, recon = caches[key_demand]
    series = demand_series(clean)
    est = _estimates(clean, cfg_run, lead_days)
    keys = list(series)
    resolved = {k: resolve_assumptions(cfg_run, k, entered, (overrides or {}).get(k), est.get(k)) for k in keys}
    horizon = horizon or cfg["horizon_months"]
    cfg_run["analytics"]["inventory"]["horizon_months"] = horizon
    longest = max([math.ceil(a["lead_time_months"]["value"] + a["review_period_months"]["value"] - 1e-9) for a in resolved.values()] or [1])
    key_fc = ("forecast", data_id, basis, model, lookback, horizon + longest)
    if key_fc not in caches:
        caches[key_fc] = forecast_adapter(series, clean, cfg_run, horizon + longest, model, lookback)
    fcs = caches[key_fc]
    tables, monthlies, trades, fc_rows, scope_diag = [], [], [], [], {}
    alpha0 = float(next(iter(resolved.values()))["service_level"]["value"]) if resolved else cfg["defaults"]["service_level"]["value"]
    for idx, key in enumerate(keys):
        if only is not None and key not in only:
            continue
        a = resolved[key]
        P = round(a["lead_time_months"]["value"] + a["review_period_months"]["value"], 1)
        key_err = ("errors", data_id, basis, model, lookback, P)
        if key_err not in caches:
            errs = backtest_errors(series, clean, cfg_run, P, model, lookback)
            caches[key_err] = (errs, calibrate(errs, cfg_run, alpha0, P))
        errs, cal = caches[key_err]
        scope_diag[P] = cal
        f = fcs[key]
        if f.empty or f["forecast"].dropna().empty:
            tables.append({"country": key[0], "product": key[1], "flags": "Not enough history to forecast", "notes": ""})
            continue
        path = f["forecast"].dropna().to_numpy()
        periods = f.loc[f["forecast"].notna(), "period"].tolist()
        info = error_source(errs, cfg_run, key)
        rng = np.random.default_rng([cfg["seed"], idx])
        row, months, trade = plan_series(key, path, info, a, cfg_run, rng, k=cal["k"], periods=periods,
                                          breaks=(price_breaks or {}).get(key), dtype=discount_type)
        row["months_of_history"], row["zero_months"] = len(series[key]), int((series[key] == 0).sum())
        if row["zero_months"]:
            row["flags"] = (row["flags"] + "; " if row["flags"] else "") + f"{row['zero_months']} month(s) with no demand counted as zero"
        if row["months_of_history"] < cfg_run["analytics"]["forecast"]["seasonal_series"]["min_history_months"]:
            row["flags"] = (row["flags"] + "; " if row["flags"] else "") + "Under 12 months of history: simpler forecast model"
        tables.append(row)
        monthlies.append(months)
        trades += trade
        fc_rows.append(pd.DataFrame({"country": key[0], "product": key[1], "period": periods, "forecast": path,
                                     "method_used": f["method_used"].iloc[-1]}))
    log = []
    for key in keys:
        for name in ASSUMPTIONS:
            a = resolved[key][name]
            log.append({"country": key[0], "product": key[1], "assumption": name, "value": a["value"], "unit": a["unit"],
                        "source": a["source"], "note": a["note"], "timestamp": stamp})
    cals = list(scope_diag.values())
    uploaded = demand is not None
    diag = {"seed": cfg["seed"], "simulations": cfg["simulations"], "basis": basis,
            "data_source": data_label or cfg["data_source_label"],
            "warning": cfg["upload_warning"] if uploaded else cfg["sample_warning"], "run_at": stamp, "calibration": cals,
            "error_sources": pd.Series([t.get("error_source", "n/a") for t in tables]).value_counts().to_dict(),
            "in_sample_residuals_used": False,
            "days_per_month": cfg["days_per_month"]}
    cols = ["country", "product", "forecast_next_12m", "protection_months", "lead_time_demand", "sigma_over_interval",
            "safety_stock", "reorder_point", "order_quantity", "orders_per_year", "average_inventory_units",
            "average_inventory_value", "cash_per_order", "turnover", "days_of_stock", "yearly_cost", "ordering_cost_year", "holding_cost_year",
            "annual_demand", "eoq_unconstrained", "unit_cost", "order_cost", "holding_per_unit_year", "purchase_cost_year",
            "total_cost_year", "holding_rate", "freight_unit_cost_year", "price_used", "savings_vs_plain_order", "econ",
            "lead_time_months", "review_period_months", "demand_ratio_at_service", "demand_ratio_mean",
            "service_level", "stockout_risk_target",
            "error_source", "backtest_windows", "months_of_history", "zero_months", "flags", "notes"]
    table = pd.DataFrame(tables).reindex(columns=cols)
    return {"table": table, "monthly": pd.concat(monthlies, ignore_index=True) if monthlies else pd.DataFrame(),
            "tradeoff": pd.DataFrame(trades), "forecast": pd.concat(fc_rows, ignore_index=True) if fc_rows else pd.DataFrame(),
            "assumptions": pd.DataFrame(log), "recon": recon, "diagnostics": diag, "history": series}


def z_for(service_level):
    """Normal z for a service level, used only by the normal fallback."""
    return NormalDist().inv_cdf(service_level)
