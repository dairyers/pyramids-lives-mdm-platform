"""Starting data for the demo: a few requests already at different stages, so no page opens empty. They go through
the same engine calls as a real request (create, submit, route, approve, sync), so the audit trail is real too.
The list lives in data/demo_seed.json. Nothing here changes behaviour; Reset demo brings it back."""
import json
from datetime import datetime, timedelta

import engine_mdm as em
import engine_security as es
from tabs import common, tab_staging

SEED_PATH = common.ROOT / "data" / "demo_seed.json"


def _approve_until(state, config, req, actors, target):
    by_state = config["state_machine"]["approver_role_by_state"]
    while req["status"] != target and req["status"] in by_state:
        who = es.get_user(actors[by_state[req["status"]]], config)
        em.transition(req, "APPROVE", who, config, now=common.now(state), audit_log=state.audit_log)


def _age(req, days):
    """Make a seeded request look like it has already waited `days` days, so the 'over N days' views have something to show."""
    shift = timedelta(days=days)

    def earlier(iso):
        return (datetime.fromisoformat(iso) - shift).isoformat() if iso else iso
    for key in ("created_at", "pending_since"):
        if req.get(key):
            req[key] = earlier(req[key])
    for h in req.get("history", []):
        h["ts"] = earlier(h["ts"])


def seed(state, config):
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    actors = data["actors"]
    for item in data["requests"]:
        ok, errors = em.validate_against_config(item["domain"], item["payload"], config)
        if not ok:
            raise ValueError(f"demo seed '{item['name']}' is invalid: {errors}")
        requester = es.get_user(item["requester"], config)
        req = em.create_request(item["domain"], item["payload"], requester, config, now=common.now(state))
        em.transition(req, "SUBMIT", requester, config, now=common.now(state), audit_log=state.audit_log)
        em.transition(req, "ROUTE", em.system_user(config), config, now=common.now(state), audit_log=state.audit_log)
        state.requests_db.append(req)
        for step in item["steps"]:
            if step["action"] == "SYNC":
                tab_staging.sync_request(state, config, es.get_user(actors["MDM_LEAD"], config), req)
            elif step.get("until"):
                _approve_until(state, config, req, actors, step["until"])
            else:
                role = config["state_machine"]["approver_role_by_state"][req["status"]]
                em.transition(req, step["action"], es.get_user(actors[role], config), config, comment=step.get("comment"),
                              now=common.now(state), audit_log=state.audit_log)
        if item.get("days_waiting"):
            _age(req, item["days_waiting"])
