"""Tests for engine_advice.py: recommendations that change with the company's goal and risk comfort, each backed by a real what-if."""
import pathlib

import pandas as pd
import pytest

import engine_advice as ad
import engine_inventory as ei
from fixtures import CONFIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = pd.read_csv(ROOT / "data" / "regional_orders.csv")
NOW = pd.Timestamp("2026-10-29", tz="UTC")
PLAN = ei.build_plan(RAW, CONFIG, now=NOW)


def what_if(per_key):
    return ei.build_plan(RAW, CONFIG, overrides=per_key, only=list(per_key), now=NOW)


def ids(goal, risk):
    return [r["id"] for r in ad.recommend(PLAN, what_if, CONFIG, goal, risk)]


def test_every_recommendation_has_a_reason_effects_and_an_action():
    recs = ad.recommend(PLAN, what_if, CONFIG, "balanced", "high")
    assert recs and len(recs) <= CONFIG["analytics"]["inventory"]["advice"]["top_n"]
    for r in recs:
        assert r["title"] and r["why"] and r["effects"] and r["action"] and r["tags"] and isinstance(r["adds_risk"], bool)


def test_a_company_that_accepts_risk_is_told_to_hold_less_but_a_cautious_one_is_not():
    bold = ids("cost", "high")
    assert "slow_spare" in bold or "dial_down" in bold
    cautious = ad.recommend(PLAN, what_if, CONFIG, "cost", "low")
    assert not [r for r in cautious if r["adds_risk"]]                                      # nothing that raises the chance of running short


def test_never_run_out_with_low_risk_suggests_a_higher_safety_level():
    recs = ad.recommend(PLAN, what_if, CONFIG, "service", "low")
    assert recs[0]["id"] == "dial_up" and recs[0]["impact"] < 0                              # it costs money, and says so
    assert "dial_up" not in ids("cost", "high")


def test_goal_decides_what_comes_first():
    items = [{"id": "a", "tags": ["cost"], "impact": 5000.0, "cash": 0.0}, {"id": "b", "tags": ["cash"], "impact": 0.0, "cash": 200000.0},
             {"id": "c", "tags": ["service"], "impact": -100.0, "cash": 0.0}, {"id": "d", "tags": ["confidence"], "impact": 0.0, "cash": 0.0}]
    assert [i["id"] for i in ad.rank(items, "cost", CONFIG)][0] == "a"
    assert [i["id"] for i in ad.rank(items, "cash", CONFIG)][0] == "b"
    assert [i["id"] for i in ad.rank(items, "service", CONFIG)][0] == "c"
    assert [i["id"] for i in ad.rank(items, "cost", CONFIG)][-1] == "d"                      # "firm up the plan" items always follow


def test_the_money_in_a_recommendation_is_what_the_what_if_returns():
    rec = next(r for r in ad.recommend(PLAN, what_if, CONFIG, "cost", "high") if r["id"] == "slow_spare")
    res = what_if({k: {"service_level": 0.90} for k in rec["keys"]})
    after, before = res["table"].set_index(["country", "product"]), PLAN["table"].set_index(["country", "product"])
    assert rec["impact"] == pytest.approx(before.loc[rec["keys"], "yearly_cost"].sum() - after.loc[rec["keys"], "yearly_cost"].sum())
    assert rec["cash"] == pytest.approx(before.loc[rec["keys"], "average_inventory_value"].sum() - after.loc[rec["keys"], "average_inventory_value"].sum())
    assert rec["impact"] > 0 and rec["cash"] > 0


def test_guessed_lead_times_are_flagged_as_something_to_confirm():
    recs = ad.recommend(PLAN, what_if, CONFIG, "balanced", "medium")
    assert "confirm_lead" in [r["id"] for r in recs]
    confirm = next(r for r in recs if r["id"] == "confirm_lead")
    assert len(confirm["keys"]) == 15 and "longer" in confirm["effects"][0]


def test_same_inputs_give_the_same_advice():
    a = ad.recommend(PLAN, what_if, CONFIG, "cash", "medium")
    b = ad.recommend(PLAN, what_if, CONFIG, "cash", "medium")
    assert [(r["id"], r["impact"], r["cash"]) for r in a] == [(r["id"], r["impact"], r["cash"]) for r in b]
