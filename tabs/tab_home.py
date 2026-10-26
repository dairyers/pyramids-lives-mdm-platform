"""Home: one calm landing page per role. What this role does here, what needs attention now, and the
one or two obvious next buttons. Counts come from the requests the viewer is allowed to see."""
import streamlit as st

import engine_security as es
from tabs import common, ui


def _recent(config, reqs, limit=6):
    events = [(h["ts"], r, h) for r in reqs for h in r["history"] if h["action"] != "ROUTE"]
    return sorted(events, key=lambda e: e[0], reverse=True)[:limit]


def _show(state, view, late=False):
    """Open Requests on the list this tile or row stands for (late: only those over the first reminder time)."""
    state["inbox_filter"] = view
    state["inbox_late"] = late
    ui.go(state, "requests")


def render(config, state, user):
    role = config["roles"][user["role"]]
    copy = common.load_ui()
    ui.page_header(f"Welcome, {user['display']}", copy["home"].get(user["role"], copy["home_default"]))
    ui.story_banner(state)

    reqs = [r for r in state.requests_db if es.can_view_request(user, r, config)]
    at = common.now(state)
    allowed = es.visible_tabs(user["role"], config)
    b = common.buckets(config, user["role"], reqs, ready_separate="STAGING" in allowed)
    waiting_for_me = b["mine"]
    first = common.first_reminder_hours(config)
    can_open = "INBOX" in allowed

    def late(items):
        n = sum(common.is_overdue(config, r, at) for r in items)
        return n, (f"{n} over {ui.hours_text(first)}" if n else "")

    # Piles that never overlap, by whose turn it is. The orange line is how many in that pile have waited too long.
    tiles = []
    if role["can_decide_states"]:
        tiles.append(("Needs you", b["mine"], "red", "mine", True))
    if b["others"] or role["row_scope"] in ("OWN", "ALL"):
        tiles.append(("With others", b["others"], "yellow", "others", True))
    if "STAGING" in allowed:
        tiles.append(("Approved, ready for SAP", b["ready"], "blue", "sap", False))
    if role["can_submit"]:
        tiles.append(("Rejected, needs a fix", b["rejected"], "pink", "done", False))
    tiles.append(("Created in SAP", b["created"], "green", "done", False))
    ui.section("My work")
    for col, (label, items, colour, view, show_late) in zip(st.columns(max(len(tiles), 3)), tiles):   # at least 3 slots, so two tiles do not stretch wide
        n_late, sub = late(items) if show_late else (0, "")
        with col, st.container(border=True, key=f"tile_{colour}"):
            ui.kpi(label, len(items), sub=sub)
            if not items:
                st.caption("Nothing here")
            if can_open and items:
                st.button("Show", key=f"go_{colour}", on_click=_show, args=(state, view))
                if n_late:
                    st.button("Only the late ones", key=f"go_late_{colour}", on_click=_show, args=(state, view, True))

    ui.section("Needs you" if waiting_for_me else "Next step")
    if waiting_for_me:
        for req in waiting_for_me[:5]:
            with st.container(border=True):
                left, right = st.columns([5, 1], vertical_alignment="center")
                left.markdown(f"**{req['record_name']}** &nbsp;{ui.pill(config, req['status'])}<br>"
                              f"<span class='lv-meta'>{common.domain_text(config)[req['domain']]['name']} &middot; "
                              f"{ui.when(req['created_at'])} &middot; "
                              f"{ui.waiting_text(common.hours_pending(req, at)) or 'just arrived'}</span>",
                              unsafe_allow_html=True)
                right.button("Review", key=f"need_{req['request_id']}", type="primary", on_click=_show, args=(state, "mine"))
        if len(waiting_for_me) > 5:
            st.caption(f"and {len(waiting_for_me) - 5} more under Requests.")
    else:
        st.success("All clear. Nothing is waiting for your decision.", icon=":material/check_circle:")
        buttons = []
        if role["can_submit"]:
            buttons.append(("New request", "new", "primary"))
        if can_open:
            buttons.append(("See requests", "requests", "secondary"))
        if "ANALYTICS" in allowed:
            buttons.append(("Open sales insights", "insights", "secondary"))
        if "SLA_BOARD" in allowed and not can_open:
            buttons.append(("See waiting times", "waiting", "secondary"))
        for col, (label, target, kind) in zip(st.columns(max(len(buttons), 1)), buttons):
            col.button(label, key=f"home_{target}", type=kind, width="stretch", on_click=ui.go, args=(state, target))

    ui.section("Recent activity", "The latest steps on requests you can see.")
    events = _recent(config, reqs)
    if not events:
        st.info("Nothing has happened yet. Activity appears here as requests are raised and decided.")
    for ts, req, h in events:
        st.markdown(f"**{req['record_name']}** &nbsp;{ui.pill(config, h['to'])}<br>"
                    f"<span class='lv-meta'>{ui.when(ts)} &middot; {ui.history_text(h)}</span>", unsafe_allow_html=True)
