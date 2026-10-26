"""The hands-free demo script: every story builds, nothing is pre-filled (each field is keyed in by an action), roles are
switched through the real role switch, every page opened is one the role may see, the delays are the agreed ones and every
'backed by test' pointer names a test that exists."""
import json
import re
from pathlib import Path

import pytest

import engine_security as es
from fixtures import CONFIG
from tabs import autopilot, common, tab_intake

ROOT = Path(__file__).resolve().parent.parent
PAGE_TAB = {"home": None, "new": "INTAKE", "requests": "INBOX", "insights": "ANALYTICS", "waiting": "SLA_BOARD",
            "audit": "AUDIT", "admin": "ADMIN"}
SIDS = autopilot.story_ids()


def script(sids=None):
    return autopilot.build(CONFIG, sids or SIDS, 1)


def ops_of(segment):
    return [op for step in segment["steps"] for op in step["ops"]]


def test_every_story_builds_with_steps_and_ops():
    data = script()
    assert [len(s["steps"]) > 0 for s in data["segments"]] == [True] * len(SIDS)
    assert all(step["ops"] for s in data["segments"] for step in s["steps"])


def test_the_agreed_delays():
    t = script(["S1"])["timing"]
    assert (t["scroll"], t["click"], t["type"]) == (500, 200, 200)


def test_nothing_is_prefilled_each_field_is_keyed_in():
    ops = {op["op"] for s in script()["segments"] for op in ops_of(s)}
    assert ops <= {"role", "nav", "flag", "type", "select", "click", "radio", "expand", "collapse", "scroll", "wait", "test", "reset", "compare", "look", "json", "reveal"}


@pytest.mark.parametrize("sid,domain,source", [("S1", "ITEM", "S1"), ("S2", "CUSTOMER", "S2"), ("S4", "COST_CENTER", "S4")])
def test_every_form_field_in_the_payload_is_typed_or_chosen(sid, domain, source):
    payload = {**common.load_scenarios()[source]["payload"], **common.load_scenarios()[source].get("fix", {})}
    shown = {f["field"] for group in tab_intake.field_groups(CONFIG, domain) for f in group}
    wanted = {k for k, v in payload.items() if k in shown and v not in (None, "")}
    prefix = f"in_{domain}_"
    keyed = {op["t"]["keyp"][len(prefix):-1] for op in ops_of(script([sid])["segments"][0])
             if op["op"] in ("type", "select") and op["t"].get("keyp", "").startswith(prefix)}
    assert wanted <= keyed, f"not keyed in: {wanted - keyed}"


def test_the_blank_licence_is_left_blank_first_then_added():
    ops = [op for op in ops_of(script(["S1"])["segments"][0]) if op["op"] == "type" and "U_RegLicenseNo" in op["t"]["keyp"]]
    assert len(ops) == 1 and ops[0]["value"] == "HSA-IVD-2026-0881"
    first_submit = next(i for i, op in enumerate(ops_of(script(["S1"])["segments"][0])) if op["op"] == "click" and op["t"].get("key") == "intake_submit")
    typed_before = [op for op in ops_of(script(["S1"])["segments"][0])[:first_submit] if op["op"] == "type" and "U_RegLicenseNo" in op["t"]["keyp"]]
    assert typed_before == []


def test_roles_are_switched_through_the_role_switch_entries():
    entries = {e["label"]: [f["label"] for f in e.get("functions") or []] for e in CONFIG["demo"]["roles"]}
    created = {"Regional Compliance Officer"}                            # created on screen by the IT administrator tour
    for seg in script()["segments"]:
        for op in ops_of(seg):
            if op["op"] == "role":
                assert op["label"] in entries
                assert op["fn"] is None or op["fn"] in entries[op["label"]] or op["fn"] in created


def test_requesters_use_the_requestor_role():
    pb = autopilot.load()
    for email in ("amy@livesgroup.com", "marcus@livesgroup.com"):
        assert autopilot.role_choice(CONFIG, pb, email)["label"] == "Requestor"


def test_every_page_opened_is_visible_to_that_role():
    data = script()
    for seg in data["segments"]:
        for step in seg["steps"]:
            role = None
            for op in step["ops"]:
                if op["op"] == "role":
                    entry = next(e for e in CONFIG["demo"]["roles"] if e["label"] == op["label"])
                    fn = next((f for f in entry.get("functions", []) if f["label"] == op["fn"]), None)
                    if op["fn"] and fn is None:                      # a role the story itself creates: pages of a new approver
                        role = "NEW_APPROVER"
                        continue
                    role = next(u["role"] for u in CONFIG["users"] if u["email"] == (fn or entry)["email"])
                if op["op"] == "nav" and PAGE_TAB[op["path"]]:
                    allowed = ["INBOX"] if role == "NEW_APPROVER" else es.visible_tabs(role, CONFIG)
                    assert PAGE_TAB[op["path"]] in allowed, (seg["title"], op["path"], role)


def test_every_backed_by_test_exists():
    text = json.dumps(autopilot.load())
    refs = set(re.findall(r"tests/(test_\w+\.py)::(test_\w+)", text))
    assert refs
    for file, name in refs:
        assert re.search(rf"^def {name}\b", (ROOT / "tests" / file).read_text(encoding="utf-8"), re.M), f"{file}::{name}"


def test_every_security_check_shown_in_the_demo_passes_in_the_engine():
    checks = [op for s in script()["segments"] for op in ops_of(s) if op["op"] == "flag" and op["kind"] == "test"]
    assert len(checks) >= 8
    assert [c["text"] for c in checks if not c["pass"]] == []


def kinds(sid):
    return [op["op"] for op in ops_of(script([sid])["segments"][0])]


def test_similar_name_flags_are_never_skipped_the_comparison_is_shown_before_answering():
    for sid in ("S2", "S3", "S5"):
        ops = kinds(sid)
        assert "compare" in ops, sid
        assert ops.index("compare") < ops.index("click", ops.index("compare")), sid


def test_the_18_day_story_shows_each_email_and_explains_its_json():
    ops = ops_of(script(["S5"])["segments"][0])
    expanded = [op["text"] for op in ops if op["op"] == "expand" and op["text"].startswith("After")]
    assert expanded == ["After 2 days", "After 4 days", "After 8 days"]
    assert sum(op["op"] == "json" for op in ops) == 3


def test_the_sap_mapping_and_the_outlook_card_json_are_explained():
    s2 = ops_of(script(["S2"])["segments"][0])
    assert any(op["op"] == "json" and any(i.get("key") == "CardName" for i in op["items"]) for op in s2)
    s1 = ops_of(script(["S1"])["segments"][0])
    assert any(op["op"] == "json" and any(i.get("has") == "Action.Http" for i in op["items"]) for op in s1)


class _State(dict):
    """A stand-in for st.session_state: item and attribute access."""
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def test_a_story_starts_from_nothing_but_a_role_tour_starts_with_sample_requests():
    story, tour = _State(), _State()
    autopilot.launch(story, CONFIG, ["S1"])
    autopilot.launch(tour, CONFIG, ["R_AUD"])
    assert story.get("requests_db", []) == []
    assert len(tour["requests_db"]) >= 6 and tour["audit_log"]
    assert any(r["domain"] == "CUSTOMER" and r["payload"].get("CreditLine") for r in tour["requests_db"])


def test_the_sales_insights_story_drills_the_map_into_malaysia_and_back():
    ops = ops_of(script(["S9"])["segments"][0])
    picks = [op for op in ops if op["op"] == "select" and op["t"].get("key") == "an_f_country"]
    assert [p["value"] for p in picks] == ["Malaysia"]
    clears = [op for op in ops if op["op"] == "click" and "Remove Malaysia" in op["t"].get("css", "")]
    assert len(clears) == 1 and ops.index(clears[0]) > ops.index(picks[0])   # the filter is cleared again afterwards


def test_the_player_has_a_progress_bar_with_back_and_next():
    js = (ROOT / "tabs" / "autopilot.js").read_text(encoding="utf-8")
    for needle in ('class="bar"', 'class="nav back"', 'class="nav ff"', 'kind: "back"', 'kind: "seek"', "state.speed"):
        assert needle in js, needle
    assert 'class="nav next"' not in js                                      # fast-forward replaced the skip button
    # a replay never repeats a step that changes data
    assert "changesData" in js and "replay && changesData" in js


def test_role_tours_keep_the_data_the_stories_created():
    data = script()
    tours = [s for s in data["segments"] if s["title"].startswith("Role tour")]
    assert tours and all(not any(op["op"] == "reset" for op in ops_of(s)) for s in tours)
