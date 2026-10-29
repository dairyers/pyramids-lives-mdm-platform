"""Plain-language recommendations from the stock plan. Each one is backed by a real what-if: the affected products are planned again
with the suggested change, and the money, cash and spare stock that moved are reported. Which recommendations appear, and in what
order, depends on two choices: what matters most (cut costs, free up cash, never run out, or a bit of everything) and how much
chance of running short the company can accept. All thresholds come from config analytics.inventory.advice."""
import numpy as np
import pandas as pd

import engine_inventory as ei

TAG_LABELS = {"cost": "Saves money", "cash": "Frees up cash", "service": "Protects availability", "confidence": "Firms up the plan",
              "effort": "Fewer orders to place", "risk": "Adds some risk"}


def adv(config):
    return ei.inv(config)["advice"]


def _usd(v):
    return f"${abs(v):,.0f}"


def _chance(service):
    return f"{(1 - service) * 100:.0f}%"


def _per_key(table):
    return table.set_index(["country", "product"])


def _moved(before, after, keys):
    """What changed for `keys` between the plan and a what-if plan: yearly ordering+holding cost, money tied up, spare stock, orders a year."""
    b, a = _per_key(before).loc[keys], _per_key(after["table"]).loc[keys]
    return {"cost": float(a["yearly_cost"].sum(min_count=1) - b["yearly_cost"].sum(min_count=1)) if a["yearly_cost"].notna().any() else None,
            "money": float(a["average_inventory_value"].sum(min_count=1) - b["average_inventory_value"].sum(min_count=1)) if a["average_inventory_value"].notna().any() else None,
            "spare": float(a["safety_stock"].sum() - b["safety_stock"].sum()),
            "orders_before": float(b["orders_per_year"].mean()) if b["orders_per_year"].notna().any() else None,
            "orders_after": float(a["orders_per_year"].mean()) if a["orders_per_year"].notna().any() else None}


def _names(keys, config):
    labels = config["analytics"]["regional_view"]["country_labels"]
    lines = config["analytics"]["product_lines"]
    shown = [f"{labels.get(c, c)} {lines.get(p, p).lower()}" for c, p in keys[:4]]
    return ", ".join(shown) + (f" and {len(keys) - 4} more" if len(keys) > 4 else "")


def recommend(plan, what_if, config, goal=None, risk=None):
    """A ranked list of recommendations for the plan. `what_if(per_key_values)` plans the given {(country, product): {assumption: value}}
    again and returns a plan dict (the UI passes a cached function). Each recommendation is a dict: id, title, why, effects (list of
    short lines), action, tags, adds_risk, impact (yearly money in the company's favour, or 0), cash (cash freed), keys."""
    a = adv(config)
    goal, risk = goal or a["default_goal"], risk or a["default_risk"]
    max_short = a["risk_levels"][risk]["max_short"]
    care = ei.inv(config)["care_levels"]
    levels = sorted(v["service_level"] for v in care.values())
    allowed = [s for s in levels if 1 - s <= max_short + 1e-9]                      # service levels the company is willing to run at
    lowest_ok = min(allowed) if allowed else max(levels)
    t = plan["table"].dropna(subset=["reorder_point"]).copy()
    out = []
    if t.empty:
        return out
    has_cost = t["average_inventory_value"].notna().any()
    typical = float(t["days_of_stock"].median()) if t["days_of_stock"].notna().any() else None
    cur = float(t["service_level"].mode().iloc[0])

    # 1. hold less spare stock where stock already sits longest, down to the lowest level the company accepts
    if typical:
        slow = t[(t["days_of_stock"] > typical * a["slow_factor"]) & (t["service_level"] > lowest_ok + 1e-9)]
        keys = list(zip(slow["country"], slow["product"]))
        if keys:
            res = what_if({k: {"service_level": lowest_ok} for k in keys})
            m = _moved(t, res, keys)
            eff = []
            if m["cost"] is not None:
                eff += [f"Saves about {_usd(m['cost'])} a year" if m["cost"] < 0 else f"Costs about {_usd(m['cost'])} more a year",
                        f"Frees about {_usd(m['money'])} of cash" if m["money"] < 0 else f"Ties up {_usd(m['money'])} more cash"]
            eff += [f"Spare stock changes by {m['spare']:+,.0f} units", f"Chance of running short on these rises from {_chance(float(slow['service_level'].mean()))} to {_chance(lowest_ok)}"]
            out.append({"id": "slow_spare", "title": "Hold less spare stock where stock already sits longest",
                        "why": f"{len(keys)} product-country pairs hold more than {a['slow_factor']:.2f} times the typical {typical:,.0f} days of stock: {_names(keys, config)}.",
                        "effects": eff, "action": f"Set their safety level to the lowest you accept ({care[[c for c, v in care.items() if abs(v['service_level'] - lowest_ok) < 1e-9][0]]['label']}).",
                        "tags": ["cost", "cash", "risk"], "adds_risk": True, "impact": -(m["cost"] or 0.0), "cash": -(m["money"] or 0.0), "keys": keys})

    # 2. the product holding most of the money: shorter supply lead time
    if has_cost:
        share = t.groupby("product")["average_inventory_value"].sum()
        top = share.idxmax()
        if share.max() / share.sum() >= a["high_share"]:
            rows = t[(t["product"] == top) & (t["lead_time_months"] > a["lead_shift_months"])]
            keys = list(zip(rows["country"], rows["product"]))
            if keys:
                res = what_if({k: {"lead_time_months": float(r) - a["lead_shift_months"]} for k, r in zip(keys, rows["lead_time_months"])})
                m = _moved(t, res, keys)
                lines = config["analytics"]["product_lines"]
                out.append({"id": "lead_big", "title": f"Get {lines.get(top, top).lower()} delivered faster",
                            "why": f"{lines.get(top, top)} holds {share.max() / share.sum():.0%} of all the money tied up in stock, so a shorter wait pays off most here.",
                            "effects": [f"Each {a['lead_shift_months']:g} month faster saves about {_usd(m['cost'])} a year" if (m["cost"] or 0) < 0 else "Little change in yearly cost",
                                        f"Frees about {_usd(m['money'])} of cash" if (m["money"] or 0) < 0 else "No cash change", f"Spare stock changes by {m['spare']:+,.0f} units"],
                            "action": "Ask suppliers for a shorter lead time, split deliveries, or keep a small local buffer.",
                            "tags": ["cash", "cost"], "adds_risk": False, "impact": -(m["cost"] or 0.0), "cash": -(m["money"] or 0.0), "keys": keys})

    # 3. move every product one step on the safety dial (from the plan's own trade-off table)
    tr = plan["tradeoff"].groupby("service_level")[["safety_stock", "holding_cost_of_safety_stock"]].sum(min_count=1)
    if len(tr) > 1 and cur in tr.index:
        here = tr.loc[cur]
        lower = [s for s in tr.index if s < cur - 1e-9 and 1 - s <= max_short + 1e-9]
        if lower:
            s = max(lower)
            save = float(here["holding_cost_of_safety_stock"] - tr.loc[s, "holding_cost_of_safety_stock"]) if tr["holding_cost_of_safety_stock"].notna().any() else None
            out.append({"id": "dial_down", "title": f"Lower the safety level for every product to {care[[c for c, v in care.items() if abs(v['service_level'] - s) < 1e-9][0]]['label']}",
                        "why": f"Your risk setting allows running short about {_chance(s)} of the time, and the plan is currently set to {_chance(cur)}.",
                        "effects": ([f"Saves about {_usd(save)} a year in keeping spare stock"] if save is not None else [])
                                   + [f"Spare stock falls by {here['safety_stock'] - tr.loc[s, 'safety_stock']:,.0f} units", f"Chance of running short rises from {_chance(cur)} to {_chance(s)}"],
                        "action": "Choose this setting under 'How careful should we be'.", "tags": ["cost", "cash", "risk"], "adds_risk": True,
                        "impact": save or 0.0, "cash": 0.0, "keys": []})
        higher = [s for s in tr.index if s > cur + 1e-9]
        if higher and (goal == "service" or risk == "low"):
            s = min(higher)
            extra = float(tr.loc[s, "holding_cost_of_safety_stock"] - here["holding_cost_of_safety_stock"]) if tr["holding_cost_of_safety_stock"].notna().any() else None
            out.append({"id": "dial_up", "title": f"Raise the safety level for every product to {care[[c for c, v in care.items() if abs(v['service_level'] - s) < 1e-9][0]]['label']}",
                        "why": "You want products to be on the shelf, and a higher safety level is the direct way to get there.",
                        "effects": ([f"Costs about {_usd(extra)} more a year in keeping spare stock"] if extra is not None else [])
                                   + [f"Spare stock rises by {tr.loc[s, 'safety_stock'] - here['safety_stock']:,.0f} units", f"Chance of running short falls from {_chance(cur)} to {_chance(s)}"],
                        "action": "Choose this setting under 'How careful should we be'.", "tags": ["service"], "adds_risk": False,
                        "impact": -(extra or 0.0), "cash": 0.0, "keys": []})

    # 4. cash locked in slow movers, if they were brought back to the typical days of stock
    if has_cost and typical:
        s = t[t["days_of_stock"] > typical * a["slow_factor"]].dropna(subset=["average_inventory_value"])
        freed = float((s["average_inventory_value"] * (1 - typical / s["days_of_stock"])).sum()) if len(s) else 0.0
        if freed > 0:
            keys = list(zip(s["country"], s["product"]))
            out.append({"id": "slow_cash", "title": "Run down stock that sits longer than most",
                        "why": f"{len(keys)} product-country pairs hold more than {a['slow_factor']:.2f} times the typical {typical:,.0f} days of stock.",
                        "effects": [f"Frees roughly {_usd(freed)} of cash if they came back to the typical {typical:,.0f} days (assumes stock falls in proportion)"],
                        "action": "Order smaller amounts, or pause reorders until stock falls, while keeping the reorder level.",
                        "tags": ["cash"], "adds_risk": False, "impact": 0.0, "cash": freed, "keys": keys})

    # 5. very frequent ordering depends on a placeholder order cost
    rows = t[(t["orders_per_year"].notna()) & (365.25 / t["orders_per_year"] < a["min_cycle_days"])]
    keys = list(zip(rows["country"], rows["product"]))
    if keys:
        mult = a["order_cost_multiplier"]
        res = what_if({k: {"order_cost": float(c) * mult} for k, c in zip(keys, rows["order_cost"])})
        m = _moved(t, res, keys)
        out.append({"id": "order_cost", "title": "Check what one order really costs",
                    "why": f"{len(keys)} product-country pairs are ordered more than every {a['min_cycle_days']} days. That result leans on the cost per order, which is still a guess.",
                    "effects": [f"If an order really costs {mult:g} times more, orders a year would fall from {m['orders_before']:,.0f} to {m['orders_after']:,.0f} on average"],
                    "action": "Confirm the real cost of placing an order, and consider consolidated or standing orders.",
                    "tags": ["effort", "confidence"], "adds_risk": False, "impact": 0.0, "cash": 0.0, "keys": keys})

    # 6. the guesses the plan leans on most
    guessed = t[t["flags"].fillna("").str.contains("Lead time is a default")]
    if len(guessed):
        keys = list(zip(guessed["country"], guessed["product"]))
        res = what_if({k: {"lead_time_months": float(r) + a["lead_shift_months"]} for k, r in zip(keys, guessed["lead_time_months"])})
        m = _moved(t, res, keys)
        out.append({"id": "confirm_lead", "title": "Confirm supplier lead times before acting on this",
                    "why": f"{len(keys)} product-country pairs use a guessed lead time, and it moves the answer more than almost anything else.",
                    "effects": [f"If lead times are {a['lead_shift_months']:g} month longer, spare stock changes by {m['spare']:+,.0f} units"
                                + (f" and money tied up by {m['money']:+,.0f} USD" if m["money"] is not None else "")],
                    "action": "Put the real lead times into the grid, or include order and receipt dates in your upload.",
                    "tags": ["confidence"], "adds_risk": False, "impact": 0.0, "cash": 0.0, "keys": keys})

    return rank(out, goal, config)


def rank(items, goal, config):
    """Items that serve the chosen goal first, biggest effect first; the 'firm up the plan' items always follow, then the rest. Top N."""
    fits = {"cost": {"cost"}, "cash": {"cash"}, "service": {"service"}, "balanced": {"cost", "cash", "service"}}[goal]

    def order(it):
        confirm = it["tags"] == ["confidence"] or it["id"] in {"confirm_lead", "order_cost"}
        serves = bool(fits & set(it["tags"]))
        size = abs(it["impact"]) + abs(it["cash"]) * 0.1
        return (confirm, not serves, -size)
    return sorted(items, key=order)[:adv(config)["top_n"]]
