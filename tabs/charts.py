"""Dashboard look: one Plotly template, KPI cards with sparklines and a few chart helpers shared by the Sales insights
page. Presentation only: no business rules and no record values live here (callers pass aggregates)."""
import html

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

TEXT, MUTED, GRID = "#131E29", "#556B82", "#E5E5E5"
BRAND, GOOD, BAD, NEUTRAL = "#0070F2", "#256F3A", "#AA0808", "#8396A8"
FONT = '"72", "72full", Arial, Helvetica, sans-serif'
RAMP = [[0.0, "#CFE3FD"], [1.0, "#0058C7"]]
DIVERGING = [[0.0, "#AA0808"], [0.35, "#F2B8B8"], [0.5, "#F5F6F7"], [0.65, "#B7DDBF"], [1.0, "#256F3A"]]

pio.templates["lives"] = go.layout.Template(layout=go.Layout(
    font={"family": FONT, "size": 13, "color": TEXT},
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    colorway=["#008a7a", "#c26f1e", "#7a5cc0", BRAND, NEUTRAL],
    margin={"t": 8, "l": 8, "r": 8, "b": 8},
    hoverlabel={"bgcolor": TEXT, "bordercolor": TEXT, "font": {"family": FONT, "size": 12, "color": "#ffffff"}},
    xaxis={"showgrid": False, "zeroline": False, "linecolor": GRID, "ticks": "outside", "tickcolor": GRID,
           "tickfont": {"color": MUTED}, "title": {"standoff": 8}, "automargin": True},
    yaxis={"gridcolor": GRID, "zeroline": False, "linecolor": "rgba(0,0,0,0)", "tickfont": {"color": MUTED},
           "title": {"font": {"color": MUTED, "size": 12}}, "automargin": True},
    legend={"orientation": "h", "yanchor": "top", "y": -0.14, "x": 0, "font": {"color": MUTED, "size": 12}},
))


def show(fig, key, height=320):
    """Draw a figure with the dashboard template (theme=None stops Streamlit overriding fonts and colours) and no
    toolbar clutter."""
    fig.update_layout(template="lives", height=height)
    st.plotly_chart(fig, theme=None, key=key, config={"displayModeBar": False, "displaylogo": False})


def chart_title(text, sub=""):
    """The heading above a chart inside its card, as on a Power BI visual."""
    st.markdown(f"<div class='lv-ctitle'>{html.escape(text)}</div>"
                + (f"<div class='lv-csub'>{html.escape(sub)}</div>" if sub else ""), unsafe_allow_html=True)


def compact(value):
    """12,345 -> '12.3K'; 1,234,567 -> '1.23M'; small numbers keep their digits."""
    v = abs(value)
    if v >= 1e6:
        return f"{value / 1e6:.2f}M"
    if v >= 1e4:
        return f"{value / 1e3:.1f}K"
    return f"{value:,.0f}"


def spark(values, colour=BRAND, width=118, height=34):
    """A tiny inline-SVG area line for a KPI card."""
    vals = [float(v) for v in values]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pts = [(i / (len(vals) - 1) * (width - 4) + 2, height - 3 - (v - lo) / span * (height - 8)) for i, v in enumerate(vals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"2,{height - 2} {line} {width - 2},{height - 2}"
    x, y = pts[-1]
    return (f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' aria-hidden='true'>"
            f"<polygon points='{area}' fill='{colour}' fill-opacity='.12'/>"
            f"<polyline points='{line}' fill='none' stroke='{colour}' stroke-width='1.8' stroke-linejoin='round'/>"
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='2.6' fill='{colour}'/></svg>")


def delta_chip(pct, suffix=""):
    """Green up arrow, red down arrow, grey dash. None means there is no earlier period to compare with."""
    if pct is None:
        return "<span class='lv-delta flat'>no earlier period</span>"
    tone, arrow = ("up", "▲") if pct > 0 else ("down", "▼") if pct < 0 else ("flat", "■")
    return f"<span class='lv-delta {tone}'>{arrow} {pct:+.1f}%{html.escape(suffix)}</span>"


def card(label, value, delta_html="", sparkline="", sub=""):
    """A KPI card: label, big number, change chip and sparkline. All text is escaped except the two HTML fragments
    built by delta_chip and spark."""
    st.markdown(
        f"<div class='lv-kcard'><div class='lv-kcard-label'>{html.escape(label)}</div>"
        f"<div class='lv-kcard-value'>{html.escape(str(value))}</div>"
        f"<div class='lv-kcard-foot'><div>{delta_html}"
        + (f"<div class='lv-kcard-sub'>{html.escape(sub)}</div>" if sub else "") + f"</div>{sparkline}</div></div>",
        unsafe_allow_html=True)
