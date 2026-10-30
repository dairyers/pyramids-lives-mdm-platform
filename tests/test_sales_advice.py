"""The Sales insights advice: it changes with the goal and the capacity, quotes computed numbers, and reads its thresholds from config."""
import copy

import pytest

import engine_analytics as ea
import engine_sales_advice as esa
from fixtures import CONFIG
from test_analytics import AS_OF, CLEAN, RAW

AD = CONFIG["analytics"]["sales_advice"]


@pytest.fixture(scope="module")
def results():
    regional = ea.summarize_regional_volumes(CLEAN, CONFIG)
    return {"regional": regional, "request_types": ea.summarize_request_types(RAW, CONFIG),
            "backtest": ea.backtest_forecast_vs_baseline(CLEAN, CONFIG),
            "churn": ea.calculate_churn_radar(CLEAN, CONFIG, as_of=AS_OF),
            "cross_sell": ea.calculate_cross_sell_opportunities(CLEAN, CONFIG, as_of=AS_OF)}


def test_every_recommendation_is_complete_and_tags_are_known(results):
    recs = esa.recommend(results, CONFIG, "balanced", "all")
    assert recs
    for r in recs:
        assert {"id", "title", "why", "effects", "action", "tags", "matches", "score"} <= set(r)
        assert r["effects"] and set(r["tags"]) <= set(esa.TAG_LABELS) and r["id"] in AD["priority"]
    assert len({r["id"] for r in recs}) == len(recs)


def test_each_goal_puts_its_own_kind_of_advice_first(results):
    for goal, spec in AD["goals"].items():
        if not spec["boost"]:
            continue
        recs = esa.recommend(results, CONFIG, goal, "all")
        assert set(recs[0]["tags"]) & set(spec["boost"]), goal
        flags = [r["matches"] for r in recs]
        assert flags == sorted(flags, reverse=True)          # matching advice always comes before the rest


def test_changing_the_goal_changes_the_top_advice(results):
    tops = {goal: esa.recommend(results, CONFIG, goal, "few")[0]["id"] for goal in AD["goals"]}
    assert len(set(tops.values())) >= 4


def test_capacity_caps_the_list(results):
    for key, spec in AD["capacity"].items():
        assert len(esa.recommend(results, CONFIG, "balanced", key)) <= spec["n"]
    assert len(esa.recommend(results, CONFIG, "balanced", "few")) < len(esa.recommend(results, CONFIG, "balanced", "all"))


def test_numbers_come_from_the_computed_results(results):
    df, _ = results["regional"]
    recs = {r["id"]: r for r in esa.recommend(results, CONFIG, "balanced", "all")}
    top = df.loc[df["growth_pct"].idxmax()]
    assert f"{top['growth_pct']:+.1f}%" in " ".join(recs["grow_top"]["effects"]) and top["country"] in recs["grow_top"]["title"]
    flagged = results["churn"][results["churn"]["flagged"]]
    assert f"Call the {len(flagged)} account" in recs["churn"]["title"]
    gaps = results["cross_sell"][0]
    assert f"{len(gaps)} account" in recs["cross_sell"]["effects"][0]
    assert f"{results['backtest'][1]['overall_wape_model']:.1f}%" in " ".join(recs["forecast_trust"]["effects"])
    text = " ".join(r["title"] + " ".join(r["effects"]) for r in recs.values())
    assert not any(code in text for code in flagged["CardCode"])         # customer codes never appear in the advice


def test_thresholds_are_read_from_config(results):
    cfg = copy.deepcopy(CONFIG)
    cfg["analytics"]["sales_advice"]["thresholds"]["grow_min_pct"] = 500.0
    assert "grow_top" not in {r["id"] for r in esa.recommend(results, cfg, "balanced", "all")}
    assert "grow_top" in {r["id"] for r in esa.recommend(results, CONFIG, "balanced", "all")}
    cfg2 = copy.deepcopy(CONFIG)
    cfg2["analytics"]["sales_advice"]["thresholds"]["concentration_share"] = 0.99
    assert "concentration" not in {r["id"] for r in esa.recommend(results, cfg2, "balanced", "all")}


def test_unknown_choices_fall_back_to_the_defaults_and_empty_input_gives_nothing(results):
    assert esa.recommend(results, CONFIG, "nope", "nope") == esa.recommend(results, CONFIG, AD["default_goal"], AD["default_capacity"])
    assert esa.recommend({}, CONFIG) == []
