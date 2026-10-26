"""Hands-free demo. Builds a script from data/autopilot.json and the existing Case Synopsis stories, and hands it to
tabs/autopilot.js, which plays it on the real screens: it switches role with the header role switch, opens pages from
the top navigation, scrolls, clicks and keys every field in. Nothing is pre-filled and no request is created behind
the scenes. Wording comes from data/ui_text.json (the stories) and data/autopilot.json (extra explanations, checks);
payloads come from data/scenarios.json. No business rule lives here."""
import copy
import json
import re
import time

import streamlit.components.v1 as components

import engine_mdm as em
import engine_security as es
import engine_settings as es_set
import engine_workflow as ew
from tabs import common, demo_data, tab_intake, ui

PLAYBOOK = common.ROOT / "data" / "autopilot.json"
SCRIPT = common.ROOT / "tabs" / "autopilot.js"
RUN_KEY = "autopilot"


def load():
    return json.loads(PLAYBOOK.read_text(encoding="utf-8"))


def story_ids():
    return list(load()["stories"])


def story_label(sid):
    spec = load()["stories"][sid]
    return spec.get("label") or common.load_ui()["stories"][sid]["label"]


def _plain(text):
    return re.sub(r"\*\*|__", "", text or "")


def role_choice(config, playbook, email):
    """The role-switch entry (and approval step) that stands for this person, so the demo uses the real switch."""
    override = playbook.get("role_for", {}).get(email)
    if override:
        return {"label": override["label"], "fn": override.get("fn"), "short": ui.short_role(override["label"])}
    users = {u["email"]: u for u in config["users"]}
    role = users[email]["role"]
    for entry in config["demo"]["roles"]:
        for fn in entry.get("functions") or [None]:
            if users[(fn or entry)["email"]]["role"] == role:
                return {"label": entry["label"], "fn": fn["label"] if fn else None, "short": ui.short_role(entry["label"])}
    raise ValueError(f"no role switch entry for {email}")


def _dig(sources, path):
    node = sources
    for part in path.split("."):
        node = node[part]
    return node


def _value_text(f, value):
    """What a person would see and type for this value: the option's words for a dropdown, a plain number otherwise."""
    if f.get("type") == "enum":
        return None
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def fill_ops(config, domain, payload, skip=(), only=None, previous=None):
    """Click the request type, then key in each field the form shows, in the order the form draws them. Private and
    optional fields sit in collapsed sections, which are opened first. `previous` limits it to the fields that changed."""
    ops = [{"op": "click", "t": {"key": f"type_{domain}"}, "unless": "Selected", "optional": True}]
    must, sensitive, optional = tab_intake.field_groups(config, domain)
    enum_words = config.get("enum_labels", {})
    for opener, group in ((None, must), ("Finance details", sensitive), ("More details", optional)):
        todo = [f for f in group if payload.get(f["field"]) not in (None, "") and f["field"] not in skip
                and (only is None or f["field"] in only)
                and (previous is None or previous.get(f["field"]) != payload[f["field"]])]
        if not todo:
            continue
        if opener:
            ops.append({"op": "expand", "text": opener})
        for f in todo:
            target = {"keyp": f"in_{domain}_{f['field']}_"}
            if f.get("type") == "enum":
                words = enum_words.get(f["field"], {})
                ops.append({"op": "select", "t": target, "value": words.get(str(payload[f["field"]]), str(payload[f["field"]]))})
            else:
                ops.append({"op": "type", "t": target, "value": _value_text(f, payload[f["field"]])})
    return ops


def policy_check(config, sources, op):
    """A security check answered by the real engine (the same functions the screens use), not read back off the page:
    what a role sees of one field (full, masked or hidden), or whether a role can see a request row at all.
    The result is shown as a PASS or FAIL flag while the demo plays."""
    if op.get("kind") == "fieldpolicy":
        trial = copy.deepcopy(config)                         # the administrator's choice, applied by the same function
        es_set.set_field_policy(trial, op["apply"]["class"], op["apply"]["role"], op["apply"]["level"])
        payload = _dig(sources, op["from"])
        view, report = es.apply_field_security({op["field"]: payload[op["field"]]}, op["domain"], op["role"], trial, status=op["status"])
        policy = report[op["field"]]
        ok = policy == op["expect"].upper()
        detail = f"{config['roles'][op['role']]['label']} now sees {es.POLICY_TEXT[policy]}"
    elif op.get("kind") == "rule":
        trial = copy.deepcopy(config)
        names = {k: v["name"] for k, v in common.domain_text(config).items()}
        rules = ew.current_rules(trial)
        routes = [r for r in rules if not ew.is_add(r) and not ew.is_catch_all(r)]
        adds = [r for r in rules if ew.is_add(r)]
        catch = next(r for r in rules if ew.is_catch_all(r))
        payload = _dig(sources, op["from"])
        before = em.route_request(op["domain"], payload, trial)
        cond = ew.build_condition(op["domain"], [{"field": op["field"], "op": "gt", "value": op["more_than"]}], False)
        new = ew.make_rule(trial, ew.new_rule_id(rules), [op["step"]], cond, kind="add")
        ew.apply_rules(trial, routes + adds + [new] + [catch], names)
        after = em.route_request(op["domain"], payload, trial)
        ok = op["step"] in (after.get("then") or []) and op["step"] not in (before.get("then") or [])
        detail = (f"before the rule the route was {' > '.join([before['route_to']] + (before.get('then') or []))}; "
                  f"with it: {' > '.join([after['route_to']] + (after.get('then') or []))}")
    elif op.get("kind") == "newrole":
        trial = copy.deepcopy(config)                         # the same function the dashboard calls, on a throwaway copy
        role_id = es_set.new_approver_defaults(trial, op["label"], op["person"])["role"]
        es_set.add_approver_role(trial, op["label"], op["person"], op["email"], op["delegate"])
        payload = _dig(sources, op["from"])
        view, report = es.apply_field_security({op["field"]: payload[op["field"]]}, op["domain"], role_id, trial,
                                               status=f"PENDING_{role_id}")
        policy = report[op["field"]]
        ok = policy == op["expect"].upper() and role_id in trial["roles"] and f"PENDING_{role_id}" in trial["state_machine"]["pending_states"]
        detail = f"the new role '{op['label']}' sees {es.POLICY_TEXT[policy]} of the bank account and has its own approval step"
    elif op.get("kind") == "chain":
        log = []
        for event in op["events"]:
            em.log_event(log, em.system_user(config), event, "REQ-DEMO", {}, config=config)
        tampered = copy.deepcopy(log)
        tampered[len(tampered) // 2]["actor_email"] = "someone.else@example.com"
        intact, (caught, at) = em.verify_audit_chain(log)[0], em.verify_audit_chain(tampered)
        ok = intact and not caught
        detail = f"an untouched log verifies; changing entry {len(tampered) // 2 + 1} of {len(tampered)} is caught at entry {at + 1}" \
            if ok else "the log check did not behave as expected"
    elif op.get("kind") == "row":
        user = es.get_user(op["as"], config)
        seen = es.can_view_request(user, {"status": op["status"], "requester_email": op["requester"], "domain": op.get("domain")}, config)
        ok = seen == (op["expect"] == "visible")
        detail = f"{config['roles'][user['role']]['label']} {'can' if seen else 'cannot'} see this request"
    else:
        payload = _dig(sources, op["from"])
        view, report = es.apply_field_security({op["field"]: payload[op["field"]]}, op["domain"], op["role"], config, status=op["status"])
        policy = report[op["field"]]
        ok = policy == op["expect"].upper()
        shown = f": {view[op['field']]}" if policy == "MASKED" else ""
        detail = f"{config['roles'][op['role']]['label']} sees {es.POLICY_TEXT[policy]}{shown}"
    return {"op": "flag", "kind": "test", "pass": bool(ok), "text": f"{op['text']} Engine result: {detail}.",
            "backed": op.get("backed"), "field": op.get("field") if op.get("kind") not in ("row", "chain") and not op.get("no_screen") else None,
            "expect": op.get("expect")}


def _expand(config, sources, ops, memory):
    out = []
    for op in ops:
        if op["op"] == "policy":
            out.append(policy_check(config, sources, op))
            continue
        if op["op"] != "fill":
            out.append(op)
            continue
        payload = {**_dig(sources, op["from"]), **(_dig(sources, op["also"]) if op.get("also") else {})}
        prev = memory.get(op["domain"]) if op.get("diff") else None
        out += fill_ops(config, op["domain"], payload, op.get("skip", ()), op.get("only"), prev)
        memory[op["domain"]] = payload
    return out


def build(config, sids, run_id):
    pb, ui_stories = load(), common.load_ui()["stories"]
    sources = {"scen": common.load_scenarios(), "ui": ui_stories}
    segments = []
    for n, sid in enumerate(sids):
        spec, steps, memory = pb["stories"][sid], [], {}
        title = spec.get("label") or ui_stories[sid]["label"]
        for i, raw in enumerate(spec["steps"]):
            base = ui_stories[sid]["steps"][raw["ui"]] if "ui" in raw else raw
            who, page, notice = base["who"], base["page"], _plain(base.get("notice") or base.get("text"))
            role = ({"label": raw["role"]["label"], "fn": raw["role"].get("fn"), "short": ui.short_role(raw["role"]["label"])}
                    if raw.get("role") else role_choice(config, pb, who))              # a role the story creates itself
            ops = []
            if i == 0 and n > 0 and not spec.get("keep_data"):
                ops.append({"op": "reset"})
            ops.append({"op": "role", **role})
            ops.append({"op": "nav", "path": page})
            if notice:
                ops.append({"op": "flag", "kind": "how", "text": notice})
            ops += _expand(config, sources, raw.get("ops", []), memory)
            steps.append({"title": f"Step {i + 1} of {len(spec['steps'])}: {role['label']}"
                                   f"{' (' + role['fn'] + ')' if role['fn'] else ''}, {pb['page_names'][page]}",
                          "ops": ops})
        segments.append({"title": title, "steps": steps})
    return {"runId": run_id, "timing": pb["timing"], "segments": segments}


def launch(state, config, sids):
    """Button callback: start from a clean demo, then let the page play the script (nothing is carried over)."""
    common.reset_demo(state)
    config = common.base_config()                         # the reset also drops any setting the administrator changed in this session
    state["demo_seeded"] = True                          # a story starts from nothing, so it never meets stray requests
    pb = load()["stories"]
    if all(pb[s].get("keep_data") for s in sids):         # a role tour on its own needs something to look at: load the samples
        common.init_state(state, config)
        demo_data.seed(state, config)
    run_id = int(time.time() * 1000)
    data = build(config, sids, run_id)
    code = "window.__lvAutoData = " + json.dumps(data) + ";\n" + SCRIPT.read_text(encoding="utf-8")
    state[RUN_KEY] = {"run": run_id, "html": _injector(code)}
    ui.go(state, "home")


def _injector(code):
    quoted = json.dumps(code).replace("</", "<\\/")
    return ("<script>(function(){var P=window.parent,s=P.document.createElement('script');"
            f"s.textContent={quoted};P.document.head.appendChild(s);s.remove();}})();</script>")


def inject(state):
    """Draw the invisible loader while a run is active. The player keeps running in the page between app re-runs."""
    run = state.get(RUN_KEY)
    if run:
        components.html(run["html"], height=0)
