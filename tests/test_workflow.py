"""The workflow designer's engine: rules edited as plain rows and chains, validated, applied, and followed by the real
routing and state machine."""
import copy
import json
import pathlib

import pytest

import engine_mdm as em
import engine_security as es
import engine_workflow as wf
from fixtures import CONFIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAMES = {k: v["name"] for k, v in json.loads((ROOT / "data" / "ui_text.json").read_text(encoding="utf-8-sig"))["domains"].items()}
IVD_ITEM = {"ItemName": "Reagent", "ItmsGrpCod": 101, "U_Category": "IVD", "InvntryUom": "Box", "U_RegLicenseNo": "HSA-1",
            "U_Entity": "SPD", "LastPurPrc": 10.0}


@pytest.fixture
def cfg():
    return copy.deepcopy(CONFIG)


def _rules(cfg):
    return wf.current_rules(cfg)


def test_every_shipped_rule_survives_the_round_trip_through_editable_rows(cfg):
    for rule in _rules(cfg):
        rows = wf.condition_rows(cfg, rule, NAMES)
        cond = rule["condition"]
        assert wf.rows_to_condition(cfg, rows, NAMES) == {"all": cond.get("all", []), **({"any": cond["any"]} if cond.get("any") else {})}


def test_shipped_rules_validate_and_apply_without_changes(cfg):
    rules = _rules(cfg)
    assert wf.validate_rules(cfg, rules) == ([], [])
    assert wf.apply_rules(cfg, rules, NAMES) == []


def test_reordering_the_chain_changes_who_approves_first(cfg):
    rules = _rules(cfg)
    r04 = next(r for r in rules if r["id"] == "R04")
    new = wf.make_rule(cfg, "R04", list(reversed(wf.chain_of(r04))), r04["condition"])
    rules = [new if r["id"] == "R04" else r for r in rules]
    assert wf.apply_rules(cfg, rules, NAMES)
    routed = em.route_request("ITEM", IVD_ITEM, cfg)
    assert routed["route_to"] == "PENDING_BUSINESS_SUPPORT" and routed["then"] == ["PENDING_BU_HEAD", "PENDING_REGULATORY"]
    assert routed["approver_role"] == "BUSINESS_SUPPORT"


def test_a_request_really_walks_the_new_chain(cfg):
    rules = _rules(cfg)
    r04 = next(r for r in rules if r["id"] == "R04")
    rules = [wf.make_rule(cfg, "R04", ["PENDING_BU_HEAD", "PENDING_REGULATORY"], r04["condition"]) if r["id"] == "R04" else r
             for r in rules]
    wf.apply_rules(cfg, rules, NAMES)
    amy = es.get_user("requestor@livesgroup.com", cfg)
    req = em.create_request("ITEM", IVD_ITEM, amy, cfg)
    em.transition(req, "SUBMIT", amy, cfg)
    em.transition(req, "ROUTE", em.system_user(cfg), cfg)
    assert req["status"] == "PENDING_BU_HEAD" and req["approval_path"] == ["PENDING_BU_HEAD", "PENDING_REGULATORY"]
    em.transition(req, "APPROVE", es.get_user("bu.approver@livesgroup.com", cfg), cfg)
    assert req["status"] == "PENDING_REGULATORY"
    em.transition(req, "APPROVE", es.get_user("regulatory.approver@livesgroup.com", cfg), cfg)
    assert req["status"] == "APPROVED"


def test_a_new_rule_for_a_business_entity_routes_there_first(cfg):
    rules = _rules(cfg)
    cond = wf.rows_to_condition(cfg, [
        {"Needs": wf.ALL, "Detail": "Request type", "Is": "is", "Value": "Customer"},
        {"Needs": wf.ALL, "Detail": "Business Entity", "Is": "is one of", "Value": "QTG, QTS"}], NAMES)
    new = wf.make_rule(cfg, wf.new_rule_id(rules), ["PENDING_BU_HEAD", "PENDING_FINANCE"], cond)
    rules.insert(len(rules) - 1, new)
    assert wf.validate_rules(cfg, wf.normalise(cfg, rules)) == ([], [])
    wf.apply_rules(cfg, rules, NAMES)
    customer = {"CardName": "X", "U_Entity": "QTG", "CreditLine": 10, "GroupNum": 1}
    routed = em.route_request("CUSTOMER", customer, cfg)
    assert routed["rule_id"] == new["id"] and routed["route_to"] == "PENDING_BU_HEAD"
    assert em.route_request("CUSTOMER", {**customer, "U_Entity": "SPD"}, cfg)["rule_id"] == "R99"


def test_a_switched_off_rule_is_skipped_by_the_real_router(cfg):
    rules = _rules(cfg)
    rules = [{**r, "enabled": False} if r["id"] == "R04" else r for r in rules]
    wf.apply_rules(cfg, rules, NAMES)
    assert em.route_request("ITEM", IVD_ITEM, cfg)["rule_id"] == "R99"


@pytest.mark.parametrize("mutate, fragment", [
    (lambda rs: [r for r in rs if r["id"] != "R99"], "everything else"),
    (lambda rs: rs[::-1], "last"),
    (lambda rs: [{**r, "enabled": False} if r["id"] == "R99" else r for r in rs], "switched off"),
    (lambda rs: rs + [copy.deepcopy(rs[0])], "same name"),
    (lambda rs: [{**r, "then": ["NOPE"]} if r["id"] == "R04" else r for r in rs], "unknown approval step"),
    (lambda rs: [{**r, "then": [r["route_to"]]} if r["id"] == "R04" else r for r in rs], "twice"),
])
def test_bad_workflows_are_refused_and_change_nothing(cfg, mutate, fragment):
    before = copy.deepcopy(cfg["routing_matrix"])
    with pytest.raises(ValueError) as e:
        wf.apply_rules(cfg, mutate(_rules(cfg)), NAMES)
    assert fragment in str(e.value)
    assert cfg["routing_matrix"] == before


def test_a_rule_hidden_behind_an_earlier_one_is_warned_about(cfg):
    rules = _rules(cfg)
    r04 = next(r for r in rules if r["id"] == "R04")
    dupe = wf.make_rule(cfg, "R05", ["PENDING_MDM"], copy.deepcopy(r04["condition"]))
    rules.insert(len(rules) - 1, dupe)
    errors, warnings = wf.validate_rules(cfg, rules)
    assert not errors and any("R05 never fires" in w for w in warnings)


@pytest.mark.parametrize("row, fragment", [
    ({"Detail": "Credit Limit (USD)", "Is": "is more than", "Value": "lots"}, "not a number"),
    ({"Detail": "Request type", "Is": "is", "Value": "Spaceship"}, "not a request type"),
    ({"Detail": "Customer Group", "Is": "is", "Value": "555"}, "not one of"),
    ({"Detail": "No such detail", "Is": "is", "Value": "x"}, "not a detail"),
    ({"Detail": "Needs MDM duplicate review", "Is": "is", "Value": "maybe"}, "Yes or No"),
    ({"Detail": "Request type", "Is": "is more than", "Value": "Customer"}, "number"),
])
def test_bad_condition_rows_say_what_is_wrong(cfg, row, fragment):
    with pytest.raises(ValueError) as e:
        wf.rows_to_condition(cfg, [{"Needs": wf.ALL, **row}], NAMES)
    assert fragment in str(e.value)


def test_route_with_tries_a_draft_without_touching_the_live_rules(cfg):
    rules = _rules(cfg)
    r04 = next(r for r in rules if r["id"] == "R04")
    draft = [wf.make_rule(cfg, "R04", ["PENDING_MDM"], r04["condition"]) if r["id"] == "R04" else r for r in rules]
    assert wf.route_with(cfg, draft, "ITEM", IVD_ITEM)["chain"] == ["PENDING_MDM"]
    assert em.route_request("ITEM", IVD_ITEM, cfg)["route_to"] == "PENDING_REGULATORY"


def test_graph_and_estimate_are_plain_text(cfg):
    dot = wf.graph_dot(cfg, _rules(cfg), NAMES, highlight="R03")
    assert dot.startswith("digraph") and "R03" in dot and "MDM Analyst" in dot
    assert "about 6 days" in wf.chain_estimate(["a", "b", "c"], 2, 2)


# ----------------------------------------------------------------------------- 'also require' rules and the simple form
def _add_finance_over(cfg, rules, limit=50000):
    cond = {"all": [{"field": wf.DOMAIN_FIELD, "op": "eq", "value": "CUSTOMER"}, {"field": "CreditLine", "op": "gt", "value": limit}]}
    extra = wf.make_rule(cfg, wf.new_rule_id(rules), ["PENDING_FINANCE"], cond, kind="add")
    return rules + [extra], extra


def test_two_rules_can_both_apply_when_one_is_an_also_require_rule(cfg):
    rules, extra = _add_finance_over(cfg, _rules(cfg))
    # a BU head route for entity QTG, plus the finance add-on for big credit lines
    qtg = wf.make_rule(cfg, "R09", ["PENDING_BU_HEAD"], {"all": [{"field": "U_Entity", "op": "eq", "value": "QTG"}]})
    rules.insert(0, qtg)
    assert wf.validate_rules(cfg, rules)[0] == []
    wf.apply_rules(cfg, rules, NAMES)
    big = {"CardName": "X", "U_Entity": "QTG", "CreditLine": 90000, "GroupNum": 1}
    routed = em.route_request("CUSTOMER", big, cfg)
    assert routed["rule_id"] == "R09" and routed["route_to"] == "PENDING_BU_HEAD"
    assert routed["then"] == ["PENDING_FINANCE"] and routed["also"] == [extra["id"]]
    small = em.route_request("CUSTOMER", {**big, "CreditLine": 100}, cfg)
    assert small["rule_id"] == "R09" and "then" not in small and "also" not in small


def test_an_also_require_rule_does_not_repeat_a_step_already_in_the_chain(cfg):
    rules, extra = _add_finance_over(cfg, _rules(cfg), limit=10)
    wf.apply_rules(cfg, rules, NAMES)
    routed = em.route_request("CUSTOMER", {"CardName": "X", "CreditLine": 99999, "GroupNum": 1}, cfg)    # R03 sends it to Finance
    assert routed["route_to"] == "PENDING_FINANCE" and "then" not in routed


def test_also_require_rules_keep_the_catch_all_last_and_validate(cfg):
    rules, _ = _add_finance_over(cfg, _rules(cfg))
    new = wf.normalise(cfg, rules)
    assert [r["id"] for r in new][-1] == "R99" and new[-1]["priority"] == 99
    assert wf.validate_rules(cfg, new) == ([], [])
    em_cfg = {**cfg, "routing_matrix": new}
    assert em.route_request("ITEM", IVD_ITEM, em_cfg)["rule_id"] == "R04"      # the add-on does not take over a route


def test_an_also_require_rule_with_no_conditions_is_warned_about(cfg):
    rules = _rules(cfg)
    rules.append(wf.make_rule(cfg, "R07", ["PENDING_BU_HEAD"], {"all": []}, kind="add"))
    errors, warnings = wf.validate_rules(cfg, rules)
    assert not errors and any("every request" in w for w in warnings)


def test_the_simple_form_round_trips_the_shipped_rules(cfg):
    for rule in _rules(cfg):
        form = wf.simple_form(cfg, rule)
        assert form is not None, rule["id"]
        again = wf.build_condition(form["domain"], form["extras"], form["any"])
        cond = rule["condition"]
        assert again == {"all": cond.get("all", []), **({"any": cond["any"]} if cond.get("any") else {})}, rule["id"]


def test_a_rule_the_form_cannot_show_is_reported_not_mangled(cfg):
    odd = {"id": "R50", "route_to": "PENDING_MDM", "condition": {"all": [{"field": "U_Entity", "op": "eq", "value": "QTG"}],
                                                                    "any": [{"field": "CreditLine", "op": "gt", "value": 1},
                                                                            {"field": "GroupNum", "op": "eq", "value": 3}]}}
    assert wf.simple_form(cfg, odd) is None
    multi = {"id": "R51", "route_to": "PENDING_MDM", "condition": {"all": [{"field": wf.DOMAIN_FIELD, "op": "in", "value": ["ITEM"]}]}}
    assert wf.simple_form(cfg, multi) is None


def test_comparisons_offered_depend_on_the_detail(cfg):
    assert wf.ops_for(cfg, "CreditLine")[:2] == ["is", "is more than"]
    assert "is one of" in wf.ops_for(cfg, "U_Entity")
    assert wf.ops_for(cfg, wf.DUP_FIELD) == ["is"]


def test_a_test_request_reports_the_add_on(cfg):
    rules, extra = _add_finance_over(cfg, _rules(cfg))
    res = wf.route_with(cfg, rules, "CUSTOMER", {"CardName": "X", "CreditLine": 90000, "GroupNum": 1, "U_Entity": "SPD"})
    assert res["also"] == [extra["id"]] or res["chain"] == ["PENDING_FINANCE"]
