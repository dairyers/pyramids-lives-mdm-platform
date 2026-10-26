"""End-to-end scenario tests (spec Section 3.2), headless: the engines are driven directly, no Streamlit.

Each test plays a scenario from data/scenarios.json the way the app does: validate -> dedup against
data/sap_master_mock.json plus open requests -> create_request -> SUBMIT -> ROUTE -> decisions, with an
injected clock and one shared audit log per scenario. Scores are never asserted exactly.
"""
import copy
import itertools
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import engine_mdm as em
import engine_security as es
import m365_integration as m365
from fixtures import (C10023, C10052, C20001, C20002, CONFIG, FIXTURE_MASTER, abc_config, candidate,
                      which_nuhs_config)
from test_mdm import EVIDENCE, NAME_TABLE

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCENARIOS = json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))
MASTER = json.loads((ROOT / "data" / "sap_master_mock.json").read_text(encoding="utf-8"))
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
ROLES = list(CONFIG["roles"])
SM = CONFIG["state_machine"]
CC = CONFIG["deduplication_rules"]["OCRD"]["corroboration"]
FULL_ACCT = SCENARIOS["S2"]["payload"]["BankAcct"]
SYSTEM = em.system_user(CONFIG)


def user(email):
    return es.get_user(email, CONFIG)


def persona(role):
    """The first configured user holding this role."""
    return user(em.approver_email_for_role(role, CONFIG))


AMY, MARCUS, PRIYA = user("amy@livesgroup.com"), user("marcus@livesgroup.com"), user("priya@livesgroup.com")
WEIJIE, PAYMENTS = user(SCENARIOS["S2"]["approver_email"]), user(SCENARIOS["S2"]["release_email"])
RA, SUPERVISOR = persona("REGULATORY_OFFICER"), persona("SUPERVISOR")


class World:
    """The session state the app keeps (requests_db, audit_log) for one scenario run."""

    def __init__(self):
        self.requests_db, self.audit_log = [], []

    def intake(self, domain, payload, requester, now=T0, answer=None, justification=None):
        """The Intake tab's path: validate, dedup (master + open requests), log the flag, submit and route."""
        ok, errors = em.validate_against_config(domain, payload, CONFIG)
        if not ok:
            em.log_event(self.audit_log, requester, "VALIDATION_BLOCKED", None, {"errors": len(errors)},
                         config=CONFIG, now=now)
            return None, errors, []
        table = CONFIG["domains"][domain]["sap_table"]
        pool = em.build_dedup_pool(domain, MASTER[table], self.requests_db, CONFIG)
        matches = em.check_fuzzy_duplicates(domain, payload, pool, CONFIG)
        if matches:
            em.log_event(self.audit_log, requester, "DUPLICATE_FLAGGED", None, {"matches": matches},
                         config=CONFIG, now=now)
        req = em.create_request(domain, payload, requester, CONFIG, now=now)
        req["dup_matches"], req["dup_requester_answer"] = matches, answer
        req["dup_override_justification"] = justification
        em.transition(req, "SUBMIT", requester, CONFIG, now=now, audit_log=self.audit_log)
        em.transition(req, "ROUTE", SYSTEM, CONFIG, now=now, audit_log=self.audit_log)
        if answer is not None:
            em.log_event(self.audit_log, requester, "DUPLICATE_REQUESTER_ANSWER", req["request_id"],
                         {"answer": answer, "matches": matches}, config=CONFIG, now=now)
        self.requests_db.append(req)
        return req, [], matches

    def act(self, req, action, actor, now=T0, comment=None):
        return em.transition(req, action, actor, CONFIG, comment=comment, now=now, audit_log=self.audit_log)

    def visible_ids(self, viewer):
        return [r["request_id"] for r in self.requests_db if es.can_view_request(viewer, r, CONFIG)]


def match_for(matches, sap_id):
    found = [m for m in matches if m["sap_id"] == sap_id]
    assert len(found) <= 1, f"{sap_id} listed more than once"
    return found[0] if found else None


def _tiers(matches):
    return sorted((m["sap_id"], m["tier"]) for m in matches)


# ============================================================================ Scenario 1
def test_s1():
    s, w = SCENARIOS["S1"], World()
    req, errors, _ = w.intake(s["domain"], s["payload"], AMY)
    assert req is None and errors == s["expected"]["errors"] == [
        "VALIDATION BLOCKED: Regulatory License No (U_RegLicenseNo) is MANDATORY for IVD items."]
    assert [e["event"] for e in w.audit_log] == ["VALIDATION_BLOCKED"]

    # "Fix and resubmit"
    req, errors, matches = w.intake(s["domain"], {**s["payload"], **s["fix"]}, AMY)
    assert errors == [] and matches == []
    assert (req["routing_rule_id"], req["status"]) == (s["expected"]["after_fix_rule_id"],
                                                       s["expected"]["after_fix_status"])
    assert req["current_approver_role"] == "REGULATORY_OFFICER"

    # Regulatory Officer: row visible, license visible, cost hidden
    view = es.request_view(RA, req, CONFIG)
    assert view["fields"]["U_RegLicenseNo"] == s["fix"]["U_RegLicenseNo"]
    assert "LastPurPrc" not in view["fields"] and view["field_policies"]["LastPurPrc"] == "HIDDEN"
    for role in s["expected"]["visible_to"]:
        assert w.visible_ids(persona(role)) == [req["request_id"]]
    for role in s["expected"]["not_visible_to"]:
        assert w.visible_ids(persona(role)) == []
    assert w.visible_ids(AMY) == [req["request_id"]] and w.visible_ids(MARCUS) == []

    with pytest.raises(PermissionError):
        w.act(req, "APPROVE", WEIJIE)
    # R04 is a chain (Appendix C): Quality/Regulatory, then the Business Unit Head, then Business Support
    w.act(req, "APPROVE", RA, now=T0 + timedelta(hours=1))
    assert req["status"] == "PENDING_BU_HEAD"
    w.act(req, "APPROVE", persona("BU_HEAD"), now=T0 + timedelta(hours=2))
    assert req["status"] == "PENDING_BUSINESS_SUPPORT"
    w.act(req, "APPROVE", persona("BUSINESS_SUPPORT"), now=T0 + timedelta(hours=3))
    assert req["status"] == "APPROVED"


# ============================================================================ Scenario 2
S2_STATES = ["PENDING_FINANCE", "APPROVED", "SYNCED_TO_SAP"]
# Spec 3.1 role walk-through: what BankAcct looks like for each role in each state.
S2_BANK = {
    "REQUESTER": {"PENDING_FINANCE": "******6789", "APPROVED": "******6789", "SYNCED_TO_SAP": "******6789"},
    "MDM_LEAD": {"PENDING_FINANCE": "******6789", "APPROVED": "******6789", "SYNCED_TO_SAP": "******6789"},
    "FINANCE_APPROVER": {"PENDING_FINANCE": "******6789", "APPROVED": "******6789", "SYNCED_TO_SAP": "******6789"},
    "PAYMENTS_OFFICER": {"PENDING_FINANCE": "******6789", "APPROVED": FULL_ACCT, "SYNCED_TO_SAP": FULL_ACCT},
    "REGULATORY_OFFICER": {"PENDING_FINANCE": None, "APPROVED": None, "SYNCED_TO_SAP": None},
    "SUPERVISOR": {"PENDING_FINANCE": None, "APPROVED": None, "SYNCED_TO_SAP": None},
    "BU_HEAD": {"PENDING_FINANCE": None, "APPROVED": None, "SYNCED_TO_SAP": None},
    "BUSINESS_SUPPORT": {"PENDING_FINANCE": None, "APPROVED": None, "SYNCED_TO_SAP": None},
    "IT_ADMIN": {"PENDING_FINANCE": None, "APPROVED": None, "SYNCED_TO_SAP": None},
    "AUDITOR": {"PENDING_FINANCE": "******6789", "APPROVED": "******6789", "SYNCED_TO_SAP": "******6789"},
}
# Row scope (spec 2.4 R5): which roles see the request row at all in each state, and how.
S2_ROW = {
    "REQUESTER": {"PENDING_FINANCE": "FIELDS", "APPROVED": "FIELDS", "SYNCED_TO_SAP": "FIELDS"},
    "MDM_LEAD": {"PENDING_FINANCE": "FIELDS", "APPROVED": "FIELDS", "SYNCED_TO_SAP": "FIELDS"},
    "FINANCE_APPROVER": {"PENDING_FINANCE": "FIELDS", "APPROVED": None, "SYNCED_TO_SAP": None},
    "PAYMENTS_OFFICER": {"PENDING_FINANCE": None, "APPROVED": "FIELDS", "SYNCED_TO_SAP": "FIELDS"},
    "REGULATORY_OFFICER": {"PENDING_FINANCE": None, "APPROVED": None, "SYNCED_TO_SAP": None},
    "SUPERVISOR": {"PENDING_FINANCE": "META", "APPROVED": "META", "SYNCED_TO_SAP": "META"},
    "BU_HEAD": {"PENDING_FINANCE": None, "APPROVED": None, "SYNCED_TO_SAP": None},
    "BUSINESS_SUPPORT": {"PENDING_FINANCE": None, "APPROVED": None, "SYNCED_TO_SAP": None},
    "IT_ADMIN": {"PENDING_FINANCE": "META", "APPROVED": "META", "SYNCED_TO_SAP": "META"},
    "AUDITOR": {"PENDING_FINANCE": "FIELDS", "APPROVED": "FIELDS", "SYNCED_TO_SAP": "FIELDS"},
}


def _viewer(role):
    return MARCUS if role == "REQUESTER" else persona(role)


def _s2_check_state(req, state):
    assert req["status"] == state
    for role in ROLES:
        view, _ = es.apply_field_security(req["payload"], "CUSTOMER", role, CONFIG, status=state)
        assert view.get("BankAcct") == S2_BANK[role][state], (role, state)
        if view.get("BankAcct") != FULL_ACCT:
            assert FULL_ACCT not in json.dumps(view), (role, state)
        email_view, report = es.apply_field_security(req["payload"], "CUSTOMER", role, CONFIG, status=state,
                                                     channel="EMAIL")
        assert "BankAcct" not in email_view and report["BankAcct"] == "HIDDEN"

        row = es.request_view(_viewer(role), req, CONFIG)
        kind = None if row is None else ("FIELDS" if "fields" in row else "META")
        assert kind == S2_ROW[role][state], (role, state)
        if kind == "META":
            assert set(row) == set(CONFIG["security"]["meta_fields"])
            assert "75000" not in json.dumps(row) and FULL_ACCT not in json.dumps(row)


def test_s2():
    s, w = SCENARIOS["S2"], World()
    assert em.validate_against_config("CUSTOMER", s["payload"], CONFIG) == (True, [])
    ab = CONFIG["deduplication_rules"]["OCRD"]["abbreviation_resolution"]
    variants, hits = em.resolve_name(s["payload"]["CardName"], ab)
    assert " ".join(variants[0]) == s["expected"]["resolved_name"] == "singapore management university solutions"
    assert [(h["token"], h["expands_to"]) for h in hits] == [("smu", ["singapore management university"])]

    req, errors, matches = w.intake("CUSTOMER", s["payload"], MARCUS)
    assert errors == [] and matches == [] and s["expected"]["flagged"] is False
    assert (req["routing_rule_id"], req["status"]) == ("R03", "PENDING_FINANCE")
    assert req["current_approver_email"] == WEIJIE["email"]

    # Wei Jie's Outlook card: credit and terms shown, bank withheld
    facts = dict(m365.role_filtered_facts(req, "FINANCE_APPROVER", CONFIG))
    assert "Credit Limit (USD)" in facts and "Payment Terms" in facts
    card = json.dumps(m365.build_adaptive_card(req, "FINANCE_APPROVER", CONFIG))
    assert "75000" in card and FULL_ACCT not in card and "6789" not in card
    assert set(em.allowed_actions(req, WEIJIE, CONFIG)) == {"APPROVE", "REJECT"}

    _s2_check_state(req, "PENDING_FINANCE")
    with pytest.raises(PermissionError):
        w.act(req, "APPROVE", PAYMENTS)
    w.act(req, "APPROVE", WEIJIE, now=T0 + timedelta(hours=3), comment="credit ok")
    _s2_check_state(req, "APPROVED")
    w.act(req, "SYNC", PRIYA, now=T0 + timedelta(hours=4))
    _s2_check_state(req, "SYNCED_TO_SAP")

    assert s["expected"]["full_bank_account_only_in_states"] == ["APPROVED", "SYNCED_TO_SAP"]
    assert [e["event"] for e in w.audit_log] == ["SUBMITTED", "ROUTED", "APPROVED", "SYNCED"]
    assert FULL_ACCT not in json.dumps(w.audit_log)


# ============================================================================ Scenario 3
S3 = SCENARIOS["S3"]
S3_TIERS = {"3a": "CONFIRMED", "3b": "CONFIRMED", "3d": "UNCONFIRMED", "3e": "CONTRADICTED", "3f": "CONFIRMED",
            "3g": "CONFIRMED", "3h": "UNCONFIRMED"}
S3_DUP_REVIEW = {"3a": True, "3b": True, "3d": True, "3e": False, "3f": True, "3g": True, "3h": True}


@pytest.mark.parametrize("vid", sorted(S3_TIERS))
def test_s3_variants(vid):
    w = World()
    payload = S3["variants"][vid]["payload"]
    req, errors, matches = w.intake("CUSTOMER", payload, MARCUS)
    assert errors == []
    m = match_for(matches, S3["existing_code"])
    assert m is not None and m["match_basis"] == "ALIAS_EXPANDED" and m["tier"] == S3_TIERS[vid]
    assert m["resolved_candidate_name"] == "national university hospital singapore"
    assert em.dup_review_required(matches, CONFIG, "CUSTOMER") is S3_DUP_REVIEW[vid]
    # dup review routes to Priya by R01; CONTRADICTED is information only, so the normal rule (R99) applies
    assert req["routing_rule_id"] == ("R01" if S3_DUP_REVIEW[vid] else "R99")
    assert req["current_approver_email"] == PRIYA["email"]
    assert w.audit_log[0]["event"] == "DUPLICATE_FLAGGED"


def test_s3_negative_control_3c():
    matches = em.check_fuzzy_duplicates("CUSTOMER", S3["variants"]["3c"]["payload"], MASTER["OCRD"], CONFIG)
    assert match_for(matches, S3["existing_code"]) is None
    nus = match_for(matches, "C10031")
    assert nus["match_basis"] == "FUZZY" and nus["tier"] == "CONFIRMED"


def test_s3_name_and_evidence_tables():
    """Spec 2.2 name table and 2.2b evidence table (the same rows as test_mdm, run here as one check)."""
    threshold = CONFIG["deduplication_rules"]["OCRD"]["similarity_threshold"] * 100
    for name, existing, flagged in NAME_TABLE:
        m = match_for(em.check_fuzzy_duplicates("CUSTOMER", candidate(name), [existing], CONFIG), existing["CardCode"])
        assert (m is not None) == flagged, f"{name} vs {existing['CardName']}"
        assert m is None or m["confidence_score"] >= threshold
    for label, payload, tier in EVIDENCE:
        m = match_for(em.check_fuzzy_duplicates("CUSTOMER", payload, FIXTURE_MASTER, CONFIG), "C10023")
        assert m is not None and (m["match_basis"], m["tier"]) == ("ALIAS_EXPANDED", tier), label


def test_s3_ambiguous_alias_fixtures():
    # "abc": two expansions, nothing decides -> AMBIGUOUS_ALIAS; Apex's email domain decides -> CONFIRMED/SUPERSEDED
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("ABC", "ap@unrelated.example", group=101), [C20001, C20002],
                                   abc_config())
    assert _tiers(ms) == [("C20001", "AMBIGUOUS_ALIAS"), ("C20002", "AMBIGUOUS_ALIAS")]
    assert em.dup_review_required(ms, CONFIG, "CUSTOMER")
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("ABC", "ap@apexbio.example", group=101), [C20001, C20002],
                                   abc_config())
    assert _tiers(ms) == [("C20001", "SUPERSEDED"), ("C20002", "CONFIRMED")]
    # "which NUHS": hospital vs health system share the email domain; the postal code decides
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", "ap@nuhs-demo.example"), [C10023, C10052],
                                   which_nuhs_config())
    assert _tiers(ms) == [("C10023", "AMBIGUOUS_ALIAS"), ("C10052", "AMBIGUOUS_ALIAS")]
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", "ap@nuhs-demo.example", ZipCode="119999"),
                                   [C10023, C10052], which_nuhs_config())
    assert _tiers(ms) == [("C10023", "CONFIRMED"), ("C10052", "SUPERSEDED")]


def test_s3_evidence_disclosure_and_audit_has_no_values():
    """3f with a phone added: labels for the requester, values for MDM Lead, nothing but IDs and tier in the log."""
    w = World()
    payload = {**S3["variants"]["3f"]["payload"], "Phone1": "6000 1234"}
    req, _, matches = w.intake("CUSTOMER", payload, MARCUS, answer="YES_SAME")
    m = match_for(matches, S3["existing_code"])
    assert m["tier"] == "CONFIRMED"
    assert m["corroboration"]["agreeing_count"] >= CC["quorum"]["min_agreeing_groups"]

    finance_view = em.evidence_for_role(m["corroboration"], "FINANCE_APPROVER", CC)
    lead_view = em.evidence_for_role(m["corroboration"], "MDM_LEAD", CC)
    assert finance_view and all("detail" not in e for e in finance_view)
    assert lead_view and all("detail" in e for e in lead_view)
    assert "Lim Mei Ling" in json.dumps(lead_view) and "Lim Mei Ling" not in json.dumps(finance_view)
    assert "Lim Mei Ling" in json.dumps(em.evidence_for_role(m["corroboration"], "REQUESTER", CC))   # requester compares too
    for role in ROLES:
        has_detail = any("detail" in e for e in em.evidence_for_role(m["corroboration"], role, CC))
        assert has_detail is (role in CC["detail_visible_roles"])

    flagged = [e for e in w.audit_log if e["event"] in ("DUPLICATE_FLAGGED", "DUPLICATE_REQUESTER_ANSWER")]
    assert len(flagged) == 2
    logged = next(x for x in flagged[0]["detail"]["matches"] if x["sap_id"] == S3["existing_code"])
    assert logged["tier"] == "CONFIRMED"
    assert set(logged["signal_ids"]) == {s["id"] for s in m["corroboration"]["signals"] + m["corroboration"]["conflicts"]}
    text = json.dumps(w.audit_log)
    for value in ("Lim Mei Ling", "Mei Ling Lim", "6000 1234", "+65 6000 1234", "119999", "Demo Medical Drive",
                  "someone@gmail.com", "198400000X"):
        assert value not in text, value

    # Priya reviews: she can reject ("use C10023") or approve the override
    assert req["status"] == "PENDING_MDM" and set(em.allowed_actions(req, PRIYA, CONFIG)) >= {"APPROVE", "REJECT"}
    w.act(req, "REJECT", PRIYA, now=T0 + timedelta(hours=2), comment="use C10023")
    assert req["status"] == "REJECTED"


def test_s3_open_request_is_in_the_dedup_pool():
    """"NUHS" is caught against SAP C10023 AND against a spelled-out request still in the queue. (Spec 2.2 resolves
    existing names without the alias maps, so a queued request typed as "NUHS" is not itself expanded.)"""
    w = World()
    first, _, _ = w.intake("CUSTOMER", {**S3["variants"]["3b"]["payload"],
                                        "CardName": "National University Hospital Singapore"}, AMY)
    _, _, matches = w.intake("CUSTOMER", {**S3["variants"]["3b"]["payload"], "LicTradNum": "202600888K"}, MARCUS)
    assert match_for(matches, S3["existing_code"]) is not None
    assert match_for(matches, first["request_id"])["match_basis"] == "ALIAS_EXPANDED"


# ============================================================================ Scenario 4
def test_s4():
    s, w = SCENARIOS["S4"], World()
    other, _, _ = w.intake("CUSTOMER", SCENARIOS["S2"]["payload"], AMY)       # someone else's request in the db
    req, errors, matches = w.intake(s["domain"], s["payload"], MARCUS)
    assert errors == [] and matches == [] and s["expected"]["valid"] and not s["expected"]["flagged"]
    assert (req["routing_rule_id"], req["status"]) == (s["expected"]["rule_id"], s["expected"]["status"])
    assert w.audit_log[-1]["event"] == "ROUTED" and w.audit_log[-1]["detail"]["rule_id"] == "R99"
    assert req["history"][-1]["action"] == "ROUTE"

    for role in s["expected"]["visible_to"]:
        assert req["request_id"] in w.visible_ids(persona(role))
    for role in s["expected"]["not_visible_to"]:
        assert req["request_id"] not in w.visible_ids(persona(role))
        assert not es.can_view_request(persona(role), req, CONFIG)
    assert w.visible_ids(MARCUS) == [req["request_id"]]
    assert other["request_id"] not in w.visible_ids(MARCUS)
    assert m365.digest_queue(w.requests_db, "MDM_LEAD", CONFIG) == [req]


# ============================================================================ Scenario 5
def test_s5():
    s, w = SCENARIOS["S5"], World()
    req, _, _ = w.intake("CUSTOMER", SCENARIOS[s["uses_scenario"]]["payload"], MARCUS)
    assert req["status"] == s["start_state"]
    pending_since = req["pending_since"]
    fired = []
    for day in s["slider_days"]:
        now = datetime.fromisoformat(pending_since) + timedelta(days=day)
        out = m365.process_sla_escalations(w.requests_db, CONFIG, now=now, audit_log=w.audit_log)
        fired += out
        assert [a["action"] for a in fired] == s["expected"]["fired_by_day"][str(day)], day
        assert m365.process_sla_escalations(w.requests_db, CONFIG, now=now, audit_log=w.audit_log) == []

    by_rule = {a["rule_id"]: a for a in fired}
    rules = {r["id"]: r for r in CONFIG["sla"]["rules"]}
    assert len(fired) == len(by_rule) == len(rules)
    for rid, a in by_rule.items():
        assert a["after_hours"] == rules[rid]["after_hours"] and a["hours_pending"] >= rules[rid]["after_hours"]
        assert req[f"sla_{rid}_done"] is True
    assert by_rule["R48"]["recipients"] == [WEIJIE["email"]]
    assert set(by_rule["R96"]["recipients"]) == {WEIJIE["email"], CONFIG["sla"]["supervisor_email"]}
    delegate = CONFIG["delegates"]["FINANCE_APPROVER"]
    assert set(by_rule["R192"]["recipients"]) == {delegate, CONFIG["sla"]["supervisor_email"]}
    assert req["current_approver_email"] == delegate and req["status"] == "PENDING_FINANCE"
    assert req["pending_since"] == pending_since

    # the injected clock: an hour before a threshold nothing fires
    fresh = World()
    r2, _, _ = fresh.intake("CUSTOMER", SCENARIOS["S2"]["payload"], AMY)
    first = min(CONFIG["sla"]["rules"], key=lambda r: r["after_hours"])
    assert m365.process_sla_escalations(fresh.requests_db, CONFIG,
                                        now=T0 + timedelta(hours=first["after_hours"] - 1)) == []

    # Supervisor's SLA board: metadata only
    row = es.request_view(SUPERVISOR, req, CONFIG)
    assert set(row) == set(CONFIG["security"]["meta_fields"])
    assert "75000" not in json.dumps(row) and FULL_ACCT not in json.dumps(row)
    sla_events = [e for e in w.audit_log if e["event"] == "SLA_ACTION"]
    assert [e["detail"]["rule_id"] for e in sla_events] == ["R48", "R96", "R192"]


# ============================================================================ validation
def test_validation():
    s1 = {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"]}
    enum_field = next(f for f in CONFIG["domains"]["ITEM"]["fields"] if f.get("type") == "enum")
    ok, errors = em.validate_against_config("ITEM", {**s1, enum_field["field"]: "NOT-A-VALID-OPTION"}, CONFIG)
    assert not ok and len(errors) == 1 and enum_field["field"] in errors[0]

    min_len = next((d, f) for d, dom in CONFIG["domains"].items() for f in dom["fields"] if f.get("min_len"))
    domain, f = min_len
    base = {"ITEM": s1, "CUSTOMER": SCENARIOS["S2"]["payload"], "COST_CENTER": SCENARIOS["S4"]["payload"]}[domain]
    ok, errors = em.validate_against_config(domain, {**base, f["field"]: "x" * (f["min_len"] - 1)}, CONFIG)
    assert not ok and any(f["field"] in e for e in errors)


# ============================================================================ security matrix
RANK = {"FULL": 2, "MASKED": 1, "HIDDEN": 0}
STATES = [SM["initial"]] + SM["pending_states"] + [s for s in ("APPROVED",) + tuple(SM["terminal"])]


def _field_of_class(cls):
    return next((dn, f["field"]) for dn, d in CONFIG["domains"].items() for f in d["fields"]
                if f.get("security_class", "GENERAL") == cls)


def _expected(role, cls, status, channel):
    sec = CONFIG["security"]
    entry = sec["classes"][cls]["policy_by_role"]
    e = entry.get(role, entry.get("*", sec["default_policy"]))
    if isinstance(e, dict):
        e = e["policy"] if status in e["only_in_states"] else e["otherwise"]
    cap = sec["channel_caps"][channel].get(cls)
    return cap if cap and RANK[cap] < RANK[e] else e


@pytest.mark.parametrize("role,cls,status,channel",
                         list(itertools.product(ROLES, CONFIG["security"]["classes"], STATES,
                                                CONFIG["security"]["channel_caps"])))
def test_security_matrix(role, cls, status, channel):
    domain, field = _field_of_class(cls)
    value = "VALUE-0123456789"
    view, report = es.apply_field_security({field: value}, domain, role, CONFIG, status=status, channel=channel)
    want = _expected(role, cls, status, channel)
    assert report[field] == want
    assert (field in view) is (want != "HIDDEN")
    assert (view.get(field) == value) is (want == "FULL")


# ============================================================================ the account number never leaks
def test_full_account_never_in_cards_digests_audit_or_email():
    """Run S1-S5 into one session, walk S2 to SYNCED_TO_SAP, and search every outbound artefact."""
    w = World()
    w.intake("ITEM", SCENARIOS["S1"]["payload"], AMY)
    w.intake("ITEM", {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"]}, AMY)
    s2, _, _ = w.intake("CUSTOMER", SCENARIOS["S2"]["payload"], MARCUS)
    stuck, _, _ = w.intake("CUSTOMER", {**SCENARIOS["S2"]["payload"], "LicTradNum": "201912346Z"}, MARCUS)
    for vid in sorted(S3_TIERS):
        w.intake("CUSTOMER", S3["variants"][vid]["payload"], MARCUS, answer="YES_SAME")
    w.intake("COST_CENTER", SCENARIOS["S4"]["payload"], MARCUS)
    m365.process_sla_escalations(w.requests_db, CONFIG, now=T0 + timedelta(days=18), audit_log=w.audit_log)

    outbound = []
    for state in S2_STATES:
        if s2["status"] != state:
            w.act(s2, {"APPROVED": "APPROVE", "SYNCED_TO_SAP": "SYNC"}[state],
                  WEIJIE if state == "APPROVED" else PRIYA, now=T0 + timedelta(days=19))
        for role in ROLES:
            outbound.append(m365.build_adaptive_card(s2, role, CONFIG))
            outbound.append(m365.render_email_digest(w.requests_db, role, CONFIG, now=T0 + timedelta(days=19)))
            for r in w.requests_db:
                outbound.append(es.apply_field_security(r["payload"], r["domain"], role, CONFIG, status=r["status"],
                                                        channel="EMAIL"))
    # the SLA actions carry cards too
    outbound.append(m365.process_sla_escalations([copy.deepcopy(stuck)], CONFIG, now=T0 + timedelta(days=30)))
    outbound.append(w.audit_log)
    assert stuck["sla_R192_done"] and len(w.audit_log) > 20
    assert FULL_ACCT not in json.dumps(outbound, default=str)

    # and the only APP view that shows it is Payments in APPROVED / SYNCED_TO_SAP
    for role, state in itertools.product(ROLES, STATES):
        view, _ = es.apply_field_security(SCENARIOS["S2"]["payload"], "CUSTOMER", role, CONFIG, status=state)
        shows = FULL_ACCT in json.dumps(view)
        assert shows is (role == SCENARIOS["S2"]["expected"]["full_bank_account_only_for"]
                         and state in SCENARIOS["S2"]["expected"]["full_bank_account_only_in_states"]), (role, state)
