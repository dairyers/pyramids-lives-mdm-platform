"""Replaying the past year under the stock plan. Month by month, the plan is rebuilt using only the months before it (no peeking),
its reorder level and order amount are applied to the demand that really happened next, and stock is followed day by day. Two
old-style rules are replayed on the same demand for comparison: a flat average with no spare stock, and a moving average with a fixed
buffer. Fill rate, stockouts, stock held and cost are measured, with bootstrap ranges across products. Nothing here is a claim of
improvement unless the ranges support it. Settings come from config analytics.inventory.replay."""
import math

import numpy as np
import pandas as pd

import engine_inventory as ei

POLICIES = ("plan", "flat", "moving")


def rp(config):
    return ei.inv(config)["replay"]


def _eoq(d_month, K, H, a):
    """Order amount for a policy: the economic order quantity on 12 x the monthly demand, with pack size, minimum order and caps as in the plan."""
    D = 12.0 * d_month
    if D <= 0 or K <= 0 or H <= 0:
        return 0.0
    v = {n: a[n]["value"] for n in ("pack_size", "moq", "storage_cap", "budget_cap", "unit_cost")}
    q, _ = ei.constrain_quantity(math.sqrt(2.0 * D * K / H), D, K, H, v["pack_size"], v["moq"], v["storage_cap"], v["budget_cap"], v["unit_cost"])
    return q


def simulate(params, actual, lead_months, review_months, dpm):
    """Follow stock day by day. params: one (reorder level, order amount) per month; actual: the demand that really happened each month.
    Stock is checked every review_months (daily when 0); when stock on hand plus on order is at or below the reorder level, whole
    multiples of the order amount are ordered as one order, arriving after the lead time. Returns a dict of daily arrays and counts.
    Stock cannot go below zero: the unmet part is counted as short."""
    lead = max(int(round(lead_months * dpm)), 0)
    gap = max(int(round(review_months * dpm)), 1) if review_months and review_months > 0 else 1
    on_hand, pipeline, day = float(params[0][0] + params[0][1]), [], 0
    hand, short, demand, orders, ordered = [], [], [], 0, 0.0
    for m, (rop, q) in enumerate(params):
        days = int(round((m + 1) * dpm)) - int(round(m * dpm))
        per_day = max(actual[m], 0.0) / days
        for _ in range(days):
            for arrive, qty in [p for p in pipeline if p[0] == day]:
                on_hand += qty
            pipeline = [p for p in pipeline if p[0] > day]
            served = min(on_hand, per_day)
            on_hand -= served
            position = on_hand + sum(qty for _, qty in pipeline)
            if day % gap == 0 and q > 0 and position <= rop:
                lots = math.ceil((rop - position) / q + 1e-9)
                pipeline.append((day + max(lead, 1), lots * q))
                orders += 1
                ordered += lots * q
            hand.append(on_hand)
            short.append(per_day - served)
            demand.append(per_day)
            day += 1
    return {"on_hand": np.array(hand), "short": np.array(short), "demand": np.array(demand), "orders": orders, "units_ordered": ordered}


def _metrics(sim, params, K, H, C, month_days):
    d, s = float(sim["demand"].sum()), float(sim["short"].sum())
    ends = np.cumsum(month_days)
    starts = np.concatenate([[0], ends[:-1]])
    short_months = sum(1 for a, b in zip(starts, ends) if sim["short"][a:b].sum() > 1e-6)
    avg = float(sim["on_hand"].mean())
    years = len(sim["on_hand"]) / 365.25
    ordering, holding = sim["orders"] * K, H * avg * years
    return {"demand": d, "short": s, "fill_rate": (1 - s / d) if d > 0 else np.nan, "months_short": short_months, "months": len(month_days),
            "avg_stock_units": avg, "avg_stock_value": avg * C, "orders": sim["orders"], "ordering_cost": ordering, "holding_cost": holding,
            "total_cost": ordering + holding}


def run_replay(clean_df, config, resolved, months=None, caches=None, data_id="sample", basis="requests", model=None, lookback=None,
               price_breaks=None, discount_type=None):
    """Replays the last `months` months. `resolved` is {(country, product): {assumption: {value, source, ...}}}, as the plan used.
    Returns a dict: available (bool) and reason; params (the plan's reorder level and order amount each month); per_series (metrics by
    country, product and policy); summary (all products together, by policy); ranges (bootstrap ranges and differences);
    verdict (plain sentences); timeline (daily stock by policy); months; notes."""
    cfg = ei.inv(config)
    r, bt = cfg["replay"], cfg["backtest"]
    fc = config["analytics"]["forecast"]
    model, lookback = model or fc["default_model"], lookback or fc["lookback_months"]
    caches = {} if caches is None else caches
    out = {"available": False, "reason": "", "months": 0, "notes": [r["small_sample_note"]]}
    series = ei.demand_series(clean_df)
    if not series:
        out["reason"] = "There is no demand data to replay."
        return out
    last = max(s.index[-1] for s in series.values())
    M = months or r["months"]
    first = last - pd.DateOffset(months=M - 1)
    origin0 = first - pd.DateOffset(months=1)
    eligible, skipped = [], []
    for k, s in series.items():
        a = resolved.get(k)
        if a is None or a["unit_cost"]["value"] is None:
            skipped.append(f"{k[0]}/{k[1]}: no unit cost")
        elif len(s.loc[:origin0]) < bt["min_train_months"]:
            skipped.append(f"{k[0]}/{k[1]}: fewer than {bt['min_train_months']} months before the replay window")
        else:
            eligible.append(k)
    if not eligible:
        out["reason"] = "Replay needs a unit cost and at least " + f"{bt['min_train_months']} months of history before the replayed year. " + "; ".join(skipped[:3])
        return out
    if skipped:
        out["notes"].append("Not replayed: " + "; ".join(skipped))
    dpm = cfg["days_per_month"]
    origins = list(pd.date_range(origin0, periods=M, freq="MS"))
    longest = max(math.ceil(resolved[k]["lead_time_months"]["value"] + resolved[k]["review_period_months"]["value"] - 1e-9) for k in eligible)
    horizon = cfg["horizon_months"] + longest
    errs = {}
    for k in eligible:
        a = resolved[k]
        P = round(a["lead_time_months"]["value"] + a["review_period_months"]["value"], 1)
        if P not in errs:
            ck = ("rp_errors", data_id, basis, model, lookback, P)
            if ck not in caches:
                caches[ck] = ei.backtest_errors(series, clean_df, config, P, model, lookback)
            errs[P] = caches[ck]
    fcs = {}
    for o in origins:
        ck = ("rp_fc", data_id, basis, model, lookback, o, horizon)
        if ck not in caches:
            trains = {k: series[k].loc[:o] for k in eligible}
            caches[ck] = ei.forecast_adapter(trains, clean_df, config, horizon, model, lookback, upto=o)
        fcs[o] = caches[ck]
    seed = cfg["seed"]
    params_rows, sims, per_rows, timeline = [], {}, [], []
    month_days = [int(round((m + 1) * dpm)) - int(round(m * dpm)) for m in range(M)]
    for idx, k in enumerate(eligible):
        a = resolved[k]
        v = {n: a[n]["value"] for n in ("lead_time_months", "review_period_months", "unit_cost", "order_cost", "holding_rate")}
        L, R, C, K, h = v["lead_time_months"], v["review_period_months"], float(v["unit_cost"]), float(v["order_cost"]), float(v["holding_rate"])
        H, P = h * C, L + R
        Hh = math.ceil(P - 1e-9)
        actual = [float(series[k].get(pd.Timestamp(first) + pd.DateOffset(months=m), 0.0)) for m in range(M)]
        plan_params = []
        for oi, o in enumerate(origins):
            path = fcs[o][k]["forecast"].dropna().to_numpy()
            e = errs[round(P, 1)]
            known = e[e["origin"] + pd.DateOffset(months=Hh) <= o]                                    # only windows already finished: no peeking
            info = ei.error_source(known, config, k)
            row, _, _ = ei.plan_series(k, path, info, a, config, np.random.default_rng([seed, idx, oi]), k=1.0,
                                       periods=list(pd.date_range(o + pd.DateOffset(months=1), periods=len(path), freq="MS")),
                                       breaks=(price_breaks or {}).get(k), dtype=discount_type)
            q = row["order_quantity"] or 0.0
            plan_params.append((float(row["reorder_point"]), float(q)))
            params_rows.append({"country": k[0], "product": k[1], "month": o + pd.DateOffset(months=1), "reorder_point": row["reorder_point"],
                                "order_quantity": q, "safety_stock": row["safety_stock"], "error_from": info["source"]})
        hist = series[k].loc[:origin0].to_numpy()
        flat_d = float(np.mean(hist[-r["flat_avg_months"]:]))
        flat_q = _eoq(flat_d, K, H, a)
        flat_params = [(flat_d * P, flat_q)] * M
        moving_params = []
        for m in range(M):
            known_hist = np.concatenate([hist, actual[:m]]) if m else hist
            d = float(np.mean(known_hist[-r["baseline_avg_months"]:]))
            moving_params.append((d * P * (1 + r["baseline_buffer"]), _eoq(d, K, H, a)))
        for name, prm in (("plan", plan_params), ("flat", flat_params), ("moving", moving_params)):
            sim = simulate(prm, actual, L, R, dpm)
            per_rows.append({"country": k[0], "product": k[1], "policy": name, **_metrics(sim, prm, K, H, C, month_days)})
            step = 7                                                                                  # weekly points keep the chart light
            for day in range(0, len(sim["on_hand"]), step):
                timeline.append({"country": k[0], "product": k[1], "policy": name, "date": first + pd.Timedelta(days=day),
                                 "on_hand": float(sim["on_hand"][day]), "short": float(sim["short"][day:day + step].sum())})
    per = pd.DataFrame(per_rows)
    out.update({"available": True, "months": M, "params": pd.DataFrame(params_rows), "per_series": per, "timeline": pd.DataFrame(timeline),
                "keys": eligible, "first_month": first})
    out["summary"], out["ranges"], out["verdict"] = _summarise(per, config, seed)
    wide = int(out["params"]["error_from"].isin(["normal fallback", "borrowed from the same product", "borrowed from all series"]).sum())
    fallback = int((out["params"]["error_from"] == "normal fallback").sum())
    out["notes"].append(f"In {wide} of {len(out['params'])} monthly plans for individual products, there was little or no past forecast-miss history yet, so a wider standard margin was used "
                        f"({fallback} of them with no history at all). That makes the early months of this replay more cautious, and so more costly, than a plan with a full history would be.")
    return out


def _total(df):
    d, s = df["demand"].sum(), df["short"].sum()
    return {"fill_rate": (1 - s / d) if d > 0 else np.nan, "avg_stock_value": df["avg_stock_value"].sum(), "total_cost": df["total_cost"].sum(),
            "months_short": df["months_short"].sum() / max(df["months"].sum(), 1), "orders": df["orders"].sum()}


def _arrays(df):
    return {c: df[c].to_numpy(dtype=float) for c in ("demand", "short", "avg_stock_value", "total_cost", "months_short", "months")}


def _stats(a, idx):
    """The all-products totals for each bootstrap draw at once: idx has one row of product indices per draw."""
    d, s = a["demand"][idx].sum(axis=1), a["short"][idx].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        fill = np.where(d > 0, 1 - s / d, np.nan)
    return {"fill_rate": fill, "avg_stock_value": a["avg_stock_value"][idx].sum(axis=1), "total_cost": a["total_cost"][idx].sum(axis=1),
            "months_short": a["months_short"][idx].sum(axis=1) / np.maximum(a["months"][idx].sum(axis=1), 1)}


def _summarise(per, config, seed):
    r = rp(config)
    labels = r["policy_labels"]
    summary = pd.DataFrame([{"policy": p, "label": labels[p], **_total(per[per["policy"] == p])} for p in POLICIES])
    keys = per[per["policy"] == "plan"][["country", "product"]].drop_duplicates().to_records(index=False).tolist()
    by = {p: per[per["policy"] == p].set_index(["country", "product"]).loc[keys] for p in POLICIES}
    idx = np.random.default_rng([seed, 7]).integers(0, len(keys), size=(r["bootstrap"], len(keys)))
    boot = {p: _stats(_arrays(by[p]), idx) for p in POLICIES}
    lo, hi = r["ci"]
    rows, verdict = [], []
    for p in POLICIES:
        for metric in ("fill_rate", "avg_stock_value", "total_cost", "months_short"):
            rows.append({"policy": p, "metric": metric, "low": float(np.nanquantile(boot[p][metric], lo)), "high": float(np.nanquantile(boot[p][metric], hi))})
    for p in ("flat", "moving"):
        for metric in ("fill_rate", "avg_stock_value", "total_cost"):
            diff = boot["plan"][metric] - boot[p][metric]
            point = float(summary.loc[summary["policy"] == "plan", metric].iloc[0] - summary.loc[summary["policy"] == p, metric].iloc[0])
            dl, dh = float(np.nanquantile(diff, lo)), float(np.nanquantile(diff, hi))
            good_high = metric == "fill_rate"                                          # for fill rate higher is better; for stock and cost, lower is better
            clear = ("better" if (dl > 0 if good_high else dh < 0) else "worse" if (dh < 0 if good_high else dl > 0) else "unclear")
            verdict.append({"versus": p, "metric": metric, "point": point, "low": dl, "high": dh, "clear": clear})
    return summary, pd.DataFrame(rows), verdict