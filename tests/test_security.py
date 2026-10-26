"""Tests for engine_security.py (spec Section 2.4). The matrix tests are parametrized FROM config."""
import copy
import itertools
import json
import pathlib

import pytest

import engine_security as es

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config_rules.json").read_text(encoding="utf-8"))
SCENARIOS = json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))
SEC = CFG["security"]
STATUSES = ["DRAFT", "PENDING_FINANCE", "APPROVED", "SYNCED_TO_SAP"]
ROLES = list(CFG["roles"])
CLASSES = list(SEC["classes"])
CHANNELS = list(SEC["channel_caps"])
RANK = {"FULL": 2, "MASKED": 1, "HIDDEN": 0}
FULL_ACCT = "0123456789"


def _field_of_class(cls):
    """(domain, field) of the first configured field carrying the security class."""
    for dn, d in CFG["domains"].items():
        for f in d["fields"]:
            if f.get("security_class", "GENERAL") == cls:
                return dn, f["field"]
    raise AssertionError(f"no field with class {cls}")


def _expected_policy(role, cls, status, channel):
    entry = SEC["classes"][cls]["policy_by_role"]
    e = entry.get(role, entry.get("*", SEC["default_policy"]))
    if isinstance(e, dict):
        e = e["policy"] if status in e["only_in_states"] else e["otherwise"]
    cap = SEC["channel_caps"][channel].get(cls)
    return cap if cap and RANK[cap] < RANK[e] else e


def _expected_mask(value, cls):
    style = SEC["mask_styles"][SEC["classes"][cls].get("mask_style", "redact")]
    if "keep" in style:
        k = style["keep"]
        return style["mask_char"] * len(value) if len(value) <= k else style["mask_char"] * (len(value) - k) + value[-k:]
    return style["text"]


@pytest.mark.parametrize("role,cls,status,channel", list(itertools.product(ROLES, CLASSES, STATUSES, CHANNELS)))
def test_policy_matrix_from_config(role, cls, status, channel):
    domain, field = _field_of_class(cls)
    value = "SENSITIVE-VALUE-0123456789"
    view, report = es.apply_field_security({field: value}, domain, role, CFG, status=status, channel=channel)
    want = _expected_policy(role, cls, status, channel)
    assert report[field] == want
    if want == "FULL":
        assert view[field] == value
    elif want == "MASKED":
        assert view[field] == _expected_mask(value, cls)
        assert view[field] != value
    else:
        assert field not in view


# ----------------------------------------------------------------------------- Scenario 2 (SMU Solutions)
S2 = SCENARIOS["S2"]["payload"]
MASKED_ACCT = "******6789"


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("role", ROLES)
def test_smu_solutions_bank_account_visibility(role, status):
    view, _ = es.apply_field_security(S2, "CUSTOMER", role, CFG, status=status)
    if role == "PAYMENTS_OFFICER" and status in ("APPROVED", "SYNCED_TO_SAP"):
        assert view["BankAcct"] == FULL_ACCT
    elif role in ("REQUESTER", "MDM_LEAD", "FINANCE_APPROVER", "AUDITOR", "PAYMENTS_OFFICER"):
        assert view["BankAcct"] == MASKED_ACCT
    else:
        assert role in ("REGULATORY_OFFICER", "SUPERVISOR", "BU_HEAD", "BUSINESS_SUPPORT", "IT_ADMIN")
        assert "BankAcct" not in view
    if not (role == "PAYMENTS_OFFICER" and status in ("APPROVED", "SYNCED_TO_SAP")):
        assert FULL_ACCT not in json.dumps(view)


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("role", ROLES)
def test_email_channel_never_exposes_bank_account(role, status):
    view, report = es.apply_field_security(S2, "CUSTOMER", role, CFG, status=status, channel="EMAIL")
    assert "BankAcct" not in view
    assert report["BankAcct"] == "HIDDEN"
    assert FULL_ACCT not in json.dumps(view)


def test_status_taken_from_record_when_not_passed():
    view, _ = es.apply_field_security({**S2, "U_ReqStatus": "APPROVED"}, "CUSTOMER", "PAYMENTS_OFFICER", CFG)
    assert view["BankAcct"] == FULL_ACCT
    view, _ = es.apply_field_security({**S2, "U_ReqStatus": "PENDING_FINANCE"}, "CUSTOMER", "PAYMENTS_OFFICER", CFG)
    assert view["BankAcct"] == MASKED_ACCT


@pytest.mark.parametrize("role", ROLES + ["NOT_A_ROLE"])
def test_unknown_field_hidden_for_every_role(role):
    view, report = es.apply_field_security({"U_SecretNotInConfig": "x", "CardName": "Acme"}, "CUSTOMER", role, CFG)
    assert "U_SecretNotInConfig" not in view
    assert report["U_SecretNotInConfig"] == SEC["default_policy"] == "HIDDEN"


def test_unknown_role_gets_default_policy_for_classified_fields():
    view, _ = es.apply_field_security(S2, "CUSTOMER", "NOT_A_ROLE", CFG, status="APPROVED")
    assert "BankAcct" not in view and "CreditLine" not in view


def test_system_fields_visible():
    view, _ = es.apply_field_security({"request_id": "R1", "U_Requester": "a@b.c"}, "CUSTOMER", "SUPERVISOR", CFG)
    assert view == {"request_id": "R1", "U_Requester": "a@b.c"}


def test_mask_never_reveals_short_values():
    view, _ = es.apply_field_security({"BankAcct": "1234"}, "CUSTOMER", "AUDITOR", CFG)
    assert view["BankAcct"] == "****"
    view, _ = es.apply_field_security({"BankAcct": None}, "CUSTOMER", "AUDITOR", CFG)
    assert view["BankAcct"] is None


def test_financial_terms_redacted_for_auditor_and_hidden_for_regulatory():
    view, _ = es.apply_field_security(S2, "CUSTOMER", "AUDITOR", CFG, status="APPROVED")
    assert view["CreditLine"] == SEC["mask_styles"]["redact"]["text"]
    view, _ = es.apply_field_security(S2, "CUSTOMER", "REGULATORY_OFFICER", CFG, status="APPROVED")
    assert "CreditLine" not in view and "GroupNum" not in view


# ----------------------------------------------------------------------------- row scopes
def _req(rid, domain, status, requester, payload):
    return {"request_id": rid, "domain": domain, "sap_table": CFG["domains"][domain]["sap_table"],
            "record_name": payload.get(CFG["domains"][domain]["record_name_field"]), "payload": payload,
            "status": status, "requester_email": requester, "current_approver_role": None,
            "pending_since": None, "history": []}


AMY_ITEM = _req("REQ-1", "ITEM", "PENDING_REGULATORY", "amy@livesgroup.com",
                {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"]})
MARCUS_CUST = _req("REQ-2", "CUSTOMER", "PENDING_FINANCE", "marcus@livesgroup.com", S2)
MARCUS_CC = _req("REQ-3", "COST_CENTER", "APPROVED", "marcus@livesgroup.com", SCENARIOS["S4"]["payload"])
APPROVED_CUST = {**MARCUS_CUST, "request_id": "REQ-4", "status": "APPROVED"}
ALL_REQS = [AMY_ITEM, MARCUS_CUST, MARCUS_CC, APPROVED_CUST]


def _visible(email):
    user = es.get_user(email, CFG)
    return {r["request_id"] for r in ALL_REQS if es.can_view_request(user, r, CFG)}


def test_requester_sees_only_own():
    assert _visible("amy@livesgroup.com") == {"REQ-1"}
    assert _visible("marcus@livesgroup.com") == {"REQ-2", "REQ-3", "REQ-4"}


def test_regulatory_does_not_see_finance_request():
    assert _visible("ra.officer@livesgroup.com") == {"REQ-1"}


def test_finance_sees_only_its_queue():
    assert _visible("weijie@livesgroup.com") == {"REQ-2"}


def test_payments_release_queue_only_approved_with_bank_details():
    assert _visible("ap.officer@livesgroup.com") == {"REQ-4"}


def test_mdm_lead_and_auditor_see_all():
    ids = {r["request_id"] for r in ALL_REQS}
    assert _visible("priya@livesgroup.com") == ids
    assert _visible("auditor@livesgroup.com") == ids


def test_supervisor_row_access_is_metadata_only():
    sup = es.get_user("supervisor@livesgroup.com", CFG)
    for r in ALL_REQS:
        view = es.request_view(sup, r, CFG)
        assert view is not None
        assert set(view) <= set(SEC["meta_fields"])
        assert "fields" not in view and "payload" not in view
        assert FULL_ACCT not in json.dumps(view)


def test_request_view_hides_rows_and_secures_fields():
    ra = es.get_user("ra.officer@livesgroup.com", CFG)
    assert es.request_view(ra, MARCUS_CUST, CFG) is None
    view = es.request_view(ra, AMY_ITEM, CFG)
    assert view["fields"]["U_RegLicenseNo"] == "HSA-IVD-2026-0881"
    assert "LastPurPrc" not in view["fields"]


def test_unknown_role_sees_no_rows():
    assert not es.can_view_request({"email": "x@y.z", "role": "NOT_A_ROLE"}, MARCUS_CUST, CFG)


# ----------------------------------------------------------------------------- identity, tabs, summary, lens
def test_get_user_personas_system_and_delegates():
    assert es.get_user("WEIJIE@livesgroup.com", CFG)["role"] == "FINANCE_APPROVER"
    assert es.get_user(CFG["sla"]["system_sender"], CFG)["role"] == "SYSTEM"
    for role, email in CFG["delegates"].items():
        assert es.get_user(email, CFG)["role"] == role
    with pytest.raises(KeyError):
        es.get_user("nobody@example.com", CFG)


@pytest.mark.parametrize("role", ROLES)
def test_visible_tabs(role):
    tabs = es.visible_tabs(role, CFG)
    assert tabs[: len(CFG["roles"][role]["tabs"])] == CFG["roles"][role]["tabs"]
    for t in SEC["always_visible_tabs_in_demo"]:
        assert t in tabs
    assert len(tabs) == len(set(tabs)) and all(t in CFG["tabs"] for t in tabs)


def test_visible_tabs_without_demo_mode():
    cfg = copy.deepcopy(CFG)
    cfg["meta"]["demo_mode"] = False
    assert es.visible_tabs("SUPERVISOR", cfg) == CFG["roles"]["SUPERVISOR"]["tabs"]


@pytest.mark.parametrize("role", ROLES)
def test_capability_summary_generated_from_config(role):
    bullets = es.capability_summary(role, CFG)
    assert bullets and all(isinstance(b, str) and b for b in bullets)
    text = " ".join(bullets)
    for t in es.visible_tabs(role, CFG):
        assert CFG["tabs"][t] in text


def test_capability_summary_follows_config_changes():
    cfg = copy.deepcopy(CFG)
    before = es.capability_summary("AUDITOR", cfg)
    cfg["security"]["classes"]["BANK_DETAILS"]["policy_by_role"]["AUDITOR"] = "HIDDEN"
    after = es.capability_summary("AUDITOR", cfg)
    assert before != after
    pay = " ".join(es.capability_summary("PAYMENTS_OFFICER", CFG))
    assert "approved" in pay and "synced to sap" in pay


@pytest.mark.parametrize("status", ["PENDING_FINANCE", "APPROVED"])
def test_security_lens(status):
    lens = es.security_lens(S2, "CUSTOMER", CFG, status=status)
    assert list(lens.columns) == ROLES
    assert list(lens.index) == list(S2)
    for role in ROLES:
        view, _ = es.apply_field_security(S2, "CUSTOMER", role, CFG, status=status)
        for field in S2:
            assert lens.loc[field, role] == view.get(field, es.LENS_HIDDEN)
    full = [r for r in ROLES if lens.loc["BankAcct", r] == FULL_ACCT]
    assert full == (["PAYMENTS_OFFICER"] if status == "APPROVED" else [])

# ----------------------------------------------------------------------------- who gets which pages (least privilege)
def test_role_access_is_least_privilege():
    roles = CFG["roles"]
    assert {r for r, v in roles.items() if v["row_scope"] == "ALL"} == {"MDM_LEAD", "AUDITOR"}
    assert {r for r, v in roles.items() if v["can_submit"]} == {"REQUESTER"}      # only requestors create
    assert not any("SCENARIOS" in es.visible_tabs(r, CFG) for r in roles)         # no technical lab page for anyone
    assert {r for r in roles if "ADMIN" in es.visible_tabs(r, CFG)} == {"IT_ADMIN"}
    assert {r for r in roles if "AUDIT" in es.visible_tabs(r, CFG)} == {"MDM_LEAD", "IT_ADMIN", "AUDITOR"}
    for r, v in roles.items():                               # nobody who decides can also see every request, except the MDM Analyst
        if v["can_decide_states"] and r != "MDM_LEAD":
            assert v["row_scope"] == "QUEUE"
    for r in ("REGULATORY_OFFICER", "BUSINESS_SUPPORT"):         # pure approvers get one page: their queue
        assert es.visible_tabs(r, CFG) == ["INBOX"]
    assert len({tuple(es.visible_tabs(r, CFG)) for r in roles}) >= len(roles) - 1


def test_job_title_narrows_what_a_requestor_can_raise():
    users = {u["email"]: u for u in CFG["users"]}
    assert users["amy@livesgroup.com"]["can_submit"] == ["ITEM"]
    assert users["procurement@livesgroup.com"]["can_submit"] == ["VENDOR"]
    assert set(users["marcus@livesgroup.com"]["can_submit"]) == {"CUSTOMER", "COST_CENTER"}

# ----------------------------------------------------------------------------- the role picker (generic roles only)
def test_role_picker_lists_generic_roles_and_no_named_people():
    import importlib, sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    app = importlib.import_module("app")
    users = {u["email"]: u for u in CFG["users"]}
    named = {"amy", "marcus", "weijie", "priya", "procurement", "ra.officer", "bu.head", "biz.support"}
    picker_emails = [(f or r)["email"] for r in CFG["demo"]["roles"] for f in (r.get("functions") or [None])]
    assert [r["label"] for r in CFG["demo"]["roles"]] == ["Requestor", "Approver", "MDM Analyst", "IT / System Administrator",
                                                          "Payments Officer", "Supervisor", "Internal Auditor"]
    assert not named & {e.split("@")[0] for e in picker_emails}                 # named people live under the stories
    assert {users[e]["role"] for e in picker_emails} == set(CFG["roles"])       # every engine role is reachable
    approver = next(r for r in CFG["demo"]["roles"] if r["label"] == "Approver")
    assert [users[f["email"]]["role"] for f in approver["functions"]] == [
        "FINANCE_APPROVER", "REGULATORY_OFFICER", "BU_HEAD", "BUSINESS_SUPPORT"]
    for u in CFG["users"]:                                                      # anyone, named or not, maps to a picker entry
        entry, fn = app.current_role_choice(u, CFG)
        assert entry is not None
        assert (fn is not None) == (entry["label"] == "Approver")
    assert users[CFG["demo"]["roles"][0]["email"]].get("can_submit") is None    # the generic Requestor may raise every type
