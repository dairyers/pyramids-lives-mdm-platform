"""Settings the IT / System Administrator may change from the dashboard, as plain functions on a config dict.
Each function validates first and only then mutates, so a refused change leaves the config untouched. The UI applies
them to a per-session copy of config_rules.json (see tabs/common.live_config), so every form, check, reminder and
page reads the changed value on the next run. No Streamlit here."""
import copy
import re

# Pages an administrator can switch per role. INTAKE (New request) follows from "can raise"; INBOX is always on.
PAGE_TABS = ["STAGING", "SLA_BOARD", "AUDIT", "ADMIN"]
ANALYTICS_LEVELS = ["NONE", "AGGREGATE", "ACCOUNT_LEVEL"]


def protected_fields(config, domain):
    """Fields that cannot be made optional: the SAP key, the record's name and the name the duplicate check compares."""
    dom = config["domains"][domain]
    out = {dom["key_field"], dom["record_name_field"]}
    dd = config["deduplication_rules"].get(dom["sap_table"], {})
    if dd.get("compare_field"):
        out.add(dd["compare_field"])
    return out


def editable_fields(config, domain):
    """Fields whose 'mandatory' flag an administrator may flip (not hidden, not conditional, not protected)."""
    prot = protected_fields(config, domain)
    return [f for f in config["domains"][domain]["fields"]
            if f["field"] not in prot and not f.get("form_hidden") and not f.get("required_if")]


def set_required(config, domain, required_by_field):
    """required_by_field: {field: bool}. Returns a list of plain-words changes."""
    editable = {f["field"]: f for f in editable_fields(config, domain)}
    unknown = [k for k in required_by_field if k not in editable]
    if unknown:
        raise ValueError(f"These details cannot be changed here: {', '.join(unknown)}.")
    changes = []
    for name, want in required_by_field.items():
        f = editable[name]
        if bool(f.get("required")) != bool(want):
            f["required"] = bool(want)
            changes.append(f"{f['label']} is now {'mandatory' if want else 'optional'}")
    return changes


def _days(n):
    return f"{n} day" if n == 1 else f"{n} days"


def reminder_days(config):
    return [r["after_hours"] // 24 for r in sorted(config["sla"]["rules"], key=lambda r: r["after_hours"])]


def set_reminder_days(config, days):
    """days: whole days for each reminder, earliest first, strictly increasing. Text that quotes the old timing in the
    reminder subject or title is rewritten so the email and the invite say the new number."""
    rules = sorted(config["sla"]["rules"], key=lambda r: r["after_hours"])
    if len(days) != len(rules):
        raise ValueError(f"Expected {len(rules)} reminder times.")
    if any(int(d) != d or d < 1 for d in days) or list(days) != sorted(set(days)):
        raise ValueError("Reminder times must be whole days, each later than the one before.")
    changes = []
    for rule, d in zip(rules, days):
        hours, old = int(d) * 24, rule["after_hours"] // 24
        if hours != rule["after_hours"]:
            changes.append(f"Reminder after {_days(old)} is now after {_days(int(d))}")
        rule["after_hours"] = hours
        for key in ("subject", "title"):
            if key in rule:
                rule[key] = re.sub(r"\d+ hours", f"{hours} hours", re.sub(r"\d+ days", _days(int(d)), rule[key]))
    return changes


def duplicate_rules(config, table):
    """The three dials for one SAP table: name match threshold (0 to 1), near-miss check from (None when the table has
    none) and evidence groups needed (None when the table has no evidence check)."""
    dd = config["deduplication_rules"][table]
    corr = dd.get("corroboration") or {}
    return {"threshold": dd["similarity_threshold"],
            "near_miss": (dd.get("related_review") or {}).get("min_score"),
            "groups": (corr.get("quorum") or {}).get("min_agreeing_groups"),
            "max_groups": len({s["group"] for s in corr.get("signals", []) if s["strength"] != "CONFLICT"})}


def set_duplicate_rules(config, table, threshold=None, near_miss=None, groups=None):
    """Change the dials that exist for this table. The near-miss check must start below the name match threshold, and
    the evidence groups needed can be at most the number of distinct kinds of evidence the rules know."""
    if table not in config["deduplication_rules"]:
        raise ValueError(f"Unknown table: {table}")
    dd, now = config["deduplication_rules"][table], duplicate_rules(config, table)
    thr = now["threshold"] if threshold is None else float(threshold)
    if not 0.5 <= thr <= 1.0:
        raise ValueError("The name match threshold must be between 50% and 100%.")
    if near_miss is not None:
        if now["near_miss"] is None:
            raise ValueError("This table has no near-miss check.")
        if not 0.3 <= float(near_miss) < thr:
            raise ValueError("The near-miss check must start below the name match threshold (and not under 30%).")
    elif now["near_miss"] is not None and now["near_miss"] >= thr:
        raise ValueError("The name match threshold cannot go at or below the near-miss check. Lower the near-miss check too.")
    if groups is not None:
        if now["groups"] is None:
            raise ValueError("This table has no evidence check.")
        floor = dd["corroboration"]["quorum"].get("min_non_weak_groups", 1)
        if int(groups) != groups or not max(1, floor) <= groups <= now["max_groups"]:
            raise ValueError(f"Evidence groups needed must be a whole number from {max(1, floor)} to {now['max_groups']}.")
    changes = []
    if thr != now["threshold"]:
        changes.append(f"Name match threshold is now {thr:.0%} (was {now['threshold']:.0%})")
        dd["similarity_threshold"] = thr
    if near_miss is not None and float(near_miss) != now["near_miss"]:
        changes.append(f"Near-miss check now starts at {float(near_miss):.0%} (was {now['near_miss']:.0%})")
        dd["related_review"]["min_score"] = float(near_miss)
    if groups is not None and int(groups) != now["groups"]:
        changes.append(f"Evidence groups needed is now {int(groups)} (was {now['groups']})")
        dd["corroboration"]["quorum"]["min_agreeing_groups"] = int(groups)
    return changes


def effective_can_submit(config, user):
    return list(user["can_submit"]) if "can_submit" in user else list(config["roles"][user["role"]]["can_submit"])


def set_person(config, email, role, can_submit):
    """Give one person a role and the request types they may raise (stored as an override only when it differs
    from the role's default). The last IT administrator cannot be moved out of the role."""
    user = next((u for u in config["users"] if u["email"].lower() == str(email).lower()), None)
    if user is None:
        raise ValueError(f"Unknown person: {email}")
    if role not in config["roles"]:
        raise ValueError(f"Unknown role: {role}")
    bad = [d for d in can_submit if d not in config["domains"]]
    if bad:
        raise ValueError(f"Unknown request type: {', '.join(bad)}")
    if user["role"] == "IT_ADMIN" and role != "IT_ADMIN" and sum(u["role"] == "IT_ADMIN" for u in config["users"]) == 1:
        raise ValueError("At least one person must stay an IT / System Administrator.")
    changes = []
    if user["role"] != role:
        changes.append(f"{user['display']} is now {config['roles'][role]['label']}")
        user["role"] = role
    default = config["roles"][role]["can_submit"]
    wanted = [d for d in config["domains"] if d in can_submit]
    if set(wanted) == set(default):
        if user.pop("can_submit", None) is not None:
            changes.append(f"{user['display']} follows the role's request types")
    elif wanted != user.get("can_submit"):
        user["can_submit"] = wanted
        changes.append(f"{user['display']} can raise: {', '.join(wanted) or 'nothing'}")
    return changes


# ----------------------------------------------------------------------------- new request types (starter domain builder)
KINDS = ["Text", "Number", "Choice"]
SENSITIVITY = {"General": "GENERAL", "Financial terms": "FINANCIAL_TERMS", "Bank details": "BANK_DETAILS"}
MAX_FIELDS = 25


def custom_domains(config):
    """Request types made on the dashboard (the shipped four are not editable here)."""
    return [d for d, dom in config["domains"].items() if dom.get("custom")]


def _camel(label):
    return "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", label))


def _blank_fields(name, config):
    entity = next((f for dom in config["domains"].values() for f in dom["fields"] if f["field"] == "U_Entity"), None)
    fields = [{"field": "U_Code", "label": f"{name} Code", "type": "string", "required": False},
              {"field": "U_Name", "label": f"{name} Name", "type": "string", "required": True, "min_len": 3}]
    if entity:
        fields.append(copy.deepcopy(entity))
    return fields


def add_request_type(config, name, help_text="", copy_from=None, key_prefix=None):
    """Create a request type: a form (optionally copied from an existing type), its own SAP table with a name-similarity
    duplicate check, and a place in the people and routing screens. Anyone who may raise requests by role can raise it
    (individual overrides are set under People and access). Routing starts at the catch-all (MDM Analyst); add rules for
    it in the rule designer. Only types made here can have their fields edited."""
    name = (name or "").strip()
    if len(name) < 3 or len(name) > 40:
        raise ValueError("Give the request type a name of 3 to 40 characters, for example 'Project code'.")
    if copy_from is not None and copy_from not in config["domains"]:
        raise ValueError(f"Unknown request type to copy: {copy_from}")
    taken_names = {(dom.get("ui") or {}).get("name", d).lower() for d, dom in config["domains"].items()}
    taken_names |= {n.lower() for n in _builtin_names(config)}
    if name.lower() in taken_names:
        raise ValueError(f"There is already a request type called '{name}'.")
    base = _slug(name, "_").upper() or "TYPE"
    dom_id, n = base, 2
    while dom_id in config["domains"]:
        dom_id, n = f"{base}_{n}", n + 1
    table_base, n = "O" + re.sub(r"[^A-Z]", "", base)[:4].ljust(3, "X"), 2
    table = table_base
    while table in config["deduplication_rules"]:
        table, n = f"{table_base}{n}", n + 1
    prefix = re.sub(r"[^A-Z0-9]", "", (key_prefix or base[:3]).upper())[:6] or base[:3]
    if copy_from:
        src = config["domains"][copy_from]
        fields = [copy.deepcopy(f) for f in src["fields"]]
        key_field, name_field = src["key_field"], src["record_name_field"]
    else:
        fields = _blank_fields(name, config)
        key_field, name_field = "U_Code", "U_Name"
    config["domains"][dom_id] = {
        "sap_table": table, "key_field": key_field, "record_name_field": name_field, "key_prefix": prefix, "custom": True,
        "ui": {"name": name, "help": help_text.strip() or f"Ask for a new {name.lower()}.", "icon": "add_box"}, "fields": fields}
    template = config["deduplication_rules"].get("OITM") or next(iter(config["deduplication_rules"].values()))
    rules = copy.deepcopy(template)
    rules.update({"compare_field": name_field, "exact_match_fields": [], "exact_match_policy": "HARD_BLOCK",
                  "similarity_threshold": 0.8})
    rules.pop("corroboration", None)
    rules.pop("related_review", None)
    config["deduplication_rules"][table] = rules
    config["roles"]["REQUESTER"]["can_submit"].append(dom_id)
    return [f"Added the request type '{name}' (SAP table {table})" + (f", copied from {copy_from}" if copy_from else "")]


def _builtin_names(config):
    return ["Customer", "Vendor", "Item", "Cost center"]


def field_rows(config, domain):
    """The editable rows of a request type's form, in order."""
    dom = config["domains"][domain]
    rows = []
    for f in dom["fields"]:
        if f.get("form_hidden"):
            continue
        kind = "Choice" if f.get("type") == "enum" else "Number" if f.get("type") == "float" else "Text"
        cls = next((k for k, v in SENSITIVITY.items() if v == f.get("security_class", "GENERAL")), "General")
        rows.append({"id": f["field"], "label": f["label"], "kind": kind,
                     "choices": "; ".join(str(o) for o in f.get("options", [])), "required": bool(f.get("required")),
                     "sensitivity": cls})
    return rows


def set_type_fields(config, domain, rows):
    """Replace the form of a request type made on the dashboard with the edited rows. The code and name details stay (the
    name is always mandatory); choices need at least two options. Raises ValueError listing every problem."""
    dom = config["domains"][domain]
    if not dom.get("custom"):
        raise ValueError("Only request types made on the dashboard can have their details edited here.")
    old = {f["field"]: f for f in dom["fields"]}
    errors, labels, out, seen = [], set(), [], set()
    protected = {dom["key_field"], dom["record_name_field"]}
    for r in rows:
        label = (r.get("label") or "").strip()
        if not label and not r.get("id"):
            continue
        if not label:
            errors.append("A detail has no label.")
            continue
        if label.lower() in labels:
            errors.append(f"Two details are called '{label}'.")
        labels.add(label.lower())
        kind = r.get("kind") or "Text"
        if kind not in KINDS:
            errors.append(f"'{label}': unknown type {kind}.")
            continue
        if r.get("id") in protected and kind != "Text":
            errors.append(f"'{label}' must stay Text.")
        options = [o.strip() for o in str(r.get("choices") or "").split(";") if o.strip()]
        if kind == "Choice" and (len(options) < 2 or len(set(options)) != len(options)):
            errors.append(f"'{label}' needs at least two different choices, separated by ;")
        out.append((r, label, kind, options))
    ids = {r.get("id") for r, *_ in out}
    for p in protected:
        if p not in ids:
            errors.append(f"The {old[p]['label']} detail cannot be removed.")
    if len(out) > MAX_FIELDS:
        errors.append(f"A request type can have at most {MAX_FIELDS} details.")
    if errors:
        raise ValueError(" ".join(errors))
    fields, changes = [], []
    hidden = [f for f in dom["fields"] if f.get("form_hidden")]
    for r, label, kind, options in out:
        fid = r.get("id")
        if fid in old:
            f = dict(old[fid])
            if f["label"] != label:
                changes.append(f"Renamed '{f['label']}' to '{label}'")
                f["label"] = label
        else:
            fid = "U_" + (_camel(label) or "Detail")
            while fid in old or fid in seen:
                fid += "2"
            f = {"field": fid, "label": label}
            changes.append(f"Added '{label}'")
        seen.add(fid)
        was_kind = ("Choice" if f.get("type") == "enum" else "Number" if f.get("type") == "float" else "Text")
        if kind != was_kind or fid not in old:
            for k in ("regex", "min_len", "min", "options"):
                f.pop(k, None)
            f["type"] = {"Text": "string", "Number": "float", "Choice": "enum"}[kind]
            if kind == "Choice":
                f["options"] = options
        elif kind == "Choice" and options != [str(o) for o in f.get("options", [])]:
            f["options"] = options
            changes.append(f"Changed the choices for '{label}'")
        required = True if fid == dom["record_name_field"] else False if fid == dom["key_field"] else bool(r.get("required"))
        if bool(f.get("required")) != required and fid in old:
            changes.append(f"'{label}' is now {'mandatory' if required else 'optional'}")
        f["required"] = required
        cls = SENSITIVITY.get(r.get("sensitivity") or "General")
        if cls is None:
            raise ValueError(f"'{label}': unknown sensitivity.")
        if cls == "GENERAL":
            f.pop("security_class", None)
        else:
            if fid in old and old[fid].get("security_class") != cls:
                changes.append(f"'{label}' is now treated as {r['sensitivity'].lower()}")
            f["security_class"] = cls
        fields.append(f)
    removed = [old[i]["label"] for i in old if i not in seen and not old[i].get("form_hidden")]
    changes += [f"Removed '{x}'" for x in removed]
    dom["fields"] = fields + hidden
    return changes


def _slug(text, sep):
    return re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep)


def new_approver_defaults(config, label, person):
    """The role id, approval step (state), person's email and delegate's email that add_approver_role would use."""
    role = _slug(label, "_").upper() or "APPROVER"
    taken, n, rid = set(config["roles"]), 2, role
    while rid in taken or f"PENDING_{rid}" in config["state_machine"]["pending_states"]:
        rid, n = f"{role}_{n}", n + 1
    domain = config["users"][0]["email"].split("@", 1)[1]
    return {"role": rid, "state": f"PENDING_{rid}", "email": f"{_slug(person, '.') or 'approver'}@{domain}",
            "delegate": f"{_slug(rid, '.')}.delegate@{domain}"}


def add_approver_role(config, label, person, email=None, delegate=None):
    """Create a new kind of approver: a role, the approval step it decides, the person who holds it and the delegate who
    receives its work after 8 days. The step can then be used in any route or also-require rule. Everything the existing
    approvers have (reminders, digest, queue, role switcher) is wired the same way. Refuses a repeated name or email."""
    label, person = (label or "").strip(), (person or "").strip()
    if len(label) < 3:
        raise ValueError("Give the approver a name of at least 3 letters, for example 'Regional Compliance Officer'.")
    if not person:
        raise ValueError("Say who holds this approval step.")
    if label.lower() in {r["label"].lower() for r in config["roles"].values()}:
        raise ValueError(f"There is already a role called '{label}'.")
    d = new_approver_defaults(config, label, person)
    email, delegate = (email or d["email"]).strip().lower(), (delegate or d["delegate"]).strip().lower()
    used = {u["email"].lower() for u in config["users"]} | {v.lower() for v in config.get("delegates", {}).values()}
    for e in (email, delegate):
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
            raise ValueError(f"'{e}' is not an email address.")
        if e in used:
            raise ValueError(f"The email {e} is already in use.")
    if email == delegate:
        raise ValueError("The delegate must be a different person from the approver.")
    role, state = d["role"], d["state"]
    sm = config["state_machine"]
    config["roles"][role] = {"label": label, "tabs": ["INBOX"], "row_scope": "QUEUE", "can_submit": [],
                             "can_decide_states": [state], "analytics": "NONE"}
    sm["pending_states"].append(state)
    sm["approver_role_by_state"][state] = role
    for tr in sm["transitions"]:
        if tr["action"] in ("APPROVE", "REJECT", "REASSIGN"):
            tr["from"].append(state)
    config["sla"]["watched_states"].append(state)
    config["m365"]["digest_roles"].append(role)
    config.setdefault("delegates", {})[role] = delegate
    config["users"].append({"email": email, "display": person, "title": label, "role": role,
                            "persona": f"Approves requests that need {label} sign-off"})
    for entry in config["demo"]["roles"]:
        if entry.get("functions"):
            entry["functions"].append({"label": label, "email": email})
            break
    return [f"Added the approver '{label}' ({person}) and the approval step '{label}'; delegate {delegate}"]


PRIVATE_CLASSES = ("FINANCIAL_TERMS", "BANK_DETAILS")        # the kinds of value that are redacted for some roles
POLICY_LEVELS = ("HIDDEN", "MASKED", "FULL")


def private_classes(config):
    """The redacted classes that exist in the settings, in the order the settings file lists them."""
    return [c for c in config["security"]["classes"] if c != "GENERAL"]


def current_policy(config, security_class, role):
    """What a role sees of a private class today: HIDDEN, MASKED or FULL (a state-gated policy reads as its main level)."""
    by_role = config["security"]["classes"][security_class]["policy_by_role"]
    p = by_role.get(role, by_role.get("*", config["security"]["default_policy"]))
    return p["policy"] if isinstance(p, dict) else p


def set_field_policy(config, security_class, role, level):
    """The administrator decides who may see a redacted kind of value: hidden, masked or in full. A policy that was tied
    to a request state (the Payments Officer's full bank account only once approved) becomes the plain level chosen."""
    if security_class not in private_classes(config):
        raise ValueError(f"'{security_class}' is not a redacted kind of value.")
    if role not in config["roles"]:
        raise ValueError(f"There is no role '{role}'.")
    if level not in POLICY_LEVELS:
        raise ValueError(f"Choose one of: {', '.join(POLICY_LEVELS)}.")
    if current_policy(config, security_class, role) == level and not isinstance(
            config["security"]["classes"][security_class]["policy_by_role"].get(role), dict):
        return []
    config["security"]["classes"][security_class]["policy_by_role"][role] = level
    word = {"HIDDEN": "nothing", "MASKED": "a masked value", "FULL": "the full value"}[level]
    return [f"{config['roles'][role]['label']} now sees {word} of {security_class.replace('_', ' ').lower()}"]


def set_role_access(config, role, pages, analytics, can_submit):
    """pages: subset of PAGE_TABS the role can open; analytics: one of ANALYTICS_LEVELS; can_submit: request types.
    Requests is always on. New request follows can_submit; Sales insights follows the analytics level."""
    r = config["roles"][role]
    if analytics not in ANALYTICS_LEVELS:
        raise ValueError(f"Unknown Sales insights level: {analytics}")
    if role == "IT_ADMIN" and "ADMIN" not in pages:
        raise ValueError("The IT / System Administrator must keep Platform settings.")
    bad = [p for p in pages if p not in PAGE_TABS]
    if bad:
        raise ValueError(f"Unknown page: {', '.join(bad)}")
    submit = [d for d in config["domains"] if d in can_submit]
    tabs = (["INTAKE"] if submit else []) + ["INBOX"] + [t for t in PAGE_TABS if t in pages] + \
           (["ANALYTICS"] if analytics != "NONE" else [])
    order = list(config["tabs"])
    tabs = sorted(tabs, key=order.index)
    changes = []
    if tabs != r["tabs"]:
        changes.append(f"{r['label']} pages are now: {', '.join(config['tabs'][t] for t in tabs)}")
    if analytics != r["analytics"]:
        changes.append(f"{r['label']} Sales insights is now {analytics.replace('_', ' ').lower()}")
    if submit != r["can_submit"]:
        changes.append(f"{r['label']} can raise: {', '.join(submit) or 'nothing'}")
    r["tabs"], r["analytics"], r["can_submit"] = tabs, analytics, submit
    return changes
