"""The Case Synopsis (Appendix B personas and Appendix C day in the life) as one-click 'stories' started from the
demo toggle.

A story only switches the acting person and pre-fills or prepares the same screens a real user would use;
nothing here changes engine behaviour. Payloads come from data/scenarios.json (or data/ui_text.json for the
extra supplier story); wording, order and page come from data/ui_text.json.
"""
import engine_security as es
from tabs import common, ui
from tabs import tab_intake as intake


def order():
    return common.load_ui()["story_order"]


def variants():
    return common.load_scenarios()["S3"]["variants"]


def load_variant(state, config, domain, variant):
    """Put one of the S3 example requests into the form and clear any review or duplicate question left open."""
    intake.clear_gate(state)
    state.pop(intake.REVIEW_KEY, None)
    intake.prefill(state, config, domain, variants()[variant]["payload"])
    state.pop("intake_attempted", None)


def load_payload(state, config, domain, payload):
    """Put a story's own example request into the form (any open review or duplicate question is cleared)."""
    intake.clear_gate(state)
    state.pop(intake.REVIEW_KEY, None)
    intake.prefill(state, config, domain, payload)
    state.pop("intake_attempted", None)


def _begin(state, config, sid, email):
    common.switch_user(state, config, email)
    copy = common.load_ui()["stories"][sid]
    state["story"] = {"id": sid, "label": copy["label"], "hint": copy["hint"], "step": 0}
    state["sim_now"] = None
    ui.go(state, copy["page"])


def start(state, config, sid, variant=None):
    """Callback-safe: switch person, load the example into the real form (or set up the wait), open the page."""
    scen, copy = common.load_scenarios(), common.load_ui()["stories"][sid]
    if sid == "S5":
        return _start_wait(state, config, scen, copy)
    if sid == "S7":
        for key in ("wf_draft", "wf_form", "wf_error", common.LIVE_CONFIG_KEY, "config_history"):
            state.pop(key, None)                                    # a clean workflow, so the steps below work every time
        state["admin_gen"] = state.get("admin_gen", 0) + 1
        return _begin(state, config, sid, copy["persona"])
    if sid == "S8":
        _begin(state, config, sid, copy["persona"])
        return load_variant(state, config, copy["domain"], copy["steps"][0]["variant"])
    if sid == "S6":
        _begin(state, config, sid, copy["persona"])
        intake.prefill(state, config, copy["domain"], copy["payload"])
        state.pop("intake_attempted", None)
        return None
    s = scen[sid]
    payload = s["variants"][variant or sorted(s["variants"])[1]]["payload"] if sid == "S3" else s["payload"]
    _begin(state, config, sid, s["persona_email"])
    intake.prefill(state, config, s["domain"], payload)
    state.pop("intake_attempted", None)
    return None


def _start_wait(state, config, scen, copy):
    """A request sitting with Finance (Wei Jie's inbox), then the MDM Analyst's waiting-times page."""
    s5, s2 = scen["S5"], scen["S2"]
    req = next((r for r in state.requests_db if r["status"] == s5["start_state"]), None)
    if req is None:
        common.switch_user(state, config, s2["persona_email"])
        user = es.get_user(s2["persona_email"], config)
        res = intake.evaluate(state, config, s2["domain"], s2["payload"])
        intake.submit_request(state, config, user, s2["domain"], s2["payload"], res["matches"])
    _begin(state, config, "S5", copy["persona"])
