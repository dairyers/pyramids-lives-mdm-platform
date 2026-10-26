"""A request type made on the dashboard (starter domain builder): form, validation, duplicate check, routing and the
approval path all follow from the settings, with no code change."""
import copy

import pytest

import engine_mdm as em
import engine_security as es
import engine_settings as s
import engine_workflow as wf
from fixtures import CONFIG


@pytest.fixture
def cfg():
    c = copy.deepcopy(CONFIG)
    s.add_approver_role(c, "Regional Compliance Officer", "Aiko Tan")
    s.add_request_type(c, "Project code", "A code for a customer project.")
    return c


def _rows(cfg, domain="PROJECT_CODE"):
    return s.field_rows(cfg, domain)


def test_a_blank_type_has_a_code_a_name_and_an_entity_and_its_own_table(cfg):
    dom = cfg["domains"]["PROJECT_CODE"]
    assert [f["field"] for f in dom["fields"]] == ["U_Code", "U_Name", "U_Entity"]
    assert dom["sap_table"] == "OPROJ" and dom["sap_table"] in cfg["deduplication_rules"]
    assert dom["key_prefix"] == "PRO" and dom["custom"] is True
    assert "PROJECT_CODE" in cfg["roles"]["REQUESTER"]["can_submit"]
    assert s.custom_domains(cfg) == ["PROJECT_CODE"]
    assert "PROJECT_CODE" not in CONFIG["domains"]                                  # the shipped settings are untouched


def test_the_new_form_validates_and_the_name_is_always_mandatory(cfg):
    assert em.validate_against_config("PROJECT_CODE", {"U_Name": "Alpha Rollout", "U_Entity": "SPD"}, cfg)[0]
    ok, errors = em.validate_against_config("PROJECT_CODE", {"U_Entity": "SPD"}, cfg)
    assert not ok and any("Project code Name" in e for e in errors)
    rows = _rows(cfg)
    next(r for r in rows if r["id"] == "U_Name")["required"] = False
    s.set_type_fields(cfg, "PROJECT_CODE", rows)
    assert next(f for f in cfg["domains"]["PROJECT_CODE"]["fields"] if f["field"] == "U_Name")["required"] is True


def test_add_edit_and_remove_details(cfg):
    rows = _rows(cfg)
    rows.append({"id": None, "label": "Budget (USD)", "kind": "Number", "choices": "", "required": True, "sensitivity": "Financial terms"})
    rows.append({"id": None, "label": "Phase", "kind": "Choice", "choices": "Plan; Build; Run", "required": True, "sensitivity": "General"})
    changes = s.set_type_fields(cfg, "PROJECT_CODE", rows)
    assert "Added 'Budget (USD)'" in changes and "Added 'Phase'" in changes
    fields = {f["field"]: f for f in cfg["domains"]["PROJECT_CODE"]["fields"]}
    assert fields["U_BudgetUSD"]["type"] == "float" and fields["U_BudgetUSD"]["security_class"] == "FINANCIAL_TERMS"
    assert fields["U_Phase"]["options"] == ["Plan", "Build", "Run"]
    good = {"U_Name": "Alpha Rollout", "U_Entity": "SPD", "U_BudgetUSD": "1500", "U_Phase": "Build"}
    assert em.validate_against_config("PROJECT_CODE", good, cfg)[0]
    assert not em.validate_against_config("PROJECT_CODE", {**good, "U_Phase": "Dream"}, cfg)[0]
    assert not em.validate_against_config("PROJECT_CODE", {**good, "U_BudgetUSD": "lots"}, cfg)[0]
    rows = _rows(cfg)
    rows = [r for r in rows if r["id"] != "U_Phase"]
    assert "Removed 'Phase'" in s.set_type_fields(cfg, "PROJECT_CODE", rows)
    assert s.set_type_fields(cfg, "PROJECT_CODE", _rows(cfg)) == []                 # saving unchanged rows changes nothing


def test_bad_edits_are_refused_with_every_problem_and_change_nothing(cfg):
    before = copy.deepcopy(cfg)
    rows = [r for r in _rows(cfg) if r["id"] != "U_Name"]                            # the name cannot be removed
    rows.append({"id": None, "label": "Phase", "kind": "Choice", "choices": "Only one", "required": False, "sensitivity": "General"})
    rows.append({"id": None, "label": "phase", "kind": "Text", "choices": "", "required": False, "sensitivity": "General"})
    with pytest.raises(ValueError) as e:
        s.set_type_fields(cfg, "PROJECT_CODE", rows)
    msg = str(e.value)
    assert "cannot be removed" in msg and "at least two different choices" in msg and "Two details are called" in msg
    assert cfg == before
    with pytest.raises(ValueError):
        s.set_type_fields(cfg, "CUSTOMER", s.field_rows(cfg, "CUSTOMER"))             # shipped types are not editable here


def test_names_are_checked_and_ids_stay_unique(cfg):
    before = copy.deepcopy(cfg)
    for bad in ("ab", "Project code", "customer", "x" * 41, "Item"):
        with pytest.raises(ValueError):
            s.add_request_type(cfg, bad)
    assert cfg == before
    s.add_request_type(cfg, "Project-code!")
    assert "PROJECT_CODE_2" in cfg["domains"] and cfg["domains"]["PROJECT_CODE_2"]["sap_table"] != cfg["domains"]["PROJECT_CODE"]["sap_table"]


def test_copying_an_existing_type_brings_its_details(cfg):
    s.add_request_type(cfg, "Supplier site", copy_from="VENDOR")
    dom = cfg["domains"]["SUPPLIER_SITE"]
    assert [f["field"] for f in dom["fields"]] == [f["field"] for f in CONFIG["domains"]["VENDOR"]["fields"]]
    assert "card_type" not in dom and dom["key_field"] == "CardCode" and dom["sap_table"] != "OCRD"
    assert dom["fields"] is not cfg["domains"]["VENDOR"]["fields"]


def test_a_request_of_the_new_type_goes_through_the_whole_flow(cfg):
    rows = _rows(cfg)
    rows.append({"id": None, "label": "Phase", "kind": "Choice", "choices": "Plan; Build; Run", "required": True, "sensitivity": "General"})
    s.set_type_fields(cfg, "PROJECT_CODE", rows)
    amy = es.get_user("requestor@livesgroup.com", cfg)
    payload = {"U_Name": "Alpha Rollout", "U_Entity": "SPD", "U_Phase": "Build"}
    req = em.create_request("PROJECT_CODE", payload, amy, cfg)
    em.transition(req, "SUBMIT", amy, cfg)
    em.transition(req, "ROUTE", em.system_user(cfg), cfg)
    assert req["status"] == "PENDING_MDM" and req["sap_table"] == "OPROJ" and req["record_name"] == "Alpha Rollout"
    # a second request with a near-identical name is caught against the first (pending) one
    pool = em.build_dedup_pool("PROJECT_CODE", [], [req], cfg)
    hits = em.check_fuzzy_duplicates("PROJECT_CODE", {**payload, "U_Name": "Alpha Roll-out"}, pool, cfg)
    assert hits and hits[0]["confidence_score"] >= 80
    assert not em.check_fuzzy_duplicates("PROJECT_CODE", {**payload, "U_Name": "Zebra Migration"}, pool, cfg)
    em.transition(req, "APPROVE", es.get_user("mdm.analyst@livesgroup.com", cfg), cfg)
    assert req["status"] == "APPROVED"


def test_the_rule_designer_can_route_the_new_type_and_names_it(cfg):
    rules = wf.current_rules(cfg)
    cond = {"all": [{"field": wf.DOMAIN_FIELD, "op": "eq", "value": "PROJECT_CODE"}]}
    rule = wf.make_rule(cfg, wf.new_rule_id(rules), ["PENDING_REGIONAL_COMPLIANCE_OFFICER", "PENDING_BU_HEAD"], cond)
    rules.insert(0, rule)
    assert wf.validate_rules(cfg, rules)[0] == []
    names = {"PROJECT_CODE": "Project code"}
    wf.apply_rules(cfg, rules, names)
    routed = em.route_request("PROJECT_CODE", {"U_Name": "Alpha", "U_Entity": "SPD"}, cfg)
    assert routed["route_to"] == "PENDING_REGIONAL_COMPLIANCE_OFFICER" and routed["then"] == ["PENDING_BU_HEAD"]
    assert wf.condition_text(cfg, rule["condition"], names).lower().count("project code") == 1


def test_next_code_follows_the_prefix():
    from tabs import tab_staging
    assert tab_staging.next_key([], "PRO00001") == "PRO00001"
    assert tab_staging.next_key(["PRO00001", "PRO00007"], "PRO00001") == "PRO00008"
