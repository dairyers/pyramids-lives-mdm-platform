"""Tests for engine_analytics.py (spec Section 4) on the generated data files."""
import json
import pathlib

import pandas as pd
import pytest

import engine_analytics as ea
from fixtures import CONFIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = pd.read_csv(ROOT / "data" / "regional_orders.csv")
CHECKS = json.loads((ROOT / "data" / "expected_analytics_checks.json").read_text(encoding="utf-8"))
AN = CONFIG["analytics"]
CLEAN = ea.clean_transaction_data(RAW, CONFIG)
AS_OF = CHECKS["as_of"]


# ----------------------------------------------------------------------------- cleaning
def test_noise_rows_removed():
    nf = AN["noise_filter"]
    keep = RAW[(RAW["RecordType"] == nf["record_type"]) & RAW["U_ReqStatus"].isin(nf["status_in"])]
    assert len(CLEAN) == len(keep) < len(RAW)
    assert set(CLEAN["RecordType"]) == {nf["record_type"]}
    assert set(CLEAN["U_ReqStatus"]) <= set(nf["status_in"])
    noise = RAW[~RAW.index.isin(keep.index)]
    assert {"INQUIRY", "DRAFT_PO", "CANCELLED_PO"} <= set(noise["RecordType"])
    assert {"PENDING", "REJECTED"} <= set(noise.loc[noise["RecordType"] == nf["record_type"], "U_ReqStatus"])


def test_default_selection_matches_the_noise_filter():
    same = ea.clean_transaction_data(RAW, CONFIG, include=ea.default_include(CONFIG))
    assert len(same) == len(CLEAN)


def test_ticking_more_kinds_of_rows_brings_them_into_the_data():
    kinds = set(ea.order_categories(RAW, CONFIG))
    assert {"CONFIRMED_USED", "CONFIRMED_NOT_READY", "INQUIRY"} <= kinds
    wider = ea.clean_transaction_data(RAW, CONFIG, include=["CONFIRMED_USED", "INQUIRY"])
    assert len(wider) == len(CLEAN) + int((RAW["RecordType"] == "INQUIRY").sum())
    table = ea.summarize_request_types(RAW, CONFIG, include=["CONFIRMED_USED", "INQUIRY"])
    assert set(table.loc[table["used_in_forecast"], "category"]) == {"CONFIRMED_USED", "INQUIRY"}


def test_year_month_is_month_start():
    assert pd.api.types.is_datetime64_any_dtype(CLEAN["DocDate"])
    assert (CLEAN["YearMonth"].dt.day == 1).all()
    assert (CLEAN["YearMonth"] == CLEAN["DocDate"].dt.to_period("M").dt.to_timestamp()).all()


def test_empty_input_does_not_raise():
    empty = ea.clean_transaction_data(RAW.iloc[0:0], CONFIG)
    assert empty.empty
    assert ea.generate_demand_forecast(empty, "SPD", "SG", "A", CONFIG).empty
    assert ea.calculate_churn_radar(empty, CONFIG).empty
    gaps, stats = ea.calculate_cross_sell_opportunities(empty, CONFIG)
    assert gaps.empty and stats["overall"]["confidence"] is None


# ----------------------------------------------------------------------------- forecast
@pytest.mark.parametrize("model", AN["forecast"]["models"])
@pytest.mark.parametrize("lookback", AN["forecast"]["lookback_options"])
def test_every_model_forecasts_from_six_to_twelve_months(model, lookback):
    fc = AN["forecast"]
    out = ea.generate_demand_forecast(CLEAN, "SPD", "SG", "A", CONFIG, model=model, lookback=lookback)
    assert list(out.columns) == ea.FORECAST_COLUMNS
    hist, fut = out[out["actual"].notna()], out[out["forecast"].notna()]
    assert len(fut) == fc["horizon_months"] and set(out["method_used"]) == {model}
    assert (fut["forecast"] >= fc["clip_min"]).all() and fut["period"].min() > hist["period"].max()
    assert hist["fitted"].notna().sum() == min(lookback, len(hist))          # fitted only over the months the model used
    assert fut["forecast"].notna().all() and fut["forecast"].isna().sum() == 0


@pytest.mark.parametrize("horizon", AN["forecast"]["horizon_options"])
def test_horizon_is_selectable(horizon):
    out = ea.generate_demand_forecast(CLEAN, "SPD", "SG", "A", CONFIG, horizon=horizon)
    assert out["forecast"].notna().sum() == horizon


def test_default_model_comes_from_config():
    out = ea.generate_demand_forecast(CLEAN, "SPD", "SG", "A", CONFIG)
    assert set(out["method_used"]) == {AN["forecast"]["default_model"]}
    assert AN["forecast"]["default_model"] in AN["forecast"]["models"]


def test_models_use_only_the_chosen_window_of_history():
    """Changing months that fall outside the window must not change the forecast; changing months inside it must."""
    s = ea._series(CLEAN, "SPD", "SG", "A")
    base = ea.forecast_series(s, CONFIG, model="damped_trend", lookback=6)["forecast"].dropna().to_numpy()
    old = s.copy()
    old.iloc[0] *= 5                                                      # a month far outside the 6-month window
    assert ea.forecast_series(old, CONFIG, model="damped_trend", lookback=6)["forecast"].dropna().to_numpy() == pytest.approx(base)
    recent = s.copy()
    recent.iloc[-1] *= 5
    assert not ea.forecast_series(recent, CONFIG, model="damped_trend", lookback=6)["forecast"].dropna().to_numpy() == pytest.approx(base)


def test_seasonal_pattern_is_pooled_normalised_and_needs_enough_history():
    idx = ea.seasonal_index(CLEAN, CONFIG)
    assert sorted(idx) == list(range(1, 13)) and sum(idx.values()) / 12 == pytest.approx(1.0)
    assert ea.seasonal_index(CLEAN[CLEAN["YearMonth"] < pd.Timestamp("2025-06-01")], CONFIG) is None   # fewer than 12 months
    cut = CLEAN["YearMonth"].max() - pd.offsets.MonthBegin(6)
    assert ea.seasonal_index(CLEAN, CONFIG, upto=cut) != idx                # a backtest learns from training months only


def test_seasonal_models_say_so_when_no_pattern_exists():
    s = ea._series(CLEAN, "SPD", "SG", "A")
    out = ea.forecast_series(s, CONFIG, model="seasonal_level", season=None)
    assert out["method_used"].iloc[0] == "damped_trend (no seasonal pattern yet)"     # rolling average is no longer a fallback
    out = ea.forecast_series(s, CONFIG, model="seasonal_trend", season=None)
    assert out["method_used"].iloc[0] == "damped_trend (no seasonal pattern yet)"


def test_rolling_average_is_a_yardstick_not_a_choice():
    assert "rolling_avg" in AN["forecast"]["models"] and "rolling_avg" not in ea.selectable_models(CONFIG)
    assert AN["forecast"]["default_model"] in ea.selectable_models(CONFIG)


def test_own_season_model_beats_both_rolling_averages_at_every_history_length():
    for L in AN["forecast"]["lookback_options"]:
        s = ea.backtest_models(CLEAN, CONFIG, lookback=L)[1]
        assert s["best_model"] == "seasonal_growth"
        assert s["overall_wape"]["seasonal_growth"] < min(s["overall_wape_baseline"].values())


def test_own_season_model_steps_down_on_short_history_and_names_the_fallback():
    s = ea._series(CLEAN, "SPD", "SG", "A").iloc[-8:]                    # under min_history_months
    out = ea.forecast_series(s, CONFIG, model="seasonal_growth", season=None)
    assert out["method_used"].iloc[0] == "damped_trend (no seasonal pattern yet)"
    idx = ea.harmonic_index([float(v) for v in range(1, 25)], pd.date_range("2024-10-01", periods=24, freq="MS"), 1)
    assert sum(idx.values()) / 12 == pytest.approx(1.0)


def test_recommendation_says_to_drop_the_rolling_average():
    matrix, runs = ea.backtest_matrix(CLEAN, CONFIG)
    first = ea.build_model_observations(matrix, runs, CONFIG)[0]
    assert first.startswith("Recommendation: stop planning with the") and "Own season + regional growth" in first

def test_noise_rows_never_reach_forecast():
    key = ("SPD", "SG", "B")
    out = ea.generate_demand_forecast(CLEAN, *key, CONFIG)
    sel = CLEAN[(CLEAN["U_Entity"] == key[0]) & (CLEAN["U_Country"] == key[1]) & (CLEAN["U_ProductLine"] == key[2])]
    raw_sel = RAW[(RAW["U_Entity"] == key[0]) & (RAW["U_Country"] == key[1]) & (RAW["U_ProductLine"] == key[2])]
    assert len(raw_sel) > len(sel)                              # noise exists for this series
    hist = out[out["actual"].notna()]
    assert hist["actual"].sum() == pytest.approx(sel["Quantity"].sum())
    assert hist["actual"].sum() < raw_sel["Quantity"].sum()


def test_forecast_filters_by_entity():
    a = ea.generate_demand_forecast(CLEAN, "SPD", "SG", "C", CONFIG)
    b = ea.generate_demand_forecast(CLEAN, "QTS", "SG", "C", CONFIG)
    assert a["actual"].sum() != b["actual"].sum()


def test_short_series_still_forecasts_without_raising():
    ss = CHECKS["short_series"]
    fc = AN["forecast"]
    assert ss["months"] >= fc["min_points"]                                  # the injected 10-month series is usable
    for model in fc["models"]:
        out = ea.generate_demand_forecast(CLEAN, ss["U_Entity"], ss["U_Country"], ss["U_ProductLine"], CONFIG, model=model)
        assert not out.empty and out["actual"].notna().sum() == ss["months"]


def test_very_short_series_returns_empty_frame():
    fc = AN["forecast"]
    sel = CLEAN[(CLEAN["U_Entity"] == "SPD") & (CLEAN["U_Country"] == "SG") & (CLEAN["U_ProductLine"] == "A")]
    months = sorted(sel["YearMonth"].unique())[: fc["min_points"] - 1]
    out = ea.generate_demand_forecast(sel[sel["YearMonth"].isin(months)], "SPD", "SG", "A", CONFIG)
    assert out.empty and list(out.columns) == ea.FORECAST_COLUMNS


def test_missing_months_are_zero_filled():
    sel = CLEAN[(CLEAN["U_Entity"] == "SPD") & (CLEAN["U_Country"] == "SG") & (CLEAN["U_ProductLine"] == "A")]
    months = sorted(sel["YearMonth"].unique())
    holed = sel[sel["YearMonth"] != months[5]]
    out = ea.generate_demand_forecast(holed, "SPD", "SG", "A", CONFIG)
    hist = out[out["actual"].notna()]
    assert len(hist) == len(months)
    assert hist.loc[hist["period"] == months[5], "actual"].iloc[0] == 0.0


@pytest.mark.parametrize("country", ["SG", "MY", "TH", "ID", "VN"])
def test_every_country_forecasts(country):
    keys = [k for k in ea.series_keys(CLEAN) if k[1] == country]
    assert keys
    for key in keys:
        ea.generate_demand_forecast(CLEAN, *key, CONFIG)       # must never raise


# ----------------------------------------------------------------------------- churn
@pytest.fixture(scope="module")
def churn():
    return ea.calculate_churn_radar(CLEAN, CONFIG, as_of=AS_OF)


def test_churn_windows_baseline_precedes_recent():
    baseline, recent = ea.churn_windows(CLEAN, CONFIG, as_of=AS_OF)
    ch = AN["churn"]
    assert len(recent) == ch["recent_window_months"] and len(baseline) == ch["baseline_window_months"]
    assert recent[-1] == pd.Timestamp(CHECKS["history_end_month"])
    assert baseline[-1] + pd.offsets.MonthBegin(1) == recent[0]


def test_default_as_of_matches_expected(churn):
    pd.testing.assert_frame_equal(churn, ea.calculate_churn_radar(CLEAN, CONFIG))


def test_injected_churners_flagged(churn):
    flagged = set(churn.loc[churn["flagged"], "CardCode"])
    assert set(CHECKS["churners"]) <= flagged
    for code, target in CHECKS["churner_target_deltas"].items():
        d = churn.loc[churn["CardCode"] == code, "delta"].iloc[0]
        assert d < AN["churn"]["velocity_threshold"]
        assert d == pytest.approx(target, abs=0.08)


def test_near_threshold_decliners_not_flagged(churn):
    rows = churn[churn["CardCode"].isin(CHECKS["near_threshold_decliners"])]
    assert len(rows) == len(CHECKS["near_threshold_decliners"])
    assert not rows["flagged"].any()
    assert (rows["delta"] < 0).all()


def test_short_history_customer_excluded(churn):
    assert not set(CHECKS["short_history_customers"]) & set(churn["CardCode"])


def test_churn_output_columns_and_no_div_by_zero(churn):
    assert list(churn.columns) == ea.CHURN_COLUMNS
    assert (churn["baseline_avg"] >= AN["churn"]["min_baseline_monthly_volume"]).all()
    assert churn["delta"].notna().all()


def test_zero_baseline_skipped_not_raised():
    df = CLEAN.copy()
    baseline, _ = ea.churn_windows(df, CONFIG, as_of=AS_OF)
    target = CHECKS["churners"][0]
    df = df[~((df["CardCode"] == target) & df["YearMonth"].isin(baseline))]
    out = ea.calculate_churn_radar(df, CONFIG, as_of=AS_OF)
    assert target not in set(out["CardCode"])


# ----------------------------------------------------------------------------- cross-sell
@pytest.fixture(scope="module")
def cross_sell():
    return ea.calculate_cross_sell_opportunities(CLEAN, CONFIG, as_of=AS_OF)


def test_cross_sell_gap_accounts_returned(cross_sell):
    gaps, _ = cross_sell
    assert set(CHECKS["cross_sell_gaps"]) <= set(gaps["CardCode"])
    assert list(gaps.columns) == ea.CROSS_SELL_COLUMNS
    cs = AN["cross_sell"]
    w_start = pd.Timestamp(AS_OF) - pd.Timedelta(days=cs["window_days"])
    recent_c = CLEAN[(CLEAN["DocDate"] > w_start) & (CLEAN["U_ProductLine"] == cs["consequent_product"])]
    assert not set(gaps["CardCode"]) & set(recent_c["CardCode"])


def test_cross_sell_stats_overall_and_by_entity(cross_sell):
    _, stats = cross_sell
    o = stats["overall"]
    assert 0 < o["support"] <= o["confidence"] <= 1
    assert o["lift"] is not None and o["lift"] > 0
    assert set(stats["by_entity"]) == {"SPD", "QTS", "QTG"}
    for s in stats["by_entity"].values():
        assert s["confidence"] is None or 0 <= s["confidence"] <= 1
    assert sum(s["accounts"] for s in stats["by_entity"].values()) == o["accounts"]


def test_aggregate_counts_have_no_identities():
    agg = ea.aggregate_counts(CLEAN, CONFIG, as_of=AS_OF)
    assert agg["churn_flagged"] >= len(CHECKS["churners"])
    assert agg["cross_sell_gaps"] >= len(CHECKS["cross_sell_gaps"])
    text = json.dumps(agg)
    assert not any(code in text for code in CHECKS["churners"] + CHECKS["cross_sell_gaps"])


# ----------------------------------------------------------------------------- regional business view
RV = AN["regional_view"]


@pytest.fixture(scope="module")
def regional():
    return ea.summarize_regional_volumes(CLEAN, CONFIG, as_of=AS_OF)


def test_regional_period_dates(regional):
    _, period = regional
    end_month = pd.Timestamp(CHECKS["history_end_month"])
    start = end_month - pd.DateOffset(months=RV["window_months"] - 1)
    assert period["current_start"] == start.date().isoformat()
    assert period["current_end"] == (end_month + pd.offsets.MonthEnd(0)).date().isoformat()
    assert period["prior_start"] == (start - pd.DateOffset(months=RV["prior_offset_months"])).date().isoformat()
    assert period["prior_end"] == (end_month - pd.DateOffset(months=RV["prior_offset_months"])
                                   + pd.offsets.MonthEnd(0)).date().isoformat()
    assert period["prior_complete"] is True
    assert period["definition"] == RV["growth_definition"]


def test_regional_grid_covers_every_country_and_product(regional):
    df, _ = regional
    countries = next(f["options"] for f in CONFIG["domains"]["CUSTOMER"]["fields"] if f["field"] == "U_Country")
    assert len(df) == len(countries) * len(AN["product_lines"])
    assert set(df["U_Country"]) == set(countries) and set(df["U_ProductLine"]) == set(AN["product_lines"])


def test_regional_known_country_product_total(regional):
    df, period = regional
    row = df[(df["U_Country"] == "SG") & (df["U_ProductLine"] == "C")].iloc[0]
    sel = CLEAN[(CLEAN["U_Country"] == "SG") & (CLEAN["U_ProductLine"] == "C")]
    cur = sel[(sel["DocDate"] >= period["current_start"]) & (sel["DocDate"] <= period["current_end"])]
    pri = sel[(sel["DocDate"] >= period["prior_start"]) & (sel["DocDate"] <= period["prior_end"])]
    cq, pq = cur["Quantity"].sum(), pri["Quantity"].sum()
    assert row["current_qty"] == cq and row["prior_qty"] == pq and cq > 0 and pq > 0
    assert row["current_usd"] == pytest.approx(cur["LineTotal_USD"].sum(), abs=0.01)
    assert row["growth_pct"] == pytest.approx((cq - pq) / pq * 100, abs=0.05)


def test_regional_new_account_volume_split(regional):
    df, _ = regional
    vn_c = df[(df["U_Country"] == CHECKS["short_series"]["U_Country"]) &
              (df["U_ProductLine"] == CHECKS["short_series"]["U_ProductLine"])].iloc[0]
    assert vn_c["new_account_qty"] > 0                      # the short-series customer only started recently
    assert vn_c["growth_excl_new_pct"] < vn_c["growth_pct"]


def test_regional_zero_prior_returns_none():
    months = pd.date_range(end=pd.Timestamp(CHECKS["history_end_month"]), periods=RV["window_months"], freq="MS")
    df = pd.DataFrame({"CardCode": "C99999", "CardName": "New Account", "U_Entity": "SPD", "U_Country": "SG",
                       "U_ProductLine": "A", "Quantity": 10, "LineTotal_USD": 100.0, "DocDate": months,
                       "YearMonth": months})
    out, _ = ea.summarize_regional_volumes(df, CONFIG, as_of=AS_OF)
    sg_a = out[(out["U_Country"] == "SG") & (out["U_ProductLine"] == "A")].iloc[0]
    assert sg_a["prior_qty"] == 0 and sg_a["growth_pct"] is None and sg_a["usd_growth_pct"] is None
    assert out[(out["U_Country"] == "MY")]["growth_pct"].isna().all()   # nothing in either window


def test_request_types_add_up_to_raw_rows():
    rt = ea.summarize_request_types(RAW, CONFIG)
    assert rt["rows"].sum() == len(RAW)
    assert rt["quantity"].sum() == pytest.approx(RAW["Quantity"].sum())
    used = rt[rt["used_in_forecast"]]
    assert len(used) == 1 and int(used["rows"].iloc[0]) == len(CLEAN)
    nf = AN["noise_filter"]
    not_ready = RAW[(RAW["RecordType"] == nf["record_type"]) & ~RAW["U_ReqStatus"].isin(nf["status_in"])]
    assert int(rt.loc[rt["category"] == "CONFIRMED_NOT_READY", "rows"].iloc[0]) == len(not_ready)
    for rtype in set(RAW["RecordType"]) - {nf["record_type"]}:
        assert int(rt.loc[rt["category"] == rtype, "rows"].iloc[0]) == int((RAW["RecordType"] == rtype).sum())
    assert set(rt["label"]) <= set(RV["request_type_labels"].values())


@pytest.fixture(scope="module")
def backtest():
    return ea.backtest_forecast_vs_baseline(CLEAN, CONFIG)


def test_backtest_one_row_per_eligible_series(backtest):
    df, summary = backtest
    h, need = RV["backtest_holdout_months"], max(AN["forecast"]["min_points"], AN["forecast"]["lookback_months"], max(RV["baseline_windows_months"]))
    last = CLEAN["YearMonth"].max()
    eligible = 0
    for (c, p), g in CLEAN.groupby(["U_Country", "U_ProductLine"]):
        n = len(pd.date_range(g["YearMonth"].min(), last, freq="MS"))
        eligible += (n - h) >= need
    assert len(df) == eligible == summary["series"]
    assert not df.duplicated(["U_Country", "U_ProductLine"]).any()
    assert (df["train_months"] == len(pd.date_range(CLEAN["YearMonth"].min(), last, freq="MS")) - h).all()


def test_backtest_needs_only_two_years_of_history_and_picks_a_data_science_model(backtest):
    df, s = backtest
    assert RV["mock_history_months"] == 24
    assert s["model_id"] in AN["forecast"]["models"] and s["model_id"] != "rolling_avg"
    assert set(df["method_used"]) == {s["model_id"]} and s["model_label"] == AN["forecast"]["labels"][s["model_id"]]


@pytest.fixture(scope="module")
def models_run():
    return ea.backtest_models(CLEAN, CONFIG)


def test_every_model_is_scored_on_the_same_hidden_months(models_run):
    df, s = models_run
    for m in AN["forecast"]["models"]:
        assert df[f"wape_{m}"].notna().all() and s["overall_wape"][m] is not None
    assert s["best_model"] == min((m for m in AN["forecast"]["models"] if m != "rolling_avg"), key=lambda m: s["overall_wape"][m])
    assert s["holdout_months"] == RV["backtest_holdout_months"] and s["series"] == len(df) == 15


def test_rolling_average_model_equals_the_traditional_average_at_the_same_history():
    for w in RV["baseline_windows_months"]:
        _, s = ea.backtest_models(CLEAN, CONFIG, lookback=w)
        assert s["overall_wape"]["rolling_avg"] == s["overall_wape_baseline"][f"wape_avg{w}m"]


def test_model_matrix_covers_every_model_and_history_length():
    matrix, runs = ea.backtest_matrix(CLEAN, CONFIG)
    assert list(matrix.columns) == [f"{L} months of history" for L in AN["forecast"]["lookback_options"]]
    assert len(matrix) == len(AN["forecast"]["models"]) + len(RV["baseline_windows_months"])
    assert matrix.notna().all().all() and set(runs) == set(AN["forecast"]["lookback_options"])


def test_observations_quote_computed_numbers_and_do_not_overclaim():
    matrix, runs = ea.backtest_matrix(CLEAN, CONFIG)
    text = " ".join(ea.build_model_observations(matrix, runs, CONFIG))
    s = runs[AN["forecast"]["lookback_months"]]
    assert f"{s['overall_wape'][s['best_model']]:.1f}%" in text and AN["forecast"]["labels"][s["best_model"]] in text
    assert "real results may differ" in text
    assert ea.build_model_observations(matrix.iloc[0:0], {}, CONFIG) == ["Not enough history to compare the models."]

def test_backtest_summary_is_consistent(backtest):
    df, s = backtest
    m = s["overall_wape_model"]
    for w in RV["baseline_windows_months"]:
        col = f"wape_avg{w}m"
        b = s["overall_wape_baseline"][col]
        assert b is not None and df[col].notna().all()
        assert s["improvement_pct"][col] == pytest.approx((b - m) / b * 100, abs=0.15)
        assert s["model_wins"][col] + len(s["model_not_better"][col]) == s["series"]


def test_recommended_actions_quote_computed_numbers(regional, backtest):
    df, _ = regional
    churn_df = ea.calculate_churn_radar(CLEAN, CONFIG, as_of=AS_OF)
    gaps, stats = ea.calculate_cross_sell_opportunities(CLEAN, CONFIG, as_of=AS_OF)
    actions = ea.build_recommended_actions({"regional": regional, "backtest": backtest, "churn": churn_df,
                                            "cross_sell": (gaps, stats),
                                            "request_types": ea.summarize_request_types(RAW, CONFIG)}, CONFIG)
    text = " ".join(actions)
    top = df.loc[df["growth_pct"].idxmax()]
    assert f"{top['growth_pct']:+.1f}%" in text and top["country"] in text
    assert f"{int(churn_df['flagged'].sum())} account(s) are on the Churn Radar" in text
    assert f"{len(gaps)} account(s) bought" in text
    assert f"{backtest[1]['overall_wape_model']:.1f}%" in text
    assert not any(code in text for code in CHECKS["churners"] + CHECKS["cross_sell_gaps"])


def test_regional_functions_handle_empty_input():
    empty = CLEAN.iloc[0:0]
    assert ea.summarize_regional_volumes(empty, CONFIG)[0].empty
    assert ea.backtest_forecast_vs_baseline(empty, CONFIG)[0].empty
    assert ea.summarize_request_types(RAW.iloc[0:0], CONFIG).empty
    assert ea.build_recommended_actions({}, CONFIG) == []


# ----------------------------------------------------------------------------- rolling backtest and order patterns
def test_rolling_backtest_repeats_the_test_on_earlier_windows():
    rb, summary = ea.backtest_rolling(CLEAN, CONFIG)
    n = AN["rolling_backtest"]["origins"]
    assert summary["windows"] == n and rb["window"].nunique() == n
    latest = rb[rb["holdout_end"] == rb["holdout_end"].max()]
    _, s = ea.backtest_models(CLEAN, CONFIG)
    got = latest[latest["name"] == ea._model_label(CONFIG, summary["headline_model"])]["wape"].iloc[0]
    assert got == s["overall_wape"][summary["headline_model"]]               # latest window is the usual single backtest
    for _, w in rb.groupby("window"):
        assert w[w["name"] == ea._model_label(CONFIG, summary["headline_model"])]["wape"].iloc[0] < w[w["kind"] == "baseline"]["wape"].min()


def _orders(card, months, qty):
    return pd.DataFrame({"CardCode": card, "U_Country": "SG", "U_ProductLine": "A",
                         "YearMonth": pd.to_datetime([f"2025-{m:02d}-01" for m in months]), "Quantity": qty,
                         "LineTotal_USD": [q * 10.0 for q in qty]})


def test_order_patterns_classify_by_timing_and_size():
    op = AN["order_pattern"]
    frame = pd.concat([_orders("SMOOTH1", range(1, 9), [100, 101, 99, 100, 102, 98, 100, 101]),
                       _orders("ERRATIC1", range(1, 9), [10, 300, 20, 500, 5, 400, 15, 350]),
                       _orders("INTERM1", [1, 4, 7, 10], [100, 102, 98, 100]),
                       _orders("LUMPY1", [1, 2, 8, 12], [5, 400, 10, 900]),
                       _orders("FEW1", [1, 2], [100, 100])])
    got = ea.classify_order_patterns(frame, CONFIG).set_index("CardCode")["pattern"].to_dict()
    assert got == {"SMOOTH1": "SMOOTH", "ERRATIC1": "ERRATIC", "INTERM1": "INTERMITTENT", "LUMPY1": "LUMPY", "FEW1": "TOO_FEW"}
    by_pattern, by_country = ea.summarize_order_patterns(ea.classify_order_patterns(frame, CONFIG), CONFIG)
    assert set(by_pattern["pattern"]) == set(op["labels"])
    assert by_pattern["pairs_share_pct"].sum() == pytest.approx(100.0, abs=0.2)
    assert "CardCode" not in by_pattern.columns and "CardCode" not in by_country.columns


def test_order_patterns_on_the_generated_data_cover_every_pair():
    pairs = ea.classify_order_patterns(CLEAN, CONFIG)
    assert len(pairs) == CLEAN.groupby(["CardCode", "U_ProductLine"]).ngroups
    assert set(pairs["pattern"]) <= set(AN["order_pattern"]["labels"])


def test_new_vs_existing_matches_the_regional_view():
    reg, _ = ea.summarize_regional_volumes(CLEAN, CONFIG, as_of=AS_OF)
    nve = ea.summarize_new_vs_existing(CLEAN, CONFIG, as_of=AS_OF)
    merged = reg.merge(nve, on=["U_Country", "U_ProductLine"])
    assert len(merged) == len(reg)
    assert (merged["new_qty_x"] - merged["new_qty_y"]).abs().max() < 1e-6 if "new_qty_x" in merged else True
    assert ((merged["existing_qty"] + merged["new_qty"]) - merged["current_qty"]).abs().max() < 1e-6
    assert ((merged["existing_usd"] + merged["new_usd"]) - merged["current_usd"]).abs().max() < 0.05
    assert (merged["prior_qty_x"] - merged["prior_qty_y"]).abs().max() < 1e-6 if "prior_qty_x" in merged else True
    assert (merged["new_account_qty"] - merged["new_qty"]).abs().max() < 1e-6
    status = ea.account_status(CLEAN, CONFIG, as_of=AS_OF)
    assert set(status) <= {"New", "Existing"}


# ----------------------------------------------------------------------------- yearly waves chosen from the data
def _spiky(df):
    f = df["YearMonth"].dt.month.map({12: 2.2, 1: 1.8, 6: 0.5}).fillna(1.0)
    return df.assign(Quantity=df["Quantity"] * f)


def test_waves_follow_the_data_and_the_months_available():
    assert ea.choose_waves(CLEAN, CONFIG) == 1                                   # the generated data has one smooth yearly wave
    assert ea.choose_waves(_spiky(CLEAN), CONFIG) >= 2                           # a sharp year-end burst needs more than one
    short = CLEAN[CLEAN["YearMonth"] >= CLEAN["YearMonth"].max() - pd.DateOffset(months=13)]
    assert ea.choose_waves(_spiky(short), CONFIG) == 1                           # 14 months only pay for one wave


def test_waves_can_still_be_fixed_in_config():
    import copy
    cfg = copy.deepcopy(CONFIG)
    cfg["analytics"]["forecast"]["seasonal_series"]["harmonics"] = 2
    assert ea.choose_waves(CLEAN, cfg) == 2


def test_overall_trend_reports_regression_growth():
    t = ea.overall_trend(CLEAN, CONFIG)
    assert t["waves"] == 1 and t["series"] == 15 and t["months"] >= 12
    assert 0 < t["monthly_growth_pct"] < 2 and t["annual_growth_pct"] == pytest.approx((1 + t["monthly_growth_pct"] / 100) ** 12 * 100 - 100, abs=0.6)
    assert ea.overall_trend(CLEAN[CLEAN["YearMonth"] > CLEAN["YearMonth"].max() - pd.DateOffset(months=5)], CONFIG) is None
