"""A new kind of approver added from the dashboard: the role, the approval step, the person and the delegate are wired the
same way as the shipped ones, and a request really waits for the new approver, is reminded about, and is hidden from
what the new approver has no need to see."""
import copy
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import engine_mdm as em
import engine_security as es
import engine_settings as es_set
import engine_workflow as wf
import m365_integration as m365
from fixtures import CONFIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAMES = {k: v["name"] for k, v in json.loads((ROOT / "data" / "ui_text.json").read_text(encoding="utf-8-sig"))["domains"].items()}
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def cfg():
    c = copy.deepcopy(CONFIG)
    es_set.add_approver_role(c, "Regional Compliance Officer", "Aiko Tan")
    return c


def _qtg_rule(cfg):
    """Every QTG request must also be signed off by the new approver."""
    rules = wf.current_rules(cfg)
    add = wf.make_rule(cfg, wf.new_rule_id(rules), ["PENDING_REGIONAL_COMPLIANCE_OFFICER"],
                       {"all": [{"field": "U_Entity", "op": "eq", "value": "QTG"}]}, kind="add")
    return rules + [add]


def test_adding_an_approver_creates_every_piece(cfg):
    sm = cfg["state_machine"]
    role, state = "REGIONAL_COMPLIANCE_OFFICER", "PENDING_REGIONAL_COMPLIANCE_OFFICER"
    assert cfg["roles"][role]["label"] == "Regional Compliance Officer" and cfg["roles"][role]["can_decide_states"] == [state]
    assert state in sm["pending_states"] and sm["approver_role_by_state"][state] == role
    assert all(state in t["from"] for t in sm["transitions"] if t["action"] in ("APPROVE", "REJECT", "REASSIGN"))
    assert state in cfg["sla"]["watched_states"] and role in cfg["m365"]["digest_roles"]
    assert cfg["delegates"][role] == "regional.compliance.officer.delegate@livesgroup.com"
    aiko = es.get_user("aiko.tan@livesgroup.com", cfg)
    assert aiko["role"] == role and em.approver_email_for_role(role, cfg) == aiko["email"]
    assert es.get_user(cfg["delegates"][role], cfg)["role"] == role                    # the delegate can act too
    assert any(f["label"] == "Regional Compliance Officer" for e in cfg["demo"]["roles"] for f in e.get("functions", []))
    assert state in wf.steps(cfg)


def test_the_shipped_settings_are_untouched():
    assert "REGIONAL_COMPLIANCE_OFFICER" not in CONFIG["roles"]


def test_a_request_waits_for_the_new_approver_and_is_approved_by_them(cfg):
    rules = _qtg_rule(cfg)
    assert wf.validate_rules(cfg, rules)[0] == []
    wf.apply_rules(cfg, rules, NAMES)
    marcus = es.get_user("marcus@livesgroup.com", cfg)
    payload = {"CardName": "Orion Labs Pte Ltd", "U_Entity": "QTG", "CreditLine": 100, "GroupNum": 1}
    req = em.create_request("CUSTOMER", payload, marcus, cfg, now=T0)
    em.transition(req, "SUBMIT", marcus, cfg, now=T0)
    em.transition(req, "ROUTE", em.system_user(cfg), cfg, now=T0)
    assert req["approval_path"][-1] == "PENDING_REGIONAL_COMPLIANCE_OFFICER"
    while req["status"] != "PENDING_REGIONAL_COMPLIANCE_OFFICER":
        who = req["current_approver_email"]
        em.transition(req, "APPROVE", es.get_user(who, cfg), cfg, now=T0)
    assert req["current_approver_role"] == "REGIONAL_COMPLIANCE_OFFICER"
    with pytest.raises(Exception):                                                   # nobody else may decide it
        em.transition(req, "APPROVE", es.get_user("bu.approver@livesgroup.com", cfg), cfg, now=T0)
    em.transition(req, "APPROVE", es.get_user("aiko.tan@livesgroup.com", cfg), cfg, now=T0)
    assert req["status"] == "APPROVED"                                                 # the last step in the chain


def test_reminders_and_the_delegate_work_for_the_new_step(cfg):
    wf.apply_rules(cfg, _qtg_rule(cfg), NAMES)
    marcus = es.get_user("marcus@livesgroup.com", cfg)
    req = em.create_request("CUSTOMER", {"CardName": "Orion Labs Pte Ltd", "U_Entity": "QTG", "CreditLine": 100, "GroupNum": 1},
                            marcus, cfg, now=T0)
    em.transition(req, "SUBMIT", marcus, cfg, now=T0)
    em.transition(req, "ROUTE", em.system_user(cfg), cfg, now=T0)
    while req["status"] != "PENDING_REGIONAL_COMPLIANCE_OFFICER":
        em.transition(req, "APPROVE", es.get_user(req["current_approver_email"], cfg), cfg, now=T0)
    pending = req["pending_since"]
    assert req["current_approver_email"] == "aiko.tan@livesgroup.com"
    m365.process_sla_escalations([req], cfg, now=datetime.fromisoformat(pending) + timedelta(days=9))
    assert req["current_approver_email"] == cfg["delegates"]["REGIONAL_COMPLIANCE_OFFICER"]
    assert req["status"] == "PENDING_REGIONAL_COMPLIANCE_OFFICER"


def test_the_new_approver_does_not_see_finance_or_bank_values(cfg):
    record = {"CardName": "Orion", "CreditLine": 90000, "BankAcct": "123456789", "U_Entity": "QTG"}
    view, meta = es.apply_field_security(record, "CUSTOMER", "REGIONAL_COMPLIANCE_OFFICER", cfg)
    assert view == {"CardName": "Orion", "U_Entity": "QTG"}
    assert meta["CreditLine"] == "HIDDEN" and meta["BankAcct"] == "HIDDEN"
    assert "123456789" not in json.dumps(view, default=str)


def test_bad_names_and_emails_are_refused_and_change_nothing(cfg):
    before = copy.deepcopy(cfg)
    for args in [("Hi", "Aiko"), ("Business Unit Head", "X Y"), ("New Role", " "), ("Another Role", "Zed", "weijie@livesgroup.com"),
                 ("Another Role", "Zed", "bad-email"), ("Another Role", "Zed", "a@livesgroup.com", "a@livesgroup.com"),
                 ("Another Role", "Zed", None, "finance.delegate@livesgroup.com")]:
        with pytest.raises(ValueError):
            es_set.add_approver_role(cfg, *args)
        assert cfg == before


def test_two_steps_with_similar_names_get_different_ids(cfg):
    es_set.add_approver_role(cfg, "Regional Compliance Officer!", "Bo Lim")
    assert "REGIONAL_COMPLIANCE_OFFICER_2" in cfg["roles"] and "PENDING_REGIONAL_COMPLIANCE_OFFICER_2" in cfg["state_machine"]["pending_states"]
