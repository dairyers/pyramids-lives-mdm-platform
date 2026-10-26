"""Tests for m365_integration.py (spec Section 2.5): cards, SLA escalations, digest, webhook."""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import engine_mdm as em
import engine_security as es
import m365_integration as m365
from fixtures import CONFIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCENARIOS = json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
FULL_ACCT = SCENARIOS["S2"]["payload"]["BankAcct"]
ROLES = list(CONFIG["roles"])


def user(email):
    return next(dict(u) for u in CONFIG["users"] if u["email"] == email)


MARCUS, WEIJIE, AMY = user("marcus@livesgroup.com"), user("weijie@livesgroup.com"), user("amy@livesgroup.com")
PRIYA, RA = user("priya@livesgroup.com"), user("ra.officer@livesgroup.com")
SYSTEM = em.system_user(CONFIG)


def routed(sid="S2", requester=MARCUS, extra=None, now=T0, audit=None):
    s = SCENARIOS[sid]
    req = em.create_request(s["domain"], {**s["payload"], **(extra or {})}, requester, CONFIG, now=now)
    em.transition(req, "SUBMIT", requester, CONFIG, now=now, audit_log=audit)
    em.transition(req, "ROUTE", SYSTEM, CONFIG, now=now, audit_log=audit)
    return req


def s2():
    req = routed()
    assert req["status"] == "PENDING_FINANCE" and req["routing_rule_id"] == "R03"
    return req


def s1_fixed():
    return routed("S1", AMY, SCENARIOS["S1"]["fix"])


def _strings(obj):
    return json.dumps(obj)


# ----------------------------------------------------------------------------- adaptive card
def test_card_structure_from_config():
    card = m365.build_adaptive_card(s2(), "FINANCE_APPROVER", CONFIG)
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == CONFIG["m365"]["card_version"]
    assert card["originator"] == CONFIG["m365"]["originator_id"]
    assert [a["type"] for a in card["actions"]] == ["Action.Http", "Action.Http"]
    assert all(a["method"] == "POST" and a["url"] == CONFIG["m365"]["respond_url"] for a in card["actions"])
    assert "{{approver_comment.value}}" in card["actions"][0]["body"]
    body = card["actions"][0]["body"].replace("{{approver_comment.value}}", "x")
    assert json.loads(body)["action"] == "APPROVE"
    assert "{request_id}" not in _strings(card)


def test_finance_card_shows_credit_and_terms_but_no_bank():
    req = s2()
    facts = dict(m365.role_filtered_facts(req, "FINANCE_APPROVER", CONFIG))
    assert "Credit Limit (USD)" in facts and "Payment Terms" in facts
    assert not any(label.startswith("Refund Bank") for label in facts)
    card = _strings(m365.build_adaptive_card(req, "FINANCE_APPROVER", CONFIG))
    assert "75000" in card and FULL_ACCT not in card and "6789" not in card


def test_regulatory_card_has_no_credit_terms():
    req = s1_fixed()
    assert req["status"] == "PENDING_REGULATORY"
    card = m365.build_adaptive_card(req, "REGULATORY_OFFICER", CONFIG)
    facts = dict(m365.role_filtered_facts(req, "REGULATORY_OFFICER", CONFIG))
    financial = [f["label"] for d in CONFIG["domains"].values() for f in d["fields"]
                 if f.get("security_class") == "FINANCIAL_TERMS"]
    assert not any(label in facts for label in financial)
    assert "Regulatory License No" in facts
    text = _strings(card)
    assert "85.0" not in text and "Estimated Unit Cost" not in text
    s2_card = _strings(m365.build_adaptive_card(s2(), "REGULATORY_OFFICER", CONFIG))
    assert "75000" not in s2_card and "Credit Limit" not in s2_card and "Payment Terms" not in s2_card


@pytest.mark.parametrize("status", ["PENDING_FINANCE", "APPROVED", "SYNCED_TO_SAP"])
@pytest.mark.parametrize("role", ROLES)
def test_no_card_contains_full_account(role, status):
    req = s2()
    req["status"] = status
    req["payload"]["U_ReqStatus"] = status
    assert FULL_ACCT not in _strings(m365.build_adaptive_card(req, role, CONFIG))


# ----------------------------------------------------------------------------- SLA escalations
def test_escalations_fire_exactly_at_thresholds_once_each():
    req = s2()
    db = [req]
    fired_all = []
    for rule in sorted(CONFIG["sla"]["rules"], key=lambda r: r["after_hours"]):
        h = rule["after_hours"]
        before = m365.process_sla_escalations(db, CONFIG, now=T0 + timedelta(hours=h) - timedelta(seconds=1))
        assert before == [], f"{rule['id']} fired before {h}h"
        at = m365.process_sla_escalations(db, CONFIG, now=T0 + timedelta(hours=h))
        assert [a["rule_id"] for a in at] == [rule["id"]]
        assert req[f"sla_{rule['id']}_done"] is True
        again = m365.process_sla_escalations(db, CONFIG, now=T0 + timedelta(hours=h, minutes=30))
        assert again == []
        fired_all += at
    assert [a["action"] for a in fired_all] == [r["action"] for r in
                                                sorted(CONFIG["sla"]["rules"], key=lambda r: r["after_hours"])]
    assert m365.process_sla_escalations(db, CONFIG, now=T0 + timedelta(days=18)) == []


def test_jump_to_day_18_fires_all_rules_once():
    db = [s2()]
    out = m365.process_sla_escalations(db, CONFIG, now=T0 + timedelta(days=18))
    assert [a["rule_id"] for a in out] == [r["id"] for r in sorted(CONFIG["sla"]["rules"], key=lambda r: r["after_hours"])]
    assert m365.process_sla_escalations(db, CONFIG, now=T0 + timedelta(days=19)) == []


@pytest.mark.parametrize("day,expected", sorted(SCENARIOS["S5"]["expected"]["fired_by_day"].items()))
def test_scenario5_fired_by_day(day, expected):
    db = [s2()]
    out = m365.process_sla_escalations(db, CONFIG, now=T0 + timedelta(days=int(day)))
    assert [a["action"] for a in out] == expected


def test_recipients_resolved_from_config():
    db = [s2()]
    out = {a["rule_id"]: a for a in m365.process_sla_escalations(db, CONFIG, now=T0 + timedelta(days=9))}
    finance = WEIJIE["email"]
    sup, delegate = CONFIG["sla"]["supervisor_email"], CONFIG["delegates"]["FINANCE_APPROVER"]
    assert out["R48"]["recipients"] == [finance]
    assert out["R48"]["subject"] == CONFIG["sla"]["rules"][0]["subject"].format(request_id=db[0]["request_id"])
    assert set(out["R96"]["recipients"]) == {finance, sup}
    assert "title" in out["R96"]
    assert set(out["R192"]["recipients"]) == {delegate, sup}
    assert all(a["sender"] == CONFIG["sla"]["system_sender"] for a in out.values())


def test_reassignment_changes_approver_and_keeps_clock():
    req = s2()
    pending_since, status = req["pending_since"], req["status"]
    assert req["current_approver_email"] == WEIJIE["email"]
    out = m365.process_sla_escalations([req], CONFIG, now=T0 + timedelta(hours=192))
    reassign = next(a for a in out if a["action"] == "AUTO_REASSIGN_TO_DELEGATE")
    assert req["current_approver_email"] == CONFIG["delegates"]["FINANCE_APPROVER"]
    assert reassign["reassigned_from"] == WEIJIE["email"]
    assert req["pending_since"] == pending_since and req["status"] == status
    assert req["current_approver_role"] == "FINANCE_APPROVER"
    assert req["history"][-1]["action"] == "REASSIGN"


def test_sla_ignores_non_pending_and_requires_aware_now():
    req = s2()
    em.transition(req, "APPROVE", WEIJIE, CONFIG, now=T0 + timedelta(hours=1))
    assert m365.process_sla_escalations([req], CONFIG, now=T0 + timedelta(days=30)) == []
    with pytest.raises(ValueError):
        m365.process_sla_escalations([req], CONFIG, now=datetime(2026, 10, 30))


def test_sla_actions_are_audited_without_values():
    audit = []
    req = s2()
    m365.process_sla_escalations([req], CONFIG, now=T0 + timedelta(days=18), audit_log=audit)
    assert [e["event"] for e in audit] == ["SLA_ACTION"] * len(CONFIG["sla"]["rules"])
    text = _strings(audit)
    assert FULL_ACCT not in text and "75000" not in text


def test_next_escalation():
    req = s2()
    rule, hours = m365.next_escalation(req, CONFIG, T0 + timedelta(hours=10))
    assert rule["id"] == "R48" and hours == pytest.approx(38.0)
    m365.process_sla_escalations([req], CONFIG, now=T0 + timedelta(days=9))
    assert m365.next_escalation(req, CONFIG, T0 + timedelta(days=9)) == (None, None)


# ----------------------------------------------------------------------------- digest
@pytest.mark.parametrize("role", ROLES)
def test_no_digest_contains_full_account(role):
    req = s2()
    approved = routed()
    em.transition(approved, "APPROVE", WEIJIE, CONFIG, now=T0)
    html_text = m365.render_email_digest([req, approved], role, CONFIG, now=T0 + timedelta(days=3))
    assert FULL_ACCT not in html_text


def test_digest_role_filtered():
    reqs = [s2(), s1_fixed(), routed("S4")]
    fin = m365.render_email_digest(reqs, "FINANCE_APPROVER", CONFIG)
    assert "SMU Solutions" in fin and "75000" in fin and "Lab Consumable" not in fin
    ra = m365.render_email_digest(reqs, "REGULATORY_OFFICER", CONFIG)
    assert "Lab Consumable" in ra and "HSA-IVD-2026-0881" in ra and "SMU Solutions" not in ra and "85.0" not in ra
    mdm = m365.render_email_digest(reqs, "MDM_LEAD", CONFIG)
    assert "New BU Regional Direct" in mdm and "SMU Solutions" not in mdm
    sup = m365.render_email_digest(reqs, "SUPERVISOR", CONFIG)
    assert all(r["request_id"] in sup for r in reqs)
    assert "75000" not in sup and "HSA-IVD" not in sup and "Metadata only" in sup


def test_digest_escapes_html():
    req = routed("S2", extra={"CardName": "<script>alert(1)</script> Clinic"})
    out = m365.render_email_digest([req], "FINANCE_APPROVER", CONFIG)
    assert "<script>" not in out and "&lt;script&gt;" in out


# ----------------------------------------------------------------------------- webhook
def test_webhook_approve_by_finance():
    req, audit = s2(), []
    resp = m365.simulate_webhook_response(req, WEIJIE, "APPROVE", "ok", CONFIG, audit_log=audit, now=T0)
    assert resp["ok"] and resp["status"] == "APPROVED" == req["status"]
    assert audit[-1]["event"] == "APPROVED"
    assert FULL_ACCT not in _strings(resp["refreshed_card"])


def test_webhook_rejects_wrong_role():
    req = s1_fixed()
    with pytest.raises(PermissionError):
        m365.simulate_webhook_response(req, WEIJIE, "APPROVE", None, CONFIG)
    with pytest.raises(PermissionError):
        m365.simulate_webhook_response(s2(), MARCUS, "APPROVE", None, CONFIG)
    assert req["status"] == "PENDING_REGULATORY"


def test_webhook_rejects_unknown_action_and_delegate_can_decide():
    req = s2()
    with pytest.raises(ValueError):
        m365.simulate_webhook_response(req, WEIJIE, "SYNC", None, CONFIG)
    m365.process_sla_escalations([req], CONFIG, now=T0 + timedelta(hours=192))
    delegate = es.get_user(CONFIG["delegates"]["FINANCE_APPROVER"], CONFIG)
    resp = m365.simulate_webhook_response(req, delegate, "REJECT", "dup", CONFIG)
    assert resp["status"] == "REJECTED"

# ----------------------------------------------------------------------------- the email mockups never widen what a viewer may see
def test_approval_email_mockup_data_never_carries_bank_details_or_extra_fields():
    import sys
    sys.path.insert(0, str(ROOT))
    from tabs import email_mock
    req = routed("S2", MARCUS)
    card = email_mock.approval_card(CONFIG, req, "FINANCE_APPROVER", "FINANCE_APPROVER")
    assert FULL_ACCT not in json.dumps(card) and "BankAcct" not in json.dumps(card)
    facts = {f["title"]: f["value"] for b in card["body"] for f in b.get("facts", [])}
    assert "Credit Limit (USD): 75000" in facts["Visible fields (role-filtered):"]          # Finance may see credit terms
    narrow = email_mock.approval_card(CONFIG, req, "FINANCE_APPROVER", "BUSINESS_SUPPORT")   # a viewer with less access
    shown = {f["title"]: f["value"] for b in narrow["body"] for f in b.get("facts", [])}
    assert "Credit Limit" not in shown["Visible fields (role-filtered):"]                    # sees no more than they may
    assert email_mock._when_invite("2026-10-26T09:00:00+08:00").startswith("Tue 27 Oct 2026, 10:00")
