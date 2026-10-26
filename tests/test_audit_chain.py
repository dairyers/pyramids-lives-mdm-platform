"""The audit log is a hash chain: changing, removing, inserting or reordering an entry is detected."""
import copy

import engine_mdm as em
from fixtures import CONFIG

ACTOR = {"email": "a@x.com", "role": "REQUESTER"}


def _log(n=5):
    log = []
    for i in range(n):
        em.log_event(log, ACTOR, "ROUTED", f"REQ-{i}", {"rule_id": f"R{i}"}, config=CONFIG)
    return log


def test_an_untouched_log_verifies_and_each_entry_points_at_the_one_before():
    log = _log()
    assert em.verify_audit_chain(log) == (True, None) and em.verify_audit_chain([]) == (True, None)
    assert log[0]["prev_hash"] == em.AUDIT_GENESIS
    assert all(b["prev_hash"] == a["hash"] for a, b in zip(log, log[1:]))


def test_editing_an_entry_is_caught_at_that_entry():
    log = _log()
    log[2]["detail"]["rule_id"] = "R99"
    assert em.verify_audit_chain(log) == (False, 2)
    log = _log()
    log[3]["actor_email"] = "someone.else@x.com"
    assert em.verify_audit_chain(log) == (False, 3)


def test_removing_inserting_or_reordering_is_caught():
    log = _log()
    del log[1]
    assert em.verify_audit_chain(log) == (False, 1)
    log = _log()
    log[1], log[2] = log[2], log[1]
    assert em.verify_audit_chain(log)[0] is False
    log = _log()
    log.insert(2, copy.deepcopy(log[0]))
    assert em.verify_audit_chain(log)[0] is False
    log = _log()
    log.pop()                                                           # dropping the newest entry is NOT detectable alone
    assert em.verify_audit_chain(log) == (True, None)


def test_rewriting_one_entry_and_its_hash_still_breaks_the_next_link():
    log = _log()
    log[1]["detail"]["rule_id"] = "R77"
    log[1]["hash"] = em._entry_hash(log[1])
    assert em.verify_audit_chain(log) == (False, 2)
