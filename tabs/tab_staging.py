"""SAP B1 Staging (spec Sections 1.2, 2.4): OCRD / OITM / OOCR tables with every cell passed through
apply_field_security for the current role. Staged requests respect row scope. The MDM Lead's Sync
button moves APPROVED requests to SYNCED_TO_SAP and adds them to the session's master copy."""
import re

import streamlit as st

import engine_mdm as em
import engine_security as es
from tabs import common, ui


def next_key(existing_keys, fallback):
    """Next code in the style of the existing ones (C10035 -> C10036, ITM-EQ-008 -> ITM-EQ-009)."""
    best = None
    for k in existing_keys:
        m = re.match(r"^(.*?)(\d+)$", str(k or ""))
        if m and (best is None or int(m.group(2)) > int(best.group(2))):
            best = m
    if best is None:
        return fallback
    return f"{best.group(1)}{int(best.group(2)) + 1:0{len(best.group(2))}d}"


def sync_request(state, config, user, req):
    """SYNC transition plus the new SAP record (key assigned when the payload has none)."""
    dom = config["domains"][req["domain"]]
    table, key = dom["sap_table"], dom["key_field"]
    em.transition(req, "SYNC", user, config, now=common.now(state), audit_log=state.audit_log)
    rec = {k: v for k, v in req["payload"].items() if not str(k).startswith("_")}
    if "card_type" in dom:
        rec["CardType"] = dom["card_type"]
        same = [r.get(key) for r in state.sap_master.get(table, []) if r.get("CardType") == dom["card_type"]]
    else:
        same = [r.get(key) for r in state.sap_master.get(table, [])]
    start = f"{dom['key_prefix']}00001" if dom.get("key_prefix") else req["request_id"]
    rec[key] = rec.get(key) or next_key(same, start)
    state.sap_master.setdefault(table, []).append(rec)
    return rec


def render_body(config, state, user):
    """SAP records, shown as a tab of the Requests page: approved requests ready to create in SAP Business One, and
    what SAP already holds. In this prototype SAP is simulated; nothing leaves the app."""
    if "staging_flash" in state:
        st.toast(state.pop("staging_flash"), icon=":material/check_circle:")
    tables = list(dict.fromkeys(d["sap_table"] for d in config["domains"].values()))
    names = common.table_names(config)
    staged_states = [s for s in ("APPROVED",) + tuple(config["state_machine"]["terminal"])
                     if s != "REJECTED"]
    for table, tab in zip(tables, st.tabs([names.get(t, t) for t in tables])):
        with tab:
            staged = [r for r in state.requests_db if r["sap_table"] == table and r["status"] in staged_states
                      and es.can_view_request(user, r, config)]
            st.markdown("##### Approved and ready to create")
            if staged:
                ui.table(common.secured_frame(state, config, user,
                                                  [(r["payload"], r["domain"], r["status"], r["request_id"])
                                                   for r in staged]), hide_index=True)
                for r in staged:
                    if r.get("dup_override_justification") or any(h.get("comment") for h in r["history"]):
                        from tabs import tab_inbox
                        with st.expander(f"Notes and comments: {r['record_name']} ({r['request_id']})"):
                            tab_inbox._requester_reason(config, r)
                            tab_inbox._comments(r)
                    if r["status"] == "APPROVED" and "SYNC" in em.allowed_actions(r, user, config):
                        if st.button(f"Create '{r['record_name']}' in SAP", key=f"sync_{r['request_id']}",
                                     type="primary"):
                            rec = sync_request(state, config, user, r)
                            state["staging_flash"] = (f"{r['request_id']} synced to {table} as "
                                                      f"{rec[config['domains'][r['domain']]['key_field']]}.")
                            st.rerun()
            else:
                st.caption("Nothing is waiting here for your role.")
            st.markdown("##### Already in SAP")
            rows = [(rec, common.domains_for_record(config, table, rec), rec.get("U_ReqStatus"), None)
                    for rec in state.sap_master.get(table, [])]
            rows = [r for r in rows if r[1]]
            if rows:
                ui.table(common.secured_frame(state, config, user, rows), hide_index=True)
