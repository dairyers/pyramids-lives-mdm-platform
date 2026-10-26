"""Requests / Approval Inbox (spec Sections 2.3-2.5, 3.1): rows filtered by can_view_request, every value
through apply_field_security, Approve/Reject only for roles whose can_decide_states hold the current
state, duplicate-review panel, Outlook card preview and a simulated webhook click. The Payments Officer
sees the release queue; a FULL bank view is audited as SENSITIVE_FIELD_VIEWED_FULL."""
import streamlit as st

import engine_mdm as em
import engine_security as es
import m365_integration as m365
from tabs import common, email_mock, ui


_existing_record = common.existing_record


def _decide(state, config, user, req, action, comment):
    try:
        em.transition(req, action, user, config, comment=comment or None, now=common.now(state),
                      audit_log=state.audit_log)
        name = req["record_name"]
        st.session_state["inbox_flash"] = (
            f"{name} approved. Now: {ui.status_label(config, req['status'])}." if action == "APPROVE"
            else f"{name} rejected and sent back to the requester." if action == "REJECT"
            else f"{name}: {action.lower()} done.")
    except (PermissionError, ValueError) as e:
        st.session_state["inbox_flash"] = f"Not allowed: {e}"


def _dup_panel(config, state, user, req):
    domain = req["domain"]
    st.markdown("**Possible duplicate: compare before deciding**")
    cc = common.dedup_cfg(config, domain).get("corroboration") or {}
    detail_roles = cc.get("detail_visible_roles", [])
    if cc and user["role"] not in detail_roles:
        # Evidence disclosure: the existing record's contact, phone and address are compared values,
        # so only detail_visible_roles get the side-by-side comparison.
        for m in req["dup_matches"]:
            common.render_match(config, domain, m, user)
        st.caption("Side-by-side comparison visible only to: "
                   + ", ".join(config["roles"][r]["label"] for r in detail_roles))
        return
    c = st.columns(2)
    for m in req["dup_matches"]:
        existing, ex_status = _existing_record(state, config, domain, m["sap_id"])
        with c[0]:
            st.caption(f"New request {req['request_id']}")
            view, report = common.secure_view(state, config, user, req["payload"], domain, status=req["status"],
                                              request_id=req["request_id"])
            common.show_view(config, domain, view, report)
        with c[1]:
            st.caption(f"Existing record {m['sap_id']}")
            if existing is not None:
                view, report = common.secure_view(state, config, user, existing, domain, status=ex_status)
                common.show_view(config, domain, view, report)
            else:
                st.write("(record no longer available)")
        common.render_match(config, domain, m, user)


ANSWER_TEXT = {"JUSTIFIED": "asked for a new record anyway",
               "DIFFERENT_ORGANISATION": "says it is a different organisation",
               "NONE_OF_THESE": "says none of the matches is the right one",
               "PICKED_CANDIDATE": "picked one of the suggested records"}


def _flag_reasons(config, req, user):
    """Why each match was flagged, in words: name similarity, the verdict, and the evidence this role may see."""
    cc = common.dedup_cfg(config, req["domain"]).get("corroboration") or {}
    tiers = common.load_ui()["tiers"]
    parts = []
    for m in _flagged(req):
        evidence = [e["label"] for e in em.evidence_for_role(m.get("corroboration"), user["role"], cc)] if cc else []
        line = (f"{m['existing_name']} ({m['sap_id']}): names {m.get('confidence_score', 0):.0f}% alike; "
                f"verdict: {tiers.get(m.get('tier'), 'flagged')}")
        alias = common.alias_explanation(m)
        if alias:
            line += "; abbreviation read as " + " / ".join(alias)
        line += "; evidence: " + (", ".join(evidence) if evidence else "the name alone")
        parts.append(line + ".")
    return " ".join(parts)


def _comments(req):
    """Every comment typed at an approval step, newest last, shown on the card itself (not only in History)."""
    notes = [h for h in req.get("history", []) if h.get("comment") and h["action"] in ("APPROVE", "REJECT", "REASSIGN")]
    if not notes:
        return
    verb = {"APPROVE": "approved", "REJECT": "rejected", "REASSIGN": "handed over"}
    st.info("**Comments**  \n" + "  \n".join(
        f"{ui.person(h['actor'])} {verb[h['action']]} on {ui.when(h['ts'])}: {h['comment']}" for h in notes),
        icon=":material/forum:")


def _requester_reason(config, req):
    """What the requester said about the possible duplicate, shown first so the approver reads it before deciding."""
    reason, answer = req.get("dup_override_justification"), req.get("dup_requester_answer")
    if not (reason or answer):
        return
    ui.banner(f"Notes attached: {reason or 'no reason given'} ({ui.person(req['requester_email'])})",
              ANSWER_TEXT.get(answer, "") and f"The requester {ANSWER_TEXT[answer]}.", tone="blue")


def _decision_buttons(config, state, user, req):
    rid = req["request_id"]
    comment = st.text_input("Add a comment (optional)", key=f"cmt_{rid}")
    c1, c2 = st.columns(2)
    flagged = [m["sap_id"] for m in req.get("dup_matches") or [] if m.get("tier") not in (None, "SUPERSEDED")]
    approve_label = "Approve override" if flagged and req["status"] == "PENDING_MDM" else "Approve"
    reject_label = f"Reject - use {flagged[0]}" if flagged and req["status"] == "PENDING_MDM" else "Reject"
    if c1.button(approve_label, key=f"approve_{rid}", type="primary"):
        _decide(state, config, user, req, "APPROVE", comment)
        st.rerun()
    if c2.button(reject_label, key=f"reject_{rid}"):
        _decide(state, config, user, req, "REJECT", comment)
        st.rerun()


def _card_preview(config, state, user, req, can_decide):
    st.caption("The email sent to the approver: mockup and technical JSON.")
    email_mock.approval_email(config, state, req, user["role"], f"mail_{req['request_id']}")
    if can_decide:
        if st.button("Click Approve in the email (simulated)", key=f"hook_{req['request_id']}"):
            try:
                resp = m365.simulate_webhook_response(req, user, "APPROVE", "Approved from Outlook", config,
                                                      audit_log=state.audit_log, now=common.now(state))
                st.session_state["inbox_flash"] = f"{req['record_name']} approved from the email. Now: {ui.status_label(config, resp['status'])}."
            except (PermissionError, ValueError) as e:
                st.session_state["inbox_flash"] = f"Webhook refused: {e}"
            st.rerun()
    else:
        st.caption("The webhook would refuse an Approve click from your role for this state.")


def _flagged(req):
    return [m for m in req.get("dup_matches") or [] if m.get("tier") not in (None, "SUPERSEDED")]


def _render_request(config, state, user, req, open_details=True):
    rid, role = req["request_id"], config["roles"][user["role"]]
    can_decide = req["status"] in role["can_decide_states"]
    names = common.domain_text(config)
    at = common.now(state)
    waiting = ui.waiting_text(common.hours_pending(req, at)) if req["status"] in config["sla"]["watched_states"] else ""
    # Object page: title and status, header facets, progress, then anchored sections as tabs and the decision footer.
    with st.container(border=True):
        st.markdown(f"<p class='lv-op-title'>{req['record_name']}</p>{ui.pill(config, req['status'])}",
                    unsafe_allow_html=True)
        ui.facets([("Type", names[req["domain"]]["name"]), ("Request", rid), ("Submitted", ui.when(req["created_at"])),
                   ("Waiting", waiting.replace("waiting ", ""))])
        st.markdown(ui.tracker(config, req), unsafe_allow_html=True)
        if _flagged(req):
            ui.banner("Near duplicate flagged for review: "
                      + ", ".join(f"{m['existing_name']} ({m['sap_id']})" for m in _flagged(req)),
                      _flag_reasons(config, req, user))
        _requester_reason(config, req)
        _comments(req)
        pending = req["status"] in config["state_machine"]["pending_states"]
        with st.expander("Details, history and email" if pending else "Details and history", expanded=open_details or can_decide):
            tabs = st.tabs(["Details", "History"] + (["Email"] if pending else []))
            with tabs[0]:
                view, report = common.secure_view(state, config, user, req["payload"], req["domain"], status=req["status"],
                                                  request_id=rid)
                common.show_view(config, req["domain"], view, report)
                if req.get("dup_matches"):
                    _dup_panel(config, state, user, req)
            with tabs[1]:
                for h in req["history"]:
                    st.write(f"{ui.when(h['ts'])}: {ui.history_text(h)}")
            if pending:
                with tabs[2]:
                    _card_preview(config, state, user, req, can_decide)
        if can_decide:
            with st.container(key=f"decision_{rid}"):
                st.markdown("**Your decision**")
                _decision_buttons(config, state, user, req)


def _lens(config, state, user, reqs):
    with st.expander("Who can see what? Pick a request and a role"):
        c1, c2 = st.columns(2)
        rid = c1.selectbox("Request", [r["request_id"] for r in reqs], key="inbox_lens_req")
        req = common.find_request(state, rid)
        if req:
            lens = common.lens_for_viewer(config, req["payload"], req["domain"], req["status"], user["role"])
            role = c2.selectbox("Role", list(lens.columns), key="inbox_lens_role")
            ui.detail_table([(f, common.field_label(config, req["domain"], f), v, "hidden" in str(v).lower())
                             for f, v in lens[role].items()], "lv-lens")


def _digest(config, state, user):
    if user["role"] in config["m365"]["digest_roles"]:
        with st.expander("Your daily email summary (preview)"):
            st.html(m365.render_email_digest(state.requests_db, user["role"], config, now=common.now(state)))


TITLES = {"OWN": ("My requests", "Everything you have asked for, and exactly where each one stands."),
          "QUEUE": ("Approvals", "Requests waiting for your decision. Approve or reject with one click."),
          "RELEASE": ("Payments release", "Approved records that carry bank details. Full account numbers appear only here."),
          "ALL": ("All requests", "Every request across the group, with its status and history.")}


def render(config, state, user):
    role = config["roles"][user["role"]]
    scope = role["row_scope"]
    _, sub = TITLES.get(scope, TITLES["ALL"])
    ui.page_header("Requests", sub)
    ui.story_banner(state)
    if "inbox_flash" in state:
        st.toast(state.pop("inbox_flash"), icon=":material/check_circle:")
    reqs = list(reversed([r for r in state.requests_db if es.can_view_request(user, r, config)]))
    has_sap = "STAGING" in es.visible_tabs(user["role"], config)
    b = common.buckets(config, user["role"], reqs, ready_separate=has_sap)
    choices = {"mine": b["mine"], "others": b["others"], "done": b["done"], "all": reqs, "sap": b["ready"]}
    ids = (["mine"] if role["can_decide_states"] else []) + (["others"] if b["others"] or scope in ("OWN", "ALL") else []) \
        + ["done", "all"] + (["sap"] if has_sap else [])
    labels = {"mine": "Needs you", "others": "With others", "done": "Finished", "all": "Everything", "sap": "SAP records"}
    first = "mine" if "mine" in ids and choices["mine"] else "all"       # decisions waiting, else everything (rejected too)
    if state.get("inbox_filter") not in ids:
        state.pop("inbox_filter", None)                                 # a stale choice from another role
    pick, late_box = st.columns([4, 2], vertical_alignment="center")
    with pick:
        sel = st.segmented_control("Show", ids, key="inbox_filter", label_visibility="collapsed",
                                   **({} if "inbox_filter" in state else {"default": first}),
                                   format_func=lambda i: f"{labels[i]} ({len(choices[i])})" if i != "sap" or choices[i] else labels[i]) or first
    with late_box:
        late_only = st.checkbox(f"Over {ui.hours_text(common.first_reminder_hours(config))} only", key="inbox_late",
                                disabled=sel in ("done", "sap"))
    if sel == "sap":
        from tabs import tab_staging
        tab_staging.render_body(config, state, user)
        return
    shown = choices[sel]
    if late_only and sel not in ("done", "sap"):
        at = common.now(state)
        shown = [r for r in shown if common.is_overdue(config, r, at)]
    if not shown:
        if not reqs:
            st.info("Nothing here yet." + (" Start with a new request." if role["can_submit"] else ""))
            if role["can_submit"]:
                st.button("New request", key="inbox_new", type="primary", on_click=ui.go, args=(state, "new"))
        else:
            st.success("All clear. Nothing in this view.")
    for req in shown:
        _render_request(config, state, user, req, open_details=len(shown) == 1)
    if reqs and scope == "ALL":
        _lens(config, state, user, reqs)
    _digest(config, state, user)
