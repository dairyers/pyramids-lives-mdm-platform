"""Settings the IT administrator can change: each change is validated, leaves the shipped config alone, and is picked up
by the engines that read the config (validation, SLA clock, visible pages, who can raise what)."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

import engine_mdm as em
import engine_security as es
import engine_settings as s
import m365_integration as m365
from fixtures import CONFIG


@pytest.fixture
def cfg():
    return copy.deepcopy(CONFIG)


def test_mandatory_flag_is_followed_by_validation(cfg):
    payload = {"CardName": "Acme", "LicTradNum": "202600001A", "GroupCode": 100, "E_Mail": "a@acme.example",
               "CreditLine": 1000, "GroupNum": 1, "U_Entity": "SPD", "U_Country": "SG", "Address": "1 Road",
               "ZipCode": "100000", "CntctPrsn": "Alex Tan", "Phone1": "+65 6000 0000"}
    assert em.validate_against_config("CUSTOMER", payload, cfg)[0]
    changes = s.set_required(cfg, "CUSTOMER", {"Phone1": True, "CntctPrsn": False})
    assert changes
    ok, _ = em.validate_against_config("CUSTOMER", {k: v for k, v in payload.items() if k != "CntctPrsn"}, cfg)
    assert ok                                                      # contact person is now optional
    assert not em.validate_against_config("CUSTOMER", {k: v for k, v in payload.items() if k != "Phone1"}, cfg)[0]
    assert em.validate_against_config("CUSTOMER", {k: v for k, v in payload.items() if k != "CntctPrsn"}, CONFIG)[0] is False


def test_protected_fields_cannot_be_made_optional(cfg):
    dom = cfg["domains"]["CUSTOMER"]
    with pytest.raises(ValueError):
        s.set_required(cfg, "CUSTOMER", {dom["record_name_field"]: False})
    with pytest.raises(ValueError):
        s.set_required(cfg, "CUSTOMER", {"NoSuchField": True})


def test_reminder_days_move_the_sla_clock_and_text(cfg):
    assert s.reminder_days(cfg) == [2, 4, 8]
    s.set_reminder_days(cfg, [1, 3, 5])
    assert s.reminder_days(cfg) == [1, 3, 5]
    first = sorted(cfg["sla"]["rules"], key=lambda r: r["after_hours"])[0]
    assert "24 hours" in first["subject"] and "48" not in first["subject"]
    req = {"status": "PENDING_FINANCE", "pending_since": "2026-10-20T00:00:00+08:00", "request_id": "R1"}
    now = datetime(2026, 10, 21, 1, 0, tzinfo=timezone(timedelta(hours=8)))
    rule, _hours = m365.next_escalation(req, cfg, now)
    assert rule is None or rule["after_hours"] != 48


@pytest.mark.parametrize("bad", [[2, 2, 8], [4, 2, 8], [0, 4, 8], [2, 4], [2.5, 4, 8]])
def test_bad_reminder_times_are_refused_and_change_nothing(cfg, bad):
    with pytest.raises(ValueError):
        s.set_reminder_days(cfg, bad)
    assert s.reminder_days(cfg) == [2, 4, 8]


def test_person_role_and_request_types_drive_pages_and_submit(cfg):
    user = next(u for u in cfg["users"] if u["email"] == "requestor@livesgroup.com")
    s.set_person(cfg, user["email"], "AUDITOR", [])
    assert es.get_user(user["email"], cfg)["role"] == "AUDITOR"
    assert "AUDIT" in es.visible_tabs("AUDITOR", cfg)
    s.set_person(cfg, user["email"], "REQUESTER", ["ITEM"])
    assert es.get_user(user["email"], cfg)["can_submit"] == ["ITEM"]
    s.set_person(cfg, user["email"], "REQUESTER", cfg["roles"]["REQUESTER"]["can_submit"])
    assert "can_submit" not in es.get_user(user["email"], cfg)       # back to the role's default: no override kept


def test_last_it_admin_cannot_be_moved(cfg):
    admins = [u for u in cfg["users"] if u["role"] == "IT_ADMIN"]
    assert len(admins) == 1
    with pytest.raises(ValueError):
        s.set_person(cfg, admins[0]["email"], "REQUESTER", [])
    with pytest.raises(ValueError):
        s.set_person(cfg, "nobody@livesgroup.com", "REQUESTER", [])
    with pytest.raises(ValueError):
        s.set_person(cfg, admins[0]["email"], "NO_SUCH_ROLE", [])


def test_role_access_changes_the_pages_a_role_can_open(cfg):
    s.set_role_access(cfg, "REGULATORY_OFFICER", ["AUDIT"], "AGGREGATE", [])
    tabs = es.visible_tabs("REGULATORY_OFFICER", cfg)
    assert tabs == ["INBOX", "ANALYTICS", "AUDIT"] and cfg["roles"]["REGULATORY_OFFICER"]["analytics"] == "AGGREGATE"
    s.set_role_access(cfg, "REGULATORY_OFFICER", [], "NONE", ["ITEM"])
    assert es.visible_tabs("REGULATORY_OFFICER", cfg) == ["INTAKE", "INBOX"]
    with pytest.raises(ValueError):
        s.set_role_access(cfg, "IT_ADMIN", ["AUDIT"], "NONE", [])
    with pytest.raises(ValueError):
        s.set_role_access(cfg, "AUDITOR", ["NOPE"], "NONE", [])


def test_shipped_config_is_never_touched(cfg):
    before = copy.deepcopy(CONFIG)
    s.set_required(cfg, "CUSTOMER", {"Phone1": False})
    s.set_reminder_days(cfg, [1, 2, 3])
    assert CONFIG == before

def _flag(cfg, variant):
    import json, pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    pool = json.loads((root / "data" / "sap_master_mock.json").read_text(encoding="utf-8"))["OCRD"]
    sc = json.loads((root / "data" / "scenarios.json").read_text(encoding="utf-8"))["S3"]["variants"][variant]["payload"]
    return {m["sap_id"]: m["tier"] for m in em.check_fuzzy_duplicates("CUSTOMER", sc, pool, cfg)}


def test_evidence_groups_needed_changes_the_verdict(cfg):
    assert _flag(cfg, "3f").get("C10023") == "CONFIRMED"          # four groups agree (postal, contact, country, customer group)
    s.set_duplicate_rules(cfg, "OCRD", groups=5)
    assert _flag(cfg, "3f").get("C10023") != "CONFIRMED"          # five groups now needed


def test_name_match_threshold_changes_what_is_flagged(cfg):
    assert _flag(cfg, "3c").get("C10031")
    s.set_duplicate_rules(cfg, "OCRD", threshold=1.0, near_miss=0.5)
    assert s.duplicate_rules(cfg, "OCRD")["threshold"] == 1.0


@pytest.mark.parametrize("kwargs", [{"threshold": 0.4}, {"threshold": 1.2}, {"near_miss": 0.8}, {"near_miss": 0.2},
                                    {"groups": 0}, {"groups": 99}, {"groups": 1.5}, {"threshold": 0.45}])
def test_bad_duplicate_rules_are_refused_and_change_nothing(cfg, kwargs):
    before = copy.deepcopy(cfg["deduplication_rules"])
    with pytest.raises(ValueError):
        s.set_duplicate_rules(cfg, "OCRD", **kwargs)
    assert cfg["deduplication_rules"] == before


def test_threshold_cannot_drop_to_the_near_miss_line(cfg):
    with pytest.raises(ValueError):
        s.set_duplicate_rules(cfg, "OCRD", threshold=0.5)           # near-miss check is at 50%


def test_tables_without_a_near_miss_or_evidence_check_only_take_the_threshold(cfg):
    assert s.duplicate_rules(cfg, "OITM")["near_miss"] is None and s.duplicate_rules(cfg, "OITM")["groups"] is None
    assert s.set_duplicate_rules(cfg, "OITM", threshold=0.9)
    with pytest.raises(ValueError):
        s.set_duplicate_rules(cfg, "OITM", groups=2)
