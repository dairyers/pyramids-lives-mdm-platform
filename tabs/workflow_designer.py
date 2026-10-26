"""Workflow designer (Platform settings). Each rule is a card in plain words. A route says who approves a request and in
what order (the first route that fits wins). An 'also require' rule adds steps to the end of whatever route a request
gets (every one that fits applies). Changing a rule is three questions: who is it for, anything extra, who approves.
Edits build a draft; nothing changes for anyone until the draft is applied, through the same checked and audited path as
every other setting. The logic lives in engine_workflow.py; this is the screen."""
import copy

import pandas as pd
import streamlit as st

import engine_mdm as em
import engine_workflow as ew
from tabs import common, ui

ROUTE, ADD = "route", "add"


def _cap(text):
    return text[:1].upper() + text[1:]


def _parts(state):
    """(route rules, also-require rules, the catch-all) from the draft."""
    d = state["wf_draft"]
    return ([r for r in d if not ew.is_add(r) and not ew.is_catch_all(r)], [r for r in d if ew.is_add(r)],
            next(r for r in d if ew.is_catch_all(r)))


def _put(state, routes, adds, catch):
    state["wf_draft"] = routes + adds + [catch]


# ----------------------------------------------------------------------------- callbacks (they run before the next screen)
def _move(state, rid, delta):
    routes, adds, catch = _parts(state)
    i = next(i for i, r in enumerate(routes) if r["id"] == rid)
    j = i + delta
    if 0 <= j < len(routes):
        routes[i], routes[j] = routes[j], routes[i]
    _put(state, routes, adds, catch)


def _toggle(state, rid):
    for r in state["wf_draft"]:
        if r["id"] == rid and not ew.is_catch_all(r):
            r["enabled"] = not r.get("enabled", True)


def _discard(state):
    state.pop("wf_draft", None)
    state.pop("wf_form", None)
    state["admin_gen"] = state.get("admin_gen", 0) + 1


def _open(state, config, names, rid, kind):
    """Open the form for an existing rule (rid) or a new one of this kind."""
    state["wf_open_n"] = state.get("wf_open_n", 0) + 1
    form = {"n": state["wf_open_n"], "id": rid, "kind": kind, "chain": [], "domain": None, "extras": [], "any": False,
            "complex": False, "catch": False, "condition": {"all": []}}
    if rid:
        rule = next(r for r in state["wf_draft"] if r["id"] == rid)
        simple = ew.simple_form(config, rule)
        form.update(kind=ADD if ew.is_add(rule) else ROUTE, chain=ew.chain_of(rule), catch=ew.is_catch_all(rule),
                    condition=copy.deepcopy(rule["condition"]), complex=simple is None and not ew.is_catch_all(rule))
        if simple:
            form.update(domain=simple["domain"], extras=simple["extras"], any=simple["any"])
    state["wf_form"] = form


def _close(state):
    state.pop("wf_form", None)


def _default_value(config, field):
    if field == ew.DUP_FIELD:
        return True
    f = ew._field_def(config, field)
    if f and f.get("type") == "enum":
        return f["options"][0]
    return 0 if f and f.get("type") == "float" else ""


def _add_extra(state, config, names):
    options = [f for f in ew.detail_options(config, names).values() if f != ew.DOMAIN_FIELD]
    floats = [f for f in options if (ew._field_def(config, f) or {}).get("type") == "float"]
    field = floats[0] if floats else options[0]
    state["wf_form"]["extras"].append({"field": field, "op": "gt" if floats else "eq", "value": _default_value(config, field)})


def _drop_extra(state, i):
    state["wf_form"]["extras"].pop(i)


def _chain_edit(state, op, i=None, step=None):
    chain = state["wf_form"]["chain"]
    if op == "left" and i > 0:
        chain[i - 1], chain[i] = chain[i], chain[i - 1]
    elif op == "right" and i < len(chain) - 1:
        chain[i + 1], chain[i] = chain[i], chain[i + 1]
    elif op == "drop":
        chain.pop(i)
    elif op == "add" and step and step not in chain:
        chain.append(step)


def _save_form(state, config, names):
    """Turn the form into a rule in the draft. A problem is shown above the buttons on the next screen."""
    form, draft = state["wf_form"], state["wf_draft"]
    extras = form["extras"]
    for c in extras:
        if c["value"] in ("", None, []):
            state["wf_error"] = "Every extra condition needs a value."
            return
    if form["complex"] or form["catch"]:
        cond = copy.deepcopy(form["condition"]) if form["complex"] else {"all": []}
    else:
        cond = ew.build_condition(form["domain"], extras, form["any"])
        if form["kind"] == ROUTE and not cond["all"] and not cond.get("any"):
            state["wf_error"] = "Say who this route is for, or add a condition. A route with neither would catch every request."
            return
    old = next((r for r in draft if r["id"] == form["id"]), None)
    try:
        new = ew.make_rule(config, form["id"] or ew.new_rule_id(draft), form["chain"], cond,
                           enabled=old.get("enabled", True) is not False if old else True, kind=form["kind"])
    except ValueError as e:
        state["wf_error"] = str(e)
        return
    if old:
        draft[draft.index(old)] = new
    else:
        routes, adds, catch = _parts(state)
        (routes if form["kind"] == ROUTE else adds).append(new)
        _put(state, routes, adds, catch)
    state.pop("wf_form", None)


def _delete_rule(state):
    rid = state["wf_form"]["id"]
    state["wf_draft"] = [r for r in state["wf_draft"] if r["id"] != rid or ew.is_catch_all(r)]
    state.pop("wf_form", None)


# ----------------------------------------------------------------------------- the rule cards
def _card(config, state, names, rule, movable):
    off = rule.get("enabled", True) is False
    with st.container(border=True):
        left, right = st.columns([7, 3], vertical_alignment="center")
        when = _cap(ew.condition_text(config, rule["condition"], names))
        left.markdown(f"**{rule['id']}**{' (switched off)' if off else ''} &nbsp; **When** {when[:1].lower() + when[1:]}"
                      f"  \n**{'Also add' if ew.is_add(rule) else 'Then'}** "
                      + ew.path_text(config, rule).replace("Also adds: ", ""))
        a, b, c, d = right.columns(4)
        a.button("", key=f"wf_edit_{rule['id']}", icon=":material/edit:", help="Change this rule", on_click=_open,
                 args=(state, config, names, rule["id"], None))
        if movable:
            b.button("", key=f"wf_up_{rule['id']}", icon=":material/arrow_upward:", help="Check this rule earlier",
                     on_click=_move, args=(state, rule["id"], -1))
            c.button("", key=f"wf_down_{rule['id']}", icon=":material/arrow_downward:", help="Check this rule later",
                     on_click=_move, args=(state, rule["id"], 1))
        if not ew.is_catch_all(rule):
            d.button("", key=f"wf_tog_{rule['id']}", icon=":material/toggle_off:" if off else ":material/toggle_on:",
                     help="Switch this rule on" if off else "Switch this rule off", on_click=_toggle, args=(state, rule["id"]))


def _cards(config, state, names):
    routes, adds, catch = _parts(state)
    st.markdown("##### Routes <span class='lv-meta'>The first one that fits decides who approves.</span>", unsafe_allow_html=True)
    for r in routes:
        _card(config, state, names, r, True)
    _card(config, state, names, catch, False)
    st.markdown("##### Also require <span class='lv-meta'>Every one that fits adds its steps to the end of the route.</span>",
                unsafe_allow_html=True)
    if not adds:
        st.caption("None yet. Use this when a condition should always bring in one more approver, whatever the route.")
    for r in adds:
        _card(config, state, names, r, False)
    a, b, _ = st.columns([2, 3, 3])
    a.button("Add a route", key="wf_new_route", icon=":material/add:", on_click=_open, args=(state, config, names, None, ROUTE))
    b.button("Add an 'also require' rule", key="wf_new_add", icon=":material/add:", on_click=_open,
             args=(state, config, names, None, ADD))


# ----------------------------------------------------------------------------- the form: three questions
def _value_widget(col, config, field, op, value, key):
    if field == ew.DUP_FIELD:
        return col.selectbox("Value", ["Yes", "No"], index=0 if value else 1, key=key, label_visibility="collapsed") == "Yes"
    f = ew._field_def(config, field) or {}
    if f.get("type") == "enum":
        labels = config.get("enum_labels", {}).get(field, {})
        show = lambda o: labels.get(str(o), str(o))                                          # noqa: E731
        if op in ew.LIST_OPS:
            return col.multiselect("Value", f["options"], default=[v for v in (value or []) if v in f["options"]], key=key,
                                   format_func=show, label_visibility="collapsed")
        return col.selectbox("Value", f["options"], index=f["options"].index(value) if value in f["options"] else 0, key=key,
                             format_func=show, label_visibility="collapsed")
    if f.get("type") == "float":
        v = col.number_input("Value", value=float(value or 0), step=1.0, key=key, label_visibility="collapsed")
        return int(v) if float(v).is_integer() else v
    return col.text_input("Value", value=str(value or ""), key=key, label_visibility="collapsed")


def _extra_row(config, state, names, i, c, n):
    options = {k: v for k, v in ew.detail_options(config, names).items() if v != ew.DOMAIN_FIELD}
    by_field = {v: k for k, v in options.items()}
    labels = list(options)
    cols = st.columns([4, 3, 4, 1], vertical_alignment="center")
    label = cols[0].selectbox("Detail", labels, index=labels.index(by_field.get(c["field"], labels[0])), key=f"wf_xf_{n}_{i}",
                              label_visibility="collapsed")
    field = options[label]
    if field != c["field"]:
        c.update(field=field, op="eq", value=_default_value(config, field))
    words = ew.ops_for(config, field)
    word = cols[1].selectbox("Is", words, index=words.index(ew.OP_WORDS[c["op"]]) if ew.OP_WORDS.get(c["op"]) in words else 0,
                             key=f"wf_xo_{n}_{i}_{field}", label_visibility="collapsed")
    op = ew.WORD_OPS[word]
    if op != c["op"]:
        listy = op in ew.LIST_OPS
        c["op"] = op
        c["value"] = (c["value"] if isinstance(c["value"], list) else [c["value"]]) if listy else \
            (c["value"][0] if isinstance(c["value"], list) and c["value"] else _default_value(config, field))
    c["value"] = _value_widget(cols[2], config, field, op, c["value"], f"wf_xv_{n}_{i}_{field}_{op}")
    cols[3].button("", key=f"wf_xd_{n}_{i}", icon=":material/close:", help="Remove this condition", on_click=_drop_extra, args=(state, i))


def _form(config, state, names):
    form, steps = state["wf_form"], ew.steps(config)
    n = form["n"]
    title = ("New route" if form["kind"] == ROUTE else "New 'also require' rule") if not form["id"] else f"Change {form['id']}"
    with st.container(border=True, key="wf_editor"):
        st.markdown(f"#### {title}")
        if form["catch"]:
            st.caption("This is the 'everything else' route. It has no conditions: it catches whatever the routes above did not.")
        elif form["complex"]:
            st.info("This rule combines conditions in a way the simple form cannot show. You can change who approves here; "
                    "to change when it applies, edit the settings file.", icon=":material/info:")
        else:
            st.markdown("**1. Who is it for?**")
            domains = [None] + list(config["domains"])
            form["domain"] = st.selectbox("Who is it for?", domains, index=domains.index(form["domain"]), key=f"wf_q1_{n}",
                                          format_func=lambda d: "Any request" if d is None else names[d], label_visibility="collapsed")
            st.markdown("**2. Anything extra?**")
            if not form["extras"]:
                st.caption("No extra condition.")
            for i, c in enumerate(form["extras"]):
                _extra_row(config, state, names, i, c, n)
            st.button("Add a condition", key=f"wf_addx_{n}", icon=":material/add:", on_click=_add_extra, args=(state, config, names))
            if len(form["extras"]) > 1:
                form["any"] = st.toggle("Only one of these has to be true", value=form["any"], key=f"wf_any_{n}")
        st.markdown(f"**{'2' if form['catch'] else '3'}. Who approves?**" if form["kind"] == ROUTE else
                    "**3. Which approvers does it add?**")
        chain = form["chain"]
        for i, s in enumerate(chain):
            nm, l, r_, x = st.columns([6, 1, 1, 1], vertical_alignment="center")
            nm.markdown(f"**{i + 1}.** {steps.get(s, s)}")
            l.button("", key=f"wf_l_{n}_{i}", icon=":material/arrow_back:", on_click=_chain_edit, args=(state, "left", i),
                     disabled=i == 0, help="Earlier")
            r_.button("", key=f"wf_r_{n}_{i}", icon=":material/arrow_forward:", on_click=_chain_edit, args=(state, "right", i),
                      disabled=i == len(chain) - 1, help="Later")
            x.button("", key=f"wf_x_{n}_{i}", icon=":material/close:", on_click=_chain_edit, args=(state, "drop", i), help="Remove")
        free = [s for s in steps if s not in chain]
        if free:
            p, q = st.columns([6, 2], vertical_alignment="bottom")
            pick = p.selectbox("Add an approval step", free, key=f"wf_add_{n}_{len(chain)}", format_func=steps.get)
            q.button("Add step", key=f"wf_addbtn_{n}", on_click=_chain_edit, args=(state, "add", None, pick), width="stretch")
        if chain and form["kind"] == ROUTE:
            first = min(r["after_hours"] for r in config["sla"]["rules"]) // 24
            st.caption(" > ".join(steps.get(s, s) for s in chain) + " > MDM Analyst creates it in SAP. "
                       + ew.chain_estimate(chain, 2, first))
        elif not chain:
            st.caption("Add at least one approval step.")
        if state.get("wf_error"):
            st.error(state.pop("wf_error"), icon=":material/error:")
        a, b, c = st.columns([3, 2, 2])
        a.button("Save to the draft", key=f"wf_save_{n}", type="primary", on_click=_save_form, args=(state, config, names))
        b.button("Cancel", key=f"wf_cancel_{n}", on_click=_close, args=(state,))
        if form["id"] and not form["catch"]:
            c.button("Delete this rule", key=f"wf_del_{n}", on_click=_delete_rule, args=(state,))


# ----------------------------------------------------------------------------- draft bar, try it, map
def _draft_bar(config, state, user, live, names, commit):
    draft = state["wf_draft"]
    errors, warnings = ew.validate_rules(config, draft)
    changes = ew.describe_changes(config, live, ew.normalise(config, draft) if not errors else draft, names)
    if not changes and not errors and not warnings:
        return
    with st.container(border=True, key="wf_draftbar"):
        st.markdown(f"**Your draft: {len(changes)} change{'' if len(changes) == 1 else 's'}.** Nothing changes for anyone until you apply it.")
        for line in changes:
            st.markdown(f"- {line}")
        for e in errors:
            st.error(e, icon=":material/error:")
        for w in warnings:
            st.warning(w, icon=":material/warning:")
        a, b, _ = st.columns([2, 2, 4])
        if a.button("Apply the workflow", key="wf_apply", type="primary", disabled=bool(errors) or not changes):
            def change(c):
                out = ew.apply_rules(c, copy.deepcopy(draft), names)
                state.pop("wf_draft", None)
                state.pop("wf_form", None)
                return out
            commit(state, user, "Workflow", change)
        b.button("Discard the draft", key="wf_discard", on_click=_discard, args=(state,), disabled=not changes)


def _try_it(config, state, live, draft, names):
    ui.section("Try a request", "Pick a request and see which rule catches it and who approves, now and with your draft.",
               tip="Nothing is created. The draft is only tried, not applied.")
    domain = st.selectbox("Request type", list(config["domains"]), key="wf_try_dom", format_func=lambda d: names[d])
    used = {c["field"] for r in draft for key in ("all", "any") for c in (r.get("condition") or {}).get(key, [])}
    fields = [f for f in config["domains"][domain]["fields"] if f["field"] in used and not f.get("form_hidden")]
    payload, cols = {}, st.columns(max(1, min(3, len(fields) + 1)))
    dup = cols[0].checkbox("MDM duplicate review needed", key="wf_try_dup")
    for i, f in enumerate(fields):
        col = cols[(i + 1) % len(cols)]
        key = f"wf_try_{domain}_{f['field']}"
        if f.get("type") == "enum":
            payload[f["field"]] = col.selectbox(f["label"], f["options"], key=key)
        elif f.get("type") == "float":
            payload[f["field"]] = col.number_input(f["label"], value=0.0, key=key)
        else:
            payload[f["field"]] = col.text_input(f["label"], key=key)
    payload = {k: v for k, v in payload.items() if v not in ("", None)}
    steps = ew.steps(config)
    hit = None
    for label, rules in (("In use now", live), ("With your draft", draft)):
        try:
            res = ew.route_with(config, rules, domain, payload, dup)
            if label == "With your draft":
                hit = res["rule_id"]
            more = f" (plus {', '.join(res['also'])})" if res["also"] else ""
            st.markdown(f"**{label}:** rule {res['rule_id']}{more}, then " + " > ".join(steps[s] for s in res["chain"])
                        + " > MDM Analyst creates it in SAP")
        except ValueError as e:
            st.markdown(f"**{label}:** {e}")
    differ = []
    for req in state.requests_db:
        try:
            was = req["approval_path"] if req.get("approval_path") else None
            dup_r = em.dup_review_required(req.get("dup_matches") or [], config, req["domain"])
            now = ew.route_with(config, draft, req["domain"], req["payload"], dup_r)["chain"]
        except (ValueError, KeyError):
            continue
        if was and was != now:
            differ.append({"Request": f"{req['record_name']} ({req['request_id']})",
                           "Today": " > ".join(steps.get(s, s) for s in was), "With the draft": " > ".join(steps.get(s, s) for s in now)})
    st.markdown("**Requests already in this session**")
    if differ:
        st.caption("These would be routed differently if they were raised now. Requests already moving keep their path.")
        ui.table(pd.DataFrame(differ), hide_index=True)
    else:
        st.caption("None would be routed differently.")
    return hit


def render(config, state, user, commit):
    names = {k: v["name"] for k, v in common.domain_text(config).items()}
    live = ew.current_rules(config)
    if "wf_draft" not in state:
        state["wf_draft"] = copy.deepcopy(live)
    ui.section("Workflow designer", "Who approves what. Change it here; nothing touches the settings file.",
               tip="A route says: when these details are true, send the request to these approval steps in order, then the MDM "
                   "Analyst creates the record in SAP. The first route that fits wins. An 'also require' rule adds approvers to "
                   "the end of whatever route a request gets, whenever its condition is true. Edits build a draft; apply it when "
                   "you are happy. Requests already moving keep the path they were given.")
    _cards(config, state, names)
    if state.get("wf_form"):                                    # opens right under the Add buttons, where the user just clicked
        _form(config, state, names)
    _draft_bar(config, state, user, live, names, commit)
    if st.toggle("Advanced: try a request and see the diagram", key="wf_adv"):
        hit = _try_it(config, state, live, state["wf_draft"], names)
        ui.section("Workflow map", "The draft as a diagram. The rule your test request lands on is highlighted.")
        draft = state["wf_draft"]
        st.graphviz_chart(ew.graph_dot(config, ew.normalise(config, draft) if not ew.validate_rules(config, draft)[0] else draft,
                                       names, highlight=hit))
