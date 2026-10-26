"""Shared Streamlit helpers for the tab modules: cached loaders, session state, the simulated clock,
role switching, audit logging and secured rendering.

Display rule (spec 2.4 R2/R8): record values reach the screen only through secure_view(), which
calls engine_security.apply_field_security(). Cached loaders take only file paths, never a role.
"""
import copy
import json
import pathlib
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from tabs import ui

import engine_help as eh
import engine_mdm as em
import engine_security as es

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config_rules.json"
MASTER_PATH = ROOT / "data" / "sap_master_mock.json"
SCENARIOS_PATH = ROOT / "data" / "scenarios.json"
ORDERS_PATH = ROOT / "data" / "regional_orders.csv"
SESSION_KEYS = ("current_user_email", "requests_db", "audit_log", "sap_master", "sim_now")
ROLE_SWITCH_KEY = "role_switch"
FORM_GEN_KEY = "formgen"                    # bumped whenever the intake form is cleared (see tab_intake.widget_key)
BADGE_COLOURS = {"EXACT_ID": "#8e2a20", "CONFIRMED": "#a3352a", "UNCONFIRMED": "#9a5b0b", "CONTRADICTED": "#50625f",
                 "AMBIGUOUS_ALIAS": "#5a4a8a", "SUPERSEDED": "#7d8b88"}
# Per-user UI state (form inputs incl. write-only bank fields, duplicate gate, comments, last result) is
# dropped on every role switch so one persona never sees what another typed.
PER_USER_PREFIXES = ("in_", "intake_", "gate_", "cmt_", "inbox_", "lab_")


# ----------------------------------------------------------------------------- cached loaders (no role in keys)
LIVE_CONFIG_KEY = "config_live"


@st.cache_resource
def base_config(path=str(CONFIG_PATH)):
    """The settings file as shipped, plus the help topics (data/help_topics.json) under "help" so that editing them uses the
    same checked, audited and undoable path as every other setting. Shared and never changed."""
    cfg = copy.deepcopy(em.load_config(path))
    cfg["help"] = eh.load_help()
    return cfg


def load_config(path=str(CONFIG_PATH)):
    """The settings this session runs on: the file, plus any change the IT administrator made on the dashboard. Every
    page and engine reads its rules from here, so a change is felt everywhere on the next run."""
    live = st.session_state.get(LIVE_CONFIG_KEY) if path == str(CONFIG_PATH) else None
    return live if live is not None else base_config(path)


def domain_text(config):
    """Name, help and icon of every request type: the shipped four from data/ui_text.json plus any made on the dashboard
    (which carry their own under "ui")."""
    out = dict(load_ui()["domains"])
    out.update({d: dom["ui"] for d, dom in config["domains"].items() if dom.get("ui")})
    return out


def table_names(config):
    """Plain names of the SAP tables, including the ones that belong to request types made on the dashboard."""
    out = dict(load_ui()["tables"])
    out.update({dom["sap_table"]: dom["ui"]["name"] for dom in config["domains"].values() if dom.get("ui")})
    return out


def live_config(state):
    """The editable per-session copy (made on first use). Only engine_settings functions should change it."""
    if LIVE_CONFIG_KEY not in state:
        state[LIVE_CONFIG_KEY] = copy.deepcopy(base_config())
    return state[LIVE_CONFIG_KEY]


@st.cache_resource
def load_master(path=str(MASTER_PATH)):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@st.cache_resource
def load_scenarios(path=str(SCENARIOS_PATH)):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@st.cache_resource
def load_orders(path=str(ORDERS_PATH)):
    return pd.read_csv(path)


@st.cache_resource
def load_ui(path=str(ROOT / "data" / "ui_text.json")):
    """Plain-language copy (page text, story hints, status wording). Presentation only."""
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


# ----------------------------------------------------------------------------- session state
def init_state(state, config):
    if "current_user_email" not in state:
        roles = config.get("demo", {}).get("roles")
        state.current_user_email = roles[0]["email"] if roles else config["users"][0]["email"]   # start as the generic Requestor
    if "requests_db" not in state:
        state.requests_db = []
    if "audit_log" not in state:
        state.audit_log = []
    if "sap_master" not in state:
        state.sap_master = copy.deepcopy(load_master())      # session copy; the cached master is never mutated
    if "sim_now" not in state:
        state.sim_now = None                                  # None = real clock
    if ROLE_SWITCH_KEY not in state:
        state[ROLE_SWITCH_KEY] = state.current_user_email


def reset_demo(state):
    """Restore a clean demo: requests, audit log, master copy, clock, and every widget/flow key."""
    gen = state.get(FORM_GEN_KEY, 0) + 1
    for k in list(state.keys()):
        if k not in ("current_user_email", ROLE_SWITCH_KEY, "tour_begun"):
            del state[k]
    state[FORM_GEN_KEY] = gen                                 # fresh widget identities: no stale values in the browser


def now(state):
    """The simulated clock when set (Scenario 5), else real UTC time. Always timezone-aware."""
    return state.get("sim_now") or datetime.now(timezone.utc)


def log(state, config, actor, event, request_id, detail):
    return em.log_event(state.audit_log, actor, event, request_id, detail, config=config, now=now(state))


def switch_user(state, config, email):
    """Change the acting persona (sidebar or a scenario button) and log ROLE_SWITCHED."""
    old = es.get_user(state.current_user_email, config)
    new = es.get_user(email, config)
    state.current_user_email = new["email"]
    state[ROLE_SWITCH_KEY] = new["email"]
    if old["email"] != new["email"]:
        for k in [k for k in state.keys() if str(k).startswith(PER_USER_PREFIXES)]:
            del state[k]
        state[FORM_GEN_KEY] = state.get(FORM_GEN_KEY, 0) + 1
        log(state, config, new, "ROLE_SWITCHED", None, {"from_role": old["role"], "to_role": new["role"]})


def user_label(user, config):
    return f"{user['display']} - {config['roles'][user['role']]['label']}"


def find_request(state, request_id):
    return next((r for r in state.requests_db if r["request_id"] == request_id), None)


# ----------------------------------------------------------------------------- secured rendering
def secure_view(state, config, user, record, domain, status=None, request_id=None, channel="APP"):
    """apply_field_security for the current user. A FULL view of a write-only class (bank details) is
    logged once per user and record as SENSITIVE_FIELD_VIEWED_FULL (spec 2.4 R7)."""
    view, report = es.apply_field_security(record, domain, user["role"], config, status=status, channel=channel)
    sec = config["security"]["classes"]
    fields = {f["field"]: f.get("security_class", "GENERAL") for f in config["domains"][domain]["fields"]}
    full_sensitive = sorted(k for k, p in report.items()
                            if p == "FULL" and sec.get(fields.get(k), {}).get("write_only_in_form")
                            and record.get(k) not in (None, ""))
    if full_sensitive:
        seen = state.setdefault("_full_views_logged", set())
        rec_key = record.get(config["domains"][domain]["key_field"])
        key = (user["email"], request_id or rec_key, tuple(full_sensitive))
        if key not in seen:
            seen.add(key)
            log(state, config, user, "SENSITIVE_FIELD_VIEWED_FULL", request_id,
                {"sap_id": rec_key, "classes": sorted({fields[k] for k in full_sensitive}),
                 "field_count": len(full_sensitive)})
    return view, report


def field_label(config, domain, field):
    f = next((x for x in config["domains"][domain]["fields"] if x["field"] == field), None)
    return f["label"] if f else field


def display_value(config, field, value):
    label = config.get("enum_labels", {}).get(field, {}).get(str(value))
    return f"{value} - {label}" if label is not None else value


def view_table(config, domain, view, report=None):
    """A secured view as a 3-column frame (field, label, value). Hidden fields are listed as hidden."""
    rows = [{"Field": k, "Label": field_label(config, domain, k), "Value": str(display_value(config, k, v))}
            for k, v in view.items()]
    for k, p in (report or {}).items():
        if p == "HIDDEN" and k not in view:
            rows.append({"Field": k, "Label": field_label(config, domain, k), "Value": "(hidden for your role)"})
    return pd.DataFrame(rows, columns=["Field", "Label", "Value"])


def show_view(config, domain, view, report=None):
    table = view_table(config, domain, view, report)
    table["Value"] = table["Value"].replace("(hidden for your role)", "Hidden for your role")
    table = table[table["Field"] != "U_ReqStatus"]                      # the status is already shown as a pill above
    who = table["Field"] == "U_Requester"
    table.loc[who, "Label"] = "Requested by"
    table.loc[who, "Value"] = table.loc[who, "Value"].map(ui.person)
    ui.detail_table([(r.Field, r.Label, r.Value, r.Value == "Hidden for your role") for r in table.itertuples()])


def existing_record(state, config, domain, sap_id):
    """The record a duplicate match points to: SAP master first, then another pending request. (record, status)."""
    table, key = config["domains"][domain]["sap_table"], config["domains"][domain]["key_field"]
    rec = next((r for r in state.sap_master.get(table, []) if r.get(key) == sap_id), None)
    if rec is not None:
        return rec, rec.get("U_ReqStatus")
    other = find_request(state, sap_id)
    return (other["payload"], other["status"]) if other else (None, None)


def compare_rows(state, config, domain, match, payload, user):
    """Your request next to the existing record, one row per compared field. Values of the existing record go through
    apply_field_security. Contact, phone, address, postal code and email of the existing record are shown only to
    detail_visible_roles (evidence disclosure); everyone else is told whether theirs matches. Returns (rows, note)."""
    dd = dedup_cfg(config, domain)
    cc = dd.get("corroboration") or {}
    privileged = user["role"] in cc.get("detail_visible_roles", [])
    existing, status = existing_record(state, config, domain, match["sap_id"])
    view = es.apply_field_security(existing, domain, user["role"], config, status=status)[0] if existing else {}
    corr = match.get("corroboration") or {}
    fired = {s["id"] for s in corr.get("signals", [])}
    clashed = {s["id"] for s in corr.get("conflicts", [])}
    sigs = cc.get("signals", [])
    name_field = dd["compare_field"]
    fields, open_fields = [name_field], {name_field}
    for s in sigs:
        if s["candidate_field"] not in fields:
            fields.append(s["candidate_field"])
        if s["strength"] in ("WEAK", "CONFLICT"):
            open_fields.add(s["candidate_field"])                      # country and customer group are not private
    rows = []
    for f in fields:
        yours = payload.get(f)
        if yours in (None, ""):
            continue
        if f == name_field:
            theirs, result = match.get("existing_name", ""), f"{match.get('confidence_score', 0):.0f}% alike"
        else:
            theirs = display_value(config, f, view.get(f)) if (privileged or f in open_fields) and view.get(f) not in (None, "") \
                else ("Hidden for your role" if not (privileged or f in open_fields) else "Not on file")
            related = [s for s in sigs if s["candidate_field"] == f]
            if any(s["id"] in fired and s["kind"] != "field_different" for s in related):
                result = "Matches"
            elif any(s["id"] in clashed for s in related):
                result = "Different"
            else:
                result = "Does not match"
        rows.append({"Detail": field_label(config, domain, f), "Your request": str(display_value(config, f, yours)),
                     f"Existing {match['sap_id']}": str(theirs), "Result": result, "_field": f})
    note = (f"{corr.get('agreeing_count', 0)} independent group(s) of evidence agree, {corr.get('non_weak_count', 0)} of them "
            "strong. A match needs at least two, one of them strong." if corr else "")
    if not privileged:
        note = (note + " " if note else "") + ("Contact, phone, address and email on the existing record are not shown "
                                               "to your role; we tell you whether yours matches.")
    return rows, note


def side_by_side(state, config, domain, match, payload, user):
    """Render the comparison so a person can judge 'same organisation or not' with the facts in front of them."""
    rows, note = compare_rows(state, config, domain, match, payload, user)
    with st.expander(f"Compare side by side with {match.get('existing_name')} ({match['sap_id']})", expanded=True):
        if rows:
            ui.compare_table(rows)
        if note:
            st.caption(note)


def tier_badge(tier):
    colour = BADGE_COLOURS.get(tier, "#50625f")
    return f"<span class='lv-stamp' style='color:{colour};border-color:{colour}'>{tier}</span>"


# ----------------------------------------------------------------------------- duplicate presentation
def dedup_cfg(config, domain):
    return config["deduplication_rules"][config["domains"][domain]["sap_table"]]


def tiers_cfg(config, domain):
    return (dedup_cfg(config, domain).get("corroboration") or {}).get("tiers", {})


def alias_explanation(match):
    """'NUHS -> National University Hospital Singapore' style lines from the alias hits."""
    out = []
    for h in match.get("alias_hits") or []:
        token = h["token"].upper() if h["source"] != "TOKEN_MAP" else h["token"]
        out.append(f"{token} -> {' | '.join(e.title() for e in h['expands_to'])}")
    return out


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def ui_message(config, domain, match):
    """The tier's ui_message from config with its placeholders filled from the match (labels only)."""
    t = tiers_cfg(config, domain).get(match.get("tier"))
    if not t:
        return match.get("explanation", "")
    corr = match.get("corroboration") or {}
    agreeing = [s["label"] for s in corr.get("signals", [])]
    conflicts = [s["label"] for s in corr.get("conflicts", [])]
    evidence = conflicts if match.get("tier") == "CONTRADICTED" else agreeing
    basis = match.get("match_basis", "")
    field = basis[len("EXACT_"):] if basis.startswith("EXACT_") else ""
    return t["ui_message"].format_map(_Safe(
        sap_id=match.get("sap_id"), existing_name=match.get("existing_name"),
        evidence=", ".join(evidence) or "none", alias=", ".join(h["token"].upper() for h in match.get("alias_hits") or []),
        field=field_label(config, domain, field) if field else ""))


def evidence_frame(config, domain, match, role):
    cc = dedup_cfg(config, domain).get("corroboration") or {}
    items = em.evidence_for_role(match.get("corroboration"), role, cc) if cc else []
    return pd.DataFrame(items, columns=["id", "label", "strength"] + (["detail"] if any("detail" in i for i in items) else []))


def plain_match(config, domain, match, user, technical=True):
    """A duplicate match in everyday words: the existing record, how alike the names are, what it means.
    The scoring detail (basis, evidence table) sits behind 'Why do we think so?' when technical is True."""
    tier = match.get("tier")
    label = load_ui()["tiers"].get(tier, "") if tier else ""
    colour = BADGE_COLOURS.get(tier, "#50625f")
    with st.container(border=True):
        st.markdown(f"**{match.get('existing_name')}** &nbsp;<span class='lv-meta'>{match.get('sap_id')} &middot; "
                    f"names {match.get('confidence_score', 0):.0f}% alike</span>"
                    + (f" &nbsp;<span class='lv-stamp' style='color:{colour};border-color:{colour}'>{label}</span>"
                       if label else ""), unsafe_allow_html=True)
        for line in alias_explanation(match):
            st.caption(f"We read the abbreviation as: {line}")
        msg = ui_message(config, domain, match)
        if msg:
            st.write(msg)
        if technical:
            with st.expander("Why do we think so?"):
                render_match(config, domain, match, user)


def render_match(config, domain, match, user):
    """One duplicate match card: score, basis, alias explanation, resolved name, tier badge, ui_message,
    evidence (via evidence_for_role) and the agreeing-group count."""
    with st.container(border=True):
        tier = match.get("tier")
        st.markdown(f"**{match.get('sap_id')}** {match.get('existing_name')} &nbsp; "
                    + (tier_badge(tier) if tier else ""), unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("Composite score", f"{match.get('confidence_score', 0):.1f}%")
        c2.metric("Match basis", match.get("match_basis", "-"))
        corr = match.get("corroboration") or {}
        c3.metric("Agreeing field groups", corr.get("agreeing_count", "-") if corr else "-")
        for line in alias_explanation(match):
            st.caption(f"Abbreviation: {line}")
        if match.get("resolved_candidate_name"):
            st.caption(f"Resolved candidate name: '{match['resolved_candidate_name']}'"
                       + (f" (token set {match.get('token_set')}, Jaro-Winkler {match.get('jaro_winkler')})"
                          if match.get("token_set") is not None else ""))
        msg = ui_message(config, domain, match)
        if msg:
            st.write(msg)
        ev = evidence_frame(config, domain, match, user["role"])
        if not ev.empty:
            ui.table(ev, hide_index=True)
            if "detail" not in ev.columns:
                st.caption("Compared values are visible only to: "
                           + ", ".join(config["roles"][r]["label"]
                                       for r in dedup_cfg(config, domain)["corroboration"]["detail_visible_roles"]))


# ----------------------------------------------------------------------------- security lens for a viewer
def lens_for_viewer(config, record, domain, status, viewer_role, viewer_status=None):
    """engine_security.security_lens(), filtered so the viewer never sees more than their own policy at the
    record's ACTUAL state (viewer_status; defaults to status): a cell whose role policy outranks the
    viewer's shows the policy name instead of the value. A what-if lens (e.g. APPROVED while the request
    is still pending) therefore never releases a value early."""
    lens = es.security_lens(record, domain, config, status=status)
    _, mine = es.apply_field_security(record, domain, viewer_role, config, status=viewer_status or status)
    out = lens.copy()
    for role in lens.columns:
        _, theirs = es.apply_field_security(record, domain, role, config, status=status)
        for field in lens.index:
            if es.RANK[theirs[field]] > es.RANK[mine[field]]:
                out.loc[field, role] = f"{theirs[field]} (withheld from your view)"
    out.columns = [config["roles"][r]["label"] for r in lens.columns]
    return out.astype(str)


# ----------------------------------------------------------------------------- request metadata
def hours_pending(req, at):
    if not req.get("pending_since"):
        return None
    return round((at - datetime.fromisoformat(req["pending_since"])).total_seconds() / 3600.0, 1)


def first_reminder_hours(config):
    return min(r["after_hours"] for r in config["sla"]["rules"])


def is_overdue(config, req, at):
    """Waiting at an approval step for at least as long as the first reminder."""
    return req["status"] in config["state_machine"]["pending_states"] and \
        (hours_pending(req, at) or 0) >= first_reminder_hours(config)


def buckets(config, role_key, reqs, ready_separate=True):
    """Requests sorted into piles that never overlap, by whose turn it is: waiting for this role to decide, waiting for
    someone else, approved and ready for SAP, created in SAP, rejected. 'done' is created plus rejected, rejected first.
    A role that does not create SAP records (ready_separate False) sees approved requests under 'others': they are
    still waiting for someone else."""
    decide = set(config["roles"][role_key]["can_decide_states"])
    pending = set(config["state_machine"]["pending_states"])
    by = lambda status: [r for r in reqs if r["status"] == status]         # noqa: E731
    rejected, created, ready = by("REJECTED"), by("SYNCED_TO_SAP"), by("APPROVED")
    others = [r for r in reqs if r["status"] in pending and r["status"] not in decide]
    if not ready_separate:
        others, ready = others + ready, []
    return {"mine": [r for r in reqs if r["status"] in decide], "others": others, "ready": ready, "created": created,
            "rejected": rejected, "done": rejected + created}


def meta_row(config, req, at):
    """Metadata only (config.security.meta_fields) plus approver email and hours pending: no field values."""
    row = {k: req.get(k) for k in config["security"]["meta_fields"]}
    row["current_approver_email"] = req.get("current_approver_email")
    row["hours_pending"] = hours_pending(req, at) if req.get("status") in config["sla"]["watched_states"] else None
    return row


def domains_for_record(config, table, record):
    """The config domain a SAP master record belongs to (OCRD splits by CardType)."""
    for d, dom in config["domains"].items():
        if dom["sap_table"] == table and ("card_type" not in dom or dom["card_type"] == record.get("CardType")):
            return d
    return None


def secured_frame(state, config, user, rows, hidden_text="(hidden)"):
    """rows = [(record, domain, status, request_id)] -> DataFrame of secured views (one chokepoint)."""
    out, cols = [], []
    for record, domain, status, request_id in rows:
        view, report = secure_view(state, config, user, record, domain, status=status, request_id=request_id)
        out.append({k: (view[k] if k in view else hidden_text) for k in record})
        cols += [k for k in record if k not in cols]
    return pd.DataFrame(out, columns=cols).fillna("").astype(str)


def render_persona_view(state, config, user, req):
    """What the current persona sees of one request: nothing, metadata only, or secured fields."""
    if not es.can_view_request(user, req, config):
        st.warning(f"{user_label(user, config)}: this request is not visible to your role.")
        return
    if config["roles"][user["role"]]["row_scope"] == "META_ONLY":
        ui.table(pd.DataFrame([meta_row(config, req, now(state))]).astype(str), hide_index=True)
        st.caption("Metadata only: no field values for your role.")
        return
    view, report = secure_view(state, config, user, req["payload"], req["domain"], status=req["status"],
                               request_id=req["request_id"])
    show_view(config, req["domain"], view, report)
