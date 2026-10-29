"""Tests for engine_replay.py: the plan replayed on the demand that really followed, against two old-style rules."""
import copy
import math
import pathlib

import numpy as np
import pandas as pd
import pytest

import engine_inventory as ei
import engine_replay as er
from fixtures import CONFIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = pd.read_csv(ROOT / "data" / "regional_orders.csv")
CLEAN, _ = ei.demand_rows(RAW, CONFIG)
RESOLVED = ei.baseline_assumptions(CONFIG, CLEAN)
OUT = er.run_replay(CLEAN, CONFIG, RESOLVED)
DPM = CONFIG["analytics"]["inventory"]["days_per_month"]


# ----------------------------------------------------------------------------- the stock simulation
def test_nothing_is_short_when_there_is_plenty_of_stock_and_everything_is_short_with_none():
    full = er.simulate([(500.0, 400.0)] * 3, [300.0] * 3, 1.0, 1.0, DPM)
    assert full["short"].sum() == pytest.approx(0.0, abs=1e-9) and (full["on_hand"] >= 0).all()
    none = er.simulate([(0.0, 0.0)] * 3, [300.0] * 3, 1.0, 1.0, DPM)
    assert none["short"].sum() == pytest.approx(900.0) and none["orders"] == 0


def test_demand_is_always_either_served_or_counted_short():
    sim = er.simulate([(120.0, 100.0)] * 6, [100, 250, 80, 400, 90, 150], 1.0, 0.0, DPM)
    assert sim["demand"].sum() == pytest.approx(1070.0)
    assert (sim["short"] >= -1e-9).all() and (sim["short"] <= sim["demand"] + 1e-9).all()
    served = sim["demand"].sum() - sim["short"].sum()
    assert 0 < served <= sim["demand"].sum() and sim["on_hand"][-1] >= 0
    assert (120.0 + 100.0) + sim["units_ordered"] >= served                                              # stock can never serve more than it had plus ordered


def test_orders_are_placed_when_stock_plus_orders_falls_to_the_reorder_level():
    sim = er.simulate([(150.0, 200.0)] * 6, [300.0] * 6, 1.0, 0.0, DPM)
    assert sim["orders"] >= 3 and sim["units_ordered"] == pytest.approx(sim["orders"] * 200.0)


def test_the_order_amount_for_a_rule_is_the_closed_form_eoq():
    a = RESOLVED[("SG", "C")]
    K, H = a["order_cost"]["value"], a["holding_rate"]["value"] * a["unit_cost"]["value"]
    assert a["pack_size"]["value"] is None and a["moq"]["value"] is None
    assert er._eoq(100.0, K, H, a) == pytest.approx(math.sqrt(2 * 1200.0 * K / H), rel=1e-9)
    assert er._eoq(0.0, K, H, a) == 0.0


# ----------------------------------------------------------------------------- the replay
def test_replay_covers_every_product_and_three_policies_with_sensible_numbers():
    assert OUT["available"] and OUT["months"] == 12 and len(OUT["keys"]) == 15
    per = OUT["per_series"]
    assert set(per["policy"]) == {"plan", "flat", "moving"} and len(per) == 45
    assert per["fill_rate"].between(0, 1).all() and (per["avg_stock_units"] > 0).all()
    assert per["total_cost"].to_numpy() == pytest.approx((per["ordering_cost"] + per["holding_cost"]).to_numpy())
    s = OUT["summary"].set_index("policy")
    assert s.loc["flat", "avg_stock_value"] < s.loc["moving", "avg_stock_value"]                                  # no buffer holds least stock


def test_the_plan_for_each_month_uses_only_the_months_before_it():
    origin0 = OUT["first_month"] - pd.DateOffset(months=1)
    cut = origin0 + pd.DateOffset(months=3)                                                                        # months 0 to 3 must not see anything after this
    later = CLEAN.copy()
    later.loc[later["YearMonth"] > cut, "Quantity"] *= 3.0
    other = er.run_replay(later, CONFIG, RESOLVED)
    a, b = OUT["params"], other["params"]
    early = a["month"] <= cut + pd.DateOffset(months=1)
    assert early.sum() == 15 * 4
    for col in ("reorder_point", "order_quantity", "safety_stock"):
        assert b.loc[early, col].to_numpy() == pytest.approx(a.loc[early, col].to_numpy())
    assert not np.allclose(a.loc[~early, "reorder_point"], b.loc[~early, "reorder_point"])                         # later months do react


def test_same_inputs_give_the_same_replay():
    again = er.run_replay(CLEAN, CONFIG, RESOLVED)
    pd.testing.assert_frame_equal(OUT["summary"], again["summary"])
    pd.testing.assert_frame_equal(OUT["ranges"], again["ranges"])


def test_ranges_surround_the_numbers_and_the_verdict_never_overclaims():
    r = OUT["ranges"]
    fill = r[(r["metric"] == "fill_rate") & (r["policy"] == "plan") & (r["difference_to"].isna() if "difference_to" in r else True)].iloc[0]
    point = OUT["summary"].set_index("policy").loc["plan", "fill_rate"]
    assert fill["low"] - 0.01 <= point <= fill["high"] + 0.01
    for v in OUT["verdict"]:
        assert v["clear"] in {"better", "worse", "unclear"} and v["low"] <= v["high"]
        if v["metric"] == "fill_rate":
            assert (v["clear"] == "better") == (v["low"] > 0) and (v["clear"] == "worse") == (v["high"] < 0)
        else:                                                                                                  # for stock and cost, lower is better
            assert (v["clear"] == "better") == (v["high"] < 0) and (v["clear"] == "worse") == (v["low"] > 0)
        if v["clear"] == "unclear":
            assert v["low"] <= 0 <= v["high"]


def test_it_says_so_when_there_is_no_cost_or_too_little_history():
    no_cost = copy.deepcopy(RESOLVED)
    for a in no_cost.values():
        a["unit_cost"]["value"] = None
    out = er.run_replay(CLEAN, CONFIG, no_cost)
    assert not out["available"] and "unit cost" in out["reason"]
    short = CLEAN[CLEAN["YearMonth"] >= CLEAN["YearMonth"].max() - pd.DateOffset(months=14)]
    out = er.run_replay(short, CONFIG, ei.baseline_assumptions(CONFIG, short))
    assert not out["available"] and "months" in out["reason"]


def test_the_small_sample_and_early_margin_are_stated():
    assert any("small sample" in n for n in OUT["notes"]) and any("wider standard margin" in n for n in OUT["notes"])
