"""Waiting times (spec Sections 2.5, 3.1 Scenario 5). Supervisor: metadata only (config.security.meta_fields),
no field values. MDM Lead: hours pending, fired rules and the next escalation, plus a manual SLA run on
the (simulated) clock. The 'fast-forward' slider is the brief's 18-day wait (Appendix C)."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import engine_settings
import m365_integration as m365
from tabs import common, email_mock, ui


def board_rows(config, state, with_next):
    at = common.now(state)
    rows = []
    for req in state.requests_db:
        if req["status"] not in config["sla"]["watched_states"]:
            continue
        row = common.meta_row(config, req, at)
        row["fired"] = ", ".join(r["id"] for r in config["sla"]["rules"] if req.get(f"sla_{r['id']}_done")) or "-"
        if with_next:
            rule, hours = m365.next_escalation(req, config, at)
            row["next_escalation"] = f"{rule['id']} {rule['action']} in {hours}h" if rule else "all fired"
        rows.append(row)
    return pd.DataFrame(rows)


def _fast_forward(config, state):
    """Demo tool for Appendix C: move the clock forward and let the real escalation engine react."""
    ladder = engine_settings.reminder_days(config)               # follows the reminder times the administrator set
    days = [0] + ladder + [ladder[-1] + 10]
    watched = [r for r in state.requests_db if r["status"] in config["sla"]["watched_states"] and r.get("pending_since")]
    if not watched:
        st.caption("Nothing is waiting, so there is nothing to fast-forward.")
        return
    oldest = min(datetime.fromisoformat(r["pending_since"]) for r in watched)

    def slide():
        chosen = state.get("waiting_days")
        if chosen is None:                                   # clicking the selected button again clears it
            return
        state.sim_now = oldest + timedelta(days=int(chosen))
        fired = m365.process_sla_escalations(state.requests_db, config, now=state.sim_now, audit_log=state.audit_log)
        for a in fired:
            state.setdefault("waiting_actions", []).append({"day": chosen, **a})
            state.setdefault("waiting_log", []).append(
                {"After": f"{chosen} days", "Request": a["request_id"],
                 "What happened": common.load_ui()["sla_actions"].get(a["action"], a["action"]),
                 "Sent to": ", ".join(a["recipients"])})

    st.caption("Fast-forward time: how long has the request been waiting?")
    if state.get("waiting_days") not in days:
        state.pop("waiting_days", None)                             # the reminder times changed since the last visit
    st.segmented_control("Days waiting", days, key="waiting_days", default=days[0], on_change=slide,
                         label_visibility="collapsed", format_func=lambda d: "Today" if d == days[0] else f"{d} day{'' if d == 1 else 's'}")
    sim = state.get("sim_now")
    st.caption(f"Pretend today is {sim.strftime('%d %b %Y') if sim else 'today (real time)'}. "
               "Each reminder is sent once. Use Reset demo under Case Synopsis stories to replay.")
    if state.get("waiting_log"):
        ui.table(pd.DataFrame(state["waiting_log"]), hide_index=True)
        st.markdown("**What was sent** (open one to see the email and the technical JSON)")
        for n, a in enumerate(state.get("waiting_actions", [])):
            kind = common.load_ui()["sla_actions"].get(a["action"], a["action"])
            with st.expander(f"After {a['day']} days: {kind}, to {len(a['recipients'])} recipient(s)"):
                email_mock.sla_message(config, {k: v for k, v in a.items() if k != "day"}, f"sla_{n}")
    elif sim:
        st.info("No reminder has been needed yet.")
    day = state.get("waiting_days")
    if day == days[-1] and state.get("waiting_log"):
        c1, c2 = st.columns(2)
        c1.success("With this system: reminders went out, a meeting was booked and the request moved to a delegate.")
        c2.error("With email only: nobody was told. The request is still unseen at day 18.")


def render(config, state, user):
    role = config["roles"][user["role"]]
    ui.page_header("Waiting times", "Every request waiting for an approval is timed. Reminders go out automatically, "
                                    "so nobody has to chase by email.")
    ui.story_banner(state)
    sla = sorted(config["sla"]["rules"], key=lambda r: r["after_hours"])
    actions = common.load_ui()["sla_actions"]
    ui.note("Reminders go out automatically.", "The reminder ladder: " + ", then ".join(
        f"after {r['after_hours'] // 24} day{'' if r['after_hours'] == 24 else 's'}, {actions.get(r['action'], r['action'])}"
        for r in sla) + ".")
    at = common.now(state)
    meta_only = role["row_scope"] == "META_ONLY"
    df = board_rows(config, state, with_next=not meta_only)
    first = sla[0]["after_hours"]
    if df.empty:
        st.info("Nothing is waiting for approval right now.")
    else:
        overdue = df[df["hours_pending"].fillna(0) >= first]
        c1, c2 = st.columns(2)
        with c1, st.container(border=True):
            st.metric("Waiting for approval", len(df))
        with c2, st.container(border=True):
            st.metric(f"Waiting more than {first // 24} days", len(overdue))
        show = pd.DataFrame({
            "Request": df["request_id"], "Record": df["record_name"],
            "Waiting for": df["status"].map(lambda s: ui.status_label(config, s).replace("Waiting for ", "")),
            "Days waiting": (df["hours_pending"].astype(float) / 24).round(1),
            "Reminders sent": df["fired"]})
        if "next_escalation" in df:
            show["Next reminder"] = df["next_escalation"]
        ui.table(show, hide_index=True)
        if meta_only:
            st.caption("You see request names and timings only, never the private field values.")
    if not meta_only:
        if st.button("Send any reminders that are due", key="sla_run"):
            fired = m365.process_sla_escalations(state.requests_db, config, now=at, audit_log=state.audit_log)
            state["sla_last_fired"] = [{k: a.get(k) for k in ("rule_id", "action", "request_id", "hours_pending",
                                                              "recipients", "subject", "title")} for a in fired]
            state["sla_last_actions"] = fired
            st.rerun()
        if state.get("sla_last_fired") is not None:
            st.write("Sent on the last check:" if state["sla_last_fired"] else "No reminder was due.")
            if state["sla_last_fired"]:
                ui.table(pd.DataFrame(state["sla_last_fired"]).astype(str), hide_index=True)
                for n, a in enumerate(state.get("sla_last_actions", [])):
                    with st.expander(f"{common.load_ui()['sla_actions'].get(a['action'], a['action'])}: {a['request_id']}"):
                        email_mock.sla_message(config, a, f"slalast_{n}")
    ui.section("Try the 18-day wait", "From the brief: a request sits unseen for weeks. Click a day to see what changes.")
    _fast_forward(config, state)
