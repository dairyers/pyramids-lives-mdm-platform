"""Role switch helpers, row scope and field-level security (spec Section 2.4).

apply_field_security() is the single chokepoint for record values shown in the UI, exported,
put in an Adaptive Card or digest, or written to the audit log. Every policy, mask style,
channel cap and row scope is read from config_rules.json.
"""
import pandas as pd

RANK = {"FULL": 2, "MASKED": 1, "HIDDEN": 0}
SYSTEM_ROLE = "SYSTEM"
LENS_HIDDEN = "(hidden)"


# ----------------------------------------------------------------------------- identity
def get_user(email, config):
    """Return the user dict for an email: a configured persona, the SLA system sender or a delegate."""
    key = str(email or "").strip().lower()
    for u in config["users"]:
        if u["email"].lower() == key:
            return dict(u)
    if key == config["sla"]["system_sender"].lower():
        return {"email": config["sla"]["system_sender"], "display": "System", "role": SYSTEM_ROLE,
                "persona": "Automated workflow actor"}
    for role, delegate in config.get("delegates", {}).items():
        if delegate.lower() == key:
            label = config["roles"][role]["label"]
            return {"email": delegate, "display": f"Delegate ({label})", "role": role,
                    "persona": f"Delegate who receives {label} work on SLA reassignment"}
    raise KeyError(f"Unknown user: {email}")


# ----------------------------------------------------------------------------- field security
def _resolve(entry, role, status, default):
    e = entry.get(role, entry.get("*", default))
    if isinstance(e, dict):
        return e["policy"] if status in e["only_in_states"] else e["otherwise"]
    return e


def _mask(value, style_name, sec):
    if value is None or str(value) == "":
        return value                                   # nothing to mask; never invent characters
    style = sec["mask_styles"][style_name]
    s = str(value)
    if "keep" in style:
        keep = style["keep"]
        if len(s) <= keep:                             # a short value would be shown in full by s[-keep:]
            return style["mask_char"] * len(s)
        return style["mask_char"] * (len(s) - keep) + s[-keep:]
    return style["text"]


def field_policy(field, domain, role, config, status=None, channel="APP"):
    """Effective policy (FULL / MASKED / HIDDEN) and security class for one field."""
    sec = config["security"]
    f = {x["field"]: x for x in config["domains"][domain]["fields"]}.get(field)
    if f is None:
        policy = "FULL" if field in sec["system_fields_visible"] else sec["default_policy"]
        cls = "GENERAL"
    else:
        cls = f.get("security_class", "GENERAL")
        policy = _resolve(sec["classes"][cls]["policy_by_role"], role, status, sec["default_policy"])
    cap = sec["channel_caps"].get(channel, {}).get(cls)
    if cap and RANK[cap] < RANK[policy]:
        policy = cap
    return policy, cls


def apply_field_security(record, domain, role, config, status=None, channel="APP"):
    """Return (view, report). view holds only what the role may see; report maps field -> policy."""
    sec = config["security"]
    status = status or record.get("U_ReqStatus") or record.get("status")
    view, report = {}, {}
    for key, val in record.items():
        policy, cls = field_policy(key, domain, role, config, status, channel)
        report[key] = policy
        if policy == "FULL":
            view[key] = val
        elif policy == "MASKED":
            view[key] = _mask(val, sec["classes"][cls].get("mask_style", "redact"), sec)
    return view, report


# ----------------------------------------------------------------------------- row scope
def _state_gated(role, config):
    """(classes, states) where this role's policy depends on the workflow state (e.g. Payments + bank)."""
    classes, states = set(), []
    for cls, c in config["security"]["classes"].items():
        e = c["policy_by_role"].get(role)
        if isinstance(e, dict):
            classes.add(cls)
            states += [s for s in e["only_in_states"] if s not in states]
    return classes, states


def can_view_request(user, request, config):
    """Row scope: OWN / ALL / QUEUE / RELEASE / META_ONLY from config.roles[role].row_scope."""
    role = config["roles"].get(user.get("role"))
    if role is None:
        return False
    scope = role["row_scope"]
    if scope == "ALL" or scope == "META_ONLY":
        return True
    if scope == "OWN":
        return str(request.get("requester_email", "")).lower() == str(user.get("email", "")).lower()
    if scope == "QUEUE":
        return request.get("status") in role["can_decide_states"]
    if scope == "RELEASE":
        classes, states = _state_gated(user["role"], config)
        if request.get("status") not in states:
            return False
        fields = {f["field"]: f.get("security_class", "GENERAL") for f in config["domains"][request["domain"]]["fields"]}
        payload = request.get("payload", {})
        return any(fields.get(k) in classes and v not in (None, "") for k, v in payload.items())
    return False


def request_view(user, request, config, channel="APP"):
    """What a user may see of one request: None (row hidden), metadata only (META_ONLY), or
    metadata plus the field-secured payload."""
    if not can_view_request(user, request, config):
        return None
    meta = {k: request.get(k) for k in config["security"]["meta_fields"]}
    if config["roles"][user["role"]]["row_scope"] == "META_ONLY":
        return meta
    view, report = apply_field_security(request.get("payload", {}), request["domain"], user["role"], config,
                                        status=request.get("status"), channel=channel)
    return {**meta, "fields": view, "field_policies": report}


# ----------------------------------------------------------------------------- tabs and capability text
def visible_tabs(role, config):
    tabs = list(config["roles"].get(role, {}).get("tabs", []))
    if config.get("meta", {}).get("demo_mode"):
        tabs += [t for t in config["security"].get("always_visible_tabs_in_demo", []) if t not in tabs]
    return [t for t in tabs if t in config["tabs"]]


ROW_SCOPE_TEXT = {
    "OWN": "Sees only the requests you submitted.",
    "ALL": "Sees every request.",
    "QUEUE": "Sees requests waiting in your approval queue ({states}).",
    "RELEASE": "Sees approved or synced records that carry {classes} (release queue, {states}).",
    "META_ONLY": "Sees request metadata only ({meta}); no field values.",
}
POLICY_TEXT = {"FULL": "full values", "MASKED": "masked values", "HIDDEN": "nothing"}


def _humanize(code):
    return str(code).replace("_", " ").lower()


def capability_summary(role, config):
    """Plain-English 'what you can see / cannot see' bullets, generated from config."""
    r = config["roles"].get(role)
    if r is None:
        return ["Unknown role: no access."]
    sec = config["security"]
    out = [f"Tabs: {', '.join(config['tabs'][t] for t in visible_tabs(role, config))}."]
    classes, states = _state_gated(role, config)
    scope_text = ROW_SCOPE_TEXT.get(r["row_scope"], "Row scope {scope} is not recognised: no rows.")
    out.append(scope_text.format(
        scope=r["row_scope"],
        states=", ".join(_humanize(s) for s in (r["can_decide_states"] if r["row_scope"] == "QUEUE" else states))
        or "none",
        classes=", ".join(_humanize(c) for c in sorted(classes)) or "sensitive fields",
        meta=", ".join(sec["meta_fields"])))
    out.append("Can submit: " + (", ".join(_humanize(d) for d in r["can_submit"]) or "nothing") + ".")
    out.append("Can approve or reject: " + (", ".join(_humanize(s) for s in r["can_decide_states"]) or "nothing") + ".")
    for cls, c in sec["classes"].items():
        e = c["policy_by_role"].get(role, c["policy_by_role"].get("*", sec["default_policy"]))
        if isinstance(e, dict):
            text = (f"{POLICY_TEXT[e['policy']]} only when the request is "
                    f"{' or '.join(_humanize(s) for s in e['only_in_states'])}, otherwise {POLICY_TEXT[e['otherwise']]}")
            best = max(RANK[e["policy"]], RANK[e["otherwise"]])
        else:
            text, best = POLICY_TEXT[e], RANK[e]
        capped = [ch for ch, caps in sec["channel_caps"].items() if cls in caps and RANK[caps[cls]] < best]
        note = f" (by {', '.join(ch.lower() for ch in capped)}: {POLICY_TEXT[sec['channel_caps'][capped[0]][cls]]})" \
            if capped else ""
        out.append(f"{_humanize(cls).capitalize()}: {text}{note}.")
    for table, dd in config.get("deduplication_rules", {}).items():
        cc = dd.get("corroboration")
        if cc:
            out.append("Duplicate evidence: " + ("signal labels and compared values"
                       if role in cc["detail_visible_roles"] else "signal labels only, no compared values") + ".")
    out.append(f"Analytics: {_humanize(r['analytics'])}.")
    return out


# ----------------------------------------------------------------------------- security lens
def security_lens(record, domain, config, status=None):
    """Rows = fields, columns = roles, cells = the value that role would actually see."""
    data = {}
    for role in config["roles"]:
        view, _ = apply_field_security(record, domain, role, config, status=status)
        data[role] = [view[k] if k in view else LENS_HIDDEN for k in record]
    return pd.DataFrame(data, index=list(record.keys()), dtype=object)
