"""Intake & Gatekeeper tab (spec Sections 2.1-2.3, 3.1): config-driven dynamic form with live validation,
abbreviation-aware duplicate check with the evidence-quorum tiers, routing and submission.

The duplicate gate is driven by config tiers[tier].action, never by tier names in code:
HARD_BLOCK stops; BLOCK_UNLESS_JUSTIFIED needs a justification; REQUESTER_MUST_CONFIRM asks
"same organisation?"; REQUESTER_MUST_CHOOSE lists candidates and re-runs with the pick;
WARN_ONLY shows a note; NONE shows alternatives only.
"""
import streamlit as st

import engine_mdm as em
from tabs import common, help_guide, ui

ACTION_ORDER = ["HARD_BLOCK", "REQUESTER_MUST_CHOOSE", "BLOCK_UNLESS_JUSTIFIED", "REQUESTER_MUST_CONFIRM",
                "WARN_ONLY", "NONE"]
NON_BLOCKING = {"WARN_ONLY", "NONE"}
UNTIERED_ACTION = "BLOCK_UNLESS_JUSTIFIED"     # tables without tier config: a name match needs a reason (MDM reviews)
REVIEW_KEY, CMP_KEY = "intake_review", "intake_cmp"
REVIEW_NOTE_KEY = "intake_review_note"
ASK, ASK_QUERY = "_ask", "intake_ask_q"
DOMAIN_KEY, FLOW_KEY, RESULT_KEY, ATTEMPT_KEY, RESET_KEY = (
    "intake_domain", "intake_flow", "intake_result", "intake_attempted", "intake_reset")


# ----------------------------------------------------------------------------- form helpers
def widget_key(domain, field):
    """One key per field and form 'generation': a fresh form gets fresh widget identities, so the browser
    never keeps showing values the app has already cleared (after a submit, a role switch or a reset)."""
    return f"in_{domain}_{field}_{st.session_state.get(common.FORM_GEN_KEY, 0)}"


def _write_only(config, f):
    return bool(config["security"]["classes"].get(f.get("security_class", "GENERAL"), {}).get("write_only_in_form"))


def _coerce(f, value):
    if value in (None, ""):
        return None
    if f.get("type") == "enum":
        return next((o for o in f["options"] if str(o) == str(value)), None)
    if f.get("type") == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return str(value)


def clear_form(state, config, domain=None):
    for d in ([domain] if domain else config["domains"]):
        for f in config["domains"][d]["fields"]:
            state.pop(widget_key(d, f["field"]), None)
    state[common.FORM_GEN_KEY] = state.get(common.FORM_GEN_KEY, 0) + 1
    state.pop(ATTEMPT_KEY, None)
    for k in [k for k in state.keys() if str(k).startswith(("intake_review", "intake_cmp", "intake_rel_"))]:
        state.pop(k, None)
    clear_gate(state)


def prefill(state, config, domain, payload):
    """Load a payload into the form widgets (used by the Example Scenarios tab, inside a callback)."""
    clear_form(state, config, domain)
    state[DOMAIN_KEY] = domain
    for f in config["domains"][domain]["fields"]:
        v = _coerce(f, payload.get(f["field"]))
        if v is not None:
            state[widget_key(domain, f["field"])] = v
    state.pop(RESULT_KEY, None)


def collect_payload(state, config, domain):
    out = {}
    for f in config["domains"][domain]["fields"]:
        v = state.get(widget_key(domain, f["field"]))
        if v not in (None, ""):
            out[f["field"]] = v
    return out


# ----------------------------------------------------------------------------- engine pipeline (UI-free)
def dedup_pool(state, config, domain, exclude=()):
    """SAP master plus pending requests; `exclude` drops given request IDs (a scenario's own earlier runs)."""
    table = config["domains"][domain]["sap_table"]
    others = [r for r in state.requests_db if r["request_id"] not in set(exclude)]
    return em.build_dedup_pool(domain, state.sap_master.get(table, []), others, config)


def evaluate(state, config, domain, payload, exclude=(), compare_name=None):
    """Validation, then the duplicate check against SAP master plus pending requests. compare_name lets the
    requester correct the name the check uses (for example after we misread an abbreviation); the stored
    request keeps the name as typed. Near-miss records are reconciled automatically (find_related_records)."""
    ok, errors = em.validate_against_config(domain, payload, config)
    pool = dedup_pool(state, config, domain, exclude)
    field = common.dedup_cfg(config, domain)["compare_field"]
    checked = {**payload, field: compare_name} if compare_name else payload
    matches = em.check_fuzzy_duplicates(domain, checked, pool, config) if ok else []
    related = (em.find_related_records(domain, payload, pool, config, {m["sap_id"] for m in matches}, compare_name)
               if ok else [])
    return {"valid": ok, "errors": errors, "matches": matches, "related": related,
            "gate": gate_action(config, domain, matches)}


def match_action(config, domain, match):
    tiers = common.tiers_cfg(config, domain)
    if match.get("tier") in tiers:
        return tiers[match["tier"]]["action"]
    return "HARD_BLOCK" if match.get("hard_block") else UNTIERED_ACTION


def gate_action(config, domain, matches):
    """The most restrictive action among the matches (None when there are no matches)."""
    actions = [match_action(config, domain, m) for m in matches]
    return min(actions, key=ACTION_ORDER.index) if actions else None


def gate_matches(config, domain, matches, action):
    return [m for m in matches if match_action(config, domain, m) == action]


def submit_request(state, config, user, domain, payload, matches, answer=None, justification=None):
    """create_request -> SUBMIT -> ROUTE (dup_review from dup_review_required). Events are audited."""
    t = common.now(state)
    req = em.create_request(domain, payload, user, config, now=t)
    review = state.pop(REVIEW_NOTE_KEY, None)               # reasons given on the review screen for near-miss records
    if review:
        justification = f"{justification} | {review}" if justification else review
        answer = answer or "DIFFERENT_ORGANISATION"
    req["dup_matches"] = matches
    req["dup_requester_answer"] = answer
    req["dup_override_justification"] = justification
    em.transition(req, "SUBMIT", user, config, now=t, audit_log=state.audit_log)
    em.transition(req, "ROUTE", em.system_user(config), config, now=t, audit_log=state.audit_log)
    state.requests_db.append(req)
    return req


def log_flagged(state, config, user, matches):
    if matches:
        common.log(state, config, user, "DUPLICATE_FLAGGED", None,
                   {"matches": matches})   # log_event reduces each match to sap_id, tier, signal IDs


def log_answer(state, config, user, answer, matches, request_id=None):
    common.log(state, config, user, "DUPLICATE_REQUESTER_ANSWER", request_id,
               {"answer": answer, "matches": matches})


def log_justified(state, config, user, matches, justification, request_id=None):
    common.log(state, config, user, "DUPLICATE_OVERRIDE_JUSTIFIED", request_id,
               {"matches": matches, "justification_chars": len(justification)})


# ----------------------------------------------------------------------------- flow (UI)
# Every flow key is namespaced (ns) so the Example Scenarios tab can run its own copy of the gate next to
# the Intake tab without widget-key collisions. ns="intake" is the Intake tab itself.
def _k(ns, name):
    return f"{ns}_{name}"


def clear_gate(state, ns="intake"):
    for k in [k for k in state.keys() if str(k).startswith(_k(ns, "gate_"))] + [_k(ns, "flow")]:
        state.pop(k, None)


def start_flow(state, config, user, domain, payload, ns="intake", exclude=(), compare_name=None):
    """Validate and duplicate-check; submit at once when nothing blocks, else open the gate (callback-safe)."""
    state[_k(ns, "attempted")] = True
    state.pop(_k(ns, "result"), None)
    clear_gate(state, ns)
    res = evaluate(state, config, domain, payload, exclude, compare_name)
    if not res["valid"]:
        common.log(state, config, user, "VALIDATION_BLOCKED", None,
                   {"domain": domain, "error_count": len(res["errors"]), "errors": res["errors"]})
        state[_k(ns, "result")] = {"kind": "BLOCKED", "errors": res["errors"]}
        return res
    log_flagged(state, config, user, res["matches"])
    if res["gate"] is None or res["gate"] in NON_BLOCKING:
        _finish(state, config, user, domain, payload, res["matches"], ns=ns)
        return res
    state[_k(ns, "flow")] = {"domain": domain, "form_payload": dict(payload), "payload": dict(payload),
                             "matches": res["matches"], "gate": res["gate"], "exclude": list(exclude)}
    return res


def _finish(state, config, user, domain, payload, matches, answer=None, justification=None, ns="intake"):
    req = submit_request(state, config, user, domain, payload, matches, answer, justification)
    if answer == "JUSTIFIED":
        log_justified(state, config, user, gate_matches(config, domain, matches, "BLOCK_UNLESS_JUSTIFIED") or matches,
                      justification, req["request_id"])
    state[_k(ns, "result")] = {"kind": "SUBMITTED", "request_id": req["request_id"], "rule_id": req["routing_rule_id"],
                               "status": req["status"], "domain": domain,
                               "notes": [common.ui_message(config, domain, m) for m in matches
                                         if match_action(config, domain, m) in NON_BLOCKING]}
    clear_gate(state, ns)
    state[_k(ns, "reset")] = True
    state[_k(ns, "flash")] = f"Request {req['request_id']} sent for approval."
    return req


def _stop(state, sap_ids, ns="intake"):
    state[_k(ns, "result")] = {"kind": "USE_EXISTING", "sap_ids": sap_ids}
    clear_gate(state, ns)
    state[_k(ns, "reset")] = True


def render_gate(config, state, user, ns="intake"):
    flow = state[_k(ns, "flow")]
    domain, payload, matches, gate = flow["domain"], flow["payload"], flow["matches"], flow["gate"]
    g = lambda name: _k(ns, "gate_" + name)            # noqa: E731
    st.markdown("##### We found a record that may already exist")
    st.caption("Please check before creating a new one. This keeps SAP free of duplicates.")
    for m in matches:
        common.plain_match(config, domain, m, user)
        common.side_by_side(state, config, domain, m, flow["form_payload"], user)
    gm = gate_matches(config, domain, matches, gate)
    ids = [m["sap_id"] for m in gm]
    with st.container(border=True):
        if gate == "HARD_BLOCK":
            st.error("Submission blocked. " + " ".join(common.ui_message(config, domain, m) for m in gm))
            if st.button("Use the existing record", key=g("hard_ok")):
                _stop(state, ids, ns)
                st.rerun()
        elif gate == "BLOCK_UNLESS_JUSTIFIED":
            names = ", ".join(f"{m['existing_name']} ({m['sap_id']})" for m in gm)
            st.markdown(f"#### Is this the same as {names}?")
            st.caption("Nothing is sent until you choose. The recommended answer is highlighted.")
            c1, c2 = st.columns(2)
            with c1, st.container(border=True, key=f"rec_{ns}_same"):
                st.markdown("**Yes, it is the same**<span class='lv-rec'>Recommended</span>", unsafe_allow_html=True)
                st.caption("Use the record we already have. No new request is needed.")
                if st.button(f"Use {', '.join(map(str, ids))}", key=g("use_existing"), type="primary", width="stretch"):
                    log_answer(state, config, user, "USE_EXISTING", gm)
                    _stop(state, ids, ns)
                    st.rerun()
            with c2, st.container(border=True):
                st.markdown("**No, it is different**")
                just = st.text_area("Why does it need a new record?", key=g("justification"),
                                    placeholder="For example: a different business unit with separate billing")
                if st.button("Send as a new record", key=g("justify"), disabled=not just.strip(), width="stretch"):
                    _finish(state, config, user, domain, payload, matches, answer="JUSTIFIED",
                            justification=just.strip(), ns=ns)
                    st.rerun()
        elif gate == "REQUESTER_MUST_CONFIRM":
            names = ", ".join(f"{m['sap_id']} {m['existing_name']}" for m in gm)
            ans = st.radio(f"Is this the same organisation as {names}?",
                           ["Yes - same organisation", "No - a different organisation"], index=None, key=g("confirm"))
            if ans and ans.startswith("Yes"):
                if st.button("Stop and use the existing record", key=g("confirm_yes")):
                    log_answer(state, config, user, "SAME_ORGANISATION", gm)
                    _stop(state, ids, ns)
                    st.rerun()
            elif ans:
                reason = st.text_input("Why is it a different organisation?", key=g("confirm_reason"))
                if st.button("Submit for MDM review", key=g("confirm_no"), disabled=not reason.strip()):
                    log_answer(state, config, user, "DIFFERENT_ORGANISATION", gm)
                    _finish(state, config, user, domain, payload, matches, answer="DIFFERENT_ORGANISATION",
                            justification=reason.strip(), ns=ns)
                    st.rerun()
        elif gate == "REQUESTER_MUST_CHOOSE":
            st.info(" ".join(dict.fromkeys(common.ui_message(config, domain, m) for m in gm)))
            none_label = "None of these - a new organisation"
            options = [f"{m['sap_id']} - {m['existing_name']}" for m in gm] + [none_label]
            pick = st.radio("Which organisation do you mean?", options, index=None, key=g("choose"))
            if pick and pick != none_label:
                chosen = gm[options.index(pick)]
                if st.button("Re-run the check with my pick", key=g("choose_rerun")):
                    log_answer(state, config, user, "PICKED_CANDIDATE", [chosen])
                    field = common.dedup_cfg(config, domain)["compare_field"]
                    new_payload = {**payload, field: chosen["existing_name"]}
                    res = evaluate(state, config, domain, new_payload, flow.get("exclude", ()))
                    log_flagged(state, config, user, res["matches"])
                    if res["gate"] is None or res["gate"] in NON_BLOCKING:
                        _finish(state, config, user, domain, new_payload, res["matches"], answer="PICKED_CANDIDATE",
                                ns=ns)
                    else:
                        clear_gate(state, ns)
                        state[_k(ns, "flow")] = {**flow, "payload": new_payload, "matches": res["matches"],
                                                 "gate": res["gate"]}
                    st.rerun()
            elif pick:
                reason = st.text_input("Describe the new organisation", key=g("choose_reason"))
                if st.button("Submit for MDM review", key=g("choose_none"), disabled=not reason.strip()):
                    log_answer(state, config, user, "NONE_OF_THESE", gm)
                    _finish(state, config, user, domain, payload, matches, answer="NONE_OF_THESE",
                            justification=reason.strip(), ns=ns)
                    st.rerun()
        if st.button("Cancel", key=g("cancel")):
            clear_gate(state, ns)
            st.rerun()


def render_result(config, state, ns="intake"):
    res = state.get(_k(ns, "result"))
    if not res:
        return
    if _k(ns, "flash") in state:
        st.toast(state.pop(_k(ns, "flash")), icon=":material/check_circle:")
    if res["kind"] == "SUBMITTED":
        req = common.find_request(state, res["request_id"])
        with st.container(border=True):
            st.success(f"Request submitted. Your reference is {res['request_id']}.")
            if req:
                st.markdown(ui.tracker(config, req), unsafe_allow_html=True)
                who = config["roles"].get(req.get("current_approver_role") or "", {}).get("label")
                st.write(f"It has been sent to **{who}** automatically. You will see its progress under Requests."
                         if who else "You will see its progress under Requests.")
            for note in res.get("notes", []):
                st.info(note)
            st.button("Track this request", key=_k(ns, "track"), on_click=ui.go, args=(state, "requests"))
    elif res["kind"] == "USE_EXISTING":
        st.info(f"Good catch, no new request is needed. Use the existing record {', '.join(map(str, res['sap_ids']))}.")


# ----------------------------------------------------------------------------- review before sending
def understood_name(state, config, domain, payload):
    """(typed name, the name the check will use, alias hits). Abbreviations are expanded automatically using the
    curated alias map plus initialisms generated from names already in SAP; the requester can still edit it."""
    dd = common.dedup_cfg(config, domain)
    ab, field = dd["abbreviation_resolution"], dd["compare_field"]
    typed = str(payload.get(field, ""))
    pool = dedup_pool(state, config, domain)
    dyn = em.build_dynamic_aliases([r.get(field, "") for r in pool], ab)
    variants, hits = em.resolve_name(typed, ab, dyn)
    return typed, (" ".join(variants[0]) if hits and variants else typed), hits


def name_override(state, config, domain, payload, name):
    """The name to compare only when the requester changed it. If it is still what we read automatically (or what they
    typed), the engine resolves the abbreviation itself, which keeps the alias evidence checks switched on."""
    name = (name or "").strip()
    if not name:
        return None
    _, read, _ = understood_name(state, config, domain, payload)
    typed = str(payload.get(common.dedup_cfg(config, domain)["compare_field"], "")).strip().lower()
    return None if name.lower() in (read.strip().lower(), typed) else name


def _approve_review(state, config, user, domain, payload, compare_name, related, answers, reasons=None):
    edited = name_override(state, config, domain, payload, compare_name) is not None
    common.log(state, config, user, "CHECKS_APPROVED", None, {"domain": domain, "name_edited": edited,
                                                              "related_reviewed": len(related)})
    for m in related:
        common.log(state, config, user, "RELATED_RECORD_REVIEWED", None,
                   {"answer": answers[m["sap_id"]], "matches": [m]})
    same = [m["sap_id"] for m in related if answers[m["sap_id"]].startswith("Yes")]
    state.pop(REVIEW_KEY, None)
    state.pop(REVIEW_NOTE_KEY, None)
    if same:
        _stop(state, same)
    else:
        notes = [f"Not the same as {m['existing_name']} ({m['sap_id']}): {(reasons or {}).get(m['sap_id'], '').strip()}"
                 for m in related if (reasons or {}).get(m["sap_id"], "").strip()]
        if notes:
            state[REVIEW_NOTE_KEY] = "; ".join(notes)
        start_flow(state, config, user, domain, payload,
                   compare_name=name_override(state, config, domain, payload, compare_name))


def render_review(config, state, user, domain, payload):
    """Nothing is committed until the requester has vetted what the system understood and found."""
    typed, default, hits = understood_name(state, config, domain, payload)
    st.markdown("##### Here is what we found")
    st.caption("We read abbreviations and compare your request with SAP automatically. "
               "Correct anything that looks wrong. Nothing is sent until you press the button below.")
    cmp_name = st.text_input("Name we compare with SAP (edit it if we read it wrongly)", key=CMP_KEY)
    for line in common.alias_explanation({"alias_hits": hits}):
        st.caption(f"You typed '{typed}'. We read the abbreviation as: {line}")
    res = evaluate(state, config, domain, payload, compare_name=name_override(state, config, domain, payload, cmp_name))
    related, answers, reasons = res["related"], {}, {}
    if res["matches"]:
        st.markdown(f"**{len(res['matches'])} possible duplicate{'s' if len(res['matches']) != 1 else ''}** "
                    "(you will be asked about them next)")
        for m in res["matches"]:
            common.plain_match(config, domain, m, user, technical=False)
            common.side_by_side(state, config, domain, m, payload, user)
    else:
        st.success("No likely duplicate found.", icon=":material/check_circle:")
    if related:
        st.markdown("**Other records with a similar name, checked automatically against the other details**")
        for m in related:
            with st.container(border=True):
                st.markdown(f"**{m['existing_name']}** &nbsp;<span class='lv-meta'>{m['sap_id']} &middot; "
                            f"names {m['confidence_score']:.0f}% alike</span>", unsafe_allow_html=True)
                cc = common.dedup_cfg(config, domain)["corroboration"]
                ev = em.evidence_for_role(m["corroboration"], user["role"], cc)
                st.write(m["verdict"])
                st.caption("Evidence checked: " + (", ".join(e["label"] for e in ev) or "nothing else matched") + ".")
                common.side_by_side(state, config, domain, m, payload, user)
                answers[m["sap_id"]] = st.radio(f"Is your request the same organisation as {m['existing_name']}?",
                                                ["No, a different organisation", "Yes, the same organisation"],
                                                index=None, key=f"intake_rel_{m['sap_id']}", horizontal=True) or ""
                if answers[m["sap_id"]].startswith("No"):
                    reasons[m["sap_id"]] = st.text_input(
                        "Why is it a different organisation? (optional, the approvers will see this)",
                        key=f"intake_rel_why_{m['sap_id']}")
    waiting = [k for k, v in answers.items() if not v]
    gate_now = gate_action(config, domain, res["matches"])
    blocking = gate_now is not None and gate_now not in NON_BLOCKING
    c1, c2, _ = st.columns([2, 1, 3])
    c1.button("Continue" if blocking else "Send request", key="intake_approve", type="primary", disabled=bool(waiting),
              on_click=_approve_review, args=(state, config, user, domain, payload, cmp_name, related, answers, reasons))
    c2.button("Edit details", key="intake_edit_back", on_click=lambda: state.pop(REVIEW_KEY, None))
    if waiting:
        st.caption("Answer the question above for each similar record to continue.")

def _requirement_note(config, domain, f):
    """'(required when Product Category is IVD or Consumable)' for conditional fields."""
    cond = f.get("required_if") or {}
    if not cond:
        return ""
    parts = [f"{common.field_label(config, domain, k)} is {' or '.join(map(str, v))}" for k, v in cond.items()]
    return f" (required when {'; '.join(parts)})"


def _field_widget(config, state, domain, f, col):
    key = widget_key(domain, f["field"])
    label = f["label"] + _requirement_note(config, domain, f)
    preset = key in state
    with col:
        if f.get("type") == "enum":
            labels = config.get("enum_labels", {}).get(f["field"], {})
            kw = {} if preset else {"index": None}
            st.selectbox(label, f["options"], key=key, placeholder="Choose an option",
                         format_func=lambda o, lb=labels: lb[str(o)] if str(o) in lb else str(o), **kw)
        elif f.get("type") == "float":
            kw = {} if preset else {"value": None}
            st.number_input(label, key=key, format="%.2f", **kw)
        else:
            st.text_input(label, key=key, type="password" if _write_only(config, f) else "default",
                          help="Kept private: never shown back after you submit." if _write_only(config, f) else None)


def _fields_grid(config, state, domain, fields):
    cols = st.columns(2)
    for i, f in enumerate(fields):
        _field_widget(config, state, domain, f, cols[i % 2])


def field_groups(config, domain):
    """(always shown, finance/private, optional) form fields of a request type, in the order the form draws them."""
    dom = config["domains"][domain]
    fields = [f for f in dom["fields"]
              if (f["field"] != dom["key_field"] or f.get("required")) and not f.get("form_hidden")]
    sensitive = [f for f in fields if f.get("security_class", "GENERAL") != "GENERAL"]
    plain = [f for f in fields if f not in sensitive]
    must = [f for f in plain if f.get("required") or f.get("required_if")]
    return must, sensitive, [f for f in plain if f not in must]


def _pick_type(state, domain):
    state[DOMAIN_KEY] = domain
    state.pop(FLOW_KEY, None)
    state.pop(REVIEW_KEY, None)


def _ask_panel(config, state, user, domains):
    """The 'question or follow-up' choice: this form is only for new master-data records, so anything else is sent to the
    place that answers it (status of a request, what is waiting, or the help guide)."""
    text = common.load_ui()["intake_other"]
    st.markdown(f"##### {text['heading']}")
    st.write(text["intro"])
    c1, c2, c3 = st.columns(3)
    c1.button(text["status"], key="ask_status", icon=":material/inbox:", on_click=ui.go, args=(state, "requests"), width="stretch")
    c2.button(text["waiting"], key="ask_waiting", icon=":material/home:", on_click=ui.go, args=(state, "home"), width="stretch")
    c3.button(text["record"], key="ask_record", icon=":material/add_circle:", on_click=_pick_type, args=(state, domains[0]),
              width="stretch")
    st.markdown(f"**{text['ask']}**")
    query = st.text_input(text["ask"], key=ASK_QUERY, label_visibility="collapsed", placeholder=text["placeholder"])
    if query.strip() and not help_guide.show_answers(query, config, user, lambda page: ui.go(state, page), ns="ask",
                                                      query_key=ASK_QUERY):
        st.caption(text["no_match"])
    st.caption(text["limits"])


def render(config, state, user):
    ui.page_header("New request", "Tell us what you need. We check for missing details and duplicates before it "
                                  "reaches an approver, so nothing bounces back by email.")
    ui.story_banner(state)
    domains = user.get("can_submit") or config["roles"][user["role"]]["can_submit"]   # a job title can narrow the role
    if not domains:
        st.info("Your role cannot submit master data requests. Use the Home page to see what you can do.")
        return
    if state.pop(RESET_KEY, False):
        clear_form(state, config)
    if state.get(DOMAIN_KEY) not in [*domains, ASK]:
        state[DOMAIN_KEY] = domains[0]
    copy = common.domain_text(config)
    render_result(config, state)

    bar = st.empty()                                    # filled in once we know what has been typed

    st.markdown("##### What do you need?")
    domain = state[DOMAIN_KEY]
    other = common.load_ui()["intake_other"]
    for col, d in zip(st.columns(len(domains)), domains):
        chosen = d == domain
        with col, st.container(border=True, key=f"typecard_{d}{'_on' if chosen else ''}"):
            st.markdown(f":material/{copy[d].get('icon', 'description')}: **{copy[d]['name']}**")
            st.caption(copy[d]["help"])
            st.button("Selected" if chosen else "Choose", key=f"type_{d}", type="primary" if chosen else "secondary",
                      icon=":material/check:" if chosen else None, on_click=_pick_type, args=(state, d), width="stretch")
    # not a record type, so a quiet link under the cards and not a card of its own
    st.button(other["card_help"], key=f"type_{ASK}", type="primary" if domain == ASK else "tertiary",
              icon=f":material/{other['icon']}:", on_click=_pick_type, args=(state, ASK))
    if domain == ASK:
        return _ask_panel(config, state, user, domains)
    st.caption("Codes are assigned automatically when the record is created in SAP.")
    dom = config["domains"][domain]
    must, sensitive, optional = field_groups(config, domain)

    st.markdown("##### Details")
    _fields_grid(config, state, domain, must)
    if sensitive:
        filled = any(state.get(widget_key(domain, f["field"])) not in (None, "") for f in sensitive)
        note = ("These are private. They are needed before the request can be sent."
                if any(f.get("required") for f in sensitive) else "These are private. They are optional here.")
        with st.container(key="finance_box"):
            st.markdown(f"<span class='lv-info' tabindex='0' aria-label='{note}'>i<span class='lv-info-tip'>{note}</span></span>",
                        unsafe_allow_html=True)
            with st.expander("Finance details: credit, terms and banking", expanded=filled or bool(state.get(ATTEMPT_KEY))):
                _fields_grid(config, state, domain, sensitive)
    if optional:
        has_value = any(state.get(widget_key(domain, f["field"])) not in (None, "") for f in optional)
        with st.expander("More details (optional, helps us spot duplicates)", expanded=has_value):
            _fields_grid(config, state, domain, optional)
    payload = collect_payload(state, config, domain)
    res_now = state.get(RESULT_KEY)
    done = bool(res_now) and res_now.get("kind") in ("SUBMITTED", "USE_EXISTING") and not payload
    with bar:
        ui.stepbar(["Details", "Check", "Send"], 3 if done else 1 if (state.get(FLOW_KEY) or state.get(REVIEW_KEY)) else 0)

    flow = state.get(FLOW_KEY)
    if flow and (flow["domain"] != domain or flow["form_payload"] != payload):
        state.pop(FLOW_KEY, None)                       # the form changed: the duplicate check must re-run
        flow = None

    ok, errors = em.validate_against_config(domain, payload, config)
    if not state.get(ATTEMPT_KEY):                       # live: touched fields and conditionals they trigger
        live = set(payload) | {f["field"] for f in dom["fields"] if set(f.get("required_if", {})) & set(payload)}
        errors = [e for e in errors if any(f"({k})" in e for k in live)]
    for e in errors:
        st.error(ui.plain_error(e), icon=":material/error:")

    st.markdown("##### Check and send")
    if payload and ok and not flow:
        route = em.route_request(domain, payload, config)
        st.success(f"Looks complete. After you send it, it goes to **{config['roles'][route['approver_role']]['label']}**.",
                   icon=":material/check_circle:")
    elif not payload:
        st.caption("Nothing to check yet. Fill in the details above.")
    review = state.get(REVIEW_KEY)
    if review and (review["domain"] != domain or review["payload"] != payload):
        state.pop(REVIEW_KEY, None)                     # the form changed: the review must be redone
        review = None
    if flow:
        render_gate(config, state, user)
    elif review and ok:
        render_review(config, state, user, domain, payload)
    elif st.button("Check my request", key="intake_submit", type="primary"):
        state[ATTEMPT_KEY] = True
        if ok:
            state[REVIEW_KEY] = {"domain": domain, "payload": dict(payload)}
            typed, default, _hits = understood_name(state, config, domain, payload)
            state[CMP_KEY] = default
        st.rerun()

    with st.expander("See how this maps to SAP Business One (technical)"):
        sap = {**({"CardType": dom["card_type"]} if "card_type" in dom else {}), **payload}
        view, report = common.secure_view(state, config, user, sap, domain, status=config["state_machine"]["initial"])
        st.caption(f"This is what would be written to the SAP table {dom['sap_table']}, as your role may see it.")
        st.json(view)
        hidden = [k for k, p in report.items() if p == "HIDDEN"]
        if hidden:
            st.caption("Hidden for your role: " + ", ".join(hidden))
