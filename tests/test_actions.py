"""Tests for engine_actions.py: today's actions from the stock you actually have."""
import pathlib

import numpy as np
import pandas as pd
import pytest

import engine_actions as ea_
import engine_inventory as ei
from fixtures import CONFIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAN = ei.build_plan(pd.read_csv(ROOT / "data" / "regional_orders.csv"), CONFIG, now=pd.Timestamp("2026-10-29", tz="UTC"))
T = PLAN["table"].dropna(subset=["reorder_point"])


def stock(by_key=None):
    """Current stock for every row; rows named in by_key get those values (on_hand, on_order), the rest are left blank."""
    rows = [{"country": r.country, "product": r.product, "on_hand": np.nan, "on_order": np.nan} for r in T.itertuples()]
    for row in rows:
        row.update((by_key or {}).get((row["country"], row["product"]), {}))
    return pd.DataFrame(rows)


def one(key, on_hand, on_order=0.0):
    out = ea_.status_table(PLAN["table"], stock({key: {"on_hand": on_hand, "on_order": on_order}}), CONFIG)
    return out[(out["country"] == key[0]) & (out["product"] == key[1])].iloc[0]


KEY = ("SG", "B")
ROW = T[(T["country"] == "SG") & (T["product"] == "B")].iloc[0]
ROP, Q = float(ROW["reorder_point"]), float(ROW["order_quantity"])


def test_missing_stock_is_never_guessed():
    out = ea_.status_table(PLAN["table"], stock(), CONFIG)
    assert (out["status"] == "Enter current stock").all() and out["suggested_order"].isna().all()


def test_each_status_follows_the_position_against_the_reorder_level():
    assert one(KEY, 0.0)["status"] == "Out of stock"
    assert one(KEY, ROP * 0.5)["status"] == "Order now"
    assert one(KEY, ROP * 1.1)["status"] == "Order soon"
    assert one(KEY, ROP + 1.5 * Q)["status"] == "Fine"
    assert one(KEY, ROP + 3 * Q + 1)["status"] == "More than needed"


def test_orders_on_the_way_count_towards_the_position():
    assert one(KEY, ROP * 0.6, on_order=ROP * 2)["status"] != "Order now"
    assert one(KEY, ROP * 0.6, on_order=0.0)["status"] == "Order now"


def test_suggested_order_is_whole_orders_that_clear_the_reorder_level():
    r = one(KEY, ROP * 0.2)
    lots = r["suggested_order"] / Q
    assert lots == pytest.approx(round(lots)) and lots >= 1
    assert r["position"] + r["suggested_order"] > ROP and r["position"] + r["suggested_order"] - Q <= ROP
    assert r["order_cash"] == pytest.approx(r["suggested_order"] * float(ROW["unit_cost"]))


def test_extra_stock_is_measured_against_what_the_plan_aims_for():
    r = one(KEY, ROP + 5 * Q)
    assert r["status"] == "More than needed" and r["excess_units"] == pytest.approx(4 * Q) and r["excess_cash"] == pytest.approx(4 * Q * float(ROW["unit_cost"]))


def test_most_urgent_rows_come_first_and_the_summary_adds_up():
    cur = stock({("SG", "B"): {"on_hand": 0.0}, ("MY", "B"): {"on_hand": 1e9}, ("TH", "B"): {"on_hand": float(T[(T["country"] == "TH") & (T["product"] == "B")]["reorder_point"].iloc[0]) * 1.1}})
    out = ea_.status_table(PLAN["table"], cur, CONFIG)
    order = CONFIG["analytics"]["inventory"]["actions"]["status_order"]
    ranks = [order.index(s) for s in out["status"]]
    assert ranks == sorted(ranks) and out.iloc[0]["status"] == "Out of stock"
    s = ea_.summary(out)
    assert s["out"] == 1 and s["over"] == 1 and s["soon"] == 1 and s["missing"] == 12
    assert s["order_cash"] == pytest.approx(out["order_cash"].sum())


def test_example_stock_is_repeatable_and_not_blank():
    a, b = ea_.example_stock(PLAN["table"], CONFIG), ea_.example_stock(PLAN["table"], CONFIG)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) == 15 and a["on_hand"].notna().all() and (a["on_hand"] >= 0).all()
    statuses = set(ea_.status_table(PLAN["table"], a, CONFIG)["status"])
    assert len(statuses) >= 2                                                                              # the example shows more than one situation
