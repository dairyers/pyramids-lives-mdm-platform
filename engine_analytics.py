"""Engine 2: noise cleaning, a choice of demand-forecast models, churn radar and cross-sell
(spec Section 4). Every parameter is read from config.analytics.

Error handling: empty inputs, missing months (reindexed to zero), small samples and zero
baselines return empty frames or labelled results, never exceptions.
"""
import numpy as np
import pandas as pd

FORECAST_COLUMNS = ["period", "actual", "fitted", "forecast", "method_used"]
CHURN_COLUMNS = ["CardCode", "CardName", "U_Entity", "U_Country", "baseline_avg", "recent_avg", "delta",
                 "flagged", "risk_band"]
CROSS_SELL_COLUMNS = ["CardCode", "CardName", "U_Entity", "U_Country", "antecedent_products",
                      "last_antecedent_date", "antecedent_quantity", "entity_confidence", "entity_lift"]


# ============================================================================ 4.2 noise cleaning
def order_categories(raw_df, config):
    """The category of every raw row: CONFIRMED_USED (a confirmed PO with a status in noise_filter.status_in),
    CONFIRMED_NOT_READY (a confirmed PO still outside status_in) or the row's own RecordType (enquiry, draft, cancelled)."""
    nf = config["analytics"]["noise_filter"]
    is_rt = raw_df["RecordType"] == nf["record_type"]
    ok = raw_df["U_ReqStatus"].isin(nf["status_in"])
    return raw_df["RecordType"].where(~is_rt, "CONFIRMED_NOT_READY").mask(is_rt & ok, "CONFIRMED_USED")


def default_include(config):
    return ["CONFIRMED_USED"]


def clean_transaction_data(raw_df, config, include=None):
    """Rows the analytics use. include is a list of categories from order_categories(); None means the default
    (confirmed POs only), which is what the noise filter in the config describes."""
    nf = config["analytics"]["noise_filter"]
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=list(getattr(raw_df, "columns", [])) + ["YearMonth"])
    if include is None:
        df = raw_df[(raw_df["RecordType"] == nf["record_type"]) &
                    (raw_df["U_ReqStatus"].isin(nf["status_in"]))].copy()
    else:
        df = raw_df[order_categories(raw_df, config).isin(list(include))].copy()
    df["DocDate"] = pd.to_datetime(df["DocDate"])
    df["YearMonth"] = df["DocDate"].dt.to_period("M").dt.to_timestamp()   # month start
    return df


# ============================================================================ 4.3 forecast
# Section 4.3 forecast models

def _window(s, lookback):
    return s.iloc[-lookback:] if lookback else s


def _damped_fit(w, fc):
    """Damped-trend smoothing on a numpy array. alpha, beta and phi are chosen from the small grids in config by
    the lowest one-step-ahead squared error on the window. Returns (fitted, level, trend, phi)."""
    n, g = len(w), fc["damped"]
    steps = max(min(3, n - 1), 1)
    best = None
    for alpha in g["alphas"]:
        for beta in g["betas"]:
            for phi in g["phis"]:
                level, trend, sse = float(w[0]), float((w[steps] - w[0]) / steps), 0.0
                fitted = np.empty(n)
                fitted[0] = w[0]
                for i in range(1, n):
                    pred = level + phi * trend
                    fitted[i] = pred
                    sse += (w[i] - pred) ** 2
                    new_level = alpha * w[i] + (1 - alpha) * pred
                    trend = beta * (new_level - level) + (1 - beta) * phi * trend
                    level = new_level
                if best is None or sse < best[0]:
                    best = (sse, fitted, level, trend, phi)
    return best[1], best[2], best[3], best[4]


def _damped_path(level, trend, phi, h):
    return np.array([level + trend * sum(phi ** k for k in range(1, i + 1)) for i in range(1, h + 1)])


def _m_rolling(w, h, fc, months, season):
    v = np.asarray(w, dtype=float)
    fitted = np.array([v[0]] + [v[max(0, i - len(v)):i].mean() for i in range(1, len(v))])
    return fitted, np.full(h, v.mean())


def _m_linear(w, h, fc, months, season):
    v = np.asarray(w, dtype=float)
    x = np.arange(len(v))
    slope, icpt = np.polyfit(x, v, 1) if len(v) > 1 else (0.0, v[0])
    return slope * x + icpt, slope * np.arange(len(v), len(v) + h) + icpt


def _m_damped(w, h, fc, months, season):
    fitted, level, trend, phi = _damped_fit(np.asarray(w, dtype=float), fc)
    return fitted, _damped_path(level, trend, phi, h)


def _m_seasonal(w, h, fc, months, season):
    if not season:
        return None
    idx = np.array([season[m.month] for m in months["hist"]])
    fut_idx = np.array([season[m.month] for m in months["future"]])
    fitted, level, trend, phi = _damped_fit(np.asarray(w, dtype=float) / idx, fc)
    return fitted * idx, _damped_path(level, trend, phi, h) * fut_idx


def _m_seasonal_level(w, h, fc, months, season):
    """No trend at all: the average level of the de-seasonalised window, re-seasonalised for each future month."""
    if not season:
        return None
    idx = np.array([season[m.month] for m in months["hist"]])
    fut_idx = np.array([season[m.month] for m in months["future"]])
    level = float((np.asarray(w, dtype=float) / idx).mean())
    return level * idx, np.full(h, level) * fut_idx


def harmonic_index(y, months, harmonics):
    """Seasonal factors (1..12 -> factor, mean 1.0) for ONE series: log(volume) is regressed on a straight-line trend
    plus `harmonics` sine/cosine pairs of the calendar month, and only the seasonal part is kept. A smooth curve needs
    2 numbers per harmonic instead of 11 monthly factors, so it stays stable on 12 to 24 months of history."""
    mon = np.array([m.month for m in months], dtype=float)
    t = np.arange(len(mon), dtype=float)
    cols = [np.ones(len(mon)), t]
    for k in range(1, harmonics + 1):
        cols += [np.sin(2 * np.pi * k * mon / 12), np.cos(2 * np.pi * k * mon / 12)]
    beta, *_ = np.linalg.lstsq(np.column_stack(cols), np.log(np.clip(np.asarray(y, dtype=float), 1.0, None)), rcond=None)
    f = {}
    for m in range(1, 13):
        f[m] = float(np.exp(sum(beta[2 + 2 * (k - 1)] * np.sin(2 * np.pi * k * m / 12) +
                                beta[3 + 2 * (k - 1)] * np.cos(2 * np.pi * k * m / 12) for k in range(1, harmonics + 1))))
    mean = float(np.mean(list(f.values())))
    return {m: v / mean for m, v in f.items()}


def _history_series(clean_df, config, upto=None):
    """(month span, one monthly array per country x product series) over the history up to `upto`, or None when the
    history is shorter than min_history_months. A series that started late is left out: it would fake a step up."""
    ss = config["analytics"]["forecast"]["seasonal_series"]
    if clean_df is None or clean_df.empty:
        return None
    d = clean_df if upto is None else clean_df[clean_df["YearMonth"] <= upto]
    span = pd.date_range(d["YearMonth"].min(), d["YearMonth"].max(), freq="MS")
    if len(span) < ss["min_history_months"]:
        return None
    arrays = [g.groupby("YearMonth")["Quantity"].sum().astype(float).reindex(span, fill_value=0.0).to_numpy()
              for _, g in d.groupby(["U_Country", "U_ProductLine"]) if g["YearMonth"].min() <= span[0]]
    return (span, arrays) if arrays else None


def _wave_columns(months, waves):
    mon = np.array([m.month for m in months], dtype=float)
    cols = [np.ones(len(mon)), np.arange(len(mon), dtype=float)]
    for k in range(1, waves + 1):
        cols += [np.sin(2 * np.pi * k * mon / 12), np.cos(2 * np.pi * k * mon / 12)]
    return np.column_stack(cols)


def choose_waves(clean_df, config, upto=None):
    """How many yearly waves the seasonal curve gets. A fixed number in config (seasonal_series.harmonics) is used as it
    is. With "auto" every allowed count is scored on all series together (AICc on the log-volume fit: it rewards a closer
    fit and charges for each extra fitted number, more heavily on short history) and the lowest score wins. A count is
    allowed only when there are at least min_months_per_number months per fitted number, so 12 months allow one wave, 18
    allow two and 24 allow three. `upto` keeps a backtest honest: only months up to it are looked at."""
    ss = config["analytics"]["forecast"]["seasonal_series"]
    if ss["harmonics"] != "auto":
        return int(ss["harmonics"])
    found = _history_series(clean_df, config, upto)
    if found is None:
        return int(ss["fallback_harmonics"])
    span, arrays = found
    n, best = len(span), None
    for k in range(1, int(ss["max_harmonics"]) + 1):
        p = 2 + 2 * k
        if n < ss["min_months_per_number"] * p or n - p - 1 <= 0:
            break
        X, score = _wave_columns(span, k), 0.0
        for y in arrays:
            ly = np.log(np.clip(y, 1.0, None))
            beta, *_ = np.linalg.lstsq(X, ly, rcond=None)
            rss = float(((ly - X @ beta) ** 2).sum())
            score += n * np.log(max(rss / n, 1e-12)) + 2 * p + 2 * p * (p + 1) / (n - p - 1)
        if best is None or score < best[0]:
            best = (score, k)
    return best[1] if best else int(ss["fallback_harmonics"])


def pooled_growth(clean_df, config, upto=None):
    """Monthly growth rate shared by every country and product: the slope of a straight-line regression on the log of
    the regional volume (every series added up after its own seasonal pattern is removed), so it is a steady percentage per
    month. One number from all series, so a single noisy series cannot invent a trend. None when no series has enough
    history. `upto` keeps a backtest honest."""
    found = _history_series(clean_df, config, upto)
    if found is None:
        return None
    span, arrays = found
    waves = choose_waves(clean_df, config, upto)
    total = np.zeros(len(span))
    for y in arrays:
        idx = harmonic_index(y, span, waves)
        total += y / np.array([idx[m.month] for m in span])
    if total.sum() <= 0:
        return None
    return float(np.polyfit(np.arange(len(span)), np.log(np.clip(total, 1.0, None)), 1)[0])


def overall_trend(clean_df, config, upto=None):
    """The two numbers the seasonal-growth forecast rests on, for display: the yearly waves chosen and the overall growth
    from the straight-line regression (per month and per year, as percentages). None when there is too little history."""
    g = pooled_growth(clean_df, config, upto)
    found = _history_series(clean_df, config, upto)
    if g is None or found is None:
        return None
    return {"waves": choose_waves(clean_df, config, upto), "months": len(found[0]), "series": len(found[1]),
            "monthly_growth_pct": round((np.exp(g) - 1) * 100, 2), "annual_growth_pct": round((np.exp(12 * g) - 1) * 100, 1)}


def _m_seasonal_growth(w, h, fc, months, season):
    """This series' own yearly pattern (learned from all its history), a recency-weighted level taken from the
    window, and one regional growth rate. Needs enough history of its own; otherwise None and the caller falls back."""
    ss, full = fc["seasonal_series"], months.get("full")
    if full is None or len(full) < ss["min_history_months"]:
        return None
    idx_map = harmonic_index(full.to_numpy(), full.index, months["waves"])
    idx = np.array([idx_map[m.month] for m in months["hist"]])
    fut_idx = np.array([idx_map[m.month] for m in months["future"]])
    des = np.asarray(w, dtype=float) / idx
    age = np.arange(len(des))[::-1]
    wt = 0.5 ** (age / ss["recency_half_life_months"])
    level, centre = float((des * wt).sum() / wt.sum()), float((age * wt).sum() / wt.sum())
    g = (months.get("growth") or 0.0) * ss["growth_share"]
    fitted = level * np.exp(g * (np.arange(len(des)) - (len(des) - 1) + centre)) * idx
    path = level * np.exp(g * (centre + np.arange(1, h + 1))) * fut_idx
    return fitted, path


MODEL_FUNCS = {"rolling_avg": _m_rolling, "linear_trend": _m_linear, "damped_trend": _m_damped,
               "seasonal_level": _m_seasonal_level, "seasonal_trend": _m_seasonal, "seasonal_growth": _m_seasonal_growth}
NEEDS_SEASON = ("seasonal_level", "seasonal_trend", "seasonal_growth", "blend")
NEEDS_GROWTH = ("seasonal_growth",)
# used until the seasonal pattern it needs exists; followed down the chain until a model can run
FALLBACK = {"seasonal_growth": "seasonal_level", "seasonal_level": "damped_trend", "seasonal_trend": "damped_trend"}


def selectable_models(config):
    """The models a planner may choose to forecast with. Models listed in forecast.comparison_only (the traditional
    rolling average) are still tested and drawn as a yardstick, but are not offered for planning."""
    fc = config["analytics"]["forecast"]
    return [m for m in fc["models"] if m not in fc.get("comparison_only", [])]


def seasonal_index(clean_df, config, upto=None):
    """Monthly seasonal factors (1..12 -> factor, mean 1.0) learned from every country and product pooled, or None
    when the pooled history is shorter than seasonal.min_pooled_months. The pooled monthly total is divided by its
    own straight-line trend, and each calendar month's ratios are averaged. `upto` keeps a backtest honest."""
    sc = config["analytics"]["forecast"]["seasonal"]
    if clean_df is None or clean_df.empty:
        return None
    d = clean_df if upto is None else clean_df[clean_df["YearMonth"] <= upto]
    tot = d.groupby("YearMonth")["Quantity"].sum().astype(float)
    if tot.empty:
        return None
    tot = tot.reindex(pd.date_range(tot.index.min(), tot.index.max(), freq="MS"), fill_value=0.0)
    if len(tot) < sc["min_pooled_months"]:
        return None
    x = np.arange(len(tot))
    slope, icpt = np.polyfit(x, tot.to_numpy(), 1)
    trend = np.clip(slope * x + icpt, 1e-9, None)
    ratio = pd.Series(tot.to_numpy() / trend, index=tot.index.month).groupby(level=0).mean()
    ratio = ratio.reindex(range(1, sc["period"] + 1)).fillna(1.0)
    return (ratio / ratio.mean()).to_dict()


def _series(clean_df, entity, country, product_line):
    if clean_df is None or clean_df.empty:
        return pd.Series(dtype=float)
    s = (clean_df[(clean_df["U_Entity"] == entity) & (clean_df["U_Country"] == country) &
                  (clean_df["U_ProductLine"] == product_line)]
         .groupby("YearMonth")["Quantity"].sum().astype(float))
    if s.empty:
        return s
    full = pd.date_range(s.index.min(), s.index.max(), freq="MS")
    return s.reindex(full, fill_value=0.0)


def generate_demand_forecast(clean_df, entity, country, product_line, config, model=None, lookback=None,
                             horizon=None):
    """DataFrame[period, actual, fitted, forecast, method_used]; empty below forecast.min_points. `model`,
    `lookback` and `horizon` are optional and default to config.analytics.forecast."""
    fc = config["analytics"]["forecast"]
    model = model or fc["default_model"]
    season = seasonal_index(clean_df, config) if model in NEEDS_SEASON else None
    growth = pooled_growth(clean_df, config) if model in NEEDS_GROWTH else None
    waves = choose_waves(clean_df, config) if model in NEEDS_GROWTH else None
    return forecast_series(_series(clean_df, entity, country, product_line), config, horizon=horizon, model=model,
                           lookback=lookback, season=season, growth=growth, waves=waves)


def forecast_series(s, config, horizon=None, model=None, lookback=None, season=None, growth=None, waves=None):
    """The forecast for any monthly series with the chosen model (entity-agnostic). Only the last `lookback`
    months are used to fit (6 to 12 in the app). method_used names the model that produced the numbers; seasonal_trend
    falls back to damped_trend, and says so, when no pooled seasonal pattern is available."""
    fc = config["analytics"]["forecast"]
    h, model = horizon or fc["horizon_months"], model or fc["default_model"]
    if len(s) < fc["min_points"]:
        return pd.DataFrame(columns=FORECAST_COLUMNS)          # caller shows "not enough history"
    w = _window(s, lookback or fc["lookback_months"])
    months = {"hist": list(w.index), "future": list(pd.date_range(s.index[-1] + pd.offsets.MonthBegin(1), periods=h, freq="MS")),
              "full": s, "growth": growth,
              "waves": waves or (fc["seasonal_series"]["harmonics"] if fc["seasonal_series"]["harmonics"] != "auto"
                                 else fc["seasonal_series"]["fallback_harmonics"])}
    method = model
    if model == "blend":
        parts = [MODEL_FUNCS[m](w.to_numpy(), h, fc, months, season) for m in fc["blend_of"]]
        parts = [p for p in parts if p is not None]
        fitted, fut = np.mean([p[0] for p in parts], axis=0), np.mean([p[1] for p in parts], axis=0)
    else:
        res, used = MODEL_FUNCS[model](w.to_numpy(), h, fc, months, season), model
        while res is None:                                    # not enough history for its own pattern: step down
            used = FALLBACK[used]
            res = MODEL_FUNCS[used](w.to_numpy(), h, fc, months, season)
        if used != model:
            method = f"{used} (no seasonal pattern yet)"
        fitted, fut = res
    clip = fc["clip_min"]
    fit_full = pd.Series(np.nan, index=s.index)
    fit_full.loc[w.index] = np.clip(fitted, clip, None)
    hist = pd.DataFrame({"period": s.index, "actual": s.values, "fitted": fit_full.values})
    new = pd.DataFrame({"period": months["future"], "forecast": np.clip(fut, clip, None)})
    out = pd.concat([hist, new], ignore_index=True)
    out["method_used"] = method
    return out[FORECAST_COLUMNS]

# ============================================================================ 4.4 churn radar
def _default_as_of(clean_df):
    """End of the latest month present in the data (history is generated in whole months)."""
    return clean_df["DocDate"].max().to_period("M").to_timestamp(how="end").normalize()


def _last_complete_month(as_of):
    """Month-start timestamp of the latest month fully elapsed on as_of."""
    return ((pd.Timestamp(as_of).normalize() + pd.Timedelta(days=1)).to_period("M") - 1).to_timestamp()


def churn_windows(clean_df, config, as_of=None):
    """(baseline_months, recent_months) as month-start DatetimeIndexes."""
    ch = config["analytics"]["churn"]
    as_of = pd.Timestamp(as_of) if as_of is not None else _default_as_of(clean_df)
    last = _last_complete_month(as_of)
    recent = pd.date_range(end=last, periods=ch["recent_window_months"], freq="MS")
    base_end = recent[0] - pd.offsets.MonthBegin(1) if ch.get("baseline_precedes_recent", True) else last
    baseline = pd.date_range(end=base_end, periods=ch["baseline_window_months"], freq="MS")
    return baseline, recent


def _band(delta, threshold):
    if delta < threshold:
        return "CHURN_RISK"
    return "DECLINING" if delta < 0 else "STABLE_OR_GROWING"


def calculate_churn_radar(clean_df, config, as_of=None):
    """Per customer: monthly average of the recent window vs the monthly average of the baseline window
    immediately before it. delta = (recent - baseline) / baseline. Returns every eligible customer with
    a 'flagged' column (delta < velocity_threshold), sorted by delta."""
    ch = config["analytics"]["churn"]
    if clean_df is None or clean_df.empty:
        return pd.DataFrame(columns=CHURN_COLUMNS)
    baseline, recent = churn_windows(clean_df, config, as_of)
    months = baseline.union(recent)
    df = clean_df[clean_df["YearMonth"] <= recent[-1]]
    monthly = df.groupby(["CardCode", "YearMonth"])["Quantity"].sum().astype(float).unstack(fill_value=0.0)
    monthly = monthly.reindex(columns=months.union(monthly.columns), fill_value=0.0)
    first_month = df.groupby("CardCode")["YearMonth"].min()
    info = df.sort_values("DocDate").groupby("CardCode")[["CardName", "U_Entity", "U_Country"]].last()
    rows = []
    for code, row in monthly.iterrows():
        if first_month[code] > months[0]:                       # insufficient history for both windows
            continue
        base_avg = float(row.reindex(baseline, fill_value=0.0).mean())
        rec_avg = float(row.reindex(recent, fill_value=0.0).mean())
        if base_avg <= 0 or base_avg < ch["min_baseline_monthly_volume"]:
            continue
        delta = (rec_avg - base_avg) / base_avg
        rows.append({"CardCode": code, **info.loc[code].to_dict(), "baseline_avg": round(base_avg, 2),
                     "recent_avg": round(rec_avg, 2), "delta": round(delta, 4),
                     "flagged": bool(delta < ch["velocity_threshold"]),
                     "risk_band": _band(delta, ch["velocity_threshold"])})
    if not rows:
        return pd.DataFrame(columns=CHURN_COLUMNS)
    return pd.DataFrame(rows)[CHURN_COLUMNS].sort_values("delta", kind="stable").reset_index(drop=True)


# ============================================================================ 4.4 cross-sell
def _ratio(a, b):
    return round(a / b, 4) if b else None


def _rule_stats(accounts, ante, cons):
    """support, confidence P(C | A or B) and lift over a set of active accounts."""
    n, both = len(accounts), ante & cons
    confidence = _ratio(len(both), len(ante))
    base = _ratio(len(cons), n)
    return {"accounts": n, "antecedent_accounts": len(ante), "consequent_accounts": len(cons),
            "both_accounts": len(both), "support": _ratio(len(both), n), "confidence": confidence,
            "lift": round(confidence / base, 4) if confidence is not None and base else None}


def calculate_cross_sell_opportunities(clean_df, config, as_of=None):
    """Accounts that bought any antecedent product in the last window_days and no consequent product
    in that window, plus rule statistics (support, confidence, lift) overall and per entity."""
    cs = config["analytics"]["cross_sell"]
    empty_stats = {"window_start": None, "window_end": None, "window_days": cs["window_days"],
                   "antecedent_products": cs["antecedent_products"], "consequent_product": cs["consequent_product"],
                   "overall": _rule_stats(set(), set(), set()), "by_entity": {}}
    if clean_df is None or clean_df.empty:
        return pd.DataFrame(columns=CROSS_SELL_COLUMNS), empty_stats
    end = pd.Timestamp(as_of).normalize() if as_of is not None else _default_as_of(clean_df)
    start = end - pd.Timedelta(days=cs["window_days"])
    w = clean_df[(clean_df["DocDate"] > start) & (clean_df["DocDate"] < end + pd.Timedelta(days=1))]
    is_ante = w["U_ProductLine"].isin(cs["antecedent_products"])
    is_cons = w["U_ProductLine"] == cs["consequent_product"]
    entity_of = w.groupby("CardCode")["U_Entity"].last()

    def sets(codes):
        return (set(codes), set(w.loc[is_ante & w["CardCode"].isin(codes), "CardCode"]),
                set(w.loc[is_cons & w["CardCode"].isin(codes), "CardCode"]))

    all_codes = set(w["CardCode"])
    overall = _rule_stats(*sets(all_codes))
    by_entity = {ent: _rule_stats(*sets(set(entity_of[entity_of == ent].index)))
                 for ent in sorted(entity_of.unique())}
    stats = {**empty_stats, "window_start": (start + pd.Timedelta(days=1)).date().isoformat(),
             "window_end": end.date().isoformat(), "overall": overall, "by_entity": by_entity}

    _, ante, cons = sets(all_codes)
    gaps = sorted(ante - cons)
    if not gaps:
        return pd.DataFrame(columns=CROSS_SELL_COLUMNS), stats
    a = w[is_ante & w["CardCode"].isin(gaps)].sort_values("DocDate")
    grouped = a.groupby("CardCode")
    out = pd.DataFrame({
        "CardName": grouped["CardName"].last(), "U_Entity": grouped["U_Entity"].last(),
        "U_Country": grouped["U_Country"].last(),
        "antecedent_products": grouped["U_ProductLine"].agg(lambda x: ", ".join(sorted(set(x)))),
        "last_antecedent_date": grouped["DocDate"].max().dt.date.astype(str),
        "antecedent_quantity": grouped["Quantity"].sum().astype(float),
    }).reset_index()
    out["entity_confidence"] = out["U_Entity"].map(lambda e: by_entity.get(e, {}).get("confidence"))
    out["entity_lift"] = out["U_Entity"].map(lambda e: by_entity.get(e, {}).get("lift"))
    return out[CROSS_SELL_COLUMNS].reset_index(drop=True), stats


# ============================================================================ helpers for the UI
def series_keys(clean_df):
    """Sorted (entity, country, product_line) tuples present in the cleaned data."""
    if clean_df is None or clean_df.empty:
        return []
    return sorted(clean_df[["U_Entity", "U_Country", "U_ProductLine"]].drop_duplicates().itertuples(index=False, name=None))


def aggregate_counts(clean_df, config, as_of=None):
    """Counts only (no account identities), for roles with AGGREGATE analytics access."""
    churn = calculate_churn_radar(clean_df, config, as_of)
    gaps, stats = calculate_cross_sell_opportunities(clean_df, config, as_of)
    flagged = churn[churn["flagged"]] if not churn.empty else churn
    return {"active_accounts": int(clean_df["CardCode"].nunique()) if not clean_df.empty else 0,
            "churn_flagged": int(len(flagged)), "churn_eligible": int(len(churn)),
            "churn_flagged_by_entity": {k: int(v) for k, v in flagged.groupby("U_Entity").size().items()}
            if not flagged.empty else {},
            "cross_sell_gaps": int(len(gaps)),
            "cross_sell_confidence_overall": stats["overall"]["confidence"],
            "cross_sell_lift_overall": stats["overall"]["lift"]}


# ============================================================================ regional business view
def _months_ending(last, n):
    return pd.date_range(end=last, periods=n, freq="MS")


def _period_text(months):
    return months[0].date().isoformat(), (months[-1] + pd.offsets.MonthEnd(0)).date().isoformat()


def _growth(cur, prior):
    return round((cur - prior) / prior * 100.0, 1) if prior else None


def summarize_regional_volumes(clean_df, config, as_of=None):
    """Confirmed-PO quantity and LineTotal_USD per country x product line for the last window_months complete
    months and for the same months prior_offset_months earlier. growth_pct = (current - prior) / prior * 100,
    None when the prior is zero. Returns (DataFrame, period dict with exact dates as text)."""
    rv, an = config["analytics"]["regional_view"], config["analytics"]
    countries = next(f["options"] for f in config["domains"]["CUSTOMER"]["fields"] if f["field"] == "U_Country")
    lines = list(an["product_lines"])
    cols = ["U_Country", "country", "U_ProductLine", "product", "current_qty", "prior_qty", "growth_pct",
            "new_account_qty", "growth_excl_new_pct", "current_usd", "prior_usd", "usd_growth_pct"]
    if clean_df is None or clean_df.empty:
        return pd.DataFrame(columns=cols), {}
    as_of = pd.Timestamp(as_of) if as_of is not None else _default_as_of(clean_df)
    current = _months_ending(_last_complete_month(as_of), rv["window_months"])
    prior = current - pd.DateOffset(months=rv["prior_offset_months"])
    cs, ce = _period_text(current)
    ps, pe = _period_text(prior)
    period = {"current_start": cs, "current_end": ce, "prior_start": ps, "prior_end": pe,
              "window_months": rv["window_months"], "prior_offset_months": rv["prior_offset_months"],
              "prior_complete": bool(prior[0] >= clean_df["YearMonth"].min()),
              "definition": rv["growth_definition"]}

    def totals(months):
        sel = clean_df[clean_df["YearMonth"].isin(months)]
        return sel.groupby(["U_Country", "U_ProductLine"])[["Quantity", "LineTotal_USD"]].sum()

    cur, pri = totals(current), totals(prior)
    # Volume from accounts with no prior-period volume in the same pair (new accounts) is reported separately,
    # so growth driven by onboarding is visible next to like-for-like growth.
    by_acct = lambda months: clean_df[clean_df["YearMonth"].isin(months)].groupby(  # noqa: E731
        ["U_Country", "U_ProductLine", "CardCode"])["Quantity"].sum()
    ca, pa = by_acct(current), by_acct(prior)
    new = ca[~ca.index.isin(pa.index)].groupby(level=[0, 1]).sum()
    rows = []
    for c in countries:
        for p in lines:
            cq, cu = (float(cur.loc[(c, p), "Quantity"]), float(cur.loc[(c, p), "LineTotal_USD"])) if (c, p) in cur.index else (0.0, 0.0)
            pq, pu = (float(pri.loc[(c, p), "Quantity"]), float(pri.loc[(c, p), "LineTotal_USD"])) if (c, p) in pri.index else (0.0, 0.0)
            nq = float(new.get((c, p), 0.0))
            rows.append({"U_Country": c, "country": rv["country_labels"].get(c, c), "U_ProductLine": p,
                         "product": an["product_lines"][p], "current_qty": cq, "prior_qty": pq,
                         "growth_pct": _growth(cq, pq), "new_account_qty": nq,
                         "growth_excl_new_pct": _growth(cq - nq, pq), "current_usd": round(cu, 2), "prior_usd": round(pu, 2),
                         "usd_growth_pct": _growth(cu, pu)})
    return pd.DataFrame(rows, columns=cols), period


def _windows(clean_df, config, as_of=None):
    """(current months, prior months) exactly as the regional view uses them."""
    rv = config["analytics"]["regional_view"]
    as_of = pd.Timestamp(as_of) if as_of is not None else _default_as_of(clean_df)
    current = _months_ending(_last_complete_month(as_of), rv["window_months"])
    return current, current - pd.DateOffset(months=rv["prior_offset_months"])


def account_status(clean_df, config, as_of=None):
    """'New' or 'Existing' for every customer x country x product line: New when the customer bought nothing in that
    country and product in the same months a year earlier (the regional view's definition of a new account), Existing
    otherwise. Returns a Series indexed by (CardCode, U_Country, U_ProductLine)."""
    keys = ["CardCode", "U_Country", "U_ProductLine"]
    if clean_df is None or clean_df.empty:
        return pd.Series(dtype=object, index=pd.MultiIndex.from_arrays([[], [], []], names=keys))
    _, prior = _windows(clean_df, config, as_of)
    had = set(map(tuple, clean_df[clean_df["YearMonth"].isin(prior)][keys].drop_duplicates().to_numpy()))
    every = clean_df[keys].drop_duplicates()
    return pd.Series(["Existing" if tuple(k) in had else "New" for k in every.to_numpy()],
                     index=pd.MultiIndex.from_frame(every))


def summarize_new_vs_existing(clean_df, config, as_of=None):
    """Per country x product line: current-window units and USD from existing and from new customers, and the prior
    window's units and USD (all of which came from customers who were then buying). Companion to
    summarize_regional_volumes, adding the USD split that table does not have."""
    countries = next(f["options"] for f in config["domains"]["CUSTOMER"]["fields"] if f["field"] == "U_Country")
    cols = ["U_Country", "U_ProductLine", "existing_qty", "new_qty", "prior_qty", "existing_usd", "new_usd", "prior_usd"]
    if clean_df is None or clean_df.empty:
        return pd.DataFrame(columns=cols)
    current, prior = _windows(clean_df, config, as_of)
    status = account_status(clean_df, config, as_of)
    cur = clean_df[clean_df["YearMonth"].isin(current)].copy()
    cur["status"] = status.reindex(pd.MultiIndex.from_frame(cur[["CardCode", "U_Country", "U_ProductLine"]])).to_numpy()
    pri = clean_df[clean_df["YearMonth"].isin(prior)]
    g = cur.groupby(["U_Country", "U_ProductLine", "status"])[["Quantity", "LineTotal_USD"]].sum()
    p = pri.groupby(["U_Country", "U_ProductLine"])[["Quantity", "LineTotal_USD"]].sum()
    rows = []
    for c in countries:
        for line in config["analytics"]["product_lines"]:
            def pick(state, col):
                return float(g.loc[(c, line, state), col]) if (c, line, state) in g.index else 0.0
            rows.append({"U_Country": c, "U_ProductLine": line,
                         "existing_qty": pick("Existing", "Quantity"), "new_qty": pick("New", "Quantity"),
                         "prior_qty": float(p.loc[(c, line), "Quantity"]) if (c, line) in p.index else 0.0,
                         "existing_usd": round(pick("Existing", "LineTotal_USD"), 2), "new_usd": round(pick("New", "LineTotal_USD"), 2),
                         "prior_usd": round(float(p.loc[(c, line), "LineTotal_USD"]), 2) if (c, line) in p.index else 0.0})
    return pd.DataFrame(rows, columns=cols)


def summarize_request_types(raw_df, config, include=None):
    """Rows, quantity and USD value per category: confirmed POs used in the forecast, confirmed POs whose status
    is outside noise_filter.status_in (pending MDM setup or rejected), and every other RecordType (enquiries,
    draft POs, cancelled POs). Categories come from noise_filter; labels from regional_view.request_type_labels."""
    nf, labels = config["analytics"]["noise_filter"], config["analytics"]["regional_view"]["request_type_labels"]
    cols = ["category", "label", "used_in_forecast", "statuses", "rows", "quantity", "value_usd", "share_of_rows_pct"]
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=cols)
    df = raw_df.assign(category=order_categories(raw_df, config))
    g = df.groupby("category")
    out = pd.DataFrame({"rows": g.size(), "quantity": g["Quantity"].sum().astype(float),
                        "value_usd": g["LineTotal_USD"].sum().round(2),
                        "statuses": g["U_ReqStatus"].agg(lambda x: ", ".join(sorted(set(map(str, x)))))}).reset_index()
    out["label"] = out["category"].map(lambda c: labels.get(c, c))
    out["used_in_forecast"] = out["category"].isin(default_include(config) if include is None else list(include))
    out["share_of_rows_pct"] = (out["rows"] / len(raw_df) * 100).round(1)
    order = list(labels)
    out["_o"] = out["category"].map(lambda c: order.index(c) if c in order else len(order))
    return out.sort_values(["_o", "category"]).drop(columns="_o")[cols].reset_index(drop=True)


def _wape(actual, pred):
    total = float(np.abs(actual).sum())
    return round(float(np.abs(actual - pred).sum()) / total * 100.0, 1) if total else None


def _model_label(config, model):
    return config["analytics"]["forecast"]["labels"].get(model, model)


def backtest_models(clean_df, config, lookback=None):
    """Every forecast model on the same test. Per country x product series (summed across entities) the last
    backtest_holdout_months are hidden, each model is fitted on the previous `lookback` months only (6 to 12), and its
    forecast is compared with what actually happened. The traditional flat rolling averages
    (baseline_windows_months) are scored on the same months. WAPE in % (lower is better); nothing is tuned.
    Returns (DataFrame with one row per series, summary dict)."""
    rv, an = config["analytics"]["regional_view"], config["analytics"]
    fcfg = an["forecast"]
    h, windows, models = rv["backtest_holdout_months"], rv["baseline_windows_months"], fcfg["models"]
    lookback = lookback or fcfg["lookback_months"]
    base_cols = [f"wape_avg{w}m" for w in windows]
    cols = ["U_Country", "country", "U_ProductLine", "product", "train_months", "holdout_qty"] \
        + [f"wape_{m}" for m in models] + base_cols
    summary = {"holdout_months": h, "lookback_months": lookback, "baseline_windows_months": windows, "series": 0,
               "skipped": [], "overall_wape": {}, "overall_wape_baseline": {}, "wins": {}, "best_model": None}
    if clean_df is None or clean_df.empty:
        return pd.DataFrame(columns=cols), summary
    last = clean_df["YearMonth"].max()
    cut = last - pd.offsets.MonthBegin(h)                                              # learned from training months only
    season, growth = seasonal_index(clean_df, config, upto=cut), pooled_growth(clean_df, config, upto=cut)
    waves = choose_waves(clean_df, config, upto=cut)
    summary["waves"], summary["monthly_growth"] = waves, growth
    countries = next(f["options"] for f in config["domains"]["CUSTOMER"]["fields"] if f["field"] == "U_Country")
    rows, actual_all = [], []
    pred_all = {m: [] for m in models} | {c: [] for c in base_cols}
    for c in countries:
        for p in an["product_lines"]:
            s = clean_df[(clean_df["U_Country"] == c) & (clean_df["U_ProductLine"] == p)].groupby("YearMonth")[
                "Quantity"].sum().astype(float)
            if s.empty:
                summary["skipped"].append(f"{c}/{p}: no data")
                continue
            s = s.reindex(pd.date_range(s.index.min(), last, freq="MS"), fill_value=0.0)
            train, test = s.iloc[:-h], s.iloc[-h:]
            if len(test) < h or len(train) < max(fcfg["min_points"], lookback, max(windows)):
                summary["skipped"].append(f"{c}/{p}: {len(train)} training months")
                continue
            a = test.to_numpy()
            row = {"U_Country": c, "country": rv["country_labels"].get(c, c), "U_ProductLine": p,
                   "product": an["product_lines"][p], "train_months": len(train), "holdout_qty": float(a.sum())}
            for m in models:
                fc = forecast_series(train, config, horizon=h, model=m, lookback=lookback, season=season, growth=growth,
                                     waves=waves)
                pred = fc["forecast"].dropna().to_numpy()
                row[f"wape_{m}"] = _wape(a, pred)
                pred_all[m].append(pred)
            for w, col in zip(windows, base_cols):
                flat = np.full(h, float(train.iloc[-w:].mean()))
                row[col] = _wape(a, flat)
                pred_all[col].append(flat)
            actual_all.append(a)
            rows.append(row)
    df = pd.DataFrame(rows, columns=cols)
    if rows:
        a = np.concatenate(actual_all)
        span = _period_text(_months_ending(last, h))
        summary.update({"series": len(rows), "holdout_start": span[0], "holdout_end": span[1]})
        for m in models:
            summary["overall_wape"][m] = _wape(a, np.concatenate(pred_all[m]))
        for col in base_cols:
            summary["overall_wape_baseline"][col] = _wape(a, np.concatenate(pred_all[col]))
        ds = selectable_models(config)
        summary["best_model"] = min(ds, key=lambda m: summary["overall_wape"][m])
        best_base = min(summary["overall_wape_baseline"], key=lambda k: summary["overall_wape_baseline"][k])
        summary["best_baseline"] = best_base
        for m in models:
            summary["wins"][m] = {col: int((df[f"wape_{m}"] < df[col]).sum()) for col in base_cols}
    return df, summary


def backtest_matrix(clean_df, config):
    """Overall WAPE of every model at every history length (config forecast.lookback_options), plus the traditional
    rolling averages. Rows are models and baselines, columns are months of history used. Returns
    (DataFrame, {lookback: summary})."""
    fcfg, lookbacks = config["analytics"]["forecast"], config["analytics"]["forecast"]["lookback_options"]
    runs = {L: backtest_models(clean_df, config, lookback=L)[1] for L in lookbacks}
    rows = {_model_label(config, m): {L: runs[L]["overall_wape"].get(m) for L in lookbacks} for m in fcfg["models"]}
    for w in config["analytics"]["regional_view"]["baseline_windows_months"]:
        rows[f"Traditional: {w}-month average"] = {L: runs[L]["overall_wape_baseline"].get(f"wape_avg{w}m") for L in lookbacks}
    return pd.DataFrame(rows).T.rename(columns={L: f"{L} months of history" for L in lookbacks}), runs


def backtest_forecast_vs_baseline(clean_df, config):
    """The headline comparison: the most accurate data-science model (at the configured history length) against the
    traditional rolling averages, in the shape the dashboard tiles and the recommended actions use. Returns
    (DataFrame, summary dict). Every model is in backtest_models(); this picks the winner."""
    df, s = backtest_models(clean_df, config)
    fcfg = config["analytics"]["forecast"]
    base_cols = [f"wape_avg{w}m" for w in s["baseline_windows_months"]]
    out = {"holdout_months": s["holdout_months"], "baseline_windows_months": s["baseline_windows_months"],
           "series": s["series"], "skipped": s["skipped"], "overall_wape_model": None, "overall_wape_baseline": {},
           "improvement_pct": {}, "model_wins": {}, "lookback_months": s["lookback_months"]}
    if not s["series"]:
        return pd.DataFrame(columns=["U_Country", "country", "U_ProductLine", "product", "train_months", "method_used",
                                     "holdout_qty", "wape_model"] + base_cols), out
    best = s["best_model"]
    m = s["overall_wape"][best]
    d = df[["U_Country", "country", "U_ProductLine", "product", "train_months", "holdout_qty"]].copy()
    d.insert(5, "method_used", best)
    d["wape_model"] = df[f"wape_{best}"]
    for col in base_cols:
        d[col] = df[col]
    out.update({"holdout_start": s["holdout_start"], "holdout_end": s["holdout_end"], "overall_wape_model": m,
                "model_id": best, "model_label": _model_label(config, best), "methods_used": {best: len(d)},
                "overall_wape_baseline": dict(s["overall_wape_baseline"]), "model_not_better": {}})
    for col in base_cols:
        b = s["overall_wape_baseline"][col]
        out["improvement_pct"][col] = round((b - m) / b * 100.0, 1) if b and m is not None else None
        out["model_wins"][col] = int((d["wape_model"] < d[col]).sum())
        out["model_not_better"][col] = [f"{r.U_Country}/{r.U_ProductLine}" for r in d.itertuples()
                                        if not r.wape_model < getattr(r, col)]
    return d, out


def backtest_rolling(clean_df, config):
    """The same backtest repeated with the test window moved back in steps (config analytics.rolling_backtest), each time
    fitting only on months before that window. Shows whether the headline error holds across several windows rather
    than one. Returns (DataFrame with one row per window and method: window, holdout_start, holdout_end, series, name,
    kind ('model' or 'baseline'), wape; summary dict with the headline model, the one that is best on the latest window)."""
    rb, fcfg = config["analytics"]["rolling_backtest"], config["analytics"]["forecast"]
    cols = ["window", "holdout_start", "holdout_end", "series", "name", "kind", "wape"]
    summary = {"headline_model": None, "windows": 0}
    if clean_df is None or clean_df.empty:
        return pd.DataFrame(columns=cols), summary
    last, rows = clean_df["YearMonth"].max(), []
    for k in range(rb["origins"]):
        part = clean_df[clean_df["YearMonth"] <= last - pd.DateOffset(months=k * rb["origin_step_months"])]
        _, s = backtest_models(part, config)
        if not s["series"]:
            continue
        summary["windows"] += 1
        if summary["headline_model"] is None:
            summary["headline_model"] = s["best_model"]
        meta = {"window": f"{s['holdout_start'][:7]} to {s['holdout_end'][:7]}", "holdout_start": s["holdout_start"],
                "holdout_end": s["holdout_end"], "series": s["series"]}
        rows += [{**meta, "name": _model_label(config, m), "kind": "model", "wape": s["overall_wape"][m]} for m in fcfg["models"]]
        rows += [{**meta, "name": f"Traditional: {w}-month average", "kind": "baseline",
                  "wape": s["overall_wape_baseline"][f"wape_avg{w}m"]} for w in s["baseline_windows_months"]]
    return pd.DataFrame(rows, columns=cols).sort_values("holdout_start").reset_index(drop=True), summary


def classify_order_patterns(clean_df, config):
    """How each customer buys each product line (Syntetos-Boylan): ADI is the average gap in months between months with
    an order; CV2 is the squared coefficient of variation of the monthly quantity. Regular timing (ADI below the cutoff)
    with steady size is SMOOTH, with varying size ERRATIC; irregular timing is INTERMITTENT (steady size) or LUMPY.
    Pairs with fewer than min_orders order months are TOO_FEW. Returns one row per customer x product line."""
    op, rv = config["analytics"]["order_pattern"], config["analytics"]["regional_view"]
    cols = ["CardCode", "U_Country", "country", "U_ProductLine", "order_months", "adi", "cv2", "pattern", "group", "units", "usd"]
    if clean_df is None or clean_df.empty:
        return pd.DataFrame(columns=cols)
    kinds = {(True, True): "SMOOTH", (True, False): "ERRATIC", (False, True): "INTERMITTENT", (False, False): "LUMPY"}
    rows = []
    for (card, country, line), g in clean_df.groupby(["CardCode", "U_Country", "U_ProductLine"]):
        m = g.groupby("YearMonth")["Quantity"].sum()
        m = m[m > 0]
        adi = cv2 = None
        pattern = "TOO_FEW"
        if len(m) >= op["min_orders"]:
            stamp = np.sort((m.index.year * 12 + m.index.month).to_numpy())
            adi, cv2 = float(np.diff(stamp).mean()), float((m.std(ddof=0) / m.mean()) ** 2)
            pattern = kinds[(adi < op["adi_cutoff"], cv2 < op["cv2_cutoff"])]
        rows.append({"CardCode": card, "U_Country": country, "country": rv["country_labels"].get(country, country),
                     "U_ProductLine": line, "order_months": len(m), "adi": adi, "cv2": cv2, "pattern": pattern,
                     "group": op["group_of"][pattern], "units": float(m.sum()), "usd": float(g["LineTotal_USD"].sum())})
    return pd.DataFrame(rows, columns=cols)


def summarize_order_patterns(pairs, config):
    """Aggregates only (no customer ids): per pattern, the number of customer-product pairs and their share of units and
    USD; and units per country and group. Returns (by_pattern, by_country)."""
    op = config["analytics"]["order_pattern"]
    by_pattern = pairs.groupby("pattern").agg(pairs=("CardCode", "size"), units=("units", "sum"), usd=("usd", "sum")) \
        .reindex(list(op["labels"])).fillna(0.0).reset_index()
    by_pattern["label"] = by_pattern["pattern"].map(op["labels"])
    by_pattern["group"] = by_pattern["pattern"].map(op["group_of"])
    for c in ("pairs", "units", "usd"):
        by_pattern[f"{c}_share_pct"] = (by_pattern[c] / by_pattern[c].sum() * 100).round(1) if by_pattern[c].sum() else 0.0
    by_country = pairs.groupby(["country", "group"])["units"].sum().reset_index()
    return by_pattern, by_country


def build_model_observations(matrix, runs, config):
    """Plain-English observations written only from the model comparison (backtest_matrix output). Every figure is
    computed; nothing is asserted that the numbers do not show."""
    fcfg = config["analytics"]["forecast"]
    default_L = fcfg["lookback_months"]
    s = runs.get(default_L) or (next(iter(runs.values())) if runs else None)
    if not s or not s["series"]:
        return ["Not enough history to compare the models."]
    lab = lambda m: _model_label(config, m)                                    # noqa: E731
    out, ow, base = [], s["overall_wape"], s["overall_wape_baseline"]
    best = s["best_model"]
    bb = s["best_baseline"]
    bb_name = f"{bb.replace('wape_avg', '').replace('m', '')}-month average"
    gain = (base[bb] - ow[best]) / base[bb] * 100 if base[bb] else 0
    if gain > 0:
        out.append(f"Recommendation: stop planning with the {bb_name} and use {lab(best)} instead. It is kept below only as "
                   f"the yardstick the models are measured against.")
    else:
        out.append(f"Recommendation: no model beat the {bb_name} on this data, so keep it until one does.")
    out.append(f"Most accurate: {lab(best)} using {s['lookback_months']} months of history, with an average error of "
               f"{ow[best]:.1f}% on the last {s['holdout_months']} months ({s['holdout_start']} to {s['holdout_end']}), "
               f"against {base[bb]:.1f}% for the traditional {bb_name}. That is {gain:.0f}% "
               f"{'better' if gain >= 0 else 'worse'}.")
    ranked = sorted(selectable_models(config), key=lambda m: ow[m])
    out.append("Ranking at that history length: " + "; ".join(f"{lab(m)} {ow[m]:.1f}%" for m in ranked) + f". "
               f"Rolling average with the same history: {ow['rolling_avg']:.1f}%.")
    wins = s["wins"][best][bb]
    out.append(f"{lab(best)} beat the {bb_name} on {wins} of {s['series']} country and product series.")
    by_L = {L: r["overall_wape"][best] for L, r in runs.items() if r["series"]}
    if len(by_L) > 1:
        bestL = min(by_L, key=by_L.get)
        out.append("History length for " + lab(best) + ": " + ", ".join(f"{L} months {v:.1f}%" for L, v in by_L.items())
                   + f". The lowest error came from {bestL} months of history.")
    if "seasonal_trend" in ow and "damped_trend" in ow:
        d = ow["damped_trend"] - ow["seasonal_trend"]
        out.append(f"Seasonality {'helped' if d > 0 else 'did not help'}: the seasonal model's error was "
                   f"{ow['seasonal_trend']:.1f}% against {ow['damped_trend']:.1f}% for the same model without a seasonal "
                   f"pattern ({abs(d):.1f} points {'lower' if d > 0 else 'higher'}).")
    if "blend" in ow:
        singles = [ow[m] for m in fcfg["blend_of"]]
        out.append(f"The blend scored {ow['blend']:.1f}%, between the best ({min(singles):.1f}%) and worst ({max(singles):.1f}%) "
                   f"of the models it averages: it trades a little accuracy for steadiness.")
    out.append("The demo history is generated with a trend and a yearly pattern, so real results may differ. Re-run the "
               "comparison on real SAP data before relying on any model.")
    return out

def _new_note(row):
    if not row["new_account_qty"]:
        return ""
    excl = row["growth_excl_new_pct"]
    return (f"; {row['new_account_qty']:,.0f} of those units come from accounts with no prior-period volume"
            + (f", so like-for-like growth is {excl:+.1f}%" if excl is not None else ""))


def build_recommended_actions(results, config):
    """Plain-English actions built only from computed results. results keys (each optional): 'regional'
    (DataFrame, period), 'request_types' DataFrame, 'backtest' (DataFrame, summary), 'churn' DataFrame,
    'cross_sell' (DataFrame, stats)."""
    an, out = config["analytics"], []
    if "regional" in results:
        df, period = results["regional"]
        g = df.dropna(subset=["growth_pct"])
        span = f"{period.get('current_start')} to {period.get('current_end')} vs {period.get('prior_start')} to " \
               f"{period.get('prior_end')}"
        if not g.empty:
            top, low = g.loc[g["growth_pct"].idxmax()], g.loc[g["growth_pct"].idxmin()]
            out.append(f"Grow: {top['product']} (Product {top['U_ProductLine']}) in {top['country']} is the "
                       f"fastest-growing pair at {top['growth_pct']:+.1f}% year over year ({top['prior_qty']:,.0f} -> "
                       f"{top['current_qty']:,.0f} units, {span}){_new_note(top)}. Secure stock and sales coverage there first.")
            out.append(f"Investigate: {low['product']} (Product {low['U_ProductLine']}) in {low['country']} is the "
                       f"weakest pair at {low['growth_pct']:+.1f}% ({low['prior_qty']:,.0f} -> {low['current_qty']:,.0f} "
                       f"units){_new_note(low)}. Review pricing, competition and account coverage for this pair.")
        none = df[df["growth_pct"].isna()]
        if not none.empty:
            out.append(f"{len(none)} country/product pair(s) had no prior-period volume, so growth is not defined: "
                       + ", ".join(f"{r.country}/{r.U_ProductLine}" for r in none.itertuples()) + ".")
    if "churn" in results and results["churn"] is not None and not results["churn"].empty:
        ch, n = an["churn"], int(results["churn"]["flagged"].sum())
        out.append(f"Retain: {n} account(s) are on the Churn Radar (last {ch['recent_window_months']}-month average "
                   f"more than {abs(ch['velocity_threshold']):.0%} below the prior {ch['baseline_window_months']}-month "
                   f"average). Assign each to an account manager for a call this month.")
    if "cross_sell" in results:
        gaps, stats = results["cross_sell"]
        cs, lines = an["cross_sell"], an["product_lines"]
        conf = stats.get("overall", {}).get("confidence")
        out.append(f"Cross-sell: {len(gaps)} account(s) bought {' or '.join(lines[p] for p in cs['antecedent_products'])} "
                   f"in the last {cs['window_days']} days but no {lines[cs['consequent_product']]}"
                   + (f"; across all accounts {conf:.0%} of antecedent buyers also bought it" if conf is not None else "")
                   + f". Offer a bundled {lines[cs['consequent_product']].lower()} quote to these accounts.")
    if "backtest" in results:
        bdf, s = results["backtest"]
        if s.get("overall_wape_model") is not None:
            base = s["overall_wape_baseline"]
            best = min(base, key=lambda k: base[k])
            wins = s["model_wins"][best]
            comp = ", ".join(f"{base[k]:.1f}% ({k.replace('wape_avg', '').replace('m', '')}-month average)" for k in base)
            if s["overall_wape_model"] < base[best]:
                out.append(f"Stop planning with the rolling average and plan with the model: on a {s['holdout_months']}-month backtest ({s['holdout_start']} to "
                           f"{s['holdout_end']}, {s['series']} series) the {s.get('model_label', 'best')} forecast had an overall average error of "
                           f"{s['overall_wape_model']:.1f}% vs {comp}, {s['improvement_pct'][best]:.1f}% better than "
                           f"the best rolling average. It won on {wins} of {s['series']} series; review the others "
                           f"({', '.join(s['model_not_better'][best]) or 'none'}) by hand before relying on it there.")
            else:
                out.append(f"Keep the rolling average for now: on a {s['holdout_months']}-month backtest the model's "
                           f"overall average error was {s['overall_wape_model']:.1f}% vs {comp}. It won on {wins} of "
                           f"{s['series']} series only.")
    if "request_types" in results and not results["request_types"].empty:
        rt = results["request_types"]
        excluded = rt[~rt["used_in_forecast"]]
        parts = ", ".join(f"{int(r.rows)} {r.label.split(' (')[0]}" for r in excluded.itertuples())
        out.append(f"Data quality: {int(excluded['rows'].sum())} of {int(rt['rows'].sum())} order-history rows "
                   f"({excluded['rows'].sum() / rt['rows'].sum():.1%}) are excluded from every forecast ({parts}). "
                   f"MDM master-data setup requests are a separate workflow and never count as orders.")
    return out
