"""Tests for engine_mdm.py (spec Sections 2.1, 2.2, 2.2b, 2.3). Scores are never asserted exactly."""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import engine_mdm as em
from fixtures import (C10007, C10012, C10018, C10023, C10031, C10045, C10052, C20001, C20002, CONFIG,
                      FIXTURE_MASTER, SMU_SOLUTIONS, SMU_SOLUTIONS_PTE, abc_config, candidate, which_nuhs_config)

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCENARIOS = json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))
MASTER = json.loads((ROOT / "data" / "sap_master_mock.json").read_text(encoding="utf-8"))
CC = CONFIG["deduplication_rules"]["OCRD"]["corroboration"]
NU, GM = "ap@nuhs-demo.example", "someone@gmail.com"
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def users(email):
    return next(dict(u) for u in CONFIG["users"] if u["email"] == email)


AMY, MARCUS, PRIYA = users("amy@livesgroup.com"), users("marcus@livesgroup.com"), users("priya@livesgroup.com")
WEIJIE, RA = users("weijie@livesgroup.com"), users("ra.officer@livesgroup.com")
SYSTEM = em.system_user(CONFIG)


def match_for(matches, sap_id):
    found = [m for m in matches if m["sap_id"] == sap_id]
    assert len(found) <= 1, f"{sap_id} listed more than once"
    return found[0] if found else None


def tier_vs(pool, payload, sap_id, config=CONFIG):
    m = match_for(em.check_fuzzy_duplicates("CUSTOMER", payload, pool, config), sap_id)
    return m["tier"] if m else None


# ============================================================================ validation (2.1)
def test_scenario1_single_required_if_error():
    ok, errors = em.validate_against_config("ITEM", SCENARIOS["S1"]["payload"], CONFIG)
    assert not ok
    assert errors == ["VALIDATION BLOCKED: Regulatory License No (U_RegLicenseNo) is MANDATORY for IVD items."]


def test_scenario1_fixed_payload_is_valid():
    ok, errors = em.validate_against_config("ITEM", {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"]}, CONFIG)
    assert ok and errors == []


@pytest.mark.parametrize("sid,domain", [("S2", "CUSTOMER"), ("S4", "COST_CENTER")])
def test_scenario_payloads_valid(sid, domain):
    assert em.validate_against_config(domain, SCENARIOS[sid]["payload"], CONFIG) == (True, [])


@pytest.mark.parametrize("vid", sorted(SCENARIOS["S3"]["variants"]))
def test_scenario3_variant_payloads_valid(vid):
    assert em.validate_against_config("CUSTOMER", SCENARIOS["S3"]["variants"][vid]["payload"], CONFIG) == (True, [])


def test_enum_violation_caught():
    ok, errors = em.validate_against_config("CUSTOMER", {**SCENARIOS["S2"]["payload"], "U_Country": "JP"}, CONFIG)
    assert not ok
    assert errors == [CONFIG["messages"]["invalid_option"].format(label="Country Location", field="U_Country",
                                                                  options=["SG", "MY", "TH", "ID", "VN"])]


def test_min_len_violation_caught():
    ok, errors = em.validate_against_config("CUSTOMER", {**SCENARIOS["S2"]["payload"], "CardName": "AB"}, CONFIG)
    assert not ok
    assert errors == [CONFIG["messages"]["min_len"].format(label="Company Name", field="CardName", min_len=3)]


def test_number_regex_required_and_min_checks():
    p = {**SCENARIOS["S2"]["payload"], "CreditLine": "lots", "BankAcct": "12AB", "E_Mail": "", "ZipCode": ""}
    ok, errors = em.validate_against_config("CUSTOMER", p, CONFIG)
    assert not ok
    assert CONFIG["messages"]["not_number"].format(label="Credit Limit (USD)", field="CreditLine") in errors
    assert CONFIG["messages"]["invalid_format"].format(label="Refund Bank Account No", field="BankAcct") in errors
    assert CONFIG["messages"]["required"].format(label="Contact Email", field="E_Mail") in errors
    assert CONFIG["messages"]["required"].format(label="Postal Code", field="ZipCode") in errors   # now mandatory
    assert len(errors) == 4
    ok, errors = em.validate_against_config("ITEM", {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"],
                                                     "LastPurPrc": 0}, CONFIG)
    assert errors == [CONFIG["messages"]["min_value"].format(label="Estimated Unit Cost (USD)", field="LastPurPrc",
                                                             min=0.01)]


def test_invalid_domain():
    assert em.validate_against_config("NOPE", {}, CONFIG) == (False, ["Invalid domain specified."])


# ============================================================================ name table (2.2)
NAME_TABLE = [
    ("Nat. Uni. Hospital Singapore", C10023, True), ("NUHS", C10023, True), ("N.U.H.S.", C10023, True),
    ("NUH", C10023, True), ("Nat Univ Hosp", C10023, True), ("SMU Solutions", SMU_SOLUTIONS, True),
    ("SMU Solutions Pte Ltd", SMU_SOLUTIONS_PTE, True), ("TTSH", C10012, True), ("Tan Tock Seng Hosp", C10012, True),
    ("National University of Singapore", C10023, False), ("NUS", C10023, False), ("SMU Solutions", C10045, False),
    ("Singapore Eye Hospital", C10007, False), ("National Heart Centre Singapore", C10023, False),
    ("Tock Seng Clinic", C10012, False),
]


@pytest.mark.parametrize("name,existing,flagged", NAME_TABLE,
                         ids=[f"{n} vs {e['CardName']}" for n, e, _ in NAME_TABLE])
def test_name_table(name, existing, flagged):
    matches = em.check_fuzzy_duplicates("CUSTOMER", candidate(name), [existing], CONFIG)
    m = match_for(matches, existing["CardCode"])
    assert (m is not None) == flagged
    if m:
        assert m["confidence_score"] >= CONFIG["deduplication_rules"]["OCRD"]["similarity_threshold"] * 100


def test_nhcs_record_not_flagged_by_hospital_name():
    assert not em.check_fuzzy_duplicates("CUSTOMER", candidate("National University Hospital Singapore"), [C10018],
                                         CONFIG)


def test_alias_explanation_and_resolved_name():
    m = match_for(em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", NU), FIXTURE_MASTER, CONFIG), "C10023")
    assert m["match_basis"] == "ALIAS_EXPANDED"
    assert m["resolved_candidate_name"] == "national university hospital singapore"
    assert any(h["token"] == "nuhs" and "national university hospital singapore" in h["expands_to"]
               for h in m["alias_hits"])
    assert "C10023" in m["explanation"] and "NUHS" in m["explanation"]
    for k in ("token_set", "jaro_winkler", "confidence_score", "corroboration", "tier"):
        assert k in m


def test_dotted_initialism_and_token_map():
    ab = CONFIG["deduplication_rules"]["OCRD"]["abbreviation_resolution"]
    variants, hits = em.resolve_name("N.U.H.S.", ab)
    assert variants[0] == ["national", "university", "hospital", "singapore"]
    variants, hits = em.resolve_name("Nat Univ Hosp Pte Ltd", ab)
    assert variants == [["national", "university", "hospital"]]
    assert {h["source"] for h in hits} == {"TOKEN_MAP"}


def test_dynamic_initialisms_unioned_with_alias_map():
    ab = CONFIG["deduplication_rules"]["OCRD"]["abbreviation_resolution"]
    dyn = em.build_dynamic_aliases([C10023["CardName"], C10052["CardName"]], ab)
    assert set(dyn["nuhs"]) == {"national university hospital singapore", "national university health system"}
    assert "nuh" in dyn
    _, hits = em.resolve_name("NUHS", ab, dyn)
    assert len(hits[0]["expands_to"]) == 2 and hits[0]["source"] == "ALIAS_MAP"


def test_scenario2_smu_solutions_resolves_and_is_not_flagged():
    ab = CONFIG["deduplication_rules"]["OCRD"]["abbreviation_resolution"]
    variants, hits = em.resolve_name("SMU Solutions", ab)
    assert " ".join(variants[0]) == SCENARIOS["S2"]["expected"]["resolved_name"]
    assert hits[0]["token"] == "smu"
    assert em.check_fuzzy_duplicates("CUSTOMER", SCENARIOS["S2"]["payload"], MASTER["OCRD"], CONFIG) == []


def test_card_type_pool_filter():
    vendor = {**C10023, "CardType": "S"}
    assert em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", NU), [vendor], CONFIG) == []


def test_tax_id_exact_match_hard_blocks_even_with_different_name():
    p = candidate("Totally Different Name Ltd", "a@b.example", tax="198400000x")
    matches = em.check_fuzzy_duplicates("CUSTOMER", p, FIXTURE_MASTER, CONFIG)
    assert [(m["sap_id"], m["tier"], m["hard_block"], m["match_basis"]) for m in matches] == \
        [("C10023", "EXACT_ID", True, "EXACT_LicTradNum")]


def test_tax_id_exact_match_with_similar_name_listed_once():
    matches = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", NU, tax="198400000X"), FIXTURE_MASTER, CONFIG)
    assert [(m["sap_id"], m["tier"]) for m in matches] == [("C10023", "EXACT_ID")]


# ============================================================================ evidence table (2.2b)
EVIDENCE = [
    ("3a Nat. Uni. + corp email", candidate("Nat. Uni. Hospital Singapore", NU), "CONFIRMED"),
    ("3b NUHS + corp email", candidate("NUHS", NU), "CONFIRMED"),
    ("3d NUHS gmail SG 100", candidate("NUHS", GM), "UNCONFIRMED"),
    ("3e other company MY 102", candidate("NUHS", "buyer@healthsupplies.example", "MY", 102), "CONTRADICTED"),
    ("3f gmail + zip + contact", candidate("NUHS", GM, ZipCode="119999", CntctPrsn="Mei Ling Lim"), "CONFIRMED"),
    ("3g gmail + tax typo", candidate("NUHS", GM, tax="198400001X"), "CONFIRMED"),
    ("3h corp email MY 102", candidate("NUHS", NU, "MY", 102), "UNCONFIRMED"),
    ("phone only + weak", candidate("NUHS", GM, Phone1="6000-1234"), "CONFIRMED"),
    ("GST only + weak", candidate("NUHS", GM, U_GSTRegNo="m2 0000123 4"), "CONFIRMED"),
    ("postal only + weak", candidate("NUHS", GM, ZipCode="119999"), "CONFIRMED"),
    ("abbreviated address + weak", candidate("NUHS", GM, Address="1 demo medical dr"), "CONFIRMED"),
    ("gmail only, nothing else", candidate("NUHS", GM, country=None, group=None), "UNCONFIRMED"),
]


@pytest.mark.parametrize("label,payload,tier", EVIDENCE, ids=[e[0] for e in EVIDENCE])
def test_evidence_table(label, payload, tier):
    m = match_for(em.check_fuzzy_duplicates("CUSTOMER", payload, FIXTURE_MASTER, CONFIG), "C10023")
    assert m is not None and m["match_basis"] == "ALIAS_EXPANDED"
    assert m["tier"] == tier


def test_nus_confirmed_vs_c10031_and_no_match_vs_c10023():
    matches = em.check_fuzzy_duplicates("CUSTOMER", candidate("National University of Singapore", "x@y.example"),
                                        FIXTURE_MASTER, CONFIG)
    assert match_for(matches, "C10023") is None
    m = match_for(matches, "C10031")
    assert m["match_basis"] == "FUZZY" and m["tier"] == "CONFIRMED"


@pytest.mark.parametrize("vid", sorted(SCENARIOS["S3"]["variants"]))
def test_scenario3_variants_against_generated_master(vid):
    """Variants 3a-3h behave as specified against the full demo master (data/sap_master_mock.json)."""
    v = SCENARIOS["S3"]["variants"][vid]
    matches = em.check_fuzzy_duplicates("CUSTOMER", v["payload"], MASTER["OCRD"], CONFIG)
    for sap_id, exp in v["expected"]["matches"].items():
        m = match_for(matches, sap_id)
        assert (m is not None) == exp["flagged"]
        if m:
            assert m["match_basis"] == exp["match_basis"] and m["tier"] == exp["tier"]
    assert {m["sap_id"] for m in matches} <= set(v["expected"]["matches"])
    assert em.dup_review_required(matches, CONFIG, "CUSTOMER") == v["expected"]["dup_review_required"]


# ============================================================================ ambiguous aliases
def _tiers(matches):
    return sorted((m["sap_id"], m["tier"]) for m in matches)


def test_abc_without_deciding_evidence_is_ambiguous():
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("ABC", "ap@unrelated.example", group=101), [C20001, C20002],
                                   abc_config())
    assert _tiers(ms) == [("C20001", "AMBIGUOUS_ALIAS"), ("C20002", "AMBIGUOUS_ALIAS")]
    assert em.dup_review_required(ms, CONFIG, "CUSTOMER")


def test_abc_with_deciding_evidence_picks_winner():
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("ABC", "ap@apexbio.example", group=101), [C20001, C20002],
                                   abc_config())
    assert _tiers(ms) == [("C20001", "SUPERSEDED"), ("C20002", "CONFIRMED")]


def test_which_nuhs_with_zip_hospital_wins():
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", NU, ZipCode="119999"), [C10023, C10052],
                                   which_nuhs_config())
    assert _tiers(ms) == [("C10023", "CONFIRMED"), ("C10052", "SUPERSEDED")]
    assert match_for(ms, "C10023")["corroboration"]["agreeing_count"] == 4
    assert match_for(ms, "C10052")["corroboration"]["agreeing_count"] == 3


def test_which_nuhs_without_zip_is_ambiguous():
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", NU), [C10023, C10052], which_nuhs_config())
    assert _tiers(ms) == [("C10023", "AMBIGUOUS_ALIAS"), ("C10052", "AMBIGUOUS_ALIAS")]


def test_demo_config_still_ambiguous_when_master_holds_both():
    assert CONFIG["deduplication_rules"]["OCRD"]["abbreviation_resolution"]["alias_map"]["nuhs"] == \
        ["national university hospital singapore"]
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", NU), [C10023, C10052], CONFIG)
    assert _tiers(ms) == [("C10023", "AMBIGUOUS_ALIAS"), ("C10052", "AMBIGUOUS_ALIAS")]


# ============================================================================ quorum rules
def _corr(payload, existing=C10023, tokens=("nuhs",), cc=CC):
    return em.corroborate(payload, existing, list(tokens), cc)


def _ids(corr):
    return {s["id"] for s in corr["signals"] + corr["conflicts"]}


def test_country_and_group_alone_never_confirmed():
    c = _corr(candidate("NUHS", GM))
    assert _ids(c) == {"SAME_COUNTRY", "SAME_CUSTOMER_GROUP"}
    assert c["agreeing_count"] == 2 and c["tier"] == "UNCONFIRMED"


def test_one_strong_group_alone_unconfirmed():
    c = _corr(candidate("NUHS", NU, country=None, group=None))
    assert {s["group"] for s in c["signals"]} == {"EMAIL"} and c["tier"] == "UNCONFIRMED"


def test_postal_code_and_address_count_as_one_group():
    c = _corr(candidate("NUHS", GM, country=None, group=None, ZipCode="119 999", Address="1 Demo Medical Drive"))
    assert _ids(c) == {"POSTAL_CODE_MATCH", "ADDRESS_SIMILAR"}
    assert c["agreeing_count"] == 1 and c["tier"] == "UNCONFIRMED"


def test_email_domain_and_alias_token_count_as_one_group():
    c = _corr(candidate("NUHS", NU, country=None, group=None))
    assert _ids(c) == {"EMAIL_DOMAIN_MATCH", "EMAIL_DOMAIN_HAS_ALIAS"} and c["agreeing_count"] == 1


def test_tax_id_one_char_difference_fires_near_match():
    assert "TAX_ID_NEAR_MATCH" in _ids(_corr(candidate("NUHS", GM, tax="198400001X")))
    assert "TAX_ID_NEAR_MATCH" in _ids(_corr(candidate("NUHS", GM, tax="198400000Y")))
    assert "TAX_ID_NEAR_MATCH" in _ids(_corr(candidate("NUHS", GM, tax="194800000X")))      # one swap
    assert "TAX_ID_NEAR_MATCH" not in _ids(_corr(candidate("NUHS", GM, tax="198400011X")))  # two edits
    assert "TAX_ID_NEAR_MATCH" not in _ids(_corr(candidate("NUHS", GM, tax="198400000X")))  # identical: step 0


def test_identical_tax_id_is_hard_block_not_near_match():
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", GM, tax="198400000X"), FIXTURE_MASTER, CONFIG)
    assert match_for(ms, "C10023")["tier"] == "EXACT_ID"


def test_different_tax_id_is_not_a_contradiction():
    c = _corr(candidate("NUHS", GM, tax="ZZ12345678"))
    assert c["conflicts"] == [] and c["tier"] == "UNCONFIRMED"


def test_free_mail_domains_never_fire_email_signals():
    gmail_existing = {**C10023, "E_Mail": "nuhs.buyer@gmail.com"}
    c = _corr(candidate("NUHS", "other@gmail.com"), existing=gmail_existing)
    assert not {"EMAIL_DOMAIN_MATCH", "EMAIL_DOMAIN_HAS_ALIAS"} & _ids(c)
    cc = json.loads(json.dumps(CC))
    cc["free_email_domains"].append("nuhsmail.example")
    c = _corr(candidate("NUHS", "x@nuhsmail.example"), cc=cc)
    assert "EMAIL_DOMAIN_HAS_ALIAS" not in _ids(c)


def test_empty_optional_fields_never_fire():
    p = candidate("NUHS", GM, country=None, group=None, ZipCode="", Address="", CntctPrsn="", Phone1="",
                  U_GSTRegNo="", tax="")
    c = _corr(p)
    assert c["signals"] == [] and c["conflicts"] == [] and c["tier"] == "UNCONFIRMED"
    c = _corr(candidate("NUHS", GM, country=None, group=None, ZipCode="119999", CntctPrsn="Lim Mei Ling"),
              existing=C10031)
    assert c["signals"] == []


def test_short_values_do_not_fire_text_signals():
    assert "CONTACT_PERSON_MATCH" not in _ids(_corr(candidate("NUHS", GM, CntctPrsn="Lim")))
    assert "ADDRESS_SIMILAR" not in _ids(_corr(candidate("NUHS", GM, Address="Demo Drive")))
    assert "PHONE_MATCH" not in _ids(_corr(candidate("NUHS", GM, Phone1="1234")))


def test_contact_person_ignores_order_and_titles():
    assert "CONTACT_PERSON_MATCH" in _ids(_corr(candidate("NUHS", GM, CntctPrsn="Dr. Mei Ling Lim")))


def test_plain_fuzzy_match_takes_default_tier_without_corroboration():
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("Singapore General Hospital Pte Ltd", GM), [C10007], CONFIG)
    assert ms[0]["match_basis"] == "FUZZY" and ms[0]["tier"] == CC["default_tier_otherwise"]
    assert "corroboration" not in ms[0]


# ============================================================================ disclosure + audit
def _m3f():
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", GM, ZipCode="119999", CntctPrsn="Mei Ling Lim",
                                                         Phone1="6000 1234"), FIXTURE_MASTER, CONFIG)
    return ms, match_for(ms, "C10023")


def test_evidence_for_role_labels_only_outside_the_detail_roles():
    """The requester now sees the compared values (they cannot judge 'same organisation?' blind); other approvers
    still get labels only."""
    _, m = _m3f()
    finance = em.evidence_for_role(m["corroboration"], "FINANCE_APPROVER", CC)
    req = em.evidence_for_role(m["corroboration"], "REQUESTER", CC)
    lead = em.evidence_for_role(m["corroboration"], "MDM_LEAD", CC)
    assert finance and all("detail" not in e for e in finance)
    assert all({"id", "label", "strength"} <= set(e) for e in finance)
    assert req and all("detail" in e for e in req)
    assert lead and all("detail" in e for e in lead)
    assert any("Lim Mei Ling" in e["detail"] for e in lead)
    assert "Lim Mei Ling" not in json.dumps(finance)


def test_log_event_duplicate_stores_ids_and_tier_only():
    ms, m = _m3f()
    log = []
    entry = em.log_event(log, MARCUS, "DUPLICATE_FLAGGED", "REQ-X", {"matches": ms, "payload": candidate("NUHS")},
                         config=CONFIG)
    assert log == [entry]
    assert set(entry) == {"ts", "actor_email", "actor_role", "event", "request_id", "detail", "prev_hash", "hash"}
    dumped = json.dumps({k: v for k, v in entry.items() if k not in ("prev_hash", "hash")})    # hex hashes can hold any digits
    for value in ("Lim Mei Ling", "Mei Ling Lim", "119999", "6000", "1234", "nuhs-demo", "gmail", "198400000X",
                  "Demo Medical", "202600777K"):
        assert value not in dumped
    logged = entry["detail"]["matches"][0]
    assert logged["sap_id"] == "C10023" and logged["tier"] == "CONFIRMED"
    assert set(logged["signal_ids"]) == {s["id"] for s in m["corroboration"]["signals"]}
    assert set(logged) == {"sap_id", "tier", "signal_ids"}


def test_log_event_rejects_unknown_events_and_strips_field_values():
    with pytest.raises(ValueError):
        em.log_event([], MARCUS, "SOMETHING_ELSE", "REQ-X", {}, config=CONFIG)
    entry = em.log_event([], MARCUS, "SUBMITTED", "REQ-X", {"BankAcct": "0123456789", "note": "ok"}, config=CONFIG)
    assert entry["detail"] == {"note": "ok"}


def test_dup_review_required_for_tables_without_tiers():
    assert em.dup_review_required([], CONFIG, "ITEM") is False
    assert em.dup_review_required([{"sap_id": "X", "match_basis": "FUZZY", "hard_block": False}], CONFIG, "ITEM")
    assert not em.dup_review_required([{"sap_id": "CC-1", "tier": "EXACT_ID", "hard_block": True}], CONFIG,
                                      "COST_CENTER")


def test_scenario_items_and_cost_center_not_duplicates_in_master():
    item = {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"]}
    assert em.check_fuzzy_duplicates("ITEM", item, MASTER["OITM"], CONFIG) == []
    assert em.check_fuzzy_duplicates("COST_CENTER", SCENARIOS["S4"]["payload"], MASTER["OOCR"], CONFIG) == []
    exact = em.check_fuzzy_duplicates("COST_CENTER", {**SCENARIOS["S4"]["payload"], "PrcCode": "CC-4010"},
                                      MASTER["OOCR"], CONFIG)
    assert [(m["sap_id"], m["tier"]) for m in exact] == [("CC-4010", "EXACT_ID")]


def test_dedup_pool_includes_pending_requests():
    req = em.create_request("CUSTOMER", candidate("Brand New Clinic Group", "a@bnc.example"), MARCUS, CONFIG, now=T0)
    em.transition(req, "SUBMIT", MARCUS, CONFIG, now=T0)
    em.transition(req, "ROUTE", SYSTEM, CONFIG, now=T0)
    pool = em.build_dedup_pool("CUSTOMER", FIXTURE_MASTER, [req], CONFIG)
    ms = em.check_fuzzy_duplicates("CUSTOMER", candidate("Brand New Clinic Group", "b@other.example"), pool, CONFIG)
    assert [m["sap_id"] for m in ms] == [req["request_id"]]


# ============================================================================ routing (2.3)
def test_route_smu_solutions_r03():
    r = em.route_request("CUSTOMER", SCENARIOS["S2"]["payload"], CONFIG)
    assert (r["rule_id"], r["route_to"], r["approver_role"]) == ("R03", "PENDING_FINANCE", "FINANCE_APPROVER")


def test_route_string_enum_values_from_forms():
    p = {**SCENARIOS["S2"]["payload"], "CreditLine": "1000", "GroupNum": "3"}
    assert em.route_request("CUSTOMER", p, CONFIG)["rule_id"] == "R03"
    p = {**SCENARIOS["S2"]["payload"], "CreditLine": "1000", "GroupNum": "1"}
    assert em.route_request("CUSTOMER", p, CONFIG)["rule_id"] == "R99"


def test_route_ivd_item_r04():
    p = {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"]}
    r = em.route_request("ITEM", p, CONFIG)
    assert (r["rule_id"], r["route_to"]) == ("R04", "PENDING_REGULATORY")


def test_route_cost_center_r99():
    r = em.route_request("COST_CENTER", SCENARIOS["S4"]["payload"], CONFIG)
    assert (r["rule_id"], r["route_to"], r["approver_role"]) == ("R99", "PENDING_MDM", "MDM_LEAD")


def test_route_dup_review_r01():
    r = em.route_request("CUSTOMER", SCENARIOS["S2"]["payload"], CONFIG, dup_review=True)
    assert (r["rule_id"], r["route_to"]) == ("R01", "PENDING_MDM")


def test_route_vendor_r02_and_malformed_number_never_matches():
    assert em.route_request("VENDOR", {}, CONFIG)["rule_id"] == "R02"
    p = {**SCENARIOS["S2"]["payload"], "CreditLine": "lots", "GroupNum": 1}
    assert em.route_request("CUSTOMER", p, CONFIG)["rule_id"] == "R99"


# ============================================================================ state machine
def _submitted(domain, payload, user, dup_matches=None, now=T0, audit=None):
    req = em.create_request(domain, payload, user, CONFIG, now=now)
    req["dup_matches"] = dup_matches or []
    em.transition(req, "SUBMIT", user, CONFIG, now=now, audit_log=audit)
    em.transition(req, "ROUTE", SYSTEM, CONFIG, now=now, audit_log=audit)
    return req


def test_create_request_contract():
    req = em.create_request("CUSTOMER", {**SCENARIOS["S2"]["payload"], "_dup_review": True}, MARCUS, CONFIG, now=T0)
    for k in ("request_id", "domain", "sap_table", "record_name", "payload", "status", "requester_email",
              "current_approver_role", "current_approver_email", "created_at", "pending_since", "last_updated_at",
              "routing_rule_id", "dup_matches", "dup_requester_answer", "dup_override_justification",
              "sla_R48_done", "sla_R96_done", "sla_R192_done", "history"):
        assert k in req
    assert req["status"] == CONFIG["state_machine"]["initial"] and req["pending_since"] is None
    assert req["record_name"] == "SMU Solutions" and "_dup_review" not in req["payload"]
    assert datetime.fromisoformat(req["created_at"]).tzinfo is not None


def test_create_request_requires_can_submit():
    with pytest.raises(PermissionError):
        em.create_request("CUSTOMER", SCENARIOS["S2"]["payload"], WEIJIE, CONFIG)


def test_full_flow_scenario2_with_audit():
    audit = []
    req = _submitted("CUSTOMER", SCENARIOS["S2"]["payload"], MARCUS, audit=audit)
    assert req["status"] == "PENDING_FINANCE" and req["routing_rule_id"] == "R03"
    assert req["current_approver_email"] == "weijie@livesgroup.com"
    ps = datetime.fromisoformat(req["pending_since"])
    assert ps.tzinfo is not None and ps == T0
    em.transition(req, "APPROVE", WEIJIE, CONFIG, comment="ok", now=T0 + timedelta(hours=5), audit_log=audit)
    assert req["status"] == "APPROVED" and req["payload"]["U_ReqStatus"] == "APPROVED"
    em.transition(req, "SYNC", PRIYA, CONFIG, now=T0 + timedelta(hours=6), audit_log=audit)
    assert req["status"] == "SYNCED_TO_SAP"
    assert [h["action"] for h in req["history"]] == ["SUBMIT", "ROUTE", "APPROVE", "SYNC"]
    assert [a["event"] for a in audit] == ["SUBMITTED", "ROUTED", "APPROVED", "SYNCED"]
    assert audit[1]["detail"]["rule_id"] == "R03"
    assert "0123456789" not in json.dumps(audit)


def test_finance_cannot_approve_pending_regulatory():
    req = _submitted("ITEM", {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"]}, AMY)
    assert req["status"] == "PENDING_REGULATORY" and req["routing_rule_id"] == "R04"
    with pytest.raises(PermissionError):
        em.transition(req, "APPROVE", WEIJIE, CONFIG)
    with pytest.raises(PermissionError):
        em.transition(req, "APPROVE", AMY, CONFIG)
    em.transition(req, "APPROVE", RA, CONFIG)
    assert req["status"] == "PENDING_BU_HEAD"           # the chain continues: it is not approved by one signature


def test_invalid_action_for_state_and_submit_by_other_user():
    req = em.create_request("COST_CENTER", SCENARIOS["S4"]["payload"], MARCUS, CONFIG)
    with pytest.raises(ValueError):
        em.transition(req, "APPROVE", PRIYA, CONFIG)
    with pytest.raises(PermissionError):
        em.transition(req, "SUBMIT", AMY, CONFIG)
    em.transition(req, "SUBMIT", MARCUS, CONFIG)
    with pytest.raises(PermissionError):
        em.transition(req, "ROUTE", MARCUS, CONFIG)


def test_dup_matches_route_to_mdm_r01():
    matches = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", GM), FIXTURE_MASTER, CONFIG)
    req = _submitted("CUSTOMER", candidate("NUHS", GM), MARCUS, dup_matches=matches)
    assert (req["routing_rule_id"], req["status"]) == ("R01", "PENDING_MDM")
    contradicted = em.check_fuzzy_duplicates("CUSTOMER", candidate("NUHS", "b@hs.example", "MY", 102),
                                             FIXTURE_MASTER, CONFIG)
    req = _submitted("CUSTOMER", candidate("NUHS", "b@hs.example", "MY", 102), MARCUS, dup_matches=contradicted)
    assert req["routing_rule_id"] == "R99"


def test_editing_does_not_reset_pending_since():
    req = _submitted("COST_CENTER", SCENARIOS["S4"]["payload"], MARCUS)
    assert (req["routing_rule_id"], req["status"]) == ("R99", "PENDING_MDM")
    before = req["pending_since"]
    em.update_request(req, {"PrcName": "New BU Regional Direct Sales"}, MARCUS, CONFIG, now=T0 + timedelta(days=3))
    em.transition(req, "REASSIGN", PRIYA, CONFIG, comment="cover", now=T0 + timedelta(days=4))
    assert req["pending_since"] == before
    assert req["record_name"] == "New BU Regional Direct Sales"
    assert req["last_updated_at"] != before
    assert [h["action"] for h in req["history"]][-2:] == ["EDIT", "REASSIGN"]


def test_edit_permissions():
    req = _submitted("COST_CENTER", SCENARIOS["S4"]["payload"], MARCUS)
    with pytest.raises(PermissionError):
        em.update_request(req, {"PrcName": "x"}, AMY, CONFIG)
    em.update_request(req, {"PrcName": "Fixed name"}, PRIYA, CONFIG)


def test_allowed_actions():
    req = _submitted("CUSTOMER", SCENARIOS["S2"]["payload"], MARCUS)
    assert set(em.allowed_actions(req, WEIJIE, CONFIG)) == {"APPROVE", "REJECT"}
    assert em.allowed_actions(req, MARCUS, CONFIG) == []

# ----------------------------------------------------------------------------- automatic reconciliation of near-misses
def _customers():
    return [r for r in MASTER["OCRD"] if r.get("CardType") == "C"]


def test_related_smu_solutions_is_reconciled_automatically_but_not_flagged():
    payload = SCENARIOS["S2"]["payload"]
    flagged = em.check_fuzzy_duplicates("CUSTOMER", payload, _customers(), CONFIG)
    assert flagged == []                                               # spec: not a duplicate of the university
    related = em.find_related_records("CUSTOMER", payload, _customers(), CONFIG, {m["sap_id"] for m in flagged})
    assert [r["sap_id"] for r in related] == ["C10045"]
    assert related[0]["confidence_score"] < CONFIG["deduplication_rules"]["OCRD"]["similarity_threshold"] * 100
    assert related[0]["verdict"]
    assert "EMAIL_DOMAIN_HAS_ALIAS" in [s["id"] for s in related[0]["corroboration"]["signals"]]


def test_related_ignores_records_no_abbreviation_points_to():
    for v in SCENARIOS["S3"]["variants"].values():
        flagged = em.check_fuzzy_duplicates("CUSTOMER", v["payload"], _customers(), CONFIG)
        assert em.find_related_records("CUSTOMER", v["payload"], _customers(), CONFIG, {m["sap_id"] for m in flagged}) == []


def test_related_respects_config_switch_and_edited_name():
    payload = SCENARIOS["S2"]["payload"]
    off = json.loads(json.dumps(CONFIG))
    off["deduplication_rules"]["OCRD"]["related_review"]["enabled"] = False
    assert em.find_related_records("CUSTOMER", payload, _customers(), off) == []
    # the already-expanded name the review screen pre-fills still reconciles (the typed abbreviation is remembered)
    expanded = em.find_related_records("CUSTOMER", payload, _customers(), CONFIG, compare_name="singapore management university solutions")
    assert [r["sap_id"] for r in expanded] == ["C10045"]
    # the requester edits the name the check uses: an unrelated name finds nothing to reconcile
    assert em.find_related_records("CUSTOMER", payload, _customers(), CONFIG, compare_name="Acme Trading") == []

# ----------------------------------------------------------------------------- mandatory contact details
MANDATORY_CONTACT = [("Address", "Street Address"), ("ZipCode", "Postal Code"), ("CntctPrsn", "Contact Person"),
                     ("Phone1", "Phone")]


@pytest.mark.parametrize("domain,base", [("CUSTOMER", SCENARIOS["S2"]["payload"]),
                                         ("VENDOR", {"CardName": "Acme Supplies", "LicTradNum": "201800123K", "BankCode": "Example Bank",
                                                     "BankAcct": "0123456789", "SwiftCode": "EXMPSGSG", "GroupNum": 1, "U_Entity": "SPD",
                                                     "Address": "5 Supply Road", "ZipCode": "118888", "CntctPrsn": "Bo Lim",
                                                     "Phone1": "+65 6000 9999"})])
def test_address_postal_contact_phone_are_mandatory(domain, base):
    assert em.validate_against_config(domain, base, CONFIG) == (True, [])
    for field, label in MANDATORY_CONTACT:
        ok, errors = em.validate_against_config(domain, {k: v for k, v in base.items() if k != field}, CONFIG)
        assert not ok
        assert CONFIG["messages"]["required"].format(label=label, field=field) in errors


def test_gst_is_the_tax_registration_number_not_a_second_form_field():
    fields = {f["field"]: f for f in CONFIG["domains"]["CUSTOMER"]["fields"]}
    assert fields["LicTradNum"]["label"] == "Tax / GST Registration No"
    assert fields["U_GSTRegNo"].get("form_hidden") and fields["U_GSTRegNo"]["same_as"] == "LicTradNum"

# ----------------------------------------------------------------------------- approval chain (Appendix C)
def _user_by_role(role):
    return next(dict(u) for u in CONFIG["users"] if u["role"] == role)


def test_regulated_item_follows_the_approval_chain_in_order_and_restarts_the_clock():
    req = _submitted("ITEM", {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"]}, AMY)
    assert req["approval_path"] == ["PENDING_REGULATORY", "PENDING_BU_HEAD", "PENDING_BUSINESS_SUPPORT"]
    t0 = datetime.fromisoformat(req["pending_since"])
    for role, expect, hours in [("REGULATORY_OFFICER", "PENDING_BU_HEAD", 30), ("BU_HEAD", "PENDING_BUSINESS_SUPPORT", 60),
                                ("BUSINESS_SUPPORT", "APPROVED", 70)]:
        stamp = t0 + timedelta(hours=hours)
        with pytest.raises(PermissionError):
            em.transition(req, "APPROVE", AMY, CONFIG, now=stamp)
        em.transition(req, "APPROVE", _user_by_role(role), CONFIG, now=stamp)
        assert req["status"] == expect
        if expect != "APPROVED":                                  # the next approver's clock starts when it reaches them
            assert datetime.fromisoformat(req["pending_since"]) == stamp.astimezone(datetime.fromisoformat(req["pending_since"]).tzinfo)
            assert req["current_approver_email"] == em.approver_email_for_role(CONFIG["state_machine"]["approver_role_by_state"][expect], CONFIG)
    assert req["current_approver_role"] is None


def test_chain_rejection_stops_the_request_and_unrelated_routes_have_no_chain():
    req = _submitted("ITEM", {**SCENARIOS["S1"]["payload"], **SCENARIOS["S1"]["fix"]}, AMY)
    em.transition(req, "APPROVE", _user_by_role("REGULATORY_OFFICER"), CONFIG)
    em.transition(req, "REJECT", _user_by_role("BU_HEAD"), CONFIG, comment="Not for this entity")
    assert req["status"] == "REJECTED"
    cc = _submitted("COST_CENTER", SCENARIOS["S4"]["payload"], MARCUS)
    assert cc["approval_path"] == ["PENDING_MDM"] and cc["remaining_chain"] == []

def test_only_requestors_can_create_or_submit_requests():
    payload = SCENARIOS["S4"]["payload"]
    with pytest.raises(PermissionError):
        em.create_request("COST_CENTER", payload, PRIYA, CONFIG)         # the MDM Analyst reviews, never raises
    req = em.create_request("COST_CENTER", payload, MARCUS, CONFIG)
    with pytest.raises(PermissionError):
        em.transition(req, "SUBMIT", PRIYA, CONFIG)
    em.transition(req, "SUBMIT", MARCUS, CONFIG)
    assert req["status"] == "SUBMITTED"
