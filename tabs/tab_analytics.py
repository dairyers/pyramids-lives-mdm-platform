"""Sales insights (spec Section 4): the regional business view (volumes and growth for Products A, B, C
across five countries), a choice of demand-forecast models (6 to 12 months of history each) with a backtest against the traditional rolling
average, Churn Radar and Cross-sell. Access comes from config.roles[role].analytics: NONE -> page hidden
(app.py); AGGREGATE -> forecasts and counts only; ACCOUNT_LEVEL -> also the account tables. Only the
analytics output columns are displayed. Cached functions take no role."""
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import engine_analytics as ea
import engine_sales_advice as esa
from tabs import charts, common, ui

# Categorical slots 1-3 in the app palette (validated: CVD worst dE 10.8, normal 21.5, >= 3:1 on the page):
# actual, fitted, forecast; and products A, B, C. Kept in step with chartCategoricalColors in .streamlit/config.toml.
SLOTS = ["#008a7a", "#c26f1e", "#7a5cc0"]
RULE = "#e7e5e4"
SERIES = {"actual": SLOTS[0], "fitted": SLOTS[1], "forecast": SLOTS[2]}


INCLUDE_KEY = "fc_include"


def included():
    """Which kinds of rows the user has ticked as 'used in forecast' (default: confirmed POs only). Part of every
    cache key below, so changing the ticks recomputes everything that depends on the data."""
    chosen = st.session_state.get(INCLUDE_KEY)
    return tuple(sorted(chosen)) if chosen else tuple(ea.default_include(common.base_config()))


def _by_selection(fn):
    """Cache fn(include, *args) and call it as fn(*args) with the current ticks filled in."""
    def cached(include, *args):
        return fn(include, *args)
    cached.__name__, cached.__qualname__ = fn.__name__, fn.__qualname__
    cached = st.cache_data(cached)

    def call(*args):
        return cached(included(), *args)
    return call


@st.cache_data
def _clean(include):
    return ea.clean_transaction_data(common.load_orders(), common.base_config(), include=list(include))


@_by_selection
def forecast(include, entity, country, line, model=None, lookback=None, horizon=None):
    return ea.generate_demand_forecast(_clean(include), entity, country, line, common.base_config(), model=model,
                                       lookback=lookback, horizon=horizon)


@_by_selection
def trend_facts(include):
    return ea.overall_trend(_clean(include), common.base_config())


@_by_selection
def model_matrix(include):
    return ea.backtest_matrix(_clean(include), common.base_config())


@_by_selection
def churn(include):
    return ea.calculate_churn_radar(_clean(include), common.base_config())


@_by_selection
def cross_sell(include):
    return ea.calculate_cross_sell_opportunities(_clean(include), common.base_config())


@_by_selection
def monthly(include):
    """Aggregated units and USD per month, country, product line and customer status, New or Existing (no customer rows)."""
    d = _with_status(include)
    d["margin"] = d["LineTotal_USD"] * d["U_ProductLine"].map(common.base_config()["analytics"]["margin"]["by_product"])
    return d.groupby(["YearMonth", "U_Country", "U_ProductLine", "status"])[["Quantity", "LineTotal_USD", "margin"]].sum().reset_index()


def _with_status(include):
    """The cleaned orders with a 'status' column: New or Existing, as the regional view defines a new account."""
    d = _clean(include).copy()
    status = ea.account_status(d, common.base_config())
    d["status"] = status.reindex(pd.MultiIndex.from_frame(d[["CardCode", "U_Country", "U_ProductLine"]])).to_numpy()
    return d


@_by_selection
def new_existing(include):
    return ea.summarize_new_vs_existing(_clean(include), common.base_config())


@_by_selection
def rolling(include):
    return ea.backtest_rolling(_clean(include), config=common.base_config())


COMPARE_BY = {"U_Country": "Country", "U_ProductLine": "Product", "U_Entity": "Business entity",
              "status": "New vs existing customers"}
COMPARE_MEASURES = {"usd": "Sales value (USD)", "margin": "Gross margin (USD, assumed)", "units": "Units",
                    "margin_pct": "Gross margin % (assumed)", "price": "Price per unit (USD)",
                    "customers": "Active customers", "basket": "Units per customer"}
RATIOS = ("price", "basket", "margin_pct")        # averages cannot be stacked or shared out, only drawn as lines


@_by_selection
def compare_series(include, by, measure, country_codes, products):
    """Monthly values of one measure, one column per country, product or entity. Aggregates only: customer ids are
    counted here and never leave."""
    d = _with_status(include)
    if country_codes:
        d = d[d["U_Country"].isin(country_codes)]
    if products:
        d = d[d["U_ProductLine"].isin(products)]
    d = d.assign(margin=d["LineTotal_USD"] * d["U_ProductLine"].map(common.base_config()["analytics"]["margin"]["by_product"]))
    g = d.groupby(["YearMonth", by])
    units, usd, customers, margin = (g["Quantity"].sum(), g["LineTotal_USD"].sum(), g["CardCode"].nunique(), g["margin"].sum())
    series = {"usd": usd, "units": units, "price": usd / units, "customers": customers, "basket": units / customers,
              "margin": margin, "margin_pct": margin / usd * 100}[measure]
    return series.unstack(by).sort_index()


@st.cache_resource
def map_points():
    """Capital and demo-city coordinates for the map (presentation data, not business rules)."""
    with open(common.ROOT / "data" / "map_points.json", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


@_by_selection
def counts(include):
    return ea.aggregate_counts(_clean(include), common.base_config())


@_by_selection
def regional(include):
    return ea.summarize_regional_volumes(_clean(include), common.base_config())


@_by_selection
def request_types(include):
    return ea.summarize_request_types(common.load_orders(), common.base_config(), include=list(include))


@_by_selection
def backtest(include):
    return ea.backtest_forecast_vs_baseline(_clean(include), common.base_config())


def _country(config, code):
    return config["analytics"]["regional_view"]["country_labels"].get(code, code)


def _options(config, field):
    return next(f["options"] for f in config["domains"]["CUSTOMER"]["fields"] if f["field"] == field)


OTHERS = ["#78716c", "#a16207", "#0e7490", "#be185d", "#4d7c0f"]


def forecast_figure(df, plan=None, others=(), band_pct=None):
    """History, the chosen model's forecast with a band of its typical test error (band_pct, WAPE in %), optional
    comparison models (thin dashed) and an approved plan."""
    hist, fut = df[df["actual"].notna()], df[df["forecast"].notna()]
    fig = go.Figure()
    if band_pct is not None and not fut.empty:
        half = fut["forecast"] * band_pct / 100.0
        fig.add_trace(go.Scatter(x=list(fut["period"]) + list(fut["period"])[::-1],
                                 y=list(fut["forecast"] + half) + list((fut["forecast"] - half).clip(lower=0))[::-1],
                                 fill="toself", fillcolor="rgba(122,92,192,.14)", line={"width": 0}, hoverinfo="skip",
                                 name=f"Typical error band (±{band_pct:.1f}% in the test)"))
    if plan is not None:
        fig.add_trace(go.Scatter(x=plan["months"], y=plan["plan"], name="Approved plan", mode="lines+markers",
                                 line={"color": "#1c1917", "width": 3}, marker={"size": 9, "symbol": "diamond"}))
    fig.add_trace(go.Scatter(x=hist["period"], y=hist["actual"], name="Actual", mode="lines+markers",
                             line={"color": SERIES["actual"], "width": 2}, marker={"size": 8}))
    fig.add_trace(go.Scatter(x=hist["period"], y=hist["fitted"], name="Fitted (how the model reads the past)", mode="lines",
                             line={"color": SERIES["fitted"], "width": 2, "dash": "dot"}))
    fig.add_trace(go.Scatter(x=fut["period"], y=fut["forecast"], name="Forecast (chosen model)", mode="lines+markers",
                             line={"color": SERIES["forecast"], "width": 3, "dash": "dash"}, marker={"size": 8}))
    for colour, (name, other) in zip(OTHERS, others):
        o = other[other["forecast"].notna()]
        fig.add_trace(go.Scatter(x=o["period"], y=o["forecast"], name=name, mode="lines",
                                 line={"color": colour, "width": 1.5, "dash": "dot"}))
    if not fut.empty:
        fig.add_vline(x=fut["period"].min(), line_width=1, line_dash="dot", line_color="#57534e")
    fig.update_layout(hovermode="x unified", yaxis_title="Quantity (units)", xaxis_title=None, legend={"y": -0.22})
    fig.update_yaxes(rangemode="tozero")
    return fig


# ----------------------------------------------------------------------------- overview visuals (aggregates only)
def trend_figure(cur, pri, months, offset, label="Units"):
    """The chosen measure per month for the current window against the same months a year earlier."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=months, y=pri.to_numpy(), name=f"Same months {offset} months earlier", mode="lines",
                             line={"color": charts.NEUTRAL, "width": 2, "dash": "dot"}))
    fig.add_trace(go.Scatter(x=months, y=cur.to_numpy(), name="Last 12 months", mode="lines+markers",
                             line={"color": charts.BRAND, "width": 3}, marker={"size": 6}, fill="tozeroy",
                             fillcolor="rgba(0,112,242,.10)"))
    fig.update_layout(hovermode="x unified", yaxis_title=label.split(" (")[0], legend={"y": -0.16})
    fig.update_xaxes(tickformat="%b %y")
    fig.update_yaxes(rangemode="tozero", tickformat="~s")
    return fig


def heatmap_figure(df, column, lines, unit="units"):
    """Country by product growth, red to green around zero, the figure written in each cell."""
    countries = list(dict.fromkeys(df["country"]))
    z = [[_cell(df, c, p, column) for p in lines] for c in countries]
    units = [[_cell(df, c, p, "current_qty") for p in lines] for c in countries]
    spread = sorted(abs(v) for row in z for v in row if v is not None)
    reach = max(spread[int(len(spread) * .75)] if spread else 1.0, 1.0)      # one outlier must not wash out the rest
    fig = go.Figure(go.Heatmap(
        z=z, x=[f"{n} ({c})" for c, n in lines.items()], y=countries, zmin=-reach, zmax=reach, colorscale=charts.DIVERGING,
        text=[[f"{v:+.1f}%" if v is not None else "n/a" for v in row] for row in z], texttemplate="%{text}",
        textfont={"size": 13}, customdata=units, xgap=3, ygap=3, showscale=False,
        hovertemplate="%{y}, %{x}<br>Growth %{text}<br>%{customdata:,.0f} " + unit + "<extra></extra>"))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(side="top")
    return fig


def _cell(df, country, line, column):
    d = df[(df["country"] == country) & (df["U_ProductLine"] == line)]
    if d.empty or pd.isna(d.iloc[0][column]):
        return None
    return float(d.iloc[0][column])


def rolling_figure(rb, headline):
    """Error of the headline model on each test window, against the traditional averages."""
    fig = go.Figure()
    for i, (name, d) in enumerate(rb[rb["kind"] == "baseline"].groupby("name")):
        fig.add_trace(go.Scatter(x=d["window"], y=d["wape"], name=name, mode="lines+markers",
                                 line={"color": ["#8396A8", "#c26f1e"][i % 2], "width": 2, "dash": "dot"}, marker={"size": 7}))
    d = rb[(rb["kind"] == "model") & (rb["name"] == headline)]
    fig.add_trace(go.Scatter(x=d["window"], y=d["wape"], name=headline, mode="lines+markers+text", text=[f"{v:.1f}%" for v in d["wape"]],
                             textposition="top center", line={"color": charts.BRAND, "width": 3}, marker={"size": 9}))
    fig.update_layout(yaxis_title="Error (WAPE %)", hovermode="x unified", legend={"y": -0.2})
    fig.update_yaxes(rangemode="tozero", ticksuffix="%")
    return fig


def compare_figure(series, labels, view, measure_label):
    """Monthly lines or areas, one per country, product or entity. view: Stacked area, Share of total, Lines, or
    Index (each line starts at 100 so growth can be compared whatever the size)."""
    fig = go.Figure()
    for col in series.columns:
        y = series[col]
        if view == "Index":
            first = y[y > 0].iloc[:1]
            y = y / first.iloc[0] * 100 if len(first) else y
        stack = {"stackgroup": "one", "groupnorm": "percent" if view == "Share of total" else ""} \
            if view in ("Stacked area", "Share of total") else {}
        fig.add_trace(go.Scatter(x=series.index, y=y, name=labels.get(col, str(col)), mode="lines",
                                 line={"width": 2.2 if not stack else 1.2}, **stack))
    axis = {"Share of total": "Share of total (%)", "Index": "Index, first month = 100"}.get(view, measure_label)
    fig.update_layout(hovermode="x unified", yaxis_title=axis, legend={"y": -0.16})
    fig.update_xaxes(tickformat="%b %y")
    fig.update_yaxes(rangemode="tozero" if view != "Index" else "normal", tickformat="~s" if view != "Share of total" else None,
                     ticksuffix="%" if view == "Share of total" else "")
    return fig


def change_figure(df, unit="units"):
    """Gained or lost against the year before, by country, largest gain at the top. delta_qty is already worked out for
    the chosen measure and customer group."""
    d = df.groupby("country")[["current_qty", "prior_qty", "delta_qty"]].sum()
    d["change"] = d["delta_qty"]
    d = d.sort_values("change")
    fig = go.Figure(go.Bar(x=d["change"], y=d.index, orientation="h",
                           marker_color=[charts.GOOD if v >= 0 else charts.BAD for v in d["change"]],
                           text=[f"{v:+,.0f}" for v in d["change"]], textposition="outside", cliponaxis=False,
                           customdata=d[["prior_qty", "current_qty"]].to_numpy(),
                           hovertemplate="%{y}: %{x:+,.0f} " + unit + "<br>year before %{customdata[0]:,.0f}, now "
                                         "%{customdata[1]:,.0f}<extra></extra>"))
    fig.update_layout(showlegend=False, margin={"r": 40})
    fig.update_xaxes(showgrid=True, gridcolor=charts.GRID, zeroline=True, zerolinecolor=charts.MUTED, tickformat="~s")
    fig.update_yaxes(showgrid=False)
    return fig


def map_rows(df, points):
    """The places on the map. One dot per country on its capital; with one country picked, one per city of that country
    (units split by the shares in data/map_points.json). Returns (DataFrame: place, lat, lon, units, growth, share; opened)."""
    d = df.groupby("country").agg(units=("current_qty", "sum"), prior=("prior_qty", "sum"), delta=("delta_qty", "sum")).reset_index()
    d["growth"] = [round(c / p * 100, 1) if p else None for c, p in zip(d["delta"], d["prior"])]
    code = df.groupby("country")["U_Country"].first()
    one = len(d) == 1
    rows = []
    for r in d.itertuples():
        place = points[code[r.country]]
        if one:
            rows += [(c["name"], c["lat"], c["lon"], r.units * c["share"], r.growth) for c in place["cities"]]
        else:
            rows.append((r.country, place["capital"]["lat"], place["capital"]["lon"], r.units, r.growth))
    out = pd.DataFrame(rows, columns=["place", "lat", "lon", "units", "growth"])
    out["share"] = (out["units"] / out["units"].sum() * 100).round(1) if out["units"].sum() else 0.0
    return out.sort_values("units", ascending=False).reset_index(drop=True), one


def _label_sides(lat, lon, near=3.0):
    """Labels go above their dot, except for dots with a close neighbour: the western one is labelled on its left and
    the eastern one on its right, so neighbours such as Singapore and Kuala Lumpur do not print over each other."""
    out = []
    for i, (la, lo) in enumerate(zip(lat, lon)):
        close = [lon[j] for j in range(len(lat)) if j != i and abs(lat[j] - la) < near and abs(lon[j] - lo) < near]
        out.append("top center" if not close else "middle left" if lo <= min(close) else "middle right")
    return out


def map_figure(rows, opened, ratio=1.6, unit="units"):
    """A heat dot on each place: size is units ordered, colour is growth (or units when a country is opened up into its
    cities, which share one growth figure). The frame is centred on the dots, widened to `ratio` so it fills the card."""
    names, lat, lon, units, growth = (rows[c].tolist() for c in ("place", "lat", "lon", "units", "growth"))
    if opened:
        values, title, shown = units, unit.capitalize(), "%{text}<br>%{customdata[0]:,.0f} " + unit
        scale, lo, hi = charts.RAMP, min(values) - (max(values) - min(values)) * .3, max(values)
    else:
        g = [v for v in growth if v is not None] or [0.0]
        values, title = [v or 0.0 for v in growth], "Growth %"
        shown = "%{text}<br>%{customdata[0]:,.0f} " + unit + "<br>Growth %{customdata[1]:+.1f}%"
        # a red-to-green scale when something shrank; otherwise a single blue ramp so small gains are still visible
        scale, lo, hi = ((charts.DIVERGING, -max(map(abs, g)), max(map(abs, g))) if min(g) < 0
                         else (charts.RAMP, min(g) - (max(g) - min(g)) * .3, max(g)))
    size = [10 + 18 * u / max(max(units), 1) for u in units]
    custom = [[u, g] for u, g in zip(units, growth)]
    fig = go.Figure(go.Scattergeo(lat=lat, lon=lon, mode="markers", hoverinfo="skip", showlegend=False,
                                  marker={"size": [s * 1.6 for s in size], "color": values, "colorscale": scale, "cmin": lo,
                                          "cmax": hi, "opacity": .22, "line": {"width": 0}}))          # the glow of the heat dot
    fig.add_trace(go.Scattergeo(
        lat=lat, lon=lon, text=names, mode="markers+text", textposition=_label_sides(lat, lon),
        textfont={"size": 11, "color": charts.TEXT},
        marker={"size": size, "color": values, "colorscale": scale, "cmin": lo, "cmax": hi, "opacity": .95,
                "line": {"width": 1.5, "color": "#ffffff"}, "colorbar": {"title": {"text": title}, "thickness": 10, "len": .6}},
        customdata=custom, hovertemplate=shown + "<extra></extra>"))
    lo_lat, hi_lat, lo_lon, hi_lon = min(lat), max(lat), min(lon), max(lon)
    lat_span, lon_span = max(hi_lat - lo_lat, 0.3) * 1.35, max(hi_lon - lo_lon, 0.3) * 1.35
    lon_span, lat_span = max(lon_span, lat_span * ratio), max(lat_span, lon_span / ratio)
    mid_lat, mid_lon = (lo_lat + hi_lat) / 2, (lo_lon + hi_lon) / 2
    fig.update_layout(showlegend=False)
    fig.update_geos(projection_type="mercator", resolution=50, lataxis_range=[mid_lat - lat_span / 2, mid_lat + lat_span / 2],
                    lonaxis_range=[mid_lon - lon_span / 2, mid_lon + lon_span / 2], showland=True, landcolor="#EFF1F2",
                    showcountries=True, countrycolor="#BCC3CA", showocean=True, oceancolor="#F5F6F7", showframe=False,
                    bgcolor="rgba(0,0,0,0)", coastlinecolor="#BCC3CA")
    return fig


def _compare(config, countries, products):
    """An interactive comparison across the full history: pick what to measure, what to compare by and how to draw it.
    Follows the country and product filters above."""
    lines, labels_c = config["analytics"]["product_lines"], config["analytics"]["regional_view"]["country_labels"]
    codes = tuple(k for k, v in labels_c.items() if v in countries)
    with st.container(border=True):
        charts.chart_title("Compare", "Pick a measure, what to compare by and how to draw it. Follows the filters above.")
        c1, c2, c3 = st.columns([2, 3, 3])
        measure = c1.selectbox("Measure", list(COMPARE_MEASURES), format_func=COMPARE_MEASURES.get, key="an_cmp_measure")
        by = c2.segmented_control("Compare by", list(COMPARE_BY), default="U_Country", format_func=COMPARE_BY.get,
                                  key="an_cmp_by") or "U_Country"
        views = ["Lines"] if measure in RATIOS else ["Stacked area", "Share of total", "Lines", "Index"]
        view = c3.segmented_control("Show as", views, default=views[0], key=f"an_cmp_view_{'r' if measure in RATIOS else 'a'}") \
            or views[0]
        series = compare_series(by, measure, codes, tuple(products))
        if series.empty:
            st.info("Nothing to compare for those filters.")
            return
        labels = {**labels_c, **{k: f"{v} ({k})" for k, v in lines.items()},
                  "New": "New customers", "Existing": "Existing customers"}
        charts.show(compare_figure(series, labels, view, COMPARE_MEASURES[measure]), "an_cmp_chart", 360)


# ----------------------------------------------------------------------------- overview
COLUMN = {"units": "Quantity", "usd": "LineTotal_USD", "margin": "margin"}      # which monthly column each measure reads
BASES = {"units": ("Units", "units"), "usd": ("Sales value (USD)", "USD"), "margin": ("Gross margin (USD)", "USD margin")}
GROUPS = {"all": "All customers", "existing": "Existing customers", "new": "New customers"}
BLOCKS = {"kpi": "Headline numbers", "map": "Map", "trend": "Units per month", "heat": "Growth heatmap",
          "change": "Gained or lost", "compare": "Compare chart"}


def _filters(config, df):
    """The filter bar. Every visual on the Overview follows it, and the user also chooses what to measure, which customers
    to look at and which blocks to show. Returns (countries, products, basis, group, shown blocks); an empty pick means all."""
    lines = config["analytics"]["product_lines"]
    with st.container(key="an_filters"):
        c1, c2, c3, c4 = st.columns([5, 5, 7, 5])
        countries = c1.multiselect("Country", list(dict.fromkeys(df["country"])), key="an_f_country",
                                   placeholder="All countries")
        products = c2.multiselect("Product", list(lines), key="an_f_product", placeholder="All products",
                                  format_func=lambda k: f"{lines[k]} ({k})")
        basis = c3.segmented_control("Measure", list(BASES), default="units", key="an_f_basis",
                                     format_func=lambda b: BASES[b][0].split(" (")[0]) or "units"
        group = c4.segmented_control("Customers", list(GROUPS), default="all", key="an_f_group",
                                     format_func=lambda g: GROUPS[g].replace(" customers", "")) or "all"
        with st.popover("Customise page", icon=":material/tune:"):
            st.caption("Choose which blocks appear on this page.")
            shown = {k: st.checkbox(v, value=True, key=f"an_show_{k}") for k, v in BLOCKS.items()}
    return countries, products, basis, group, shown


def _basis(df, nve, margins, basis, group):
    """The regional table reshaped for the chosen measure (units, sales value or gross margin) and customer group.
    Columns keep the names the figures read: current_qty is the value now, prior_qty the same months a year earlier,
    delta_qty the change attributed to the group (new customers: everything they bought, as none bought before), and
    growth_pct that change as a percentage of the year before."""
    d = df.merge(nve[["U_Country", "U_ProductLine", "existing_qty", "new_qty", "existing_usd", "new_usd"]],
                 on=["U_Country", "U_ProductLine"])
    scale = d["U_ProductLine"].map(margins) if basis == "margin" else 1.0
    if basis == "units":
        total, prior, existing, new = d["current_qty"], d["prior_qty"], d["existing_qty"], d["new_qty"]
    else:
        total, prior, existing, new = (d[c] * scale for c in ("current_usd", "prior_usd", "existing_usd", "new_usd"))
    cur = {"all": total, "existing": existing, "new": new}[group]
    delta = {"all": total - prior, "existing": existing - prior, "new": new}[group]
    out = d[["U_Country", "country", "U_ProductLine", "product"]].copy()
    out["current_qty"], out["prior_qty"], out["delta_qty"] = cur, prior, delta
    out["growth_pct"] = [round(x / p * 100, 1) if p else None for x, p in zip(delta, prior)]
    return out


def _series(m, period, countries, products, column, group="all"):
    """(current months, prior months, month index) of one measure for the filtered countries, products and customers."""
    if countries:
        m = m[m["U_Country"].isin(countries)]
    if products:
        m = m[m["U_ProductLine"].isin(products)]
    if group != "all":
        m = m[m["status"] == GROUPS[group].split()[0]]
    cur = pd.date_range(period["current_start"], periods=period["window_months"], freq="MS")
    pri = cur - pd.DateOffset(months=period["prior_offset_months"])
    s = m.groupby("YearMonth")[column].sum()
    return s.reindex(cur, fill_value=0.0), s.reindex(pri, fill_value=0.0), cur


def _pct(cur, prior):
    return (cur - prior) / prior * 100 if prior else None


def _headline(config, sel, period, m, countries, products, basis, group):
    """Four KPI cards: units and sales value (all customers) with a monthly sparkline, then the best and weakest country
    and product for the chosen measure and customer group."""
    cu, pu, _ = _series(m, period, countries, products, "Quantity")
    cv, pv, _ = _series(m, period, countries, products, "LineTotal_USD")
    valid = sel.dropna(subset=["growth_pct"])
    cards = [("Units, last 12 months", f"{cu.sum():,.0f}", charts.delta_chip(_pct(cu.sum(), pu.sum()), " vs year before"),
              charts.spark(cu), ""),
             ("Sales value, last 12 months (USD)", f"${charts.compact(cv.sum())}",
              charts.delta_chip(_pct(cv.sum(), pv.sum()), " vs year before"), charts.spark(cv, "#008a7a"), "")]
    suffix = " of last year's volume added" if group == "new" else " a year"
    for label, colour, pick in (("Fastest growing", charts.GOOD, "idxmax"), ("Needs attention", charts.BAD, "idxmin")):
        if valid.empty:
            cards.append((label, "n/a", "", "", ""))
            continue
        row = valid.loc[getattr(valid["growth_pct"], pick)()]
        s, _, _ = _series(m, period, [row["U_Country"]], [row["U_ProductLine"]], COLUMN[basis], group)
        cards.append((label, f"{row['country']} · {row['product']}", charts.delta_chip(row["growth_pct"], suffix),
                      charts.spark(s, colour), f"{BASES[basis][0].split(' (')[0]}, {GROUPS[group].lower()}"))
    for col, args in zip(st.columns(len(cards)), cards):
        with col:
            charts.card(*args)


def _overview(config, state):
    df, period = regional()
    if df.empty:
        st.warning("No confirmed purchase orders to summarise.")
        return
    bdf, bs = backtest()
    lines = config["analytics"]["product_lines"]
    ui.section("Where are we growing?", f"Customer orders, {period['current_start']} to {period['current_end']} "
                                        f"({period['window_months']} months), compared with the same months a year earlier.",
               tip="How growth is measured: " + period["definition"]
               + ("" if period["prior_complete"] else " Warning: the prior window starts before the data does.")
               + " 'Existing customers' removes accounts that bought nothing in the earlier period, so you can "
                 "tell growth from new customers apart from growth in what current customers buy.")
    countries, products, basis, group, shown = _filters(config, df)
    view = df[(df["country"].isin(countries) if countries else df["country"].notna())
              & (df["U_ProductLine"].isin(products) if products else df["country"].notna())]
    if view.empty:
        st.info("Nothing matches those filters.")
    else:
        m = monthly()
        names = {v: k for k, v in config["analytics"]["regional_view"]["country_labels"].items()}
        codes = [names.get(c, c) for c in countries]
        margins = config["analytics"]["margin"]["by_product"]
        sel = _basis(view, new_existing(), margins, basis, group)
        label, unit = BASES[basis]
        who = GROUPS[group].lower()
        if basis == "margin":
            st.caption(config["analytics"]["margin"]["note"])
        if shown["kpi"]:
            _headline(config, sel, period, m, codes, products, basis, group)
            st.write("")
        if shown["map"]:
            rows, opened = map_rows(sel, map_points())
            with st.container(border=True):
                charts.chart_title(f"Where the {label.lower().split(' (')[0]} goes, {who}",
                                   "By city. Pick one country above to open the map up to its cities" if opened
                                   else "Southeast Asia: dot size is the amount, colour is growth. "
                                        "Pick one country above to open the map up to its cities")
                wide, side = st.columns([5, 2])
                with wide:
                    charts.show(map_figure(rows, opened, unit=unit), "an_map", 480)
                with side:
                    ui.table(pd.DataFrame({"Place": rows["place"], label.split(" (")[0]: rows["units"].round(0).astype(int),
                                           "Share": rows["share"].map("{:.1f}%".format) if opened
                                           else rows["growth"].map(lambda g: "n/a" if pd.isna(g) else f"{g:+.1f}%")}).rename(
                        columns={"Share": "Share of country" if opened else "Growth"}), hide_index=True,
                        column_config={label.split(" (")[0]: st.column_config.NumberColumn(format="%d")})
        blocks = [k for k in ("trend", "heat", "change") if shown[k]]
        for slot, key in zip(st.columns(len(blocks)) if blocks else [], blocks):
            with slot, st.container(border=True):
                if key == "trend":
                    cu, pu, months = _series(m, period, codes, products, COLUMN[basis], group)
                    charts.chart_title(f"{label.split(' (')[0]} per month, {who}", "Last 12 months against a year earlier")
                    charts.show(trend_figure(cu, pu, months, period["prior_offset_months"], label), "an_trend", 320)
                elif key == "heat":
                    charts.chart_title("Growth by country and product", f"Year-on-year change in {label.lower().split(' (')[0]}, {who}")
                    charts.show(heatmap_figure(sel, "growth_pct", {k: v for k, v in lines.items() if not products or k in products},
                                               unit), "an_heat", 320)
                else:
                    charts.chart_title(f"{label.split(' (')[0]} gained or lost", f"Against the year before, {who}")
                    charts.show(change_figure(sel, unit), "an_change", 320)

    if not view.empty and shown["compare"]:
        _compare(config, countries, products)

    rt = request_types()
    gaps, stats = cross_sell()
    _advice({"regional": (df, period), "request_types": rt, "backtest": (bdf, bs), "churn": churn(), "cross_sell": (gaps, stats)}, config)

    with st.expander("Full numbers, including value in USD"):
        table = df[["country", "U_ProductLine", "product", "current_qty", "prior_qty", "growth_pct", "new_account_qty",
                    "growth_excl_new_pct", "current_usd", "usd_growth_pct"]].sort_values(["country", "U_ProductLine"])
        names = {c: _country(config, c) for c in table["country"].unique()}
        pick = st.selectbox("Country", ["All countries"] + [names[c] for c in sorted(names, key=names.get)],
                            key="an_full_country")
        if pick != "All countries":
            table = table[table["country"].map(names) == pick]
        st.download_button("Download CSV", table.to_csv(index=False).encode("utf-8"),
                           file_name=f"regional_numbers_{pick.lower().replace(' ', '_')}.csv", mime="text/csv",
                           key="an_full_csv", icon=":material/download:")
        ui.table(table, hide_index=True, column_config={
            "country": "Country", "U_ProductLine": "Line", "product": "Product",
            "current_qty": st.column_config.NumberColumn("Units (current)", format="%d"),
            "prior_qty": st.column_config.NumberColumn("Units (year before)", format="%d"),
            "growth_pct": st.column_config.NumberColumn("Growth %", format="%+.1f"),
            "new_account_qty": st.column_config.NumberColumn("Units from new accounts", format="%d"),
            "growth_excl_new_pct": st.column_config.NumberColumn("Existing customers %", format="%+.1f"),
            "current_usd": st.column_config.NumberColumn("Value (USD)", format="localized"),
            "usd_growth_pct": st.column_config.NumberColumn("Value growth %", format="%+.1f")})


def _advice(results, config):
    """What should we do: recommendations that change with what the business needs and how much the team can take on."""
    a = esa.adv(config)
    badge = {"grow": "green", "keep": "blue", "fix": "orange", "risk": "gray", "trust": "gray", "soon": "red"}
    with st.container(key="an_actions"):
        ui.section("What should we do?", "Tell us what the business needs and how much the team can take on. The advice below changes with your "
                                         "answers, and every number comes from the results on this page (all countries and products).")
        c = st.columns(2)
        goal = c[0].segmented_control("What matters most right now?", list(a["goals"]), default=a["default_goal"], key="an_goal",
                                      format_func=lambda g: a["goals"][g]["label"]) or a["default_goal"]
        cap = c[1].segmented_control("How much can the team take on?", list(a["capacity"]), default=a["default_capacity"], key="an_capacity",
                                     format_func=lambda k: a["capacity"][k]["label"]) or a["default_capacity"]
        st.caption(f"For a business that wants to {a['goals'][goal]['plain']}, and {a['capacity'][cap]['plain']}:")
        recs = esa.recommend(results, config, goal, cap)
        if not recs:
            st.info("Nothing stands out. There is not enough data for advice yet.")
            return
        for i, r in enumerate(recs, 1):
            with st.container(border=True):
                tags = ([":blue-badge[Matches what you chose]"] if r["matches"] and goal != a["default_goal"] else []) + \
                    [f":{badge[t]}-badge[{esa.TAG_LABELS[t]}]" for t in r["tags"]]
                st.markdown(f"**{i}. {r['title']}**  " + " ".join(tags))
                st.markdown(r["why"])
                for line in r["effects"]:
                    st.markdown(f"- {line}")
                st.markdown(f"**What to do:** {r['action']}")


def _data_in_use(config, state):
    """Which kinds of order rows feed every number on this page. A data-quality control, kept with the forecast's
    advanced settings and not on the overview; render() shows a warning above the tabs when it is changed."""
    rt = request_types()
    nf = config["analytics"]["noise_filter"]
    setup = pd.Series([r["domain"] for r in state.requests_db], dtype=object).value_counts()
    ui.section("Which orders count", "Tick the kinds of rows used for the forecast and for every number on the Sales insights page.",
               tip=f"By default only {nf['record_type']} rows with status {' or '.join(nf['status_in'])} count as customer "
                   "orders. Enquiries, drafts, cancellations and orders still waiting for master-data setup are kept out. "
                   "Requests to set up master data (the New request page) are a separate workflow and never enter the "
                   f"forecast: {len(state.requests_db)} in this session"
                   + (f" ({', '.join(f'{k.title()} {v}' for k, v in setup.items())})" if len(setup) else "") + ".")
    shown = rt[["label", "used_in_forecast", "rows", "quantity", "value_usd", "share_of_rows_pct"]]
    edited = ui.editor(shown, hide_index=True, key="an_include_editor",
                       disabled=["label", "rows", "quantity", "value_usd", "share_of_rows_pct"],
                       column_config={"label": "Type", "used_in_forecast": st.column_config.CheckboxColumn("Counted"),
                                      "rows": "Rows", "quantity": "Units",
                                      "share_of_rows_pct": st.column_config.NumberColumn("Share %", format="%.1f"),
                                      "value_usd": st.column_config.NumberColumn("Value (USD)", format="localized")})
    picked = tuple(sorted(rt.loc[edited["used_in_forecast"].to_numpy(), "category"]))
    if picked != included():
        if picked:
            st.session_state[INCLUDE_KEY] = list(picked)
            st.rerun()
        st.warning("Tick at least one kind of row. The page keeps using the previous choice.")


def _data_warning(config):
    """Above every tab when the user has changed which rows count."""
    if included() != tuple(ea.default_include(config)):
        st.warning("You changed which orders count, so every number on this page now includes those rows.",
                   icon=":material/tune:")
        st.button("Back to confirmed orders only", key="an_include_reset",
                  on_click=lambda: st.session_state.pop(INCLUDE_KEY, None))


# ----------------------------------------------------------------------------- forecast and accounts
def _approve_roles(config):
    return config["analytics"]["approve_roles"]


def _forecast_review(config, state, user, key, label, df, how=''):
    """Human-in-the-loop for the forecast: the model's numbers are a proposal. The planner can change any month,
    add a note, and must approve before the figures count as the plan."""
    fut = df[df["forecast"].notna()]
    months = fut["period"].dt.strftime("%Y-%m").tolist()
    model = fut["forecast"].round(1).tolist()
    approved = state.setdefault("approved_forecasts", {})
    ui.section("Review before you use it", "Change any month you disagree with, then approve it.",
               tip="The forecast is the model's proposal. Only an approved plan counts.")
    can = user["role"] in _approve_roles(config)
    gen = state.get("fc_gen", 0)
    edit = pd.DataFrame({"Month": months, "Model forecast": model, "Your plan": model})
    edited = ui.editor(edit, hide_index=True, key=f"fc_edit_{'_'.join(key)}_{gen}", disabled=["Month", "Model forecast"],
                            column_config={"Your plan": st.column_config.NumberColumn("Your plan (editable)", min_value=0.0,
                                                                                      step=1.0, format="%.1f")})
    note = st.text_input("Why did you change it? (optional)", key=f"fc_note_{'_'.join(key)}_{gen}")
    changed = int((edited["Your plan"].round(1) != edited["Model forecast"]).sum())
    if can:
        if st.button("Approve this plan", key=f"fc_ok_{'_'.join(key)}", type="primary"):
            approved[key] = {"label": label, "months": months, "model": model,
                             "plan": edited["Your plan"].round(1).tolist(), "by": user["display"],
                             "at": common.now(state).strftime("%d %b %Y"), "note": note.strip(), "changed": changed, "how": how}
            common.log(state, config, user, "FORECAST_APPROVED", None, {"series": label, "months": len(months), "changed": changed})
            st.rerun()
        st.caption(f"{changed} month(s) changed from the model's proposal." if changed else "No changes from the model's proposal.")
    else:
        st.info("You can review but not approve. Approval is limited to: "
                + ", ".join(config["roles"][r]["label"] for r in _approve_roles(config)) + ".")
    if key in approved:
        a = approved[key]
        st.success(f"Approved by {a['by']} on {a['at']}: {sum(a['plan']):,.0f} units over {len(a['months'])} months "
                   f"(model proposed {sum(a['model']):,.0f}; {a.get('how', '')})." + (f" Note: {a['note']}" if a["note"] else ""),
                   icon=":material/verified:")


def _model_card(config, model):
    copy = common.load_ui()["models"][model]
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**What it does**  \n{copy['what']}")
        c2.markdown(f"**Why choose it**  \n{copy['why']}")
        c3.markdown(f"**Watch out for**  \n{copy['watch']}")


def _comparison(config):
    """The model comparison table, the observations and the plain-language notes on every model."""
    fcfg = config["analytics"]["forecast"]
    matrix, runs = model_matrix()
    ui.section("Model comparison and observations", "Average error on months each model never saw. Lower is better.",
               tip="Each model was fitted on older months only and asked to forecast months it had never seen. The table "
                   "shows how far off it was, on average, across the 15 country and product series. 'Traditional' rows are "
                   "today's method: a flat average of the last 6 or 12 months. The model rows use the number of months of "
                   "history shown in each column.")
    show = matrix.round(1).reset_index().rename(columns={"index": "Model"})
    ui.table(show, hide_index=True, column_config={c: st.column_config.NumberColumn(c, format="%.1f%%")
                                                   for c in show.columns if c != "Model"})
    st.markdown("**What we observed**")
    for line in ea.build_model_observations(matrix, runs, config):
        st.markdown(f"- {line}")


def _rolling_section(config):
    """The backtest repeated on earlier windows, so the headline error is not one lucky window."""
    rb, summary = rolling()
    ui.section("Does the accuracy hold up?", "The same test repeated on earlier windows. Every point is the error on months the model never saw.",
               tip="For each window the models are fitted only on the months before it, then asked to forecast the window. "
                   "The headline model is the one that is most accurate on the latest window. Error is WAPE: total absolute "
                   "miss divided by total actual units, across the 15 country and product series. Lower is better.")
    if rb.empty:
        st.info("Not enough history for a rolling test.")
        return
    headline = ea._model_label(config, summary["headline_model"])
    model = rb[rb["name"] == headline]["wape"]
    base = rb[rb["kind"] == "baseline"].groupby("window")["wape"].min()
    with st.container(border=True):
        charts.chart_title(f"{headline}: error on {summary['windows']} test windows",
                           f"Between {model.min():.1f}% and {model.max():.1f}%, against {base.min():.1f}% to {base.max():.1f}% for the "
                           "better traditional average on the same windows")
        charts.show(rolling_figure(rb, headline), "an_rolling", 300)
    with st.expander("Every model on every window"):
        ui.table(rb.pivot(index="name", columns="window", values="wape").round(1).reset_index().rename(columns={"name": "Method"}),
                 hide_index=True)


def _forecast_tab(config, state, user):
    fcfg, lines = config["analytics"]["forecast"], config["analytics"]["product_lines"]
    text = common.load_ui()
    matrix, runs = model_matrix()
    best = (runs.get(fcfg["lookback_months"]) or next(iter(runs.values())))["best_model"] or fcfg["default_model"]
    label = lambda m: ea._model_label(config, m)                                             # noqa: E731
    ui.note("Choose a model, how much history it uses and how far ahead to look.", text["models_intro"])

    c1, c2, c3 = st.columns(3)
    entity = c1.selectbox("Business entity", _options(config, "U_Entity"), key="an_entity")
    country = c2.selectbox("Country", _options(config, "U_Country"), key="an_country",
                           format_func=lambda c: _country(config, c))
    line = c3.selectbox("Product", list(lines), key="an_line", format_func=lambda k: f"Product {k}: {lines[k]}")
    m1, m2, m3 = st.columns([2, 2, 2])
    choices = ea.selectable_models(config)
    model = m1.selectbox("Forecast model", choices, index=choices.index(best), key="an_model",
                         format_func=label)
    lookback = m2.segmented_control("Months of history used", fcfg["lookback_options"], default=fcfg["lookback_months"],
                                    key="an_lookback", format_func=lambda n: f"{n} months") or fcfg["lookback_months"]
    horizon = m3.segmented_control("Forecast ahead", fcfg["horizon_options"], default=fcfg["horizon_months"],
                                   key="an_horizon", format_func=lambda n: f"{n} months") or fcfg["horizon_months"]
    advanced = st.toggle("Advanced: compare the models and see how each one performed", key="an_advanced")
    others = [m for m in fcfg["models"] if m != model]
    compare = (st.multiselect("Draw other models on the chart for comparison", others,
                              default=fcfg.get("comparison_only", []), key="an_compare",
                              format_func=label) if advanced else [])
    if advanced:
        _model_card(config, model)

    df = forecast(entity, country, line, model, lookback, horizon)
    series_label = f"{entity} | {_country(config, country)} | Product {line}: {lines[line]}"
    key = (entity, country, line)
    if df.empty:
        st.warning(f"Not enough history to forecast {series_label} (fewer than {fcfg['min_points']} months).")
    else:
        plan = state.get("approved_forecasts", {}).get(key)
        extra = [(label(m), forecast(entity, country, line, m, lookback, horizon)) for m in compare]
        band = matrix.loc[label(model), f"{lookback} months of history"] if label(model) in matrix.index else None
        with st.container(border=True):
            charts.chart_title(series_label, f"Next {horizon} months · {label(model)} · {lookback} months of history")
            charts.show(forecast_figure(df, plan, extra, None if pd.isna(band) else float(band)), "an_chart", 380)
            facts = trend_facts() if model in ea.NEEDS_GROWTH else None
            if facts:
                waves = facts["waves"]
                st.caption(f"Yearly pattern: {waves} wave{'s' if waves != 1 else ''}, chosen from the data. Overall growth: "
                           f"{facts['monthly_growth_pct']:+.2f}% a month (about {facts['annual_growth_pct']:+.1f}% a year), "
                           f"from a straight-line fit across all {facts['series']} country and product series.")
        _forecast_review(config, state, user, key, series_label, df, f"{label(model)}, {lookback} months of history")
    rows = []
    for c in _options(config, "U_Country"):
        f = forecast(entity, c, line, model, lookback, horizon)
        rows.append({"Country": _country(config, c),
                     "Months of history": int(f["actual"].notna().sum()) if not f.empty else 0,
                     "Model": label(model),
                     "Next month forecast (units)": round(float(f["forecast"].dropna().iloc[0]), 1) if not f.empty else None,
                     "Approved?": "Yes" if (entity, c, line) in state.get("approved_forecasts", {}) else "No"})
    st.markdown(f"**All five countries for {entity}, Product {line}: {lines[line]}** ({label(model)})")
    ui.table(pd.DataFrame(rows).astype(str), hide_index=True)

    _rolling_section(config)
    if advanced:
        _data_in_use(config, state)
        _comparison(config)
        ui.section("About the models", "Why each one is here, and what to watch for.")
        for m in fcfg["models"]:
            st.markdown(f"**{text['models'][m]['name']}**  \n{text['models'][m]['what']} {text['models'][m]['why']} "
                        f"*Watch out for:* {text['models'][m]['watch']}")

def _follow_up_table(config, state, user, kind, frame, columns, flagged_default):
    """Churn and cross-sell lists are model suggestions. The reviewer ticks the customers worth acting on, can
    untick false alarms, adds a note, and approves; only approved customers become follow-ups."""
    store = state.setdefault("approved_followups", {})
    edit = frame[columns].copy()
    edit.insert(0, "Follow up?", flagged_default)
    edit["Your note"] = ""
    edited = ui.editor(edit, hide_index=True, key=f"fu_edit_{kind}_{state.get('fu_gen', 0)}",
                            disabled=[c for c in edit.columns if c not in ("Follow up?", "Your note")])
    picked = edited[edited["Follow up?"]]
    st.caption(f"{len(picked)} of {len(edited)} selected for follow-up.")
    if user["role"] in _approve_roles(config):
        if st.button(f"Approve {len(picked)} follow-up{'s' if len(picked) != 1 else ''}", key=f"fu_ok_{kind}", type="primary",
                     disabled=picked.empty):
            store[kind] = {"rows": picked.drop(columns=["Follow up?"]).to_dict("records"), "by": user["display"],
                           "at": common.now(state).strftime("%d %b %Y")}
            common.log(state, config, user, "FOLLOW_UP_APPROVED", None, {"list": kind, "customers": len(picked)})
            st.rerun()
    else:
        st.info("You can review but not approve. Approval is limited to: "
                + ", ".join(config["roles"][r]["label"] for r in _approve_roles(config)) + ".")
    if kind in store:
        a = store[kind]
        st.success(f"{len(a['rows'])} follow-up(s) approved by {a['by']} on {a['at']}.", icon=":material/verified:")


def _accounts_tab(config, state, user, access):
    c = counts()
    cols = st.columns(4)
    for col, (label, value) in zip(cols, [
            ("Active customers", c["active_accounts"]),
            ("Buying less than before", f"{c['churn_flagged']} of {c['churn_eligible']}"),
            ("Could buy Consumables", c["cross_sell_gaps"]),
            ("Chance A/B buyers also buy C",
             f"{c['cross_sell_confidence_overall']:.0%}" if c["cross_sell_confidence_overall"] is not None else "-")]):
        with col:
            charts.card(label, value)
    if c["churn_flagged_by_entity"]:
        st.caption("Customers buying less, by entity: " + ", ".join(f"{k} {v}" for k, v in sorted(c["churn_flagged_by_entity"].items())))
    if access != "ACCOUNT_LEVEL":
        st.info("Customer-by-customer lists are limited to roles with account-level access. You see the totals above.")
        return
    ch_cfg = config["analytics"]["churn"]
    ui.section("Customers buying less (churn radar)",
               "These are suggestions: tick the ones worth a call, untick false alarms, then approve.",
               tip=f"Orders over the last {ch_cfg['recent_window_months']} months compared with the "
                   f"{ch_cfg['baseline_window_months']} months before. Flagged when more than "
                   f"{1 - ch_cfg['velocity_threshold']:.0%} lower.")
    radar = churn()
    show_all = st.checkbox("Show every customer, not only those flagged", key="an_churn_all")
    table = radar if show_all else radar[radar["flagged"]]
    table = table.assign(delta=table["delta"].map(lambda d: f"{d:.1%}"))
    _follow_up_table(config, state, user, "churn", table, ea.CHURN_COLUMNS, table["flagged"].tolist())

    gaps, stats = cross_sell()
    cs = config["analytics"]["cross_sell"]
    lines = config["analytics"]["product_lines"]
    ui.section("Customers who could buy more (cross-sell)", "Tick the ones to approach, then approve.",
               tip=f"Bought {' or '.join(lines[p] for p in cs['antecedent_products'])} in the last {cs['window_days']} days "
                   f"({stats['window_start']} to {stats['window_end']}) but no {lines[cs['consequent_product']]}.")
    _follow_up_table(config, state, user, "cross_sell", gaps, ea.CROSS_SELL_COLUMNS, [True] * len(gaps))
    with st.expander("How strong is this pattern? (support, confidence, lift)"):
        rows = [{"scope": "All entities", **stats["overall"]}] + [{"scope": k, **v} for k, v in stats["by_entity"].items()]
        ui.table(pd.DataFrame(rows), hide_index=True)
        o = stats["overall"]
        ui.note("What does lift mean?",
                "Lift = confidence divided by the overall share of customers buying the product; 1.0 means buying one "
                "product adds nothing beyond the base rate."
                + (" Every active customer in this window bought an A or B product, so lift is 1.0 by construction."
                   if o["accounts"] and o["antecedent_accounts"] == o["accounts"] else ""))

def render(config, state, user):
    ui.page_header("Sales insights", "Purchase orders for Products A, B and C across the five countries: what is growing, "
                                     "what to expect next, and what to do about it.")
    ui.story_banner(state)
    access = config["roles"][user["role"]]["analytics"]
    if access == "NONE":
        st.info("Your role has no access to sales insights.")
        return
    _data_warning(config)
    from tabs import tab_stock                      # imported here because tab_stock reuses this module's chart colours
    overview, forecast_tab, stock_tab, accounts = st.tabs(["Overview", "Forecast explorer", "Stock planning", "Customers to follow up"])
    with overview:
        _overview(config, state)
    with forecast_tab:
        _forecast_tab(config, state, user)
    with stock_tab:
        tab_stock.render(config, state, user)
    with accounts:
        _accounts_tab(config, state, user, access)
