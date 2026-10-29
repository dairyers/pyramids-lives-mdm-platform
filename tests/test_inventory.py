"""Tests for engine_inventory.py: the stock plan built on the forecast."""
import copy
import math
import pathlib

import numpy as np
import pandas as pd
import pytest

import engine_analytics as ea
import engine_inventory as ei
from fixtures import CONFIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = pd.read_csv(ROOT / "data" / "regional_orders.csv")
NOW = pd.Timestamp("2026-10-29", tz="UTC")
PLAN = ei.build_plan(RAW, CONFIG, now=NOW)


def raw_between(first_month, last_month, drop=None):
    d = pd.to_datetime(RAW["DocDate"]).dt.to_period("M").dt.to_timestamp()
    keep = (d >= pd.Timestamp(first_month)) & (d <= pd.Timestamp(last_month))
    if drop is not None:
        keep &= ~drop(RAW, d)
    return RAW[keep]


# ----------------------------------------------------------------------------- stage 1: demand and reconciliation
@pytest.mark.parametrize("basis", ["requests", "orders"])
def test_reconciliation_balances_and_setup_rows_never_count(basis):
    clean, recon = ei.demand_rows(RAW, CONFIG, basis)
    assert int(recon["rows"].sum()) == len(RAW)
    assert int(recon.loc[recon["treatment"] == "Demand", "rows"].sum()) == len(clean)
    setup = recon[recon["category"] == "CONFIRMED_NOT_READY"].iloc[0]
    assert setup["treatment"].startswith("Excluded: waiting on master-data setup")
    assert not set(ea.order_categories(RAW, CONFIG).loc[clean.index]) & {"CONFIRMED_NOT_READY", "INQUIRY", "CANCELLED_PO"}


def test_requests_basis_is_the_default_and_includes_drafts():
    assert ei.inv(CONFIG)["demand_basis"]["default"] == "requests"
    req, _ = ei.demand_rows(RAW, CONFIG, "requests")
    orders, _ = ei.demand_rows(RAW, CONFIG, "orders")
    assert len(req) > len(orders)


def test_countries_and_products_come_from_the_data():
    keys = set(ei.demand_series(ei.demand_rows(RAW, CONFIG)[0]))
    assert keys == set(zip(PLAN["table"]["country"], PLAN["table"]["product"]))
    one = raw_between("2024-10-01", "2026-09-01", drop=lambda r, d: r["U_Country"] == "VN")
    assert "VN" not in set(ei.build_plan(one, CONFIG, now=NOW)["table"]["country"])


# ----------------------------------------------------------------------------- stage 3: the forecast is read, not changed
def test_adapter_returns_exactly_what_the_engine_returns():
    clean, _ = ei.demand_rows(RAW, CONFIG)
    series = ei.demand_series(clean)
    key = next(iter(series))
    got = ei.forecast_adapter({key: series[key]}, clean, CONFIG, 12)[key]["forecast"].dropna().to_numpy()
    direct = ea.forecast_series(series[key], CONFIG, horizon=12, model=CONFIG["analytics"]["forecast"]["default_model"],
                                lookback=12, season=ea.seasonal_index(clean, CONFIG), growth=ea.pooled_growth(clean, CONFIG),
                                waves=ea.choose_waves(clean, CONFIG))["forecast"].dropna().to_numpy()
    assert got == pytest.approx(direct)


def test_planning_does_not_alter_the_config_or_the_forecast():
    before = copy.deepcopy(CONFIG)
    clean = ea.clean_transaction_data(RAW, CONFIG)
    f0 = ea.generate_demand_forecast(clean, "SPD", "SG", "A", CONFIG)["forecast"].dropna().to_numpy()
    ei.build_plan(RAW, CONFIG, entered={"lead_time_months": 3.0}, now=NOW)
    assert CONFIG == before
    assert ea.generate_demand_forecast(clean, "SPD", "SG", "A", CONFIG)["forecast"].dropna().to_numpy() == pytest.approx(f0)


# ----------------------------------------------------------------------------- stage 4: errors, time order, coverage
def test_backtest_never_uses_months_after_the_origin():
    clean, _ = ei.demand_rows(RAW, CONFIG)
    series = ei.demand_series(clean)
    errs = ei.backtest_errors(series, clean, CONFIG, 2.0)
    assert len(errs) and errs["origin"].min() >= pd.Timestamp("2025-09-01")
    origin = sorted(errs["origin"].unique())[3]
    later = clean.copy()
    later.loc[later["YearMonth"] > origin, "Quantity"] *= 3.0                      # the future changes ...
    series2 = ei.demand_series(later)
    errs2 = ei.backtest_errors(series2, later, CONFIG, 2.0)
    a = errs[errs["origin"] == origin].set_index(["country", "product"])["forecast"]
    b = errs2[errs2["origin"] == origin].set_index(["country", "product"])["forecast"]
    assert a.to_numpy() == pytest.approx(b.reindex(a.index).to_numpy())            # ... the forecast made at the origin does not


def test_window_count_follows_the_data_length():
    clean, _ = ei.demand_rows(RAW, CONFIG)
    errs = ei.backtest_errors(ei.demand_series(clean), clean, CONFIG, 2.0)
    assert errs.groupby(["country", "product"]).size().eq(11).all()               # 24 months: 12 to 22 as origins


def test_coverage_is_reported_and_calibration_only_widens():
    cal = PLAN["diagnostics"]["calibration"][0]
    assert cal["checked"] > 0 and 0 <= cal["coverage_raw"] <= 1 and cal["k"] >= 1.0
    assert cal["coverage_calibrated"] >= cal["coverage_raw"] - 1e-9


def test_short_history_borrows_error_and_is_flagged():
    short = raw_between("2024-10-01", "2025-12-01")                                  # 15 months: 2 windows at most
    t = ei.build_plan(short, CONFIG, now=NOW)["table"]
    assert (t["error_source"].isin({"borrowed from the same product", "borrowed from all series", "normal fallback"})).all()
    assert t["flags"].str.contains("Error ").all()


def test_normal_fallback_is_labelled_when_there_is_no_error_history():
    info = ei.error_source(pd.DataFrame(columns=["country", "product", "origin", "actual", "forecast", "ratio"]), CONFIG, ("SG", "A"))
    assert info["source"] == "normal fallback" and info["logr"] is None
    d = ei.ratio_draws(info, CONFIG, 1000, np.random.default_rng(1))
    assert (d >= 0).all() and d.mean() == pytest.approx(1.0, abs=0.05)


def test_simulated_ratios_are_positive_and_right_skewed():
    clean, _ = ei.demand_rows(RAW, CONFIG)
    errs = ei.backtest_errors(ei.demand_series(clean), clean, CONFIG, 2.0)
    info = ei.error_source(errs, CONFIG, ("SG", "B"))
    d = ei.ratio_draws(info, CONFIG, 5000, np.random.default_rng(7))
    assert (d > 0).all() and d.mean() >= np.median(d) - 1e-9


# ----------------------------------------------------------------------------- determinism
def test_same_seed_same_plan_and_different_seed_differs():
    again = ei.build_plan(RAW, CONFIG, now=NOW)
    pd.testing.assert_frame_equal(PLAN["table"], again["table"])
    other = ei.build_plan(RAW, CONFIG, seed=99, now=NOW)
    assert other["diagnostics"]["seed"] == 99
    assert not np.allclose(PLAN["table"]["reorder_point"], other["table"]["reorder_point"])


# ----------------------------------------------------------------------------- units and what-if
def test_days_convert_to_months_with_the_configured_factor():
    assert ei.months_from_days(61.0, CONFIG) == pytest.approx(61.0 / 30.4375)


def test_interval_prorates_part_months():
    path = [10.0, 20.0, 30.0]
    assert ei.interval_sum(path, 0, 2.0) == 30.0
    assert ei.interval_sum(path, 0, 1.5) == pytest.approx(20.0)
    assert ei.interval_sum(path, 1, 0.5) == pytest.approx(10.0)


def test_longer_lead_time_and_higher_service_raise_the_stock():
    base = PLAN["table"].set_index(["country", "product"])
    longer = ei.build_plan(RAW, CONFIG, entered={"lead_time_months": 3.0}, now=NOW)["table"].set_index(["country", "product"])
    # over a longer interval the relative error can shrink as months average out, so one series' safety stock need not rise;
    # the reorder point always does, and so does the total buffer
    assert (longer["reorder_point"] > base["reorder_point"]).all() and longer["safety_stock"].sum() > base["safety_stock"].sum()
    safer = ei.build_plan(RAW, CONFIG, entered={"service_level": 0.99}, now=NOW)["table"].set_index(["country", "product"])
    assert (safer["safety_stock"] > base["safety_stock"]).all()


def test_override_for_one_series_changes_only_that_series():
    base = PLAN["table"].set_index(["country", "product"])
    t = ei.build_plan(RAW, CONFIG, overrides={("SG", "A"): {"lead_time_months": 4.0}}, now=NOW)["table"].set_index(["country", "product"])
    assert t.loc[("SG", "A"), "reorder_point"] > base.loc[("SG", "A"), "reorder_point"]
    assert t.loc[("MY", "A"), "reorder_point"] == pytest.approx(base.loc[("MY", "A"), "reorder_point"])


def test_growth_override_raises_the_forecast():
    base = PLAN["table"].set_index(["country", "product"])
    t = ei.build_plan(RAW, CONFIG, entered={"growth_override_pct": 20.0}, now=NOW)["table"].set_index(["country", "product"])
    assert (t["forecast_next_12m"] > base["forecast_next_12m"]).all()


# ----------------------------------------------------------------------------- assumptions and their sources
def test_every_assumption_has_a_source_tag_and_defaults_are_flagged():
    log = PLAN["assumptions"]
    assert set(log["source"]) <= set(ei.SOURCES) and not log["timestamp"].isna().any()
    lead = log[log["assumption"] == "lead_time_months"]
    assert (lead["source"] == "default").all() and (lead["value"] == 1.0).all()
    assert PLAN["table"]["flags"].str.contains("Lead time is a default").all()


def test_entered_beats_estimated_beats_default():
    est = ei._estimates(ei.demand_rows(RAW, CONFIG)[0], CONFIG)[("SG", "A")]
    a = ei.resolve_assumptions(CONFIG, ("SG", "A"), estimates=est)
    assert a["unit_cost"]["source"] == "estimated" and a["holding_rate"]["source"] == "default"
    b = ei.resolve_assumptions(CONFIG, ("SG", "A"), entered={"unit_cost": 5.0, "holding_rate": 0.3}, estimates=est)
    assert b["unit_cost"]["value"] == 5.0 and b["unit_cost"]["source"] == "entered" and b["holding_rate"]["source"] == "entered"
    c = ei.resolve_assumptions(CONFIG, ("SG", "A"), entered={"holding_rate": 0.3}, overrides={"holding_rate": 0.5}, estimates=est)
    assert c["holding_rate"]["value"] == 0.5


def test_service_level_derived_from_costs_when_salvage_is_given():
    a = ei.resolve_assumptions(CONFIG, ("SG", "A"), entered={"price": 100.0, "unit_cost": 60.0, "salvage": 40.0})
    assert a["service_level"]["source"] == "derived" and a["service_level"]["value"] == pytest.approx(40 / (40 + 20))
    assert ei.resolve_assumptions(CONFIG, ("SG", "A"))["service_level"]["source"] == "default"


# ----------------------------------------------------------------------------- stage 6: EOQ, pack, MOQ, caps
def test_eoq_matches_the_closed_form_for_flat_demand():
    q, d, cycle = ei.eoq_orders(np.full(24, 100.0), 80.0, 2.0, CONFIG)
    assert d == pytest.approx(1200.0) and q == pytest.approx(math.sqrt(2 * 1200 * 80 / 2.0)) and cycle == pytest.approx(q / d * 12)


def test_pack_size_compares_both_neighbouring_multiples_on_cost():
    D, K, H = 1200.0, 80.0, 2.0
    q_star = math.sqrt(2 * D * K / H)                                              # about 309.8
    q, _ = ei.constrain_quantity(q_star, D, K, H, pack=100)
    cost = lambda x: K * D / x + H * x / 2                                         # noqa: E731
    assert q in (300, 400) and cost(q) == min(cost(300), cost(400))
    near = ei.constrain_quantity(310.0, 1200.0, 80.0, 2.0, pack=100)[0]
    assert near == 300                                                              # nearest is not always the rule: cost decides
    far, _ = ei.constrain_quantity(349.0, 1200.0, 60.0, 5.0, pack=100)
    assert far in (300, 400)


def test_moq_raises_to_the_smallest_allowed_quantity():
    assert ei.constrain_quantity(40.0, 1000.0, 50.0, 2.0, moq=100)[0] == 100
    q, notes = ei.constrain_quantity(40.0, 1000.0, 50.0, 2.0, pack=24, moq=100)
    assert q == 120 and any("Minimum order" in n for n in notes)
    assert ei.constrain_quantity(500.0, 1000.0, 50.0, 2.0, moq=100)[0] == 500


def test_caps_bind_and_say_so():
    q, notes = ei.constrain_quantity(500.0, 1000.0, 50.0, 2.0, storage_cap=300)
    assert q == 300 and any("Storage cap binds" in n for n in notes)
    q, notes = ei.constrain_quantity(500.0, 1000.0, 50.0, 2.0, budget_cap=1000.0, unit_cost=5.0)
    assert q == 200 and any("Budget cap binds" in n for n in notes)


def test_lead_time_longer_than_the_order_cycle_is_noted():
    row = PLAN["table"].iloc[0]
    assert "inventory position" in row["notes"]
    big = ei.build_plan(RAW, CONFIG, entered={"lead_time_months": 6.0, "order_cost": 100000.0}, now=NOW)["table"]
    assert not big["notes"].str.contains("inventory position").all()                # a long cycle: lead time no longer exceeds it


# ----------------------------------------------------------------------------- awkward series
def row_for(path, **assumptions):
    key = ("SG", "A")
    a = ei.resolve_assumptions(CONFIG, key, entered={"unit_cost": 10.0, **assumptions})
    info = {"logr": np.log(np.array([0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 0.95, 1.05])), "source": "own windows", "windows": 8, "widen": 1.0}
    return ei.plan_series(key, np.asarray(path, float), info, a, CONFIG, np.random.default_rng(1))


def test_zero_demand_series_plans_zero_without_failing():
    row, months, trade = row_for(np.zeros(14))
    assert row["safety_stock"] == 0 and row["reorder_point"] == 0 and row["order_quantity"] == 0
    assert "No forecast demand" in row["flags"] and len(months) and len(trade) == 3


def test_missing_months_are_counted_not_hidden():
    gap = raw_between("2024-10-01", "2026-09-01", drop=lambda r, d: (r["U_Country"] == "SG") & (r["U_ProductLine"] == "A")
                      & (d == pd.Timestamp("2025-06-01")))
    t = ei.build_plan(gap, CONFIG, now=NOW)["table"].set_index(["country", "product"])
    assert t.loc[("SG", "A"), "zero_months"] >= 1 and "no demand counted as zero" in t.loc[("SG", "A"), "flags"]


def test_series_under_twelve_months_is_flagged_not_dropped():
    late = raw_between("2024-10-01", "2026-09-01", drop=lambda r, d: (r["U_Country"] == "TH") & (r["U_ProductLine"] == "B")
                       & (d < pd.Timestamp("2026-01-01")))
    t = ei.build_plan(late, CONFIG, now=NOW)["table"].set_index(["country", "product"])
    assert ("TH", "B") in t.index and "Under 12 months" in t.loc[("TH", "B"), "flags"]
    assert t.loc[("TH", "B"), "reorder_point"] >= 0


def test_without_a_unit_cost_only_units_are_planned():
    key, cfg = ("SG", "Z"), copy.deepcopy(CONFIG)
    a = ei.resolve_assumptions(cfg, key)
    info = {"logr": np.log(np.array([0.9, 1.0, 1.1, 1.0, 0.95, 1.05])), "source": "own windows", "windows": 6, "widen": 1.0}
    row, _, _ = ei.plan_series(key, np.full(14, 50.0), info, a, cfg, np.random.default_rng(1))
    assert row["order_quantity"] is None and row["reorder_point"] > 0 and "No unit cost" in row["notes"]


def test_tradeoff_rises_with_service_level():
    t = PLAN["tradeoff"]
    one = t[(t["country"] == "SG") & (t["product"] == "C")].sort_values("service_level")
    assert one["safety_stock"].is_monotonic_increasing and len(one) == 3


def test_table_layout_survives_a_column_with_no_values():
    from tabs import ui
    df = pd.DataFrame({"Country": ["ZZ", "QQ"], "Order quantity (units)": [None, None], "Reorder point (units)": [1.5, 2.5]})
    height, widths = ui._wrap_layout(df)
    assert "Order quantity (units)" in widths or height is None
