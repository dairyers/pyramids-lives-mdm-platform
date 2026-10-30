"""Look and feel plus small plain-language helpers shared by every page. Presentation only: no business
rules live here (those are in config_rules.json); status and approver names are read from the config."""
import html
import math
import re
from datetime import datetime

import pandas as pd
import streamlit as st

STATUS_TEXT = {"DRAFT": "Draft", "SUBMITTED": "Submitted", "APPROVED": "Approved, ready for SAP",
               "REJECTED": "Rejected", "SYNCED_TO_SAP": "Created in SAP"}


# ----------------------------------------------------------------------------- plain-language helpers
def plain_error(message):
    """'VALIDATION BLOCKED: Label (Field) is required.' -> 'Label is required.'"""
    text = re.sub(r"^VALIDATION BLOCKED:\s*", "", str(message))
    text = re.sub(r"\s*\([A-Za-z_][A-Za-z0-9_]*\)", "", text)
    return text[:1].upper() + text[1:]


def status_label(config, status):
    role = config["state_machine"]["approver_role_by_state"].get(status)
    if role:
        return f"Waiting for {config['roles'][role]['label']}"
    return STATUS_TEXT.get(status, str(status).replace("_", " ").title())


def status_tone(config, status):
    sm = config["state_machine"]
    if status in sm["pending_states"] or status == "SUBMITTED":
        return "wait"
    return {"APPROVED": "ok", "SYNCED_TO_SAP": "done", "REJECTED": "bad"}.get(status, "muted")


def pill(config, status):
    return f"<span class='lv-pill lv-{status_tone(config, status)}'>{html.escape(status_label(config, status))}</span>"


def when(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return ""


HISTORY_VERBS = {"SUBMIT": "Submitted by", "ROUTE": "Sent to the right approver by", "APPROVE": "Approved by",
                 "REJECT": "Rejected by", "SYNC": "Created in SAP by", "REASSIGN": "Handed to a delegate by"}


SHORT_ROLES = {"IT / System Administrator": "IT admin", "Quality / Regulatory Affairs Officer": "Regulatory Officer",
               "Business Unit Head": "BU Head"}


def short_role(label):
    """A role's name for the narrow header button; the full name is still used everywhere else."""
    return SHORT_ROLES.get(label, label)


def person(email):
    """'mdm-system@livesgroup.com' -> 'the system'; 'wei.jie@...' -> 'Wei Jie'."""
    name = str(email).split("@")[0]
    return "the system" if name.endswith("-system") else name.replace(".", " ").title()


def history_text(h):
    return (f"{HISTORY_VERBS.get(h['action'], h['action'].title() + ' by')} {person(h['actor'])}"
            + (f" ({h['comment']})" if h.get("comment") else ""))


def waiting_text(hours):
    if hours is None:
        return ""
    if hours < 1:
        return "just now"
    if hours < 48:
        return f"waiting {hours:.0f} hours"
    return f"waiting {hours / 24:.0f} days"


def tracker(config, req):
    """Plain steps: Submitted, one 'Review by <approver>' per approver in the chain, Approved, Created in SAP."""
    sm, status = config["state_machine"], req["status"]
    routed = next((h["to"] for h in req.get("history", []) if h["action"] == "ROUTE"), None)
    path = req.get("approval_path") or ([routed] if routed else [])
    names = [config["roles"][sm["approver_role_by_state"][s]]["label"] for s in path]
    rejected = status == "REJECTED"
    bad_at = None
    if rejected:
        rej = next((h for h in reversed(req["history"]) if h["action"] == "REJECT"), None)
        bad_at = path.index(rej["from"]) if rej and rej["from"] in path else 0
    steps = ["Submitted"] + [f"{'Rejected by' if bad_at == i else 'Review by'} {n}" for i, n in enumerate(names)] \
        + ["Approved", "Created in SAP"]
    n = len(path)
    if rejected:
        current = 1 + bad_at
    elif status in path:
        current = 1 + path.index(status)
    else:
        current = {"APPROVED": 1 + n, "SYNCED_TO_SAP": len(steps), "DRAFT": 0}.get(status, 1)
    items = []
    for i, label in enumerate(steps):
        cls = "bad" if (rejected and i == 1 + bad_at) else "done" if i < current else "now" if i == current else "next"
        items.append(f"<li class='{cls}'><span></span>{html.escape(label)}</li>")
    return f"<ol class='lv-steps'>{''.join(items)}</ol>"


# ----------------------------------------------------------------------------- tables that wrap their text
CHAR_PX, CELL_PAD_PX, PAGE_PX = 7.2, 26, 700


def _wrap_layout(data, line_px=20, pad_px=16, max_px=200, page_px=None):
    """Streamlit tables do not wrap by default, but a taller row wraps its cells. Fit the columns to the page width
    (so the last one is never cut off), then size every row for the column that needs the most lines. Short tables
    keep the default look. Returns (row_height or None, {column: width_px})."""
    if not isinstance(data, pd.DataFrame) or data.empty:
        return None, {}
    PAGE_PX_ = page_px or PAGE_PX
    text = data.astype(str)
    longest = {c: int(max(0 if pd.isna(text[c].str.len().max()) else text[c].str.len().max(), len(str(c)))) for c in text.columns}
    want = {c: min(n * CHAR_PX + CELL_PAD_PX, 360) for c, n in longest.items()}
    if sum(want.values()) <= PAGE_PX_ and max(longest.values()) < 40:
        return None, {}
    scale = min(1.0, PAGE_PX_ / sum(want.values()))
    width = {c: max(64, int(w * scale)) for c, w in want.items()}
    lines = max(math.ceil(longest[c] * CHAR_PX / max(width[c] - CELL_PAD_PX, 30)) for c in text.columns)
    return min(max_px, line_px * lines + pad_px), width


def _wrapped(data, kwargs):
    height, widths = _wrap_layout(data, page_px=kwargs.pop("page_px", None))
    if height and "row_height" not in kwargs:
        kwargs["row_height"] = height
        cfg = dict(kwargs.get("column_config") or {})
        for col, px in widths.items():
            if pd.api.types.is_object_dtype(data[col]) or pd.api.types.is_string_dtype(data[col]):
                cfg.setdefault(col, st.column_config.TextColumn(width=px))     # numbers and tick boxes keep their type
        kwargs["column_config"] = cfg
    return kwargs


def table(data, **kwargs):
    """st.dataframe with wrapped text."""
    return st.dataframe(data, **_wrapped(data, kwargs))


def detail_table(rows, extra_class=""):
    """(field id, label, value, hidden) rows as a plain table. Every row carries its field id so a reviewer, or the
    hands-free demo, can point at one field. Values are escaped; they have already been through apply_field_security."""
    body = "".join(f"<tr data-field='{html.escape(str(f), quote=True)}'><th>{html.escape(str(label))}</th>"
                   f"<td class='{'lv-hid' if hidden else ''}'>{html.escape(str(v))}</td></tr>" for f, label, v, hidden in rows)
    st.markdown(f"<table class='lv-detail {extra_class}'><thead><tr><th>Detail</th><th>Value</th></tr></thead><tbody>{body}</tbody></table>",
                unsafe_allow_html=True)


COMPARE_TONE = {"Matches": "ok", "Different": "bad", "Does not match": "bad"}


def compare_table(rows):
    """Your request next to an existing record as a plain table. Each row carries its field id and whether it agrees
    (ok), differs (bad) or is the name line, so a reviewer, or the hands-free demo, can point at the differences."""
    cols = [c for c in rows[0] if not c.startswith("_")]
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join(
        f"<tr data-field='{html.escape(str(r['_field']), quote=True)}' data-result='{COMPARE_TONE.get(r['Result'], 'name')}'>"
        + "".join(f"<td class='res-{COMPARE_TONE.get(r['Result'], 'name')}'>{html.escape(str(r[c]))}</td>" if c == "Result"
                  else f"<td>{html.escape(str(r[c]))}</td>" for c in cols) + "</tr>" for r in rows)
    st.markdown(f"<table class='lv-detail lv-compare'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>",
                unsafe_allow_html=True)


def editor(data, **kwargs):
    """st.data_editor with wrapped text."""
    return st.data_editor(data, **_wrapped(data, kwargs))

# ----------------------------------------------------------------------------- page furniture
def page_header(title, subtitle=""):
    st.markdown(f"<h1 class='lv-title'>{html.escape(title)}</h1>"
                + (f"<p class='lv-sub'>{html.escape(subtitle)}</p>" if subtitle else ""), unsafe_allow_html=True)


def stepbar(labels, current):
    """A short horizontal progress bar: steps before `current` are done, `current` is the one you are on.
    current == len(labels) means every step is done."""
    items = "".join(
        f"<li class='{'done' if i < current else 'now' if i == current else 'next'}'><span></span>"
        f"{i + 1}. {html.escape(label)}</li>" for i, label in enumerate(labels))
    st.markdown(f"<ol class='lv-steps lv-stepbar'>{items}</ol>", unsafe_allow_html=True)


def info(text):
    """A small (i) that shows an explanation when the mouse is over it. Definitions and how-it-works notes live here
    so the page itself stays clean."""
    return f"<span class='lv-i' tabindex='0' data-tip='{html.escape(text, quote=True)}'>i</span>"


def section(title, subtitle="", tip=""):
    st.markdown(f"<h2 class='lv-section'>{html.escape(title)}{info(tip) if tip else ''}</h2>"
                + (f"<p class='lv-sub'>{html.escape(subtitle)}</p>" if subtitle else ""), unsafe_allow_html=True)


def banner(text, tip="", tone="orange"):
    """A full-width coloured strip with an optional (i) that explains it on hover."""
    st.markdown(f"<div class='lv-banner lv-banner-{tone}'><span>{html.escape(text)}</span>{info(tip) if tip else ''}</div>",
                unsafe_allow_html=True)


def note(text, tip):
    """One short line with an (i) for the detail."""
    st.markdown(f"<span class='lv-meta'>{html.escape(text)}</span>{info(tip)}", unsafe_allow_html=True)


def facets(pairs):
    """Object-page header attributes: small grey label above the value."""
    cells = "".join(f"<div class='lv-facet'><span>{html.escape(k)}</span><b>{html.escape(str(v))}</b></div>"
                    for k, v in pairs if v not in (None, ""))
    st.markdown(f"<div class='lv-facets'>{cells}</div>", unsafe_allow_html=True)


def kpi(label, value, tone="", sub=""):
    """Launchpad numeric tile content: label above, large light number below, an optional orange line under it."""
    st.markdown(f"<div class='lv-kpi-label'>{html.escape(label)}</div>"
                f"<div class='lv-kpi {tone}'>{value}</div>"
                + (f"<div class='lv-kpi-sub'>{html.escape(sub)}</div>" if sub else ""), unsafe_allow_html=True)


def hours_text(hours):
    """48 -> '2 days', 36 -> '36 hours'."""
    return (f"{hours // 24} day{'' if hours == 24 else 's'}" if hours % 24 == 0 else f"{hours} hours")


def person_label(user, config):
    title = user.get("title") or config["roles"][user["role"]]["label"]
    return title if user["display"] == title else f"{user['display']}, {title}"


def _go_step(state, index):
    """Move a story to another step: become that step's person and open that step's page (callback-safe)."""
    from tabs import common
    config = common.load_config()
    steps = common.load_ui()["stories"][state["story"]["id"]]["steps"]
    common.switch_user(state, config, steps[index]["who"])
    if steps[index].get("prefill"):
        from tabs import stories
        story = common.load_ui()["stories"][state["story"]["id"]]
        stories.load_payload(state, common.load_config(), story["domain"], story["payload"])
    if steps[index].get("variant"):
        from tabs import stories
        stories.load_variant(state, config, common.load_ui()["stories"][state["story"]["id"]]["domain"],
                             steps[index]["variant"])
    state["story"]["step"] = index
    go(state, steps[index]["page"])


def story_banner(state):
    """Kept so pages need no demo code: the guide is drawn once by the app shell, outside the page."""
    return None


def demo_guide(state):
    """The demo guide: which story, which step, who you are now, exactly what to click, and a button to move on.
    Drawn by the app shell in a bar fixed to the bottom of the window, so the page itself shows only what a real
    user of the product would see."""
    story = state.get("story")
    if not story:
        return
    from tabs import common
    import engine_security as es
    config, text = common.load_config(), common.load_ui()["stories"][story["id"]]
    steps, i = text.get("steps") or [], story.get("step", 0)
    with st.container(key="demo_guide"):
        head, close = st.columns([12, 1], vertical_alignment="center")
        head.markdown(f"**Story: {text['label']}**")
        if close.button("Hide", key="story_hide"):
            state.pop("story", None)
            st.rerun()
        if not steps:
            st.write(story.get("hint", ""))
            return
        step = steps[i]
        who = es.get_user(step["who"], config)
        st.progress((i + 1) / len(steps), text=f"Step {i + 1} of {len(steps)}. You are {person_label(who, config)}.")
        st.markdown(step["do"] + (info(step["notice"]) if step.get("notice") else ""), unsafe_allow_html=True)
        back, nxt, _ = st.columns([1, 3, 3])
        back.button("Back", key="story_back", disabled=i == 0, on_click=_go_step, args=(state, i - 1))
        if i + 1 < len(steps):
            nxt_who = es.get_user(steps[i + 1]["who"], config)
            same = steps[i + 1]["who"] == step["who"]
            nxt.button("Next example" if same else f"Next: become {person_label(nxt_who, config)}", key="story_next",
                       type="primary",
                       on_click=_go_step, args=(state, i + 1))
        else:
            nxt.success("End of this story. Pick another from Case Synopsis stories.", icon=":material/check_circle:")

def go(state, page_id):
    """Ask the shell to open another page on the next run (safe inside button callbacks)."""
    state["goto"] = page_id


# ----------------------------------------------------------------------------- theme
STYLE = """
<style>
/* SAP Horizon (Morning Horizon): values from SAP's published theming package @sap-theming/theming-base-content 11.36.5,
   including the SAP "72" typeface. Brand #0070F2, text #131E29, shell and cards white on #F5F6F7, corners .75rem / .5rem. */
@font-face { font-family: "72"; font-weight: 400; src: url("https://unpkg.com/@sap-theming/theming-base-content@11.36.5/content/Base/baseLib/baseTheme/fonts/72-Regular.woff2") format("woff2"); }
@font-face { font-family: "72"; font-weight: 600; src: url("https://unpkg.com/@sap-theming/theming-base-content@11.36.5/content/Base/baseLib/baseTheme/fonts/72-Semibold.woff2") format("woff2"); }
@font-face { font-family: "72"; font-weight: 700; src: url("https://unpkg.com/@sap-theming/theming-base-content@11.36.5/content/Base/baseLib/baseTheme/fonts/72-Bold.woff2") format("woff2"); }
@font-face { font-family: "72"; font-weight: 300; src: url("https://unpkg.com/@sap-theming/theming-base-content@11.36.5/content/Base/baseLib/baseTheme/fonts/72-Light.woff2") format("woff2"); }
html, body, .stApp, [class*="st-"]:not([data-testid*="Icon"]), button, input, textarea, select {
  font-family: "72", "72full", Arial, Helvetica, sans-serif !important; }
[data-testid*="Icon"] { font-family: "Material Symbols Rounded" !important; }
[data-testid="stHeader"] { border-bottom: 1px solid #E5E5E5; box-shadow: 0 1px 0 rgba(34,53,72,.04); }
.stButton button, .stDownloadButton button, [data-testid="stPopover"] button { border-radius: 0.5rem; }
.stButton button[kind="secondary"], [data-testid="stPopover"] button { background: #fff; border: 1px solid #BCC3CA; color: #0064D9; }
.stButton button[kind="secondary"]:hover, [data-testid="stPopover"] button:hover { background: #EAECEE; border-color: #BCC3CA; }
.stButton button[kind="primary"] { background: #0070F2; border-color: #0070F2; color: #fff; }
.stButton button[kind="primary"]:hover { background: #0064D9; border-color: #0064D9; }
html, body, [class*="st-"] { -webkit-font-smoothing: antialiased; }
#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stToolbarActions"], [data-testid="stMainMenu"],
[data-testid="stAppDeployButton"], .stAppDeployButton, [data-testid="stStatusWidget"] { display: none !important; }
/* the sidebar is hidden while the top navigation shows; on narrow screens Streamlit moves the pages into it, so leave it usable */
.stApp:has([data-testid="stTopNav"]) [data-testid="stSidebar"], .stApp:has([data-testid="stTopNav"]) [data-testid="stSidebarCollapsedControl"] { display: none; }
.stApp:not(:has([data-testid="stTopNav"])) [data-testid="stSidebarNav"] { padding-top: 3.6rem; }
.block-container { max-width: 1120px; padding-top: 4.6rem; padding-bottom: 4rem; }
[data-testid="stHeader"] { background: rgba(255,255,255,.96); backdrop-filter: blur(6px); border-bottom: 1px solid #E5E5E5; }

/* shell bar: application name on the left, as on every Fiori page */
header[data-testid="stHeader"]::before { content: "Lives Group MDM"; position: absolute; left: max(1rem, calc((100vw - 1120px) / 2));
  top: 50%; transform: translateY(-50%); font-weight: 700; font-size: 1rem; color: #131E29; letter-spacing: 0; pointer-events: none;
  padding-left: 1.9rem; background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='6' fill='%230070F2'/%3E%3Cg stroke='white' stroke-width='1.6' fill='none' stroke-linecap='round'%3E%3Cpath d='M12 12L6.5 7M12 12l5.5-5M12 12v6.5'/%3E%3C/g%3E%3Cg fill='white'%3E%3Ccircle cx='12' cy='12' r='2.4'/%3E%3Ccircle cx='6.5' cy='7' r='1.7'/%3E%3Ccircle cx='17.5' cy='7' r='1.7'/%3E%3Ccircle cx='12' cy='18.5' r='1.7'/%3E%3C/g%3E%3C/svg%3E") no-repeat left center / 1.4rem 1.4rem; }
header[data-testid="stHeader"] { height: 3rem; padding-left: calc(max(1rem, (100vw - 1120px) / 2) + 12.4rem);
  padding-right: min(480px, 48vw); }

/* type: Fiori page title 1.5rem bold, section title 1.125rem bold, body 0.875rem */
.lv-title { font-size: 1.5rem; font-weight: 700; margin: 0.4rem 0 0.1rem; padding: 0; line-height: 1.25; }
.lv-section { font-size: 1.125rem; font-weight: 700; margin: 1.8rem 0 0.1rem; padding: 0; }
.lv-sub { color: #556B82; margin: 0 0 1rem; font-size: 0.875rem; max-width: 70ch; }

/* request form: type cards, step bar, recommended answer */
[class*="st-key-typecard_"] { transition: border-color .12s; }
[class*="st-key-typecard_"][class*="_on"] { border: 2px solid #0070F2 !important; background: #EAF3FF !important; }
.lv-stepbar { max-width: 520px; margin: 0.2rem 0 1rem; }
[class*="st-key-rec_"] { border: 2px solid #256F3A !important; background: #EDF8F0 !important; }
.lv-rec { display: inline-block; background: #256F3A; color: #fff; border-radius: 999px; padding: 1px 9px; font-size: 0.72rem;
  font-weight: 700; margin-left: 0.4rem; vertical-align: middle; }

/* banners */
.lv-banner { display: flex; align-items: center; gap: 0.2rem; padding: 0.65rem 0.9rem; border-radius: 0.5rem; margin: 0.4rem 0 0.6rem;
  font-size: 0.9rem; border-left: 4px solid; }
.lv-banner-orange { background: #FFE9D1; border-color: #E76500; color: #5A2E00; }
.lv-banner-blue { background: #E1F4FF; border-color: #0070F2; color: #0A3A6B; }

/* (i) hover blurb */
.lv-i { position: relative; display: inline-flex; align-items: center; justify-content: center; width: 1.05rem; height: 1.05rem;
  margin-left: 0.4rem; border: 1.5px solid #0070F2; border-radius: 50%; color: #0070F2; font-size: 0.7rem; font-weight: 700;
  font-style: italic; vertical-align: middle; cursor: help; }
.lv-i::after { content: attr(data-tip); position: absolute; left: 50%; top: 135%; transform: translateX(-20%); width: max-content;
  max-width: 340px; padding: 0.55rem 0.7rem; background: #131E29; color: #fff; border-radius: 0.5rem; font-size: 0.8rem;
  font-style: normal; font-weight: 400; line-height: 1.35; white-space: normal; text-align: left; z-index: 1000003;
  opacity: 0; pointer-events: none; transition: opacity .12s; box-shadow: 0 4px 16px rgba(0,0,0,.25); }
.lv-i:hover::after, .lv-i:focus::after { opacity: 1; }

/* launchpad tiles: a white card; the colour (by meaning) is a top accent and the number */
[class*="st-key-tile_"] { background: #ffffff !important; border: 1px solid #E5E5E5 !important; border-top: 4px solid var(--tile) !important;
  border-radius: 0.75rem; box-shadow: 0 1px 3px rgba(19,30,41,.08); padding-top: 0.4rem; }
.st-key-tile_red { --tile: #C62828; } .st-key-tile_yellow { --tile: #C9A400; } .st-key-tile_orange { --tile: #E76500; }
.st-key-tile_green { --tile: #256F3A; } .st-key-tile_blue { --tile: #0070F2; } .st-key-tile_pink { --tile: #AA0808; }
.lv-kpi-sub { color: #A34200; font-weight: 600; font-size: 0.82rem; margin: 0.1rem 0 0.2rem; }
[class*="st-key-tile_"] .lv-kpi-label { color: #556B82; font-size: 0.9rem; font-weight: 600; }
[class*="st-key-tile_"] .lv-kpi { font-size: 2.2rem; font-weight: 700; color: var(--tile); line-height: 1.2; margin: 0.15rem 0 0.35rem; }
[class*="st-key-tile_"] { text-align: center; align-items: center !important; }
[class*="st-key-tile_"] [data-testid="stVerticalBlock"] { align-items: center !important; }
[class*="st-key-tile_"] .stButton, [class*="st-key-tile_"] [data-testid="stButton"] { width: 100%; display: flex; justify-content: center; }
[class*="st-key-tile_"] [data-testid="stMarkdownContainer"], [class*="st-key-tile_"] [data-testid="stMetric"] { text-align: center; }

/* launchpad tiles and object-page header */
.lv-tile-title { font-weight: 700; font-size: 1rem; margin: 0.1rem 0 0; }
.lv-tile-sub { color: #556B82; font-size: 0.8125rem; min-height: 2.4em; }
.lv-kpi-label { color: #556B82; font-size: 0.8125rem; }
.lv-kpi { font-size: 2.25rem; font-weight: 300; line-height: 1.1; font-variant-numeric: tabular-nums; }
.lv-kpi.warn { color: #E76500; } .lv-kpi.good { color: #256F3A; }
.lv-op-title { font-size: 1.125rem; font-weight: 700; margin: 0; }
.lv-facets { display: flex; flex-wrap: wrap; gap: 0.4rem 2rem; margin: 0.5rem 0 0.2rem; }
.lv-facet span { display: block; color: #556B82; font-size: 0.75rem; }
.lv-facet b { font-weight: 400; font-size: 0.875rem; }
.st-key-decision_bar, [class*="st-key-decision_"] { border-top: 1px solid #E5E5E5; margin-top: 0.6rem; padding-top: 0.6rem; }
h5 { font-weight: 600 !important; margin-top: 1.4rem !important; }
.stApp [data-testid="stCaptionContainer"] { color: #556B82; opacity: 1; }

/* cards */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) { border-radius: .75rem; }
div[data-testid="stVerticalBlockBorderWrapper"] { border-color: #E5E5E5; background: #ffffff; }
[data-testid="stMetric"] { padding: 0.2rem 0.2rem; }
[data-testid="stMetricLabel"] p { font-size: 0.86rem; color: #556B82; }
[data-testid="stMetricValue"] { font-weight: 640; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
[data-testid="stExpander"] details { border-color: #E5E5E5; border-radius: .75rem; background: #ffffff; }
[data-testid="stExpander"] summary p { font-weight: 560; }
[data-testid="stAlert"] { border-radius: .75rem; }
/* demo controls live in the header bar and a guide bar at the bottom, never inside the page */
.st-key-demo_bar { position: fixed; top: 0.5rem; right: 1rem; z-index: 1000002; width: min(540px, 52vw); }
.st-key-demo_bar [data-testid="stHorizontalBlock"] { gap: 0.5rem; flex-wrap: nowrap; }
.st-key-demo_bar button { white-space: nowrap; }
.st-key-demo_bar [data-testid="stPopover"] button, .st-key-demo_bar button { font-size: 0.8rem; min-height: 2rem; padding: 0.15rem 0.6rem;
  background: transparent; border-color: #D5DADF; color: #556B82; }
.st-key-demo_bar [data-testid="stPopover"] button:hover { border-color: #0070F2; color: #0070F2; }
.st-key-demo_guide { position: fixed; left: 0; right: 0; bottom: 0; z-index: 1000001; background: #ffffff; border-top: 3px solid #0070F2;
                     box-shadow: 0 -8px 24px rgba(28,25,23,.10); padding: 0.7rem max(1rem, calc((100vw - 1120px) / 2 + 1rem)) 0.8rem; }
.st-key-demo_guide [data-testid="stProgress"] { margin-bottom: 0.1rem; }

/* narrow screens: the demo controls drop to their own strip under the header instead of overflowing it */
@media (max-width: 760px) {
  header[data-testid="stHeader"] { padding-right: 1rem; }
  .st-key-demo_bar { top: 3.05rem; left: 0.5rem; right: 0.5rem; width: auto; background: #F5F6F7; padding: 0.3rem 0; }
  .st-key-demo_bar [data-testid="stHorizontalBlock"] { flex-wrap: nowrap; gap: 0.4rem; }
  .st-key-demo_bar [data-testid="stColumn"] { min-width: 0 !important; }
  .st-key-demo_bar button { padding: 0.25rem 0.4rem; font-size: 0.8rem; }
  .block-container { padding-top: 7.2rem; }
  .lv-steps { flex-direction: column; gap: 0.2rem; }
  .lv-steps li { padding-top: 0; padding-left: 22px; }
  .lv-steps li span { top: 3px; }
  .lv-steps li::before { display: none; }
}

/* buttons */
.stButton button, .stDownloadButton button { border-radius: 9px; font-weight: 560; }
.stButton button[kind="primary"] { padding: 0.55rem 1.4rem; font-size: 1.02rem; }
:focus-visible { outline: 2px solid #0070F2 !important; outline-offset: 2px; }

/* top navigation */
[data-testid="stNavSection"] a, [data-testid="stTopNavLink"], header nav a { font-weight: 560; }
/* show every page in the top bar and scroll sideways when they do not fit, instead of a "N more" menu */
[data-testid="stTopNav"] { overflow-x: auto !important; overflow-y: hidden; max-width: 100%; scrollbar-width: thin; flex: 1 1 auto; min-width: min(460px, calc(100vw - 240px - 560px)); scroll-snap-type: x proximity; scroll-padding: 0 0.5rem; }
[data-testid="stTopNav"] .rc-overflow-item { opacity: 1 !important; position: static !important; height: auto !important;
  pointer-events: auto !important; overflow: visible !important; flex: none; scroll-snap-align: start; }
[data-testid="stTopNav"] .rc-overflow-item-rest { display: none !important; }

/* (i) at the right of the Finance details header; the note shows only on hover or keyboard focus */
.st-key-finance_box { position: relative; }
.st-key-finance_box [data-testid="stElementContainer"]:has(.lv-info) { position: absolute; top: 0.7rem; right: 2.6rem; z-index: 5; width: auto; height: auto; }
.lv-info { position: relative; display: inline-flex; align-items: center; justify-content: center; width: 1.15rem; height: 1.15rem;
  border: 1.5px solid #5B738B; border-radius: 50%; color: #5B738B; font: italic 700 0.75rem Georgia, serif; cursor: help; }
.lv-info:hover, .lv-info:focus { border-color: #0070F2; color: #0070F2; outline: none; }
.lv-info-tip { display: none; position: absolute; top: 1.6rem; right: -0.5rem; width: 240px; padding: 0.5rem 0.7rem; background: #131E29; color: #fff;
  border-radius: 6px; font: normal 400 0.82rem/1.35 "72", "72full", Arial, Helvetica, sans-serif; box-shadow: 0 2px 8px rgba(0,0,0,.25); z-index: 10; }
.lv-info:hover .lv-info-tip, .lv-info:focus .lv-info-tip { display: block; }

/* one card style: the same corner radius and border everywhere */
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 0.75rem; border-color: #E5E5E5; }
table.lv-detail { width: 100%; border-collapse: collapse; border: 1px solid #E5E5E5; border-radius: 8px; background: #fff; font-size: 0.875rem; }
table.lv-detail th { text-align: left; background: #EFF1F2; color: #556B82; font-weight: 600; padding: 8px 12px; border-bottom: 1px solid #E5E5E5; }
table.lv-detail td { padding: 8px 12px; border-bottom: 1px solid #F0F1F2; color: #131E29; }
table.lv-detail tbody th { background: #fff; font-weight: 400; color: #556B82; width: 38%; }
table.lv-detail td.lv-hid { color: #8396A8; font-style: italic; }
table.lv-compare td.res-ok { color: #256F3A; font-weight: 600; }
table.lv-compare td.res-bad { color: #AA0808; font-weight: 600; }
.lv-footer { margin-top: 2.5rem; padding-top: 0.8rem; border-top: 1px solid #E5E5E5; color: #8396A8; font-size: 0.78rem; text-align: center; }

/* clickable cards lift slightly and take the accent colour on hover */
[class*="st-key-tile_"], [class*="st-key-typecard_"], [class*="st-key-need_"] { transition: box-shadow .15s, transform .15s, border-color .15s; }
[class*="st-key-tile_"]:hover { box-shadow: 0 4px 14px rgba(19,30,41,.14); transform: translateY(-2px); }
[class*="st-key-typecard_"]:not([class*="_on"]):hover { border-color: #0070F2 !important; box-shadow: 0 4px 14px rgba(0,112,242,.16); transform: translateY(-2px); }
[class*="st-key-tile_"] button:hover, [class*="st-key-typecard_"] button:hover { border-color: #0070F2; }

/* status pills */
.lv-pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.8rem; font-weight: 600; white-space: nowrap; }
.lv-wait { background: #FFF8D6; color: #7A3D00; }
.lv-ok { background: #F5FAE5; color: #256F3A; }
.lv-done { background: #E1F4FF; color: #0064D9; }
.lv-bad { background: #FFEAF4; color: #AA0808; }
.lv-muted { background: #EFF1F2; color: #556B82; }
.lv-meta { color: #556B82; font-size: 0.86rem; }
.lv-name { font-size: 1.1rem; font-weight: 620; }

/* progress tracker */
.lv-steps { display: flex; gap: 0; list-style: none; margin: 0.7rem 0 0.4rem; padding: 0; }
.lv-steps li { flex: 1; font-size: 0.84rem; color: #758CA4; position: relative; padding-top: 22px; text-align: left; }
.lv-steps li span { position: absolute; top: 4px; left: 0; width: 12px; height: 12px; border-radius: 50%; background: #BCC3CA; z-index: 1; }
.lv-steps li::before { content: ""; position: absolute; top: 9px; left: 0; right: 0; height: 2px; background: #E5E5E5; }
.lv-steps li:last-child::before { right: 100%; }
.lv-steps li.done { color: #131E29; } .lv-steps li.done span { background: #256F3A; } .lv-steps li.done::before { background: #256F3A; }
.lv-steps li.now { color: #131E29; font-weight: 640; } .lv-steps li.now span { background: #fff; border: 3px solid #E76500; }
.lv-steps li.bad { color: #AA0808; font-weight: 640; } .lv-steps li.bad span { background: #AA0808; }

/* duplicate stamps and slips used by shared widgets */
.lv-stamp { display: inline-block; border: 1.5px solid; border-radius: 6px; padding: 1px 8px; font-size: 0.78em; font-weight: 650; vertical-align: 0.1em; }
.lv-slip { border: 1px solid #E5E5E5; border-left: 5px solid; background: #fff; padding: 0.5rem 0.9rem; border-radius: 8px; margin-bottom: 0.5rem; }

/* dashboard: filter bar, KPI cards with sparklines, chart cards, action list (Sales insights) */
.st-key-an_filters { background: #fff; border: 1px solid #E5E5E5; border-radius: .75rem; padding: .7rem 1rem .2rem; margin-bottom: .8rem;
  box-shadow: 0 1px 3px rgba(19,30,41,.06); }
.st-key-an_filters label p { font-size: .75rem; color: #556B82; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; }
.lv-kcard { background: #fff; border: 1px solid #E5E5E5; border-top: 3px solid #0070F2; border-radius: .75rem; padding: .8rem 1rem .65rem;
  box-shadow: 0 1px 3px rgba(19,30,41,.08); min-height: 7.6rem; }
.lv-kcard-label { color: #556B82; font-size: .8125rem; font-weight: 600; }
.lv-kcard-value { font-size: 1.85rem; font-weight: 700; line-height: 1.25; font-variant-numeric: tabular-nums; margin: .1rem 0 .35rem; }
.lv-kcard-foot { display: flex; justify-content: space-between; align-items: flex-end; gap: .5rem; }
.lv-kcard-sub { color: #556B82; font-size: .75rem; margin-top: .25rem; }
.lv-delta { display: inline-block; font-size: .78rem; font-weight: 700; border-radius: 999px; padding: 1px 9px; white-space: nowrap; }
.lv-delta.up { background: #F5FAE5; color: #256F3A; } .lv-delta.down { background: #FFEAF4; color: #AA0808; }
.lv-delta.flat { background: #EFF1F2; color: #556B82; }
.lv-ctitle { font-size: .95rem; font-weight: 700; margin: .1rem 0 0; }
.lv-csub { color: #556B82; font-size: .78rem; margin: 0 0 .3rem; }
</style>
"""


def apply_style():
    st.markdown(STYLE, unsafe_allow_html=True)
