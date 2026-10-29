"""The inventory layer reads forecasts and must never change them. tests/golden_forecasts.json holds forecast values
recorded from the code before the inventory layer existed; these tests fail if any of them moves."""
import json
import pathlib

import pandas as pd
import pytest

import engine_analytics as ea
from fixtures import CONFIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests" / "golden_forecasts.json").read_text(encoding="utf-8"))
CLEAN = ea.clean_transaction_data(pd.read_csv(ROOT / "data" / "regional_orders.csv"), CONFIG)


@pytest.mark.parametrize("name", sorted(GOLDEN["series"]))
def test_series_forecast_is_unchanged(name):
    *key, model = name.split("|")
    now = ea.generate_demand_forecast(CLEAN, *key, CONFIG, model=model)["forecast"].dropna().tolist()
    assert now == pytest.approx(GOLDEN["series"][name], abs=1e-5)


def test_backtest_headline_is_unchanged():
    _, s = ea.backtest_models(CLEAN, CONFIG)
    g = GOLDEN["backtest"]
    assert s["best_model"] == g["best_model"] and s["series"] == g["series"]
    assert s["overall_wape"] == pytest.approx(g["overall_wape"]) and s["overall_wape_baseline"] == pytest.approx(g["baseline"])
    _, r = ea.backtest_rolling(CLEAN, CONFIG)
    assert r["headline_model"] == GOLDEN["rolling"]["headline_model"] and r["windows"] == GOLDEN["rolling"]["windows"]
