"""Platform settings: what the IT / System Administrator sees and changes. Approval routes, duplicate detection and the
rules behind them are read back in plain words. Mandatory details by request type, reminder times, and who can open
what are editable: a change is checked, applied to this session's copy of config_rules.json (common.live_config),
written to the audit trail, and felt by every page and engine on the next run. The settings file on disk is not
rewritten (download the current rules to keep them)."""
import copy
import json

import pandas as pd
import streamlit as st

import engine_settings as es_set
from tabs import common, help_editor, ui, workflow_designer

OPS = {"eq": "is", "gt": "is more than", "in": "is one of", "lt": "is less than"}
LEVELS = {"NONE": "None", "AGGREGATE": "Totals only", "ACCOUNT_LEVEL": "Account level"}
PAGE_COLUMNS = {"STAGING": "SAP records", "SLA_BOARD": "Waiting times", "AUDIT": "Audit trail", "ADMIN": "Platform settings"}


def _cond_text(config, cond):
    def leaf(c):
        field = c["field"]
        if field == "_domain":
            return f"the request is a {common.domain_text(config).get(c['value'], {}).get('name', c['value']).lower()}"
        if field == "_dup_review":
            return "a likely duplicate needs MDM review"
        label = next((f["label"] for d in config["domains"].values() for f in d["fields"] if f["field"] == field), field)
        value = c["value"]
        value = " or ".join(map(str, value)) if isinstance(value, list) else value
        return f"{label} {OPS.get(c['op'], c['op'])} {value}"
    all_ = [leaf(c) for c in cond.get("all", [])]
    any_ = [leaf(c) for c in cond.get("any", [])]
    text = " and ".join(all_)
    if any_:
        text += (" and " if text else "") + "(" + " or ".join(any_) + ")"
    return text or "everything else"


def _role(config, state_name):
    role = config["state_machine"]["approver_role_by_state"].get(state_name)
    return config["roles"][role]["label"] if role else state_name


def _commit(state, user, area, change):
    """Run change(trial_config) on a copy; if it is accepted, make the copy the live settings, record it and refresh."""
    trial = copy.deepcopy(common.load_config())
    try:
        changes = change(trial)
    except ValueError as e:
        st.error(str(e), icon=":material/error:")
        return
    if not changes:
        st.info("Nothing to change: the values are the same as now.")
        return
    state.setdefault("config_history", []).append(copy.deepcopy(common.load_config()))    # so the last change can be undone
    state[common.LIVE_CONFIG_KEY] = trial
    common.log(state, trial, user, "SETTING_CHANGED", None, {"area": area, "summary": "; ".join(changes)})
    state["admin_gen"] = state.get("admin_gen", 0) + 1
    state["admin_flash"] = f"{area}: " + "; ".join(changes)
    st.rerun()


def _undo(state, user):
    """Go back to the settings as they were before the last applied change (the audit trail keeps both entries)."""
    previous = state["config_history"][-1]
    known = set(previous["state_machine"]["pending_states"])
    stuck = [r for r in state.requests_db if r["status"] in common.load_config()["state_machine"]["pending_states"]
             and not ({r["status"], *r.get("remaining_chain", [])} <= known)]
    orphans = [r for r in state.requests_db if r["domain"] not in previous["domains"]]
    if stuck or orphans:                          # a step or request type cannot vanish under a request that still uses it
        state["admin_flash"] = (f"Not undone: {len(stuck) + len(orphans)} request(s) use an approval step or request type that "
                                "the earlier settings do not have. Finish or reject them first.")
        return
    state["config_history"].pop()
    state[common.LIVE_CONFIG_KEY] = previous
    common.log(state, previous, user, "SETTING_CHANGED", None, {"area": "Undo", "summary": "The last change was undone"})
    state["admin_gen"] = state.get("admin_gen", 0) + 1
    state.pop("wf_draft", None)


# ----------------------------------------------------------------------------- sections
def _routes(config):
    ui.section("Who approves what", "Rules are checked top to bottom. The first one that fits sends the request on its way.")
    rows = []
    for r in sorted(config["routing_matrix"], key=lambda r: r["priority"]):
        chain = [_role(config, r["route_to"])] + [_role(config, s) for s in r.get("then", [])]
        rows.append({"Rule": r["id"], "When": (lambda s: s[:1].upper() + s[1:])(_cond_text(config, r["condition"])),
                     "Approval order": " > ".join(chain) + " > then MDM Analyst creates it in SAP"
                                       if r["route_to"] != "PENDING_MDM" else "MDM Analyst reviews and creates it in SAP"})
    ui.table(pd.DataFrame(rows), hide_index=True)
    st.caption("To add an approval step for a new business unit, add the next approver to a rule's chain in the settings file.")


def _approvers(config, state, user):
    sm = config["state_machine"]
    ui.section("Approval steps", "Who can be asked to approve. Add one for a new business unit or team, then use it in a rule below.",
               tip="Each step is held by a role and a person, and has a delegate who receives its work after the last reminder. "
                   "A new step gets the same reminders, queue and email digest as the others. It sees only the details its "
                   "approvals need: private finance and banking values stay hidden until you change that in the settings file.")
    holders = {}
    for u in config["users"]:
        label = config["roles"][u["role"]]["label"]
        if u["display"] != label and not u["display"].startswith("Approver (") and u["display"] not in holders.get(u["role"], []):
            holders.setdefault(u["role"], []).append(u["display"])        # people by name; the role's own demo logins add nothing
    rows = [{"Approval step": config["roles"][role]["label"], "Held by": ", ".join(holders.get(role, [])) or "Anyone in this role",
             "Delegate": config.get("delegates", {}).get(role, "")}
            for state_name, role in sm["approver_role_by_state"].items()]
    ui.table(pd.DataFrame(rows), hide_index=True)
    with st.expander("Add an approval step for a new business unit or team"):
        gen = state.get("admin_gen", 0)
        c1, c2 = st.columns(2)
        label = c1.text_input("Name of the approval step", key=f"adm_ap_label_{gen}", placeholder="Regional Compliance Officer")
        person = c2.text_input("Person who holds it", key=f"adm_ap_person_{gen}", placeholder="Aiko Tan")
        d = es_set.new_approver_defaults(config, label or "approver", person or "approver")
        c3, c4 = st.columns(2)
        email = c3.text_input("Their email", key=f"adm_ap_email_{gen}", placeholder=d["email"])
        delegate = c4.text_input("Delegate's email (after 8 days)", key=f"adm_ap_deleg_{gen}", placeholder=d["delegate"])
        st.caption("Leave the emails blank to use the suggestions. The step appears in the rule designer straight away; it is "
                   "not used by any route until you add it to one.")
        if st.button("Add approval step", key="adm_apply_approver", type="primary"):
            _commit(state, user, "Approval steps", lambda c: es_set.add_approver_role(c, label, person, email or None,
                                                                                         delegate or None))


def _request_types(config, state, user):
    names = common.domain_text(config)
    ui.section("Request types", "What people can ask for. Add a new kind of record, then shape its form.",
               tip="Each request type has its own form, its own duplicate check by name and its own place in SAP. A new one starts "
                   "with a code, a name and a business entity, or a copy of an existing type's details. It is routed to the MDM "
                   "Analyst until you add rules for it in the rule designer, and anyone who may raise requests by role can raise "
                   "it (change individuals under People and access). Duplicates are caught by name similarity only; the extra "
                   "evidence checks used for customers and vendors are not set up for new types.")
    rows = [{"Request type": names[d]["name"], "Made": "On the dashboard" if dom.get("custom") else "Shipped",
             "SAP table": dom["sap_table"], "Details": sum(not f.get("form_hidden") for f in dom["fields"])}
            for d, dom in config["domains"].items()]
    ui.table(pd.DataFrame(rows), hide_index=True)
    gen = state.get("admin_gen", 0)
    with st.expander("Add a request type"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", key=f"adm_rt_name_{gen}", placeholder="Project code")
        source = c2.selectbox("Start from", [None, *config["domains"]], key=f"adm_rt_src_{gen}",
                              format_func=lambda d: "A blank form (code, name, business entity)" if d is None
                              else f"A copy of {names[d]['name']}")
        about = st.text_input("One line that explains it to requestors", key=f"adm_rt_help_{gen}",
                              placeholder="A code for a customer project.")
        if st.button("Add request type", key="adm_apply_type", type="primary"):
            _commit(state, user, "Request types", lambda c: es_set.add_request_type(c, name, about, source))
    custom = es_set.custom_domains(config)
    if not custom:
        st.caption("No request types made on the dashboard yet. The shipped ones are changed under Mandatory details.")
        return
    ui.section("Details of a new request type", "Rename, add or remove details, make them mandatory and mark private ones.")
    pick = st.segmented_control("Request type", custom, default=custom[0], key="adm_rt_pick", label_visibility="collapsed",
                                format_func=lambda d: names[d]["name"]) or custom[0]
    frame = pd.DataFrame(es_set.field_rows(config, pick)).rename(columns={
        "label": "Detail", "kind": "Type", "choices": "Choices (separate with ;)", "required": "Mandatory",
        "sensitivity": "Who sees it"})
    edited = ui.editor(frame, hide_index=True, num_rows="dynamic", key=f"adm_rt_fields_{pick}_{gen}",
                       column_order=["Detail", "Type", "Choices (separate with ;)", "Mandatory", "Who sees it"],
                       column_config={"id": None,
                                      "Type": st.column_config.SelectboxColumn("Type", options=es_set.KINDS, default="Text", required=True),
                                      "Who sees it": st.column_config.SelectboxColumn(
                                          "Who sees it", options=list(es_set.SENSITIVITY), default="General", required=True,
                                          help="Financial terms and Bank details are hidden from roles that do not need them."),
                                      "Mandatory": st.column_config.CheckboxColumn("Mandatory", default=False)})
    dom = config["domains"][pick]
    st.caption(f"The {next(f['label'] for f in dom['fields'] if f['field'] == dom['record_name_field'])} detail is always mandatory "
               "and the code is assigned when the record is created in SAP.")
    if st.button("Apply details", key="adm_apply_fields", type="primary"):
        wanted = [{"id": r["id"] if isinstance(r.get("id"), str) else None, "label": r["Detail"], "kind": r["Type"],
                   "choices": r["Choices (separate with ;)"], "required": bool(r["Mandatory"]), "sensitivity": r["Who sees it"]}
                  for r in edited.to_dict("records")]
        _commit(state, user, f"Details, {names[pick]['name']}", lambda c: es_set.set_type_fields(c, pick, wanted))


def _mandatory(config, state, user):
    names = common.domain_text(config)
    ui.section("Mandatory details by request type", "Tick what a requester must fill in. The form and the checks follow at once.")
    domain = st.segmented_control("Request type", list(config["domains"]), default=list(config["domains"])[0], key="adm_dom",
                                  format_func=lambda d: names[d]["name"], label_visibility="collapsed") or list(config["domains"])[0]
    fields = es_set.editable_fields(config, domain)
    frame = pd.DataFrame({"Detail": [f["label"] for f in fields], "Mandatory": [bool(f.get("required")) for f in fields]})
    edited = ui.editor(frame, hide_index=True, disabled=["Detail"], key=f"adm_req_{domain}_{state.get('admin_gen', 0)}",
                       column_config={"Mandatory": st.column_config.CheckboxColumn("Mandatory")})
    dom = config["domains"][domain]
    always = [f["label"] for f in dom["fields"] if f["field"] in es_set.protected_fields(config, domain)
              and f["field"] != dom["key_field"] and not f.get("form_hidden")]
    cond = [f["label"] for f in dom["fields"] if f.get("required_if")]
    st.caption("Always mandatory: " + (", ".join(always) or "none") + "."
               + (" Mandatory only in some cases (set in the settings file): " + ", ".join(cond) + "." if cond else ""))
    if st.button("Apply mandatory details", key="adm_apply_req", type="primary"):
        wanted = {f["field"]: bool(v) for f, v in zip(fields, edited["Mandatory"])}
        _commit(state, user, f"Mandatory details, {names[domain]['name']}", lambda c: es_set.set_required(c, domain, wanted))


def _reminders(config, state, user):
    actions = common.load_ui()["sla_actions"]
    ui.section("Reminders when an approver is slow", "The day each reminder goes out. Reminders count calendar days.")
    rules = sorted(config["sla"]["rules"], key=lambda r: r["after_hours"])
    days = []
    for col, rule in zip(st.columns(len(rules)), rules):
        with col, st.container(border=True):
            st.caption(actions.get(rule["action"], rule["action"]).capitalize())
            days.append(st.number_input("After (days)", min_value=1, max_value=60, step=1, value=rule["after_hours"] // 24,
                                        key=f"adm_days_{rule['id']}_{state.get('admin_gen', 0)}"))
            st.caption("Told: " + ", ".join(rule["notify"]).replace("_", " "))
    if st.button("Apply reminder times", key="adm_apply_days", type="primary"):
        _commit(state, user, "Reminders", lambda c: es_set.set_reminder_days(c, days))


def _people(config, state, user):
    names = common.domain_text(config)
    ui.section("People and access", "Give each person a role and the request types they may raise.")
    labels = {k: r["label"] for k, r in config["roles"].items()}
    by_label = {v: k for k, v in labels.items()}
    domains = list(config["domains"])
    rows = []
    for u in config["users"]:
        can = es_set.effective_can_submit(config, u)
        rows.append({"Person": f"{u['display']}" + (f", {u['title']}" if u.get("title") and u["title"] != u["display"] else ""),
                     "Role": labels[u["role"]], **{names[d]["name"]: d in can for d in domains}})
    frame = pd.DataFrame(rows)
    gen = state.get("admin_gen", 0)
    edited = ui.editor(frame, hide_index=True, disabled=["Person"], key=f"adm_people_{gen}",
                       column_config={"Role": st.column_config.SelectboxColumn("Role", options=list(labels.values()), required=True)})
    if st.button("Apply people and access", key="adm_apply_people", type="primary"):
        def change(c):
            out = []
            for u, (_, was), (_, now) in zip(c["users"], frame.iterrows(), edited.iterrows()):
                role = by_label[now["Role"]]
                boxes = [d for d in domains if now[names[d]["name"]]]
                if now["Role"] != was["Role"] and all(now[names[d]["name"]] == was[names[d]["name"]] for d in domains):
                    boxes = list(c["roles"][role]["can_submit"])      # new role, request types untouched: follow the role
                out += es_set.set_person(c, u["email"], role, boxes)
            return out
        _commit(state, user, "People and access", change)

    st.markdown("**What each role can open**")
    rframe = pd.DataFrame([{"Role": r["label"], **{PAGE_COLUMNS[t]: t in r["tabs"] for t in es_set.PAGE_TABS},
                            "Sales insights": LEVELS[r["analytics"]],
                            **{names[d]["name"]: d in r["can_submit"] for d in domains}} for r in config["roles"].values()])
    redit = ui.editor(rframe, hide_index=True, disabled=["Role"], key=f"adm_roles_{gen}",
                      column_config={"Sales insights": st.column_config.SelectboxColumn("Sales insights", options=list(LEVELS.values()),
                                                                                       required=True)})
    st.caption("Requests is open to every role. New request opens for any role that may raise a request type. "
               "Which requests a role sees and what it can decide follow its approval step.")
    if st.button("Apply role access", key="adm_apply_roles", type="primary"):
        back = {v: k for k, v in LEVELS.items()}

        def change(c):
            out = []
            for key, (_, now) in zip(c["roles"], redit.iterrows()):
                out += es_set.set_role_access(
                    c, key, [t for t in es_set.PAGE_TABS if now[PAGE_COLUMNS[t]]], back[now["Sales insights"]],
                    [d for d in domains if now[names[d]["name"]]])
            return out
        _commit(state, user, "Role access", change)
    _private_values(config, state, user, labels, gen)


PRIVATE_NAMES = {"FINANCIAL_TERMS": "Credit terms", "BANK_DETAILS": "Bank details"}
LEVEL_WORDS = {"HIDDEN": "Hidden", "MASKED": "Masked", "FULL": "Full value"}


def _private_values(config, state, user, labels, gen):
    """Redaction is the administrator's call: pick a role and a kind of private value, then choose what that role may see."""
    ui.section("Who sees private values", "Credit terms and bank details are redacted for some roles. You decide who sees what.",
               tip="Hidden: the field is not shown. Masked: only part of the value is shown (the last four digits of an account, "
                   "or asterisks for credit terms). Full value: the role sees everything in the app. Bank details are never "
                   "put in an email, whatever you choose here. Every change is written to the audit trail.")
    classes = es_set.private_classes(config)
    c1, c2 = st.columns(2)
    role = c1.selectbox("Role", list(labels), key="adm_sec_role", format_func=labels.get)
    cls = c2.selectbox("Kind of private value", classes, key="adm_sec_class", format_func=lambda c: PRIVATE_NAMES.get(c, c))
    today = es_set.current_policy(config, cls, role)
    level = st.segmented_control("What this role may see", list(LEVEL_WORDS), default=today, format_func=LEVEL_WORDS.get,
                                 key=f"adm_sec_level_{role}_{cls}_{gen}") or today
    st.caption(f"Today {labels[role]} sees: {LEVEL_WORDS[today].lower()}.")
    if level == "FULL" and cls == "BANK_DETAILS":
        st.warning("Full account numbers will be shown to this role in the app. They are still never sent by email.",
                   icon=":material/warning:")
    if st.button("Apply", key="adm_apply_security", type="primary"):
        _commit(state, user, "Private values", lambda c: es_set.set_field_policy(c, cls, role, level))


def _duplicates(config, state, user):
    tables = common.table_names(config)
    ui.section("Duplicate detection", "How alike two names must be, and how much else must agree, before a request is flagged.",
               tip="Name match threshold: how alike two names must score to be called a possible duplicate. Lower catches more "
                   "but raises more false alarms. Near-miss check: names scoring between this and the threshold are checked "
                   "automatically against the other details. Evidence groups needed: how many independent kinds of evidence "
                   "(email domain, location, contact person and so on) must agree before a name match is trusted; at least "
                   "one must be strong.")
    keys = list(config["deduplication_rules"])
    table = st.segmented_control("Records", keys, default=keys[0], key="adm_dup_table", label_visibility="collapsed",
                                 format_func=lambda t: tables.get(t, t)) or keys[0]
    now = es_set.duplicate_rules(config, table)
    gen = state.get("admin_gen", 0)
    c1, c2, c3 = st.columns(3)
    with c1, st.container(border=True):
        threshold = st.slider("Name match threshold", 50, 100, round(now["threshold"] * 100), format="%d%%",
                              key=f"adm_thr_{table}_{gen}")
    near = groups = None
    if now["near_miss"] is not None:
        with c2, st.container(border=True):
            near = st.slider("Near-miss check from", 30, 99, round(now["near_miss"] * 100), format="%d%%",
                             key=f"adm_near_{table}_{gen}")                  # fixed range: a moving range resets the slider
            if near >= threshold:
                st.caption("The near-miss check must start below the name match threshold.")
    if now["groups"] is not None:
        with c3, st.container(border=True):
            groups = st.number_input("Evidence groups needed", min_value=1, max_value=now["max_groups"], step=1,
                                     value=now["groups"], key=f"adm_groups_{table}_{gen}")
    if groups is not None and groups < 2:
        st.warning("With one group, a name match plus a single agreeing detail is enough to call it a duplicate.",
                   icon=":material/warning:")
    if st.button("Apply duplicate rules", key="adm_apply_dup", type="primary"):
        _commit(state, user, f"Duplicate detection, {tables.get(table, table)}",
                lambda c: es_set.set_duplicate_rules(c, table, threshold / 100,
                                                     None if near is None else near / 100, groups))


def _history_and_files(config, state, user):
    ui.section("Changes made in this session")
    log = [e for e in state.audit_log if e["event"] == "SETTING_CHANGED"]
    if log:
        ui.table(pd.DataFrame([{"When": ui.when(e["ts"]), "Who": e["actor_email"], "Area": e["detail"].get("area", ""),
                                "What changed": e["detail"].get("summary", "")} for e in reversed(log)]), hide_index=True)
    else:
        st.caption("No changes yet. The platform is running on the settings file as shipped.")
    c0, c1, c2 = st.columns(3)
    if state.get("config_history"):
        c0.button("Undo the last change", key="adm_undo", icon=":material/undo:", on_click=_undo, args=(state, user))
    c1.download_button("Download the current rules", json.dumps(config, indent=2, ensure_ascii=False).encode("utf-8"),
                       file_name="config_rules.json", mime="application/json", key="admin_dl", icon=":material/download:")
    if state.get(common.LIVE_CONFIG_KEY) is not None:
        c2.button("Reset to the settings file", key="adm_reset", icon=":material/restart_alt:",
                  on_click=lambda: (state.pop(common.LIVE_CONFIG_KEY, None), state.__setitem__("admin_gen", state.get("admin_gen", 0) + 1)))


def render(config, state, user):
    ui.page_header("Platform settings", "Change how the platform works: approvals, reminders, who can do what and the Help guide.")
    ui.story_banner(state)
    if "admin_flash" in state:
        st.toast(state.pop("admin_flash"), icon=":material/check_circle:")
    st.info("Changes apply straight away and last for this session. Each one is written to the audit trail.", icon=":material/sync:")
    approvals, types, timing, people, helping, changes = st.tabs(
        ["Approvals", "Request types", "Reminders and duplicates", "People and access", "Help guide", "Changes"])
    with approvals:
        _approvers(config, state, user)
        workflow_designer.render(config, state, user, _commit)
    with types:
        _request_types(config, state, user)
        _mandatory(config, state, user)
    with timing:
        _reminders(config, state, user)
        _duplicates(config, state, user)
    with people:
        _people(config, state, user)
    with helping:
        help_editor.render(config, state, user, _commit)
    with changes:
        _history_and_files(config, state, user)
