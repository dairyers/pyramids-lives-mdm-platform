"""Outlook mockups for the messages the platform sends, each with a 'Technical JSON' tab beside it.

Nothing is sent: the platform builds each message as data (m365_integration) and these helpers draw it the way
the recipient would see it. Every value comes from the same data as the JSON, which has already been through
apply_field_security(channel='EMAIL'), so no mockup ever shows a bank account.
"""
import html
from datetime import datetime, timedelta

import streamlit as st

import engine_security as es
import m365_integration as m365
from tabs import common, ui

WITHHELD = m365.WITHHELD
FRAME = ("border:1px solid #d6d3d1;border-radius:10px;background:#fff;overflow:hidden;"
         "font-family:Segoe UI,Inter,sans-serif;color:#1c1917")


def _esc(value):
    return html.escape(str(value))


def person(config, email):
    """'Wei Jie <weijie@livesgroup.com>' for anyone the platform knows, else the bare address."""
    try:
        return f"{es.get_user(email, config)['display']} &lt;{_esc(email)}&gt;"
    except KeyError:
        return _esc(email)


def _people(config, emails):
    return ", ".join(person(config, e) for e in emails) or "-"


def _header(config, subject, sender, to, sent, importance=None, cc=None, tag="Simulated email, nothing was sent"):
    chip = (f"<span style='background:#fee2e2;color:#991b1b;border-radius:999px;padding:1px 9px;font-size:.75rem;"
            f"font-weight:600;margin-left:.5rem'>{_esc(importance)}</span>" if importance else "")
    meta = lambda k, v: (f"<div style='font-size:.85rem;color:#57534e;line-height:1.55'><span style='display:inline-block;"      # noqa: E731
                         f"width:3.2rem;color:#78716c'>{k}</span>{v}</div>")
    return (f"<div style='background:#f5f5f4;padding:.8rem 1rem;border-bottom:1px solid #e7e5e4'>"
            f"<div style='font-size:1.05rem;font-weight:650'>{_esc(subject)}{chip}</div>"
            f"{meta('From', person(config, sender))}{meta('To', _people(config, to))}"
            + (meta("Cc", _people(config, cc)) if cc else "") + meta("Sent", _esc(sent))
            + f"<div style='font-size:.72rem;color:#a8a29e;margin-top:.2rem'>{_esc(tag)}</div></div>")


def _facts(card):
    fact_set = next((b for b in card["body"] if b.get("type") == "FactSet"), {"facts": []})
    rows = "".join(f"<tr><td style='padding:.18rem .8rem .18rem 0;color:#78716c;white-space:nowrap;vertical-align:top'>"
                   f"{_esc(f['title'])}</td><td style='padding:.18rem 0'>{_esc(f['value'])}</td></tr>"
                   for f in fact_set["facts"])
    return f"<table style='border-collapse:collapse;font-size:.88rem'>{rows}</table>"


def _card_block(card, buttons=True):
    title = next((b["text"] for b in card["body"] if b.get("type") == "TextBlock"), "")
    note = next((i["text"] for b in card["body"] if b.get("type") == "Container" for i in b.get("items", [])), "")
    acts = "".join(f"<span style='display:inline-block;margin:.5rem .5rem 0 0;padding:.4rem 1rem;border-radius:6px;"
                   f"font-size:.85rem;font-weight:600;color:#fff;background:{'#15803d' if 'Approve' in a['title'] else '#b91c1c'}'>"
                   f"{_esc(a['title'])}</span>" for a in card.get("actions", [])) if buttons else ""
    return (f"<div style='border:1px solid #e7e5e4;border-left:4px solid #0f766e;border-radius:6px;padding:.7rem .9rem;"
            f"margin:.6rem 0'><div style='font-weight:650;margin-bottom:.4rem'>{_esc(title)}</div>{_facts(card)}"
            f"<div style='background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:.4rem .6rem;margin:.6rem 0 .4rem;"
            f"font-size:.8rem;color:#92400e'>{_esc(note)}</div>"
            + ("<div style='border:1px solid #d6d3d1;border-radius:6px;padding:.5rem .6rem;color:#a8a29e;font-size:.82rem;"
               "min-height:2.2rem'>Enter approval or rejection comments here...</div>" if buttons else "")
            + f"{acts}</div>")


def _show(mock_html, technical, key):
    email_tab, json_tab = st.tabs(["Email", "Technical JSON"])
    with email_tab:
        st.markdown(f"<div style='{FRAME}'>{mock_html}</div>", unsafe_allow_html=True)
    with json_tab:
        st.caption("What the platform actually produces. In production this is posted to Microsoft 365; here nothing leaves the app.")
        st.json(technical, expanded=True)


# ----------------------------------------------------------------------------- the approval email
def approval_card(config, req, approver_role, viewer_role):
    """The Outlook card sent to the approver, limited to fields the person looking at it may also see."""
    card = m365.build_adaptive_card(req, approver_role, config)
    allowed = {label for label, _ in m365.role_filtered_facts(req, viewer_role, config)}
    kept = [(l, v) for l, v in m365.role_filtered_facts(req, approver_role, config) if l in allowed]
    dom = config["domains"][req["domain"]]
    for block in card["body"]:
        for fact in block.get("facts", []):
            if fact["title"].startswith("Visible fields"):
                fact["value"] = "; ".join(f"{l}: {v}" for l, v in kept) or WITHHELD
            if fact["title"] == "Record Name:" and next(f["label"] for f in dom["fields"]
                                                       if f["field"] == dom["record_name_field"]) not in allowed:
                fact["value"] = WITHHELD
            if fact["title"] == "Target Entity:" and "Business Entity" not in allowed:
                fact["value"] = WITHHELD
    return card


def approval_email(config, state, req, viewer_role, key):
    """The email an approver receives the moment a request reaches them, with Approve and Reject in the message."""
    role = req.get("current_approver_role")
    if not role:
        return
    card = approval_card(config, req, role, viewer_role)
    to = req.get("current_approver_email") or m365.em.approver_email_for_role(role, config)
    sent = ui.when(req.get("pending_since"))
    subject = f"Approval needed: {req['domain'].replace('_', ' ').title()} request {req['request_id']}"
    mock = (_header(config, subject, config["sla"]["system_sender"], [to], sent)
            + f"<div style='padding:.8rem 1rem;font-size:.92rem'>Hello,<br>A new request has reached you. You can decide "
              f"it from this email.{_card_block(card)}<span style='font-size:.8rem;color:#78716c'>Sent by "
              f"{_esc(config['sla']['system_sender'])}</span></div>")
    _show(mock, card, key)


# ----------------------------------------------------------------------------- reminders and invites (SLA engine)
def _when_invite(fired_at):
    start = datetime.fromisoformat(fired_at) + timedelta(days=1)
    start = start.replace(hour=10, minute=0, second=0, microsecond=0)
    return f"{start.strftime('%a %d %b %Y')}, 10:00 to 10:30 (Asia/Singapore)"


def sla_message(config, action, key):
    """Mockup plus JSON for one escalation produced by m365.process_sla_escalations()."""
    kind, sent = action["action"], ui.when(action["fired_at"])
    to, sender = action["recipients"], action["sender"]
    waited = f"{action['hours_pending'] / 24:.0f} days" if action["hours_pending"] >= 48 else f"{action['hours_pending']:.0f} hours"
    card = action.get("card")
    if kind == "SEND_HIGH_PRIORITY_EMAIL":
        body = (f"<div style='padding:.8rem 1rem;font-size:.92rem'>Request <b>{_esc(action['request_id'])}</b> has been "
                f"waiting for your decision for <b>{_esc(waited)}</b>, past the {action['after_hours']}-hour reminder point."
                f" Please approve or reject it today.{_card_block(card) if card else ''}</div>")
        mock = _header(config, action["subject"], sender, to, sent, importance="High importance") + body
    elif kind == "CREATE_CALENDAR_INVITE":
        mock = (_header(config, action["title"], sender, to, sent, tag="Simulated calendar invite, nothing was sent")
                + f"<div style='padding:.8rem 1rem;font-size:.92rem'><div style='border-left:4px solid #0f766e;padding:.2rem .8rem;"
                  f"background:#f0fdfa;border-radius:4px'><b>When:</b> {_esc(_when_invite(action['fired_at']))}<br>"
                  f"<b>Where:</b> Microsoft Teams meeting<br><b>Organizer:</b> {person(config, sender)}</div>"
                  f"<p>Agenda: why has request <b>{_esc(action['request_id'])}</b> been waiting <b>{_esc(waited)}</b>, and "
                  f"what unblocks it?</p>"
                  + "".join(f"<span style='display:inline-block;margin-right:.5rem;padding:.4rem 1rem;border-radius:6px;"
                            f"font-size:.85rem;font-weight:600;color:#fff;background:{c}'>{t}</span>"
                            for t, c in (("Accept", "#15803d"), ("Tentative", "#a16207"), ("Decline", "#b91c1c")))
                  + "</div>")
    else:                                                   # hand-over to the delegate
        body = (f"<div style='padding:.8rem 1rem;font-size:.92rem'>Request <b>{_esc(action['request_id'])}</b> was handed "
                f"from {person(config, action.get('reassigned_from', ''))} to {person(config, action.get('reassigned_to', ''))} "
                f"after <b>{_esc(waited)}</b> without a decision. The delegate can approve or reject it from this email."
                f"{_card_block(card) if card else ''}</div>")
        mock = _header(config, action["subject"], sender, to, sent, importance="High importance") + body
    _show(mock, action, key)
