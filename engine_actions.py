"""Today's actions: what the stock plan says to do right now, from the stock you actually have. For every country and product it compares
stock on hand plus on order with the plan's reorder level, and says: out of stock, order now (and how much), order soon, fine, or more
than needed. Settings come from config analytics.inventory.actions. The example figures are labelled as such and are never real."""
import math

import numpy as np
import pandas as pd

import engine_inventory as ei

COLUMNS = ["country", "product", "on_hand", "on_order", "position", "reorder_point", "days_of_cover", "days_to_reorder", "status", "suggested_order",
           "order_cash", "excess_units", "excess_cash", "note"]


def act(config):
    return ei.inv(config)["actions"]


def example_stock(plan_table, config):
    """Made-up current stock, the same every time, for showing the screen before real figures exist: each product holds between
    example_low and example_high times its typical working stock (reorder level plus half an order). It is not data."""
    a = act(config)
    t = plan_table.dropna(subset=["reorder_point"])
    rng = np.random.default_rng(a["example_seed"])
    mid = t["reorder_point"].to_numpy() + t["order_quantity"].fillna(0).to_numpy() / 2.0
    return pd.DataFrame({"country": t["country"].to_numpy(), "product": t["product"].to_numpy(),
                         "on_hand": np.round(mid * rng.uniform(a["example_low"], a["example_high"], len(t)), 0), "on_order": 0.0})


def status_table(plan_table, current, config):
    """One row per plan row. `current` has country, product, on_hand and optionally on_order (blank is allowed). Rows without stock
    on hand are shown as 'Enter current stock', never guessed. Sorted so the most urgent come first."""
    a = act(config)
    t = plan_table.dropna(subset=["reorder_point"]).merge(current[["country", "product", "on_hand"] + (["on_order"] if "on_order" in current else [])],
                                                          on=["country", "product"], how="left")
    if "on_order" not in t:
        t["on_order"] = np.nan
    rows = []
    for r in t.to_dict("records"):
        rop, q = float(r["reorder_point"]), r.get("order_quantity")
        q = None if q is None or pd.isna(q) or q <= 0 else float(q)
        daily = max(float(r["forecast_next_12m"]) / 365.25, 1e-9)
        out = {"country": r["country"], "product": r["product"], "on_hand": r["on_hand"], "on_order": r["on_order"], "reorder_point": rop,
               "days_of_cover": np.nan, "days_to_reorder": np.nan, "suggested_order": np.nan, "order_cash": np.nan, "excess_units": 0.0, "excess_cash": np.nan,
               "position": np.nan, "note": ""}
        if pd.isna(r["on_hand"]):
            rows.append({**out, "status": "Enter current stock"})
            continue
        oh = float(r["on_hand"])
        oo = 0.0 if pd.isna(r["on_order"]) else float(r["on_order"])
        pos = oh + oo
        out.update({"position": pos, "days_of_cover": oh / daily})
        price = r.get("unit_cost")
        if oh <= 0:
            status = "Out of stock"
        elif pos <= rop:
            status = "Order now"
        elif pos <= rop + (min(a["soon_margin"] * rop, q) if q else a["soon_margin"] * rop):             # never wider than one order above the reorder level
            status = "Order soon"
        elif q is not None and pos > rop + a["over_orders"] * q:
            status = "More than needed"
        else:
            status = "Fine"
        if status in {"Out of stock", "Order now"}:
            if q is None:
                out["note"] = "Needs a unit cost to say how much to order."
            else:
                lots = max(math.ceil((rop - pos) / q + 1e-9), 1)
                out["suggested_order"] = lots * q
                if price is not None and not pd.isna(price):
                    out["order_cash"] = lots * q * float(price)
                out["note"] = f"Order {lots * q:,.0f} units to get back above the reorder level of {rop:,.0f}."
        elif status == "Order soon":
            out["days_to_reorder"] = (pos - rop) / daily
            out["note"] = f"Reaches the reorder level in about {out['days_to_reorder']:,.0f} days."
        elif status == "More than needed":
            target = rop + (q or 0.0)
            out["excess_units"] = pos - target
            if price is not None and not pd.isna(price):
                out["excess_cash"] = out["excess_units"] * float(price)
            out["note"] = f"About {out['excess_units']:,.0f} units above what the plan aims for. Hold off on new orders."
        rows.append({**out, "status": status})
    df = pd.DataFrame(rows)
    rank = {s: i for i, s in enumerate(a["status_order"])}
    df["_r"] = df["status"].map(rank)
    return df.sort_values(["_r", "days_of_cover"], kind="stable").drop(columns="_r").reset_index(drop=True)[COLUMNS]


def summary(status):
    """Counts and money for the headline cards."""
    s = status["status"].value_counts().to_dict()
    return {"out": s.get("Out of stock", 0), "now": s.get("Order now", 0), "soon": s.get("Order soon", 0), "fine": s.get("Fine", 0),
            "over": s.get("More than needed", 0), "missing": s.get("Enter current stock", 0),
            "order_cash": float(status["order_cash"].sum(skipna=True)), "excess_cash": float(status["excess_cash"].sum(skipna=True))}
