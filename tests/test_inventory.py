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


def test_yearly_cost_splits_into_ordering_and_holding():
    t = PLAN["table"].dropna(subset=["yearly_cost"])
    assert len(t) and (t["ordering_cost_year"] + t["holding_cost_year"]).to_numpy() == pytest.approx(t["yearly_cost"].to_numpy())


# ----------------------------------------------------------------------------- numbers differ by product and country
def test_every_row_starts_with_its_own_estimated_price_not_one_shared_number():
    clean, _ = ei.demand_rows(RAW, CONFIG)
    base = ei.baseline_assumptions(CONFIG, clean)
    cost = {k: a["unit_cost"]["value"] for k, a in base.items()}
    assert len(cost) == 15 and len(set(round(v, 4) for v in cost.values())) > 3                      # not one price for all
    by_product = {p: np.mean([v for (c, q), v in cost.items() if q == p]) for p in "ABC"}
    assert by_product["A"] > by_product["B"] > by_product["C"]                                          # equipment costs more than consumables
    assert all(a["unit_cost"]["source"] == "estimated" for a in base.values())


def test_a_price_typed_for_one_country_and_product_changes_only_that_row():
    base = PLAN["table"].set_index(["country", "product"])
    t = ei.build_plan(RAW, CONFIG, overrides={("SG", "B"): {"unit_cost": 1000.0, "lead_time_months": 3.0, "order_cost": 500.0}}, now=NOW)["table"].set_index(["country", "product"])
    assert t.loc[("SG", "B"), "average_inventory_value"] > 3 * base.loc[("SG", "B"), "average_inventory_value"]
    for other in [("MY", "B"), ("SG", "A"), ("SG", "C")]:
        assert t.loc[other, "average_inventory_value"] == pytest.approx(base.loc[other, "average_inventory_value"])
        assert t.loc[other, "reorder_point"] == pytest.approx(base.loc[other, "reorder_point"])


def test_grid_has_one_row_per_country_and_lists_what_is_still_a_guess():
    from tabs import tab_stock
    clean, _ = ei.demand_rows(RAW, CONFIG)
    base = ei.baseline_assumptions(CONFIG, clean)
    rows = [k for k in base if k[1] == "A"]
    frame = tab_stock._grid_frame(base, rows, CONFIG)
    assert len(frame) == 5 and frame["Country"].nunique() == 5
    assert (frame["Still a guess"] == "lead time, cost per order, holding cost").all()
    entered = ei.baseline_assumptions(CONFIG, clean, {"lead_time_months": 2.0})
    assert (tab_stock._grid_frame(entered, rows, CONFIG)["Still a guess"] == "cost per order, holding cost").all()


def test_only_the_cells_the_user_changed_become_row_specific_numbers():
    from tabs import tab_stock
    clean, _ = ei.demand_rows(RAW, CONFIG)
    base = ei.baseline_assumptions(CONFIG, clean)
    rows = [k for k in base if k[1] == "B"]
    frame = tab_stock._grid_frame(base, rows, CONFIG)
    edited = frame.copy()
    edited.loc[1, "Lead time (months)"] = 2.5
    edited.loc[1, "Safety level (%)"] = 99.0
    edited.loc[3, "Purchase price per unit (USD)"] = 77.0
    edited.loc[4, "Smallest order (units)"] = None                                                     # clearing a cell changes nothing
    got = tab_stock._changes(frame, edited, rows)
    assert got == {rows[1]: {"lead_time_months": 2.5, "service_level": pytest.approx(0.99)}, rows[3]: {"unit_cost": 77.0}}
    assert tab_stock._changes(frame, frame.copy(), rows) == {}


# ----------------------------------------------------------------------------- explaining one order size
def test_textbook_example_is_reproduced_exactly():
    """The standard worked example: D=1000, K=50, C=20, h=25%: Q=141.42, 7.07 orders a year, 51.62 days, total 20,707.11."""
    q, d, cycle = ei.eoq_orders(np.full(24, 1000 / 12), 50.0, 0.25 * 20.0, CONFIG)
    assert d == pytest.approx(1000.0) and q == pytest.approx(141.42, abs=0.01)
    assert d / q == pytest.approx(7.07, abs=0.01) and q / d * 365 == pytest.approx(51.62, abs=0.01)
    tc = ei.total_cost(1000.0, 50.0, 5.0, 20.0, q)
    assert tc["holding"] == pytest.approx(353.55, abs=0.01) and tc["ordering"] == pytest.approx(353.55, abs=0.01)
    assert tc["purchases"] == 20000.0 and tc["total"] == pytest.approx(20707.11, abs=0.01)


def test_the_plan_row_total_cost_matches_the_formula_and_the_curve_bottoms_out_at_eoq():
    row = PLAN["table"].dropna(subset=["total_cost_year"]).iloc[0].to_dict()
    tc = ei.total_cost(row["annual_demand"], row["order_cost"], row["holding_per_unit_year"], row["unit_cost"], row["order_quantity"], row["safety_stock"])
    assert tc["total"] == pytest.approx(row["total_cost_year"]) and tc["ordering"] + tc["holding"] == pytest.approx(row["yearly_cost"])
    curve = ei.cost_curve(row)
    assert row["order_quantity"] in set(curve["order_quantity"]) and row["eoq_unconstrained"] in set(curve["order_quantity"])
    best = curve.loc[curve["total"].idxmin(), "order_quantity"]
    assert abs(best - row["eoq_unconstrained"]) <= curve["order_quantity"].diff().max() + 1e-9
    at = curve[curve["order_quantity"] == row["order_quantity"]].iloc[0]
    assert at["total"] == pytest.approx(row["total_cost_year"])
    assert (curve["purchases"] == curve["purchases"].iloc[0]).all()                                      # purchases do not depend on order size


def test_cost_curve_is_empty_without_a_unit_cost():
    assert ei.cost_curve({"annual_demand": None, "order_quantity": None}).empty


def test_stock_profile_follows_the_plan_and_shortages_are_counted():
    path = np.full(14, 300.0)
    prof = ei.inventory_profile(path, q=200.0, rop=360.0, ss=60.0, lead_months=1.0, review_months=0.0, config=CONFIG, months=12)
    assert len(prof) == 365 and prof["on_hand"].min() >= 0 and prof["on_hand"].iloc[0] <= 360.0 + 200.0
    assert prof["position"].min() > 0 and prof["shortage"].sum() == pytest.approx(0.0, abs=1e-6)          # plan holds at the expected demand
    high = ei.inventory_profile(path, 200.0, 360.0, 60.0, 1.0, 0.0, CONFIG, demand_scale=3.0)
    assert high["shortage"].sum() > 0                                                                       # demand three times higher runs short


def test_stock_profile_with_no_demand_never_orders():
    prof = ei.inventory_profile(np.zeros(14), 100.0, 50.0, 10.0, 1.0, 1.0, CONFIG)
    assert (prof["on_hand"] == 150.0).all() and (prof["position"] == 150.0).all()


# ----------------------------------------------------------------------------- one product at a time, and the demand factor
def test_planning_one_product_gives_the_same_row_as_the_full_plan():
    key = ("MY", "B")
    one = ei.build_plan(RAW, CONFIG, only=[key], now=NOW)["table"]
    assert len(one) == 1 and (one.iloc[0]["country"], one.iloc[0]["product"]) == key
    full = PLAN["table"].set_index(["country", "product"]).loc[key]
    for col in ("reorder_point", "safety_stock", "order_quantity", "total_cost_year"):
        assert one.iloc[0][col] == pytest.approx(full[col])


def test_more_demand_means_more_stock_and_a_bigger_order():
    key = ("SG", "C")
    base = ei.build_plan(RAW, CONFIG, only=[key], now=NOW)["table"].iloc[0]
    up = ei.build_plan(RAW, CONFIG, overrides={key: {"demand_change_pct": 50.0}}, only=[key], now=NOW)["table"].iloc[0]
    down = ei.build_plan(RAW, CONFIG, overrides={key: {"demand_change_pct": -50.0}}, only=[key], now=NOW)["table"].iloc[0]
    assert up["forecast_next_12m"] == pytest.approx(1.5 * base["forecast_next_12m"], rel=1e-6)
    assert down["forecast_next_12m"] == pytest.approx(0.5 * base["forecast_next_12m"], rel=1e-6)
    assert down["reorder_point"] < base["reorder_point"] < up["reorder_point"]
    assert down["order_quantity"] < base["order_quantity"] < up["order_quantity"]


def test_each_factor_moves_the_answer_the_way_the_textbook_says():
    key = ("TH", "B")
    run = lambda **o: ei.build_plan(RAW, CONFIG, overrides={key: o}, only=[key], now=NOW)["table"].iloc[0]       # noqa: E731
    base = run()
    assert run(order_cost=base["order_cost"] * 4)["order_quantity"] > base["order_quantity"]                    # dearer orders: bigger, fewer orders
    assert run(holding_rate=0.60)["order_quantity"] < base["order_quantity"]                                    # dearer holding: smaller orders
    assert run(unit_cost=base["unit_cost"] * 3)["order_quantity"] < base["order_quantity"]                      # dearer goods: smaller orders
    assert run(lead_time_months=4.0)["reorder_point"] > base["reorder_point"]
    assert run(service_level=0.99)["safety_stock"] > base["safety_stock"]

# ----------------------------------------------------------------------------- days of stock, and answers that apply to some rows only
def test_days_of_stock_is_average_stock_over_daily_demand():
    t = PLAN["table"].dropna(subset=["days_of_stock"])
    assert len(t) == 15
    expected = t["average_inventory_units"] / (t["annual_demand"] / 365.25)
    assert t["days_of_stock"].to_numpy() == pytest.approx(expected.to_numpy())
    by_product = t.groupby("product")["days_of_stock"].mean()
    assert by_product["C"] > by_product["A"]                                                          # cheap consumables sit longest here


def test_answers_can_apply_to_one_country_and_product_only():
    from tabs import tab_stock
    keys = [(c, p) for c in ("SG", "MY") for p in ("A", "B", "C")]
    assert tab_stock._scope_split(keys, "All", "All", {"order_cost": 5.0}) == ({"order_cost": 5.0}, {})
    base, only = tab_stock._scope_split(keys, "SG", "A", {"order_cost": 5.0})
    assert base == {} and only == {("SG", "A"): {"order_cost": 5.0}}
    base, only = tab_stock._scope_split(keys, "All", "B", {"lead_time_months": 2.0})
    assert set(only) == {("SG", "B"), ("MY", "B")}
    clean, _ = ei.demand_rows(RAW, CONFIG)
    got = ei.baseline_assumptions(CONFIG, clean, None, None, {("SG", "A"): {"order_cost": 5.0}})
    assert got[("SG", "A")]["order_cost"]["value"] == 5.0 and got[("SG", "A")]["order_cost"]["source"] == "entered"
    assert got[("MY", "A")]["order_cost"]["source"] == "default"                                      # other rows keep the standard guess


# ----------------------------------------------------------------------------- shipping, price breaks and free shipping
E0 = {"D": 1000.0, "K": 50.0, "fpo": 0.0, "fpu": 0.0, "hrate": 0.25, "ss": 0.0, "base_price": 20.0, "breaks": [], "dtype": "all_units", "free_above": None}


def test_with_nothing_entered_the_economics_are_the_textbook_formula():
    eco = ei.order_economics(141.42, E0)
    assert eco["price"] == 20.0 and eco["purchases"] == 20000.0 and eco["total"] == pytest.approx(20707.11, abs=0.02)
    assert ei.order_economics(0, E0) is None


def test_all_unit_price_breaks_reprice_the_whole_order():
    e = {**E0, "breaks": [(300.0, 18.0), (600.0, 16.0)]}
    assert [ei.price_at(q, e) for q in (1, 299, 300, 599, 600, 5000)] == [20.0, 20.0, 18.0, 18.0, 16.0, 16.0]
    assert ei.order_economics(300.0, e)["purchases"] == 1000 * 18.0


def test_incremental_price_breaks_price_each_unit_by_its_tier():
    e = {**E0, "breaks": [(300.0, 18.0), (600.0, 16.0)], "dtype": "incremental"}
    assert ei.price_at(700.0, e) == pytest.approx((300 * 20 + 300 * 18 + 100 * 16) / 700)
    assert ei.price_at(200.0, e) == 20.0 and ei.price_at(300.0, e) == 20.0


def test_the_best_order_is_the_cheapest_over_a_fine_search_and_beats_the_plain_eoq():
    e = {**E0, "breaks": [(300.0, 18.0), (600.0, 16.0)]}
    val = {"pack_size": None, "moq": None, "storage_cap": None, "budget_cap": None}
    q, notes, saving = ei.best_order(e, 141.42, val, CONFIG)
    fine = min((ei.order_economics(x, e)["total"], x) for x in np.arange(1.0, 3000.0, 0.5))
    assert ei.order_economics(q, e)["total"] == pytest.approx(fine[0], rel=1e-6)
    assert q >= 300 and saving > 0 and any("per unit instead of" in n for n in notes)


def test_a_discount_that_is_not_worth_the_extra_stock_is_not_taken():
    e = {**E0, "breaks": [(5000.0, 19.99)]}
    val = {"pack_size": None, "moq": None, "storage_cap": None, "budget_cap": None}
    q, notes, saving = ei.best_order(e, 141.42, val, CONFIG)
    assert q == pytest.approx(141.42, abs=1.0) and (saving or 0) <= 0.5


def test_pack_size_moq_and_caps_are_respected_when_a_discount_applies():
    e = {**E0, "breaks": [(300.0, 18.0)]}
    val = {"pack_size": 50.0, "moq": 100.0, "storage_cap": 400.0, "budget_cap": None}
    q, _, _ = ei.best_order(e, 141.42, val, CONFIG)
    assert q % 50 == 0 and 100 <= q <= 400
    capped, _, _ = ei.best_order(e, 141.42, {**val, "storage_cap": 250.0}, CONFIG)
    assert capped < 300 and capped % 50 == 0


def test_free_shipping_above_a_value_pulls_the_order_up_to_the_threshold():
    e = {**E0, "fpo": 400.0, "free_above": 4000.0}
    val = {"pack_size": None, "moq": None, "storage_cap": None, "budget_cap": None}
    q, notes, saving = ei.best_order(e, 300.0, val, CONFIG)
    assert q * 20.0 >= 4000.0 and ei.order_economics(q, e)["ship_per_order"] == 0.0
    assert saving > 0 and any("free shipping" in n for n in notes)


def test_shipping_per_unit_and_per_order_change_the_plan_the_way_the_formula_says():
    key = ("SG", "B")
    base = ei.build_plan(RAW, CONFIG, only=[key], now=NOW)["table"].iloc[0]
    per_unit = ei.build_plan(RAW, CONFIG, overrides={key: {"freight_per_unit": base["unit_cost"]}}, only=[key], now=NOW)["table"].iloc[0]
    assert per_unit["order_quantity"] == pytest.approx(base["order_quantity"] / np.sqrt(2), rel=1e-6)           # landed price doubles: EOQ falls by sqrt 2
    assert per_unit["freight_unit_cost_year"] == pytest.approx(per_unit["annual_demand"] * base["unit_cost"])
    per_order = ei.build_plan(RAW, CONFIG, overrides={key: {"freight_per_order": base["order_cost"] * 3}}, only=[key], now=NOW)["table"].iloc[0]
    assert per_order["order_quantity"] == pytest.approx(base["order_quantity"] * 2, rel=1e-6)                   # order cost x4: EOQ doubles


def test_a_price_break_changes_only_its_own_row_and_the_curve_steps_there():
    key = ("SG", "C")
    base = PLAN["table"].set_index(["country", "product"])
    probe = base.loc[key]
    start = probe["order_quantity"] * 1.5
    t = ei.build_plan(RAW, CONFIG, price_breaks={key: [(start, probe["unit_cost"] * 0.7)]}, only=[key, ("MY", "C")], now=NOW)["table"].set_index(["country", "product"])
    assert t.loc[key, "order_quantity"] >= start and t.loc[key, "price_used"] < probe["unit_cost"]
    assert t.loc[key, "total_cost_year"] < probe["total_cost_year"] and "per unit instead of" in t.loc[key, "notes"]
    assert t.loc[("MY", "C"), "total_cost_year"] == pytest.approx(base.loc[("MY", "C"), "total_cost_year"])
    row = t.loc[key].to_dict()
    curve = ei.cost_curve(row)
    assert curve["purchases"].nunique() > 1                                                                       # purchases now fall when the break is reached
    at = curve[curve["order_quantity"] == row["order_quantity"]].iloc[0]
    assert at["total"] == pytest.approx(row["total_cost_year"])


# ----------------------------------------------------------------------------- possible stockouts: found, never changed on their own
def _with_stockouts():
    clean, _ = ei.demand_rows(RAW, CONFIG)
    bad = clean.copy()
    bad["Quantity"] = bad["Quantity"].astype(float)
    gone = (bad["U_Country"] == "SG") & (bad["U_ProductLine"] == "A") & (bad["YearMonth"] == pd.Timestamp("2026-06-01"))
    low = (bad["U_Country"] == "MY") & (bad["U_ProductLine"] == "B") & (bad["YearMonth"] == pd.Timestamp("2026-03-01"))
    bad = bad[~gone].copy()
    bad.loc[low.reindex(bad.index, fill_value=False), "Quantity"] *= 0.1
    return clean, bad


def test_the_sample_history_has_no_suspected_stockouts_and_nothing_changes_on_its_own():
    clean, _ = ei.demand_rows(RAW, CONFIG)
    before = clean.copy()
    assert ei.stockout_candidates(clean, CONFIG).empty
    pd.testing.assert_frame_equal(clean, before)
    assert ei.apply_repairs(clean, []) is clean


def test_a_month_with_no_sales_and_a_month_far_below_the_usual_are_flagged():
    _, bad = _with_stockouts()
    c = ei.stockout_candidates(bad, CONFIG).set_index(["country", "product", "month"])
    none = c.loc[("SG", "A", pd.Timestamp("2026-06-01"))]
    low = c.loc[("MY", "B", pd.Timestamp("2026-03-01"))]
    assert none["kind"] == "no sales" and none["actual"] == 0 and none["expected"] > 0
    assert low["kind"] == "low sales" and low["ratio"] < CONFIG["analytics"]["inventory"]["stockout"]["low_fraction"]
    assert len(c) == 2                                                                                            # nothing else is flagged


def test_repairing_a_month_brings_it_to_the_expected_level_and_leaves_the_original_alone():
    _, bad = _with_stockouts()
    snapshot = bad.copy()
    c = ei.stockout_candidates(bad, CONFIG)
    repairs = [(r.country, r.product, r.month, r.expected) for r in c.itertuples()]
    fixed = ei.apply_repairs(bad, repairs)
    pd.testing.assert_frame_equal(bad, snapshot)
    for country, product, month, target in repairs:
        got = fixed[(fixed["U_Country"] == country) & (fixed["U_ProductLine"] == product) & (fixed["YearMonth"] == month)]["Quantity"].sum()
        assert got == pytest.approx(target)
    assert (fixed["CardCode"] == "ADJUSTED").sum() == 1                                                          # only the empty month needed a new row
    assert len(fixed) == len(bad) + 1
    march = pd.Timestamp("2026-03-01")
    untouched = lambda d: d[~((d["U_Country"] == "MY") & (d["U_ProductLine"] == "B") & (d["YearMonth"] == march)) & (d["CardCode"] != "ADJUSTED")]      # noqa: E731
    assert untouched(fixed)["Quantity"].sum() == pytest.approx(untouched(bad)["Quantity"].sum())                           # every other row is exactly as it was

def test_confirmed_repairs_lift_the_forecast_for_that_product_only():
    _, bad = _with_stockouts()
    c = ei.stockout_candidates(bad, CONFIG)
    fixed = ei.apply_repairs(bad, [(r.country, r.product, r.month, r.expected) for r in c.itertuples()])
    recon = ei.demand_rows(RAW, CONFIG)[1]
    run = lambda frame: ei.build_plan(None, CONFIG, demand=(frame, recon), data_id=str(len(frame)) + str(frame["Quantity"].sum()), now=NOW)["table"].set_index(["country", "product"])  # noqa: E731
    a, b = run(bad), run(fixed)
    assert b.loc[("SG", "A"), "forecast_next_12m"] > a.loc[("SG", "A"), "forecast_next_12m"]
    assert b.loc[("TH", "C"), "forecast_next_12m"] == pytest.approx(a.loc[("TH", "C"), "forecast_next_12m"], rel=0.05)


def test_months_the_file_marks_as_stockouts_are_always_listed_with_what_was_expected():
    clean, _ = ei.demand_rows(RAW, CONFIG)
    month = pd.Timestamp("2026-04-01")
    c = ei.stockout_candidates(clean, CONFIG, extra=[("SG", "C", month), ("SG", "C", pd.Timestamp("2024-10-01"))])           # the second is too early to judge
    assert len(c) == 1
    row = c.iloc[0]
    assert row["kind"] == "marked in your file" and row["expected"] > 0 and row["actual"] > 0 and (row["country"], row["product"], row["month"]) == ("SG", "C", month)


def test_price_breaks_typed_in_the_table_apply_to_the_right_countries():
    from tabs import tab_stock
    keys = [("SG", "A"), ("MY", "A"), ("SG", "B")]
    rows = pd.DataFrame([{"Country": "All", "Product": "A", "From quantity": 600.0, "Price per unit": 16.0},
                         {"Country": "All", "Product": "A", "From quantity": 300.0, "Price per unit": 18.0},
                         {"Country": "SG", "Product": "A", "From quantity": 100.0, "Price per unit": 19.0},
                         {"Country": "All", "Product": "B", "From quantity": None, "Price per unit": 5.0},
                         {"Country": "All", "Product": "B", "From quantity": 10.0, "Price per unit": 0.0}])
    got = tab_stock._breaks_from_rows(rows, keys)
    assert got == {("MY", "A"): [(300.0, 18.0), (600.0, 16.0)], ("SG", "A"): [(100.0, 19.0)]}                      # specific rows win; bad rows ignored
    assert tab_stock._breaks_from_rows(pd.DataFrame(columns=rows.columns), keys) == {}
