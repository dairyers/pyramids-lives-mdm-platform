"""Audit trail (spec Sections 2.4 R7, 6). Entries are scrubbed when written by engine_mdm.log_event (no
record values, duplicate evidence reduced to sap_id, tier and signal IDs), so they are shown as stored."""
import copy
import json
from datetime import datetime

import pandas as pd
import streamlit as st

import engine_mdm as em
from tabs import common, ui


SHORT_ROLES = {"IT_ADMIN": "IT admin", "SYSTEM": "System"}


def _role_name(config, code):
    return SHORT_ROLES.get(code) or config["roles"].get(code, {}).get("label") or str(code).replace("_", " ").title()


def _plain(config, key, value):
    """One audit detail value in words: role codes and statuses by their names, yes/no, lists joined."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(_plain(config, key, v) for v in value) or "none"
    if isinstance(value, dict):
        return ", ".join(f"{k.replace('_', ' ')} {_plain(config, k, v)}" for k, v in value.items())
    text = str(value)
    if text in config["roles"]:
        return _role_name(config, text)
    if text in config["state_machine"].get("approver_role_by_state", {}) or text in ui.STATUS_TEXT or text.isupper() and "_" in text:
        return ui.status_label(config, text)
    return text


def detail_text(config, event, detail):
    """An audit entry's detail as a short sentence instead of raw JSON."""
    if not detail:
        return ""
    if not isinstance(detail, dict):
        return str(detail)
    if event == "ROLE_SWITCHED":
        return f"Switched from {_plain(config, '', detail.get('from_role'))} to {_plain(config, '', detail.get('to_role'))}"
    if event == "SETTING_CHANGED" and "summary" in detail:
        return f"{detail.get('area', 'Settings')}: {detail['summary']}"
    if {"from", "to"} <= set(detail):
        line = f"{_plain(config, '', detail['from'])} to {_plain(config, '', detail['to'])}"
        return line + (f" ({detail['comment']})" if detail.get("comment") else "")
    if "route_to" in detail:
        line = f"Sent to {_plain(config, '', detail['route_to'])}"
        return line + ("; possible duplicate under review" if detail.get("dup_review") else "")
    return "; ".join(f"{k.replace('_', ' ').capitalize()}: {_plain(config, k, v)}" for k, v in detail.items())


def _when(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return str(iso)


def audit_frame(audit_log, config=None):
    """The log as a table. With a config the values are in words (readable); without, they are as stored (CSV export)."""
    rows = [{"ts": e["ts"] if config is None else _when(e["ts"]), "actor_email": e["actor_email"],
             "actor_role": e["actor_role"] if config is None else _role_name(config, e["actor_role"]),
             "event": e["event"], "request_id": e["request_id"] or "",
             "detail": json.dumps(e["detail"], default=str) if config is None else detail_text(config, e["event"], e["detail"])}
            for e in audit_log]
    return pd.DataFrame(rows, columns=["ts", "actor_email", "actor_role", "event", "request_id", "detail"])


def _chain_check(audit_log):
    """Show whether the record is intact, and let a reviewer see the check catch a change (on a copy, never the real log)."""
    ok, bad = em.verify_audit_chain(audit_log)
    tip = ("Each entry carries a fingerprint of its own content and of the entry before it, like links in a chain. Changing, "
           "removing or reordering any entry breaks every link after it, and the check points at the first broken one. It "
           "shows that the log was altered; it cannot stop someone who can rewrite the whole log. A production system would "
           "keep the latest fingerprint in a separate write-once store.")
    if ok:
        ui.banner(f"Record intact: all {len(audit_log)} entries match their fingerprints.", tip=tip, tone="blue")
    else:
        ui.banner(f"Record altered: entry {bad + 1} no longer matches its fingerprint.", tip=tip, tone="orange")
    with st.expander("See the check catch a change"):
        st.caption("This changes one entry in a copy of the log, not the real one, and runs the same check on it.")
        if st.button("Change one entry in a copy and check it", key="audit_tamper", disabled=len(audit_log) < 2):
            fake = copy.deepcopy(audit_log)
            i = len(fake) // 2
            fake[i]["actor_email"] = "someone.else@example.com"
            _, found = em.verify_audit_chain(fake)
            st.error(f"Caught: entry {found + 1} of {len(fake)} was changed, so it no longer matches its fingerprint.",
                     icon=":material/gpp_bad:")


def render(config, state, user):
    ui.page_header("Audit trail", "A permanent record of who did what, and when. Private values are never written here, "
                                  "only the fact that something happened.")
    ui.story_banner(state)
    _chain_check(state.audit_log)
    df = audit_frame(state.audit_log)                 # as stored: used for filtering and the CSV export
    nice = audit_frame(state.audit_log, config)       # in words: used for the table on screen
    words = common.load_ui()["events"]
    c1, c2 = st.columns(2)
    events = c1.multiselect("What happened", config["audit"]["log_events"], key="audit_events",
                            format_func=lambda e: words.get(e, e))
    rid = c2.text_input("Request reference contains", key="audit_rid", placeholder="for example REQ-")
    if events:
        df = df[df["event"].isin(events)]
    if rid:
        df = df[df["request_id"].str.contains(rid, case=False, regex=False)]
    shown = nice.loc[df.index].iloc[::-1].assign(event=lambda d: d["event"].map(lambda e: words.get(e, e)))
    st.caption(f"Showing {len(df)} of {len(state.audit_log)} entries, newest first.")
    if shown.empty:
        st.info("No activity yet. Submit or approve a request and it will appear here.")
    else:
        ui.table(shown.rename(columns={"ts": "When", "actor_email": "Who", "actor_role": "Role", "event": "What",
                                           "request_id": "Request", "detail": "Detail"}), hide_index=True, page_px=1000)
    st.download_button("Download as CSV", df.to_csv(index=False), file_name="audit_log.csv", mime="text/csv",
                       key="audit_export")
