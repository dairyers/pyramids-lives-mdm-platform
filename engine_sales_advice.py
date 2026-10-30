"""Plain-language advice for the Sales insights page, ranked by what the business needs right now. Every recommendation is built only
from computed results (regional growth, churn radar, cross-sell, backtest, request types), and every number it quotes comes from them.
Two choices change what appears and in what order: what matters most (grow sales, keep customers, fix weak spots, spread the risk, or a
bit of everything) and how much the team can take on. The two forecast and data-quality items serve no single goal, so they rank low
and appear when the team can take on more. Thresholds come from config analytics.sales_advice."""
import pandas as pd

TAG_LABELS = {"grow": "Grows sales", "keep": "Protects revenue", "fix": "Fixes a weak spot", "risk": "Spreads the risk",
              "trust": "Firms up the numbers", "soon": "Worth doing this month"}


def adv(config):
    return config["analytics"]["sales_advice"]


def _pair(r):
    return f"{r['country']} {r['product']}"


def _list_names(names):
    return names[0] if len(names) == 1 else f"{names[0]} and {len(names) - 1} more"


def _regional(results):
    if "regional" not in results:
        return None, None
    df, period = results["regional"]
    return (df, period) if df is not None and not df.empty else (None, None)


def _grow_top(g, a):
    t = a["thresholds"]
    top = g[g["growth_pct"] >= t["grow_min_pct"]].nlargest(t["grow_top_n"], "growth_pct")
    if top.empty:
        return None
    gained = float((top["current_qty"] - top["prior_qty"]).sum())
    total = float((g["current_qty"] - g["prior_qty"]).sum())
    effects = []
    for r in top.itertuples():
        like = f", {r.growth_excl_new_pct:+.1f}% from existing customers" if r.growth_excl_new_pct == r.growth_excl_new_pct and r.growth_excl_new_pct is not None else ""
        effects.append(f"{r.country} {r.product}: {r.growth_pct:+.1f}% ({r.prior_qty:,.0f} to {r.current_qty:,.0f} units{like})")
    if total > 0:
        effects.append(f"Together they added {gained:,.0f} units, {gained / total:.0%} of all units gained across the region.")
    names = [_pair(r) for _, r in top.iterrows()]
    return {"id": "grow_top", "title": f"Put stock and sales time behind {_list_names(names)}",
            "why": "These pairs are growing fastest, so running short on them costs the most sales.",
            "effects": effects,
            "action": "Confirm stock cover for them first (Stock planning tab) and give them first call on sales time.",
            "tags": ["grow"]}


def _lean_on_new(g, a):
    t = a["thresholds"]
    d = g.dropna(subset=["growth_excl_new_pct"]).copy()
    d["gap"] = d["growth_pct"] - d["growth_excl_new_pct"]
    sel = d[d["gap"] >= t["lean_gap_pts"]].nlargest(t["lean_top_n"], "gap")
    if sel.empty:
        return None
    effects = [f"{r.country} {r.product}: {r.growth_pct:+.1f}% with all customers, {r.growth_excl_new_pct:+.1f}% from existing ones "
               f"({r.new_account_qty:,.0f} units came from accounts with no earlier volume)" for r in sel.itertuples()]
    names = [_pair(r) for _, r in sel.iterrows()]
    return {"id": "new_account_lean", "title": f"Treat growth in {_list_names(names)} as unproven",
            "why": "Growth that comes from a few new accounts can disappear if one of them leaves, so it is a weaker reason to build stock "
                   "than steady growth from existing customers.",
            "effects": effects,
            "action": "Plan stock on the existing-customer rate until the new accounts have ordered for a full year, and keep them well served.",
            "tags": ["risk", "keep"]}


def _weak(g, a):
    t = a["thresholds"]
    weak = g[g["growth_pct"] < t["weak_below_pct"]].nsmallest(t["weak_top_n"], "growth_pct")
    if weak.empty:
        return None
    total_prior, total_now = float(g["prior_qty"].sum()), float(g["current_qty"].sum())
    effects = [f"{r.country} {r.product}: {r.growth_pct:+.1f}% ({r.prior_qty:,.0f} to {r.current_qty:,.0f} units)" for r in weak.itertuples()]
    if total_prior > 0:
        effects.append(f"The whole region grew {(total_now - total_prior) / total_prior * 100:+.1f}% over the same months.")
    names = [_pair(r) for _, r in weak.iterrows()]
    return {"id": "weak_pairs", "title": f"Find out why {_list_names(names)} {'is' if len(names) == 1 else 'are'} lagging",
            "why": "Flat or falling volume while the rest of the region grows usually points to price, competition or account coverage.",
            "effects": effects,
            "action": "Review pricing, competitor activity and which accounts you cover for these pairs before adding stock.",
            "tags": ["fix"]}


def _concentration(df, a, config):
    share_min = a["thresholds"]["concentration_share"]
    by = df.groupby("country")[["current_qty", "prior_qty"]].sum()
    total, gained = float(by["current_qty"].sum()), float((by["current_qty"] - by["prior_qty"]).sum())
    if total <= 0:
        return None
    top = by["current_qty"].idxmax()
    share = float(by.loc[top, "current_qty"]) / total
    if share < share_min:
        return None
    effects = [f"{top} is {share:.0%} of all units in the last 12 months ({by.loc[top, 'current_qty']:,.0f} of {total:,.0f})."]
    if gained > 0:
        mine = float(by.loc[top, "current_qty"] - by.loc[top, "prior_qty"])
        effects.append(f"It also supplied {mine / gained:.0%} of the units gained.")
    return {"id": "concentration", "title": f"Do not depend on {top} alone",
            "why": "When one market carries this much of the volume, a bad year there moves the whole total.",
            "effects": effects,
            "action": "Set a target share for the next markets and check coverage and pricing in the slower-growing countries.",
            "tags": ["risk"]}


def _churn(results, a, config):
    ch = results.get("churn")
    if ch is None or ch.empty or not ch["flagged"].any():
        return None
    f = ch[ch["flagged"]]
    cc = config["analytics"]["churn"]
    labels = config["analytics"]["regional_view"]["country_labels"]
    base, recent = float(f["baseline_avg"].sum()), float(f["recent_avg"].sum())
    where = f["U_Country"].map(lambda c: labels.get(c, c)).value_counts()
    effects = [f"Together they used to order about {base:,.0f} units a month and now order about {recent:,.0f}, "
               f"{base - recent:,.0f} units a month less ({(base - recent) * 12:,.0f} a year at this rate).",
               "Where: " + ", ".join(f"{k} {v}" for k, v in where.items()) + "."]
    n = len(f)
    return {"id": "churn", "title": f"Call the {n} account{'s' if n != 1 else ''} that {'are' if n != 1 else 'is'} buying less",
            "why": f"Each one's last {cc['recent_window_months']} months are more than {abs(cc['velocity_threshold']):.0%} below its earlier "
                   f"{cc['baseline_window_months']}-month average, which is how most lost accounts start.",
            "effects": effects,
            "action": "Give each one to an account manager for a call this month and ask what changed.",
            "tags": ["keep", "soon"]}


def _cross_sell(results, config):
    if "cross_sell" not in results:
        return None
    gaps, stats = results["cross_sell"]
    if gaps is None or len(gaps) == 0:
        return None
    cs, lines = config["analytics"]["cross_sell"], config["analytics"]["product_lines"]
    ante = " or ".join(lines[p] for p in cs["antecedent_products"])
    cons = lines[cs["consequent_product"]]
    conf = stats.get("overall", {}).get("confidence")
    effects = [f"{len(gaps)} account{'s' if len(gaps) != 1 else ''} bought {ante} in the last {cs['window_days']} days but no {cons}."]
    if conf is not None:
        effects.append(f"Across all accounts {conf:.0%} of {ante} buyers also buy {cons}.")
    return {"id": "cross_sell", "title": f"Offer a {cons} bundle to {len(gaps)} account{'s' if len(gaps) != 1 else ''} that do not buy it",
            "why": f"Accounts that buy {ante} usually buy {cons} too, so these are the easiest extra sales.",
            "effects": effects,
            "action": f"Send each a bundled {cons} quote this month.",
            "tags": ["grow", "soon"]}


def _forecast_trust(results):
    if "backtest" not in results:
        return None
    _, s = results["backtest"]
    if s.get("overall_wape_model") is None:
        return None
    base = s["overall_wape_baseline"]
    best = min(base, key=lambda k: base[k])
    wins, label = s["model_wins"][best], s.get("model_label", "the model")
    months = best.replace("wape_avg", "").replace("m", "")
    effects = [f"Over the last {s['holdout_months']} months ({s['holdout_start']} to {s['holdout_end']}, {s['series']} series) the {label} forecast "
               f"was off by {s['overall_wape_model']:.1f}%, against {base[best]:.1f}% for the best rolling average ({months}-month).",
               f"It was closer on {wins} of {s['series']} series."]
    weak = s["model_not_better"][best]
    if weak:
        effects.append("Read these by hand before relying on it: " + ", ".join(weak) + ".")
    if s["overall_wape_model"] < base[best]:
        return {"id": "forecast_trust", "title": "Plan with the model, not the rolling average",
                "why": "A forecast is only worth using if it beats the simple rule on months it never saw.",
                "effects": effects, "action": "Use the model's figure for planning and treat the rolling average only as the yardstick.", "tags": ["trust"]}
    return {"id": "forecast_trust", "title": "Keep the rolling average for now",
            "why": "The model did not beat the simple rule on months it never saw, so it has not earned the right to replace it.",
            "effects": effects, "action": "Keep planning with the rolling average and rerun this test when there is more history.", "tags": ["trust"]}


def _data_quality(results, a):
    rt = results.get("request_types")
    if rt is None or rt.empty:
        return None
    excluded = rt[~rt["used_in_forecast"]]
    total, left_out = float(rt["rows"].sum()), float(excluded["rows"].sum())
    if total <= 0 or left_out / total < a["thresholds"]["dq_min_share"]:
        return None
    parts = ", ".join(f"{int(r.rows)} {r.label.split(' (')[0].lower()}" for r in excluded.itertuples())
    return {"id": "data_quality", "title": f"Check the {int(left_out):,} order rows left out of every number",
            "why": "Drafts, cancellations and enquiries are not demand, but orders held up waiting for master-data setup may be real sales.",
            "effects": [f"{int(left_out):,} of {int(total):,} order rows ({left_out / total:.1%}) are not counted: {parts}."],
            "action": "Ask the master-data team to clear the orders waiting for setup, and review whether drafts are being left open.",
            "tags": ["trust"]}


def candidates(results, config):
    a = adv(config)
    df, _ = _regional(results)
    out = []
    if df is not None:
        g = df.dropna(subset=["growth_pct"])
        if not g.empty:
            out += [_grow_top(g, a), _lean_on_new(g, a), _weak(g, a)]
        out.append(_concentration(df, a, config))
    out += [_churn(results, a, config), _cross_sell(results, config), _forecast_trust(results), _data_quality(results, a)]
    return [r for r in out if r]


def recommend(results, config, goal=None, capacity=None):
    """A ranked list of recommendations (dicts: id, title, why, effects, action, tags, matches, score). Those that serve the chosen goal
    come first; the capacity setting caps how many are returned."""
    a = adv(config)
    goal = goal if goal in a["goals"] else a["default_goal"]
    capacity = capacity if capacity in a["capacity"] else a["default_capacity"]
    boost = set(a["goals"][goal]["boost"])
    recs = candidates(results, config)
    for r in recs:
        r["matches"] = bool(boost & set(r["tags"]))
        r["score"] = a["priority"][r["id"]] + (a["boost"] if r["matches"] else 0)
    recs.sort(key=lambda r: -r["score"])
    return recs[: a["capacity"][capacity]["n"]]
