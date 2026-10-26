"""Outlook / Microsoft 365 integration (spec Section 2.5): Adaptive Card per recipient role,
config-driven SLA escalation engine with an injectable clock, HTML email digest and a simulated
webhook receiver.

Every record value placed in a card or digest passes through
engine_security.apply_field_security(..., channel="EMAIL"), so channel caps (no bank details by
email) apply to every recipient. Rules, recipients, templates and endpoints come from config.
"""
import copy
import html
import re
from datetime import datetime, timezone

import engine_mdm as em
import engine_security as es

EMAIL = "EMAIL"
WITHHELD = "(withheld)"

# Card skeleton from spec 2.5. Single-brace placeholders are filled server-side; the double-brace
# {{approver_comment.value}} is left for Outlook to resolve at click time.
CARD_TEMPLATE = {
    "type": "AdaptiveCard",
    "version": "{card_version}",
    "originator": "{originator_id}",
    "body": [
        {"type": "TextBlock", "text": "MDM Approval Required: New Master Data Request", "weight": "Bolder", "size": "Medium"},
        {"type": "FactSet", "facts": [
            {"title": "Request ID:", "value": "{request_id}"},
            {"title": "Requester:", "value": "{requester_email}"},
            {"title": "Domain / Table:", "value": "{domain} ({sap_table})"},
            {"title": "Target Entity:", "value": "{business_entity}"},
            {"title": "Record Name:", "value": "{record_name}"},
            {"title": "Visible fields (role-filtered):", "value": "{role_filtered_fact_list}"},
        ]},
        {"type": "Container", "style": "warning", "items": [
            {"type": "TextBlock", "text": "Sensitive fields are withheld from email. Open the secure MDM app to view "
                                          "what your role permits.", "isSubtle": True, "wrap": True},
        ]},
        {"type": "Input.Text", "id": "approver_comment", "placeholder": "Enter approval or rejection comments here...",
         "isMultiline": True},
    ],
    "actions": [
        {"type": "Action.Http", "title": "Approve Request", "method": "POST", "url": "{respond_url}",
         "headers": [{"name": "Content-Type", "value": "application/json"}],
         "body": "{ \"request_id\": \"{request_id}\", \"action\": \"APPROVE\", \"comment\": \"{{approver_comment.value}}\" }"},
        {"type": "Action.Http", "title": "Reject Request", "method": "POST", "url": "{respond_url}",
         "headers": [{"name": "Content-Type", "value": "application/json"}],
         "body": "{ \"request_id\": \"{request_id}\", \"action\": \"REJECT\", \"comment\": \"{{approver_comment.value}}\" }"},
    ],
}
_PLACEHOLDER = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")


def _fill(obj, values):
    """Replace single-brace placeholders in every string of a nested structure (one pass, no re-scan)."""
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, values) for v in obj]
    if isinstance(obj, str):
        return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), obj)
    return obj


def _display_value(field, value, config):
    labels = config.get("enum_labels", {}).get(field, {})
    label = labels.get(str(value))
    return f"{label} ({value})" if label is not None else str(value)


def _secured_payload(request, role, config, channel=EMAIL):
    """The request payload as this role may see it on the given channel."""
    view, report = es.apply_field_security(request.get("payload", {}), request["domain"], role, config,
                                           status=request.get("status"), channel=channel)
    return view, report


def role_filtered_facts(request, role, config, channel=EMAIL):
    """[(label, value)] for every payload field the role may see on this channel (system fields excluded)."""
    view, _ = _secured_payload(request, role, config, channel)
    fields = config["domains"][request["domain"]]["fields"]
    return [(f["label"], _display_value(f["field"], view[f["field"]], config))
            for f in fields if f["field"] in view and view[f["field"]] not in (None, "")]


# ----------------------------------------------------------------------------- adaptive card
def build_adaptive_card(request, recipient_role, config):
    """Outlook Actionable Message card for one recipient role. Values come only from
    apply_field_security(channel='EMAIL'), so no card ever carries a full bank account."""
    m365, dom = config["m365"], config["domains"][request["domain"]]
    view, _ = _secured_payload(request, recipient_role, config)
    facts = role_filtered_facts(request, recipient_role, config)
    values = {
        "card_version": m365["card_version"], "originator_id": m365["originator_id"], "respond_url": m365["respond_url"],
        "request_id": request["request_id"], "requester_email": request.get("requester_email", ""),
        "domain": request["domain"], "sap_table": request.get("sap_table", dom["sap_table"]),
        "business_entity": view.get("U_Entity", WITHHELD),
        "record_name": view.get(dom["record_name_field"], WITHHELD),
        "role_filtered_fact_list": "; ".join(f"{label}: {value}" for label, value in facts) or WITHHELD,
    }
    return _fill(copy.deepcopy(CARD_TEMPLATE), values)


# ----------------------------------------------------------------------------- SLA engine
def _approver_email(req, config):
    return req.get("current_approver_email") or em.approver_email_for_role(req.get("current_approver_role"), config)


NOTIFY_RESOLVERS = {
    "current_approver": lambda req, config: _approver_email(req, config),
    "supervisor": lambda req, config: config["sla"]["supervisor_email"],
    "delegate": lambda req, config: config["delegates"].get(req.get("current_approver_role")),
}


def _recipients(rule, req, config):
    out = []
    for target in rule["notify"]:
        if target not in NOTIFY_RESOLVERS:
            raise ValueError(f"SLA rule {rule['id']}: unknown notify target '{target}'.")
        email = NOTIFY_RESOLVERS[target](req, config)
        if email and email not in out:
            out.append(email)
    return out


def _hours_pending(req, now):
    return (now - datetime.fromisoformat(req["pending_since"])).total_seconds() / 3600.0


def _build_sla_action(rule, req, config, now):
    """One escalation: recipients resolved from config, templates filled, reassignment applied."""
    sla = config["sla"]
    view, _ = _secured_payload(req, req.get("current_approver_role"), config)
    tpl_values = {"request_id": req["request_id"],
                  "record_name": view.get(config["domains"][req["domain"]]["record_name_field"], WITHHELD)}
    action = {"rule_id": rule["id"], "action": rule["action"], "request_id": req["request_id"],
              "after_hours": rule["after_hours"], "hours_pending": round(_hours_pending(req, now), 1),
              "fired_at": em.now_iso(config, now), "sender": sla["system_sender"],
              "recipients": _recipients(rule, req, config)}
    for key in ("subject", "title"):
        if key in rule:
            action[key] = rule[key].format_map(tpl_values)
    if rule["action"] == "AUTO_REASSIGN_TO_DELEGATE":
        delegate = config["delegates"].get(req.get("current_approver_role"))
        if delegate:
            previous = _approver_email(req, config)
            em.transition(req, "REASSIGN", em.system_user(config), config,
                          comment=f"SLA {rule['id']}: reassigned to delegate", now=now)
            req["current_approver_email"] = delegate
            action.update({"reassigned_from": previous, "reassigned_to": delegate})
    if rule["action"] in ("SEND_HIGH_PRIORITY_EMAIL", "AUTO_REASSIGN_TO_DELEGATE") and req.get("current_approver_role"):
        action["card"] = build_adaptive_card(req, req["current_approver_role"], config)
    return action


def process_sla_escalations(requests_db, config, now=None, *, audit_log=None):
    """Fire each config.sla rule once per request (flag sla_<id>_done), clock = pending_since.
    `now` is injectable and must be timezone-aware."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    sla, out = config["sla"], []
    for req in requests_db:
        if req.get("status") not in sla["watched_states"] or not req.get("pending_since"):
            continue
        hours = _hours_pending(req, now)
        for rule in sorted(sla["rules"], key=lambda r: r["after_hours"]):
            flag = f"sla_{rule['id']}_done"
            if hours >= rule["after_hours"] and not req.get(flag):
                req[flag] = True
                act = _build_sla_action(rule, req, config, now)
                out.append(act)
                if audit_log is not None:
                    em.log_event(audit_log, em.system_user(config), "SLA_ACTION", req["request_id"],
                                 {"rule_id": act["rule_id"], "action": act["action"], "recipients": act["recipients"],
                                  "hours_pending": act["hours_pending"]}, config=config, now=now)
    return out


def next_escalation(req, config, now):
    """(rule, hours_until) of the next SLA rule that has not fired yet, or (None, None)."""
    if req.get("status") not in config["sla"]["watched_states"] or not req.get("pending_since"):
        return None, None
    hours = _hours_pending(req, now)
    for rule in sorted(config["sla"]["rules"], key=lambda r: r["after_hours"]):
        if not req.get(f"sla_{rule['id']}_done"):
            return rule, round(rule["after_hours"] - hours, 1)
    return None, None


# ----------------------------------------------------------------------------- email digest
def digest_queue(requests, role, config):
    """Pending requests this role would receive in a digest: its decision queue, or (for a role
    without one) every request it may see in a watched SLA state."""
    r = config["roles"][role]
    user = {"role": role, "email": em.approver_email_for_role(role, config) or ""}
    states = r["can_decide_states"] or config["sla"]["watched_states"]
    return [q for q in requests if q.get("status") in states and es.can_view_request(user, q, config)]


def render_email_digest(requests, role, config, now=None):
    """HTML digest of the recipient's pending queue, every value via apply_field_security(channel='EMAIL').
    META_ONLY roles get metadata only."""
    r, esc = config["roles"][role], html.escape
    meta_only = r["row_scope"] == "META_ONLY"
    queue = digest_queue(requests, role, config)
    rows = []
    for q in queue:
        view, _ = _secured_payload(q, role, config)
        name = view.get(config["domains"][q["domain"]]["record_name_field"], WITHHELD)
        hours = f"{_hours_pending(q, now):.1f}" if now and q.get("pending_since") else "-"
        cells = [q["request_id"], q["domain"], q.get("sap_table", ""), name, q["status"], q.get("pending_since") or "-", hours]
        if not meta_only:
            facts = role_filtered_facts(q, role, config)
            cells.append("<br>".join(f"{esc(label)}: {esc(value)}" for label, value in facts) or WITHHELD)
            row = "".join(f"<td>{esc(str(c))}</td>" for c in cells[:-1]) + f"<td>{cells[-1]}</td>"
        else:
            row = "".join(f"<td>{esc(str(c))}</td>" for c in cells)
        rows.append(f"<tr>{row}</tr>")
    head = ["Request", "Domain", "Table", "Record", "Status", "Pending since", "Hours pending"]
    if not meta_only:
        head.append("Fields you may see")
    table = ("<table border='1' cellpadding='4' cellspacing='0'><thead><tr>"
             + "".join(f"<th>{esc(h)}</th>" for h in head) + "</tr></thead><tbody>"
             + ("".join(rows) or f"<tr><td colspan='{len(head)}'>Nothing pending.</td></tr>") + "</tbody></table>")
    note = ("Metadata only: field values are not shared with your role." if meta_only else
            "Sensitive fields are withheld from email. Open the secure MDM app to view what your role permits.")
    return (f"<html><body><h3>MDM pending digest for {esc(r['label'])}</h3>"
            f"<p>{len(queue)} request(s) pending.</p>{table}<p><i>{esc(note)}</i></p>"
            f"<p>Sent by {esc(config['sla']['system_sender'])}</p></body></html>")


# ----------------------------------------------------------------------------- webhook receiver
def simulate_webhook_response(request, user, action, comment, config, *, audit_log=None, now=None):
    """Simulated POST to config.m365.respond_url. In production the user comes from the verified
    bearer token, never from the body. Checks the role may decide the current state, then transitions."""
    role = config["roles"].get(user.get("role"), {})
    if request.get("status") not in role.get("can_decide_states", []):
        raise PermissionError(f"Role {user.get('role')} may not decide a request in {request.get('status')}.")
    card_actions = {re.search(r'"action": "(\w+)"', a["body"]).group(1) for a in CARD_TEMPLATE["actions"]}
    if action not in card_actions:
        raise ValueError(f"Unsupported card action {action}.")
    em.transition(request, action, user, config, comment=comment, now=now, audit_log=audit_log)
    return {"ok": True, "request_id": request["request_id"], "status": request["status"],
            "refreshed_card": build_adaptive_card(request, user["role"], config)}
