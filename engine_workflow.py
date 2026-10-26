"""The approval workflow as plain functions on a config dict, so it can be edited on the dashboard instead of in the
settings file. A rule is: when [conditions], send the request to a chain of approval steps (first step, then the rest in
order), after which the MDM Analyst creates the record. Rules are checked top to bottom and the first match wins.

Nothing here mutates anything except apply_rules(), and that validates first. No Streamlit here."""
import copy
import textwrap

import engine_mdm as em

OP_WORDS = {"eq": "is", "ne": "is not", "gt": "is more than", "gte": "is at least", "lt": "is less than",
            "lte": "is at most", "in": "is one of", "not_in": "is not one of"}
WORD_OPS = {v: k for k, v in OP_WORDS.items()}
LIST_OPS = {"in", "not_in"}
DOMAIN_FIELD, DUP_FIELD = "_domain", "_dup_review"
ALL, ANY = "All of these", "Any of these"
CATCH_ALL_PRIORITY = 99


# ----------------------------------------------------------------------------- names
def steps(config):
    """The approval steps that exist, in the order the settings file lists them: {state: role label}."""
    sm = config["state_machine"]["approver_role_by_state"]
    return {state: config["roles"][role]["label"] for state, role in sm.items()}


def chain_of(rule):
    return [rule["route_to"]] + list(rule.get("then") or [])


def detail_options(config, domain_names):
    """{label shown to the administrator: field id} for everything a condition can look at."""
    out = {"Request type": DOMAIN_FIELD, "Needs MDM duplicate review": DUP_FIELD}
    for d in config["domains"].values():
        for f in d["fields"]:
            if f.get("form_hidden"):
                continue
            label = f["label"]
            if label in out and out[label] != f["field"]:
                label = f"{label} ({f['field']})"
            out.setdefault(label, f["field"])
    return out


def _field_def(config, field):
    return next((f for d in config["domains"].values() for f in d["fields"] if f["field"] == field), None)


def field_label(config, field):
    if field == DOMAIN_FIELD:
        return "Request type"
    if field == DUP_FIELD:
        return "Needs MDM duplicate review"
    f = _field_def(config, field)
    return f["label"] if f else field


def is_add(rule):
    """An 'also require' rule: every one that fits adds its steps to the end of the chain (route rules: first fit wins)."""
    return rule.get("kind", "route") == "add"


def is_catch_all(rule):
    c = rule.get("condition") or {}
    return not is_add(rule) and not c.get("all") and not c.get("any")


# ----------------------------------------------------------------------------- conditions <-> editable rows
def _show_value(config, field, value, domain_names):
    if field == DOMAIN_FIELD:
        return domain_names.get(value, value)
    if field == DUP_FIELD:
        return "Yes" if value else "No"
    return str(value)


def condition_rows(config, rule, domain_names):
    """Flatten a rule's condition into editable rows: Needs, Detail, Is, Value."""
    rows = []
    for needs, key in ((ALL, "all"), (ANY, "any")):
        for c in (rule.get("condition") or {}).get(key, []):
            value = c["value"]
            text = ", ".join(_show_value(config, c["field"], v, domain_names) for v in value) if isinstance(value, list) \
                else _show_value(config, c["field"], value, domain_names)
            rows.append({"Needs": needs, "Detail": field_label(config, c["field"]), "Is": OP_WORDS.get(c["op"], c["op"]),
                         "Value": text})
    return rows


def _parse_one(config, field, text, domain_names):
    text = str(text).strip()
    if field == DOMAIN_FIELD:
        back = {v: k for k, v in domain_names.items()}
        if text in back:
            return back[text]
        if text in config["domains"]:
            return text
        raise ValueError(f"'{text}' is not a request type.")
    if field == DUP_FIELD:
        if text.lower() in ("yes", "true"):
            return True
        if text.lower() in ("no", "false"):
            return False
        raise ValueError("Needs MDM duplicate review must be Yes or No.")
    f = _field_def(config, field)
    if f is None:
        raise ValueError(f"Unknown detail: {field}")
    if f.get("type") == "float":
        try:
            return float(text) if "." in text else int(text)
        except ValueError:
            raise ValueError(f"{f['label']}: '{text}' is not a number.") from None
    if f.get("type") == "enum":
        match = next((o for o in f["options"] if str(o) == text), None)
        if match is None:
            raise ValueError(f"{f['label']}: '{text}' is not one of {', '.join(map(str, f['options']))}.")
        return match
    if text == "":
        raise ValueError(f"{f['label']}: give a value.")
    return text


def rows_to_condition(config, rows, domain_names):
    """The inverse of condition_rows. Blank rows are ignored; a bad row raises ValueError saying which and why."""
    options = detail_options(config, domain_names)
    cond = {"all": [], "any": []}
    for n, r in enumerate(rows, 1):
        if not str(r.get("Detail") or "").strip():
            continue
        field = options.get(r["Detail"])
        if field is None:
            raise ValueError(f"Row {n}: '{r['Detail']}' is not a detail the rules can look at.")
        op = WORD_OPS.get(r.get("Is") or "is")
        if op is None:
            raise ValueError(f"Row {n}: '{r.get('Is')}' is not a comparison I know.")
        try:
            if op in LIST_OPS:
                value = [_parse_one(config, field, v, domain_names) for v in str(r.get("Value") or "").split(",") if v.strip()]
                if not value:
                    raise ValueError("give at least one value.")
            else:
                value = _parse_one(config, field, r.get("Value") or "", domain_names)
            if op in ("gt", "gte", "lt", "lte") and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError("this comparison needs a number.")
        except ValueError as e:
            raise ValueError(f"Row {n}: {e}") from None
        cond["any" if r.get("Needs") == ANY else "all"].append({"field": field, "op": op, "value": value})
    if not cond["any"]:
        cond.pop("any")
    return cond


def condition_text(config, cond, domain_names):
    def leaf(c):
        value = c["value"]
        shown = " or ".join(_show_value(config, c["field"], v, domain_names) for v in value) if isinstance(value, list) \
            else _show_value(config, c["field"], value, domain_names)
        if c["field"] == DOMAIN_FIELD and c["op"] == "eq":
            return f"the request is a {shown.lower()}"
        if c["field"] == DUP_FIELD and c["op"] == "eq":
            return "a likely duplicate needs MDM review" if value else "no duplicate review is needed"
        return f"{field_label(config, c['field'])} {OP_WORDS.get(c['op'], c['op'])} {shown}"
    all_ = [leaf(c) for c in cond.get("all", [])]
    any_ = [leaf(c) for c in cond.get("any", [])]
    text = " and ".join(all_)
    if any_:
        text += (" and " if text else "") + "(" + " or ".join(any_) + ")"
    return text or "everything else"


def path_text(config, rule):
    names = steps(config)
    chain = " > ".join(names.get(s, s) for s in chain_of(rule))
    return f"Also adds: {chain}" if is_add(rule) else chain + " > MDM Analyst creates it in SAP"


# ----------------------------------------------------------------------------- rules
def new_rule_id(rules):
    used = {r["id"] for r in rules}
    n = 1
    while f"R{n:02d}" in used or n == CATCH_ALL_PRIORITY:
        n += 1
    return f"R{n:02d}"


def make_rule(config, rule_id, chain, condition, enabled=True, kind="route"):
    """A rule dict in the settings file's shape. chain is [first step, then the rest]."""
    sm = config["state_machine"]["approver_role_by_state"]
    if not chain:
        raise ValueError("A rule needs at least one approval step.")
    rule = {"id": rule_id, "priority": 0, "route_to": chain[0], "approver_role": sm.get(chain[0]), "condition": condition}
    if len(chain) > 1:
        rule["then"] = list(chain[1:])
    if not enabled:
        rule["enabled"] = False
    if kind == "add":
        rule["kind"] = "add"
    return rule


def validate_rules(config, rules):
    """(errors, warnings). Errors stop the rules being applied; warnings are things worth a look."""
    sm = config["state_machine"]["approver_role_by_state"]
    errors, warnings = [], []
    ids = [r["id"] for r in rules]
    if len(set(ids)) != len(ids):
        errors.append("Two rules share the same name.")
    for r in rules:
        chain = chain_of(r)
        bad = [s for s in chain if s not in sm]
        if bad:
            errors.append(f"{r['id']}: unknown approval step {', '.join(bad)}.")
        if len(set(chain)) != len(chain):
            errors.append(f"{r['id']}: the same approval step appears twice in the chain.")
        for key in ("all", "any"):
            for c in (r.get("condition") or {}).get(key, []):
                if c["field"] not in (DOMAIN_FIELD, DUP_FIELD) and _field_def(config, c["field"]) is None:
                    errors.append(f"{r['id']}: unknown detail {c['field']}.")
    routes = [r for r in rules if not is_add(r)]
    catch = [r for r in routes if is_catch_all(r)]
    for r in rules:
        if is_add(r) and not (r.get("condition") or {}).get("all") and not (r.get("condition") or {}).get("any"):
            warnings.append(f"{r['id']} is an 'also require' rule with no conditions, so it adds its steps to every request.")
    if len(catch) != 1:
        errors.append("There must be exactly one 'everything else' route, at the bottom.")
    elif routes[-1] is not catch[0]:
        errors.append("The 'everything else' rule must stay last.")
    elif catch[0].get("enabled", True) is False:
        errors.append("The 'everything else' rule cannot be switched off, or some requests would have nowhere to go.")
    live = [r for r in routes if r.get("enabled", True) is not False]
    for i, r in enumerate(live):
        if is_catch_all(r):
            continue
        mine = {(c["field"], c["op"], repr(c["value"])) for c in (r.get("condition") or {}).get("all", [])}
        for earlier in live[:i]:
            ec = earlier.get("condition") or {}
            theirs = {(c["field"], c["op"], repr(c["value"])) for c in ec.get("all", [])}
            if not ec.get("any") and theirs <= mine:
                warnings.append(f"{r['id']} never fires: {earlier['id']} above it already catches everything it would.")
                break
    return errors, warnings


def normalise(config, rules):
    """Priorities 1..n in the order given, the catch-all last at 99, approver_role matching the first step."""
    sm = config["state_machine"]["approver_role_by_state"]
    out = []
    body = [r for r in rules if not is_catch_all(r) and not is_add(r)]
    for i, r in enumerate(body + [r for r in rules if is_add(r)] + [r for r in rules if is_catch_all(r)], 1):
        r = copy.deepcopy(r)
        r["priority"] = CATCH_ALL_PRIORITY if is_catch_all(r) else i
        r["approver_role"] = sm[r["route_to"]]
        if not r.get("then"):
            r.pop("then", None)
        if r.get("enabled", True) is not False:
            r.pop("enabled", None)
        out.append(r)
    return out


def current_rules(config):
    return [copy.deepcopy(r) for r in sorted(config["routing_matrix"], key=lambda r: r["priority"])]


def describe_changes(config, before, after, domain_names):
    """Plain words for what differs between two lists of rules."""
    b, a = {r["id"]: r for r in before}, {r["id"]: r for r in after}
    out = []
    for rid, r in a.items():
        if rid not in b:
            out.append(f"Added {rid}: when {condition_text(config, r['condition'], domain_names)}, {path_text(config, r).replace("Also adds:", "also adds")}")
            continue
        o = b[rid]
        if condition_text(config, o["condition"], domain_names) != condition_text(config, r["condition"], domain_names):
            out.append(f"{rid} now applies when {condition_text(config, r['condition'], domain_names)}")
        if chain_of(o) != chain_of(r):
            out.append(f"{rid} now goes: {path_text(config, r)}")
        if o.get("enabled", True) != r.get("enabled", True):
            out.append(f"{rid} switched {'on' if r.get('enabled', True) else 'off'}")
    for rid in b:
        if rid not in a:
            out.append(f"Removed {rid}")
    if [r["id"] for r in before if r["id"] in a] != [r["id"] for r in after if r["id"] in b]:
        out.append("Rule order changed: " + ", ".join(r["id"] for r in after))
    return out


def apply_rules(config, rules, domain_names):
    """Validate, then replace config['routing_matrix']. Returns the plain-words changes (empty when nothing changed)."""
    errors, _ = validate_rules(config, rules)
    if errors:
        raise ValueError(" ".join(errors))
    new = normalise(config, rules)
    changes = describe_changes(config, current_rules(config), new, domain_names)
    if changes or new != current_rules(config):
        config["routing_matrix"] = new
    return changes


# ----------------------------------------------------------------------------- trying it out
def route_with(config, rules, domain, payload, dup_review=False):
    """Which rule would win, and the approval chain, if the config used these rules. {rule_id, chain}."""
    trial = {**config, "routing_matrix": normalise(config, rules)}
    out = em.route_request(domain, payload, trial, dup_review=dup_review)
    return {"rule_id": out["rule_id"], "chain": [out["route_to"]] + list(out.get("then") or []), "also": out.get("also", [])}


def chain_estimate(chain, days_each, first_reminder_days):
    """One plain sentence about how long a chain takes if every step takes days_each."""
    total = len(chain) * days_each
    return (f"If every step takes {days_each} day{'' if days_each == 1 else 's'}: about {total} days. "
            f"The first reminder goes out at {first_reminder_days} day{'' if first_reminder_days == 1 else 's'}.")


def graph_dot(config, rules, domain_names, highlight=None):
    """Graphviz source: one row per rule, from its conditions through its approvers to the SAP step."""
    names = steps(config)
    wrap = lambda s, w=30: "\\n".join(textwrap.wrap(s, w)) or s                      # noqa: E731
    lines = ["digraph W {", "rankdir=LR; bgcolor=transparent; nodesep=0.25; ranksep=0.35;",
             'node [fontname="Helvetica", fontsize=10, shape=box, style="rounded,filled", fillcolor="#FFFFFF", color="#BCC3CA"];',
             'edge [color="#6A6D70"];']
    for r in rules:
        on = r.get("enabled", True) is not False
        hot = r["id"] == highlight
        colour = "#0070F2" if hot else ("#BCC3CA" if on else "#D5DADD")
        style = "rounded,filled" if on else "rounded,filled,dashed"
        fill = "#EAF3FF" if hot else ("#F5F6F7" if on else "#EFF1F2")
        title = condition_text(config, r["condition"], domain_names)
        lines.append(f'"{r["id"]}_c" [label="{r["id"]}\\n{wrap(title[:1].upper() + title[1:])}", fillcolor="{fill}", '
                     f'color="{colour}", style="{style}", penwidth={2 if hot else 1}];')
        prev = f'{r["id"]}_c'
        for i, s in enumerate(chain_of(r)):
            node = f'{r["id"]}_{i}'
            lines.append(f'"{node}" [label="{wrap(names.get(s, s), 22)}", color="{colour}", style="{style}"];')
            lines.append(f'"{prev}" -> "{node}"{" [style=dashed]" if not on else ""};')
            prev = node
        end = "added to the end of\\nthe chain" if is_add(r) else "MDM Analyst\\ncreates it in SAP"
        lines.append(f'"{r["id"]}_z" [label="{end}", fillcolor="{"#FFF3D6" if is_add(r) else "#E1F4FF"}", color="{colour}", style="{style}"];')
        lines.append(f'"{prev}" -> "{r["id"]}_z";')
    lines.append("}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- the simple form: who, anything extra, who approves
def simple_form(config, rule):
    """A rule as the simple form shows it: {domain or None, extras [conditions], any bool}, or None when the rule uses a
    shape the form cannot show (then only the approvers can be changed there)."""
    cond = rule.get("condition") or {}
    all_, any_ = list(cond.get("all", [])), list(cond.get("any", []))
    domain = None
    for c in list(all_):
        if c["field"] == DOMAIN_FIELD:
            if c["op"] != "eq" or domain is not None or c["value"] not in config["domains"]:
                return None
            domain = c["value"]
            all_.remove(c)
    if all_ and any_:
        return None
    return {"domain": domain, "extras": copy.deepcopy(all_ or any_), "any": bool(any_)}


def build_condition(domain, extras, any_mode):
    """The inverse of simple_form."""
    first = [{"field": DOMAIN_FIELD, "op": "eq", "value": domain}] if domain else []
    cond = {"all": first + ([] if any_mode and len(extras) > 1 else list(extras))}
    if any_mode and len(extras) > 1:
        cond["any"] = list(extras)
    return cond


def ops_for(config, field):
    """The comparisons that make sense for a detail, as the words shown to the administrator."""
    if field == DUP_FIELD:
        return ["is"]
    f = _field_def(config, field)
    if f and f.get("type") == "float":
        return ["is", "is more than", "is at least", "is less than", "is at most"]
    if f and f.get("type") == "enum":
        return ["is", "is not", "is one of"]
    return ["is", "is not"]
