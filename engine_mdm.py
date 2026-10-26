"""MDM engine: validation, abbreviation resolver, fuzzy dedup, evidence quorum, routing,
state machine and audit helper (spec Sections 2.1, 2.2, 2.2b, 2.3 and Section 6 contracts).

Every business rule (regexes, thresholds, aliases, signals, quorum, tiers, routing, states,
audit events) is read from config_rules.json.
"""
import functools
import hashlib
import itertools
import json
import pathlib
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from rapidfuzz import distance, fuzz

DEFAULT_CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "config_rules.json"


def load_config(path=DEFAULT_CONFIG_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=4)
def _default_config(path=str(DEFAULT_CONFIG_PATH)):
    return load_config(path)


# ============================================================================ 2.1 validation
def _is_empty(v):
    return v is None or str(v).strip() == ""


def validate_against_config(domain, payload, config):
    dom = config["domains"].get(domain)
    if not dom:
        return False, ["Invalid domain specified."]
    msg, errors = config["messages"], []
    for f in dom["fields"]:
        key, label, val = f["field"], f["label"], payload.get(f["field"])
        empty = _is_empty(val)
        ctx = {"label": label, "field": key}

        if f.get("required") and empty:
            errors.append(msg["required"].format(**ctx))
            continue
        for dep, targets in f.get("required_if", {}).items():
            if payload.get(dep) in targets and empty:
                errors.append(msg["required_if"].format(dep_field=dep, dep_value=payload.get(dep), **ctx))
        if empty:
            continue

        ftype = f.get("type", "string")
        if ftype == "float":
            try:
                num = float(val)
            except (TypeError, ValueError):
                errors.append(msg["not_number"].format(**ctx))
                continue
            if "min" in f and num < f["min"]:
                errors.append(msg["min_value"].format(min=f["min"], **ctx))
        if ftype == "enum" and str(val) not in [str(o) for o in f["options"]]:
            errors.append(msg["invalid_option"].format(options=f["options"], **ctx))
        if "min_len" in f and len(str(val).strip()) < f["min_len"]:
            errors.append(msg["min_len"].format(min_len=f["min_len"], **ctx))
        if "regex" in f and not re.fullmatch(f["regex"], str(val)):
            errors.append(msg["invalid_format"].format(**ctx))
    return len(errors) == 0, errors


# ============================================================================ 2.2 abbreviation resolver + dedup
def _tokens(text):
    s = str(text).lower()
    s = re.sub(r"\b(?:[a-z]\.){2,}", lambda m: m.group(0).replace(".", ""), s)  # N.U.H.S. -> nuhs
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return s.split()


def _drop(words, ab):
    skip = set(ab["legal_suffix_words"]) | set(ab["stop_words"])
    return [w for w in words if w not in skip]


def build_dynamic_aliases(existing_names, ab):
    out = {}
    if not ab.get("dynamic_initialisms"):
        return out
    geo = set(ab["geo_words"])
    for name in existing_names:
        words = _drop(_tokens(name), ab)
        for ini in {"".join(w[0] for w in words), "".join(w[0] for w in words if w not in geo)}:
            if len(ini) >= ab["initialism_min_len"]:
                out.setdefault(ini, set()).add(" ".join(words))
    return {k: sorted(v) for k, v in out.items()}


def resolve_name(text, ab, dynamic_aliases=None):
    """Return (variants, hits). variants = list of token lists with abbreviations expanded."""
    alias_map = {k.lower(): [v.lower() for v in vs] for k, vs in ab["alias_map"].items()}
    token_map = {k.lower(): v.lower() for k, v in ab["token_map"].items()}
    dyn = dynamic_aliases or {}
    options, hits = [], []
    for t in _tokens(text):
        if t in alias_map or t in dyn:
            # UNION of curated aliases and initialisms generated from the master (v3), normalised so
            # "national university of singapore" and "national university singapore" count as one.
            exp = list(dict.fromkeys(" ".join(_drop(_tokens(v), ab)) for v in alias_map.get(t, []) + dyn.get(t, [])))
            options.append(exp)
            hits.append({"token": t, "expands_to": exp, "source": "ALIAS_MAP" if t in alias_map else "AUTO_INITIALISM"})
        elif t in token_map:
            options.append([token_map[t]])
            hits.append({"token": t, "expands_to": [token_map[t]], "source": "TOKEN_MAP"})
        else:
            options.append([t])
    variants = []
    for combo in itertools.islice(itertools.product(*options), ab["max_variants"]):
        variants.append(_drop([w for phrase in combo for w in phrase.split()], ab))
    return variants, hits


def _score(cand_words, exist_words, dd, ab):
    a, b = " ".join(cand_words), " ".join(exist_words)
    ts = fuzz.token_set_ratio(a, b) / 100.0
    jw = distance.JaroWinkler.similarity(a, b)
    w = dd["weights"]
    comp, penalty, capped = w["token_set"] * ts + w["jaro_winkler"] * jw, 0.0, False
    disc = set(ab["discriminator_words"])
    if {x for x in cand_words if x in disc} ^ {x for x in exist_words if x in disc}:
        penalty = ab["discriminator_penalty"]
        comp -= penalty
    skip = disc | set(ab["geo_words"])
    ca, cb = {x for x in cand_words if x not in skip}, {x for x in exist_words if x not in skip}
    if ca and cb and len(ca & cb) / len(ca | cb) < ab["core_min_jaccard"]:
        comp, capped = min(comp, ab["core_conflict_cap"]), True
    return max(comp, 0.0), ts, jw, penalty, capped


def check_fuzzy_duplicates(domain, payload, existing_records, config):
    dom = config["domains"][domain]
    dd = config["deduplication_rules"][dom["sap_table"]]
    ab, field, key = dd["abbreviation_resolution"], dd["compare_field"], dom["key_field"]
    pool = [r for r in existing_records
            if "card_type" not in dom or not dd.get("match_same_card_type_only") or r.get("CardType") == dom["card_type"]]
    matches, exact_ids = [], set()

    for ef in dd.get("exact_match_fields", []):                       # step 0
        if payload.get(ef):
            for r in pool:
                if str(r.get(ef, "")).strip().upper() == str(payload[ef]).strip().upper():
                    exact_ids.add(r.get(key))
                    matches.append({"sap_id": r.get(key), "existing_name": r.get(field), "match_basis": "EXACT_" + ef,
                                    "confidence_score": 100.0, "hard_block": dd["exact_match_policy"] == "HARD_BLOCK",
                                    "tier": "EXACT_ID", "explanation": f"{ef} is identical to existing record {r.get(key)}."})
    cand_raw = payload.get(field, "")
    if not cand_raw or not ab.get("enabled", True):
        return matches
    dyn = build_dynamic_aliases([r.get(field, "") for r in pool], ab)
    cand_variants, hits = resolve_name(cand_raw, ab, dyn)

    for r in pool:
        if r.get(key) in exact_ids:                                   # already a hard block; do not list it twice
            continue
        exist_words = resolve_name(r.get(field, ""), {**ab, "alias_map": {}, "token_map": {}})[0][0]
        best = max(((_score(cv, exist_words, dd, ab), cv) for cv in cand_variants), key=lambda x: x[0][0])
        (comp, ts, jw, pen, capped), cv = best
        if comp >= dd["similarity_threshold"]:
            expanded = bool(hits) and " ".join(cv) != " ".join(_drop(_tokens(cand_raw), ab))
            matches.append({
                "sap_id": r.get(key), "existing_name": r.get(field),
                "resolved_candidate_name": " ".join(cv),
                "match_basis": "ALIAS_EXPANDED" if expanded else "FUZZY",
                "alias_hits": hits, "token_set": round(ts, 3), "jaro_winkler": round(jw, 3),
                "discriminator_penalty": pen, "core_conflict_capped": capped,
                "confidence_score": round(comp * 100, 1), "hard_block": False,
                "explanation": (f"'{cand_raw}' resolves to '{' '.join(cv)}' and matches existing record "
                                f"{r.get(key)} '{r.get(field)}' at {round(comp * 100, 1)}%.")})
    ranked = sorted(matches, key=lambda m: m["confidence_score"], reverse=True)
    return assign_tiers(ranked, payload, {r.get(key): r for r in pool}, hits, dd)   # Section 2.2b


def find_related_records(domain, payload, existing_records, config, matched_ids=(), compare_name=None):
    """Automatic reconciliation of near-misses. Records whose name scores between related_review.min_score and
    the duplicate threshold are not flagged as duplicates, but they are never ignored: the same non-name evidence
    check used for confirmed matches (email domain, tax IDs, address, contact, country, group) runs on them and
    the outcome is reported with a plain verdict. Never changes routing or blocking; a person decides."""
    dom = config["domains"][domain]
    dd = config["deduplication_rules"][dom["sap_table"]]
    rr, cc = dd.get("related_review") or {}, dd.get("corroboration") or {}
    ab, field, key = dd["abbreviation_resolution"], dd["compare_field"], dom["key_field"]
    cand_raw = compare_name if compare_name else payload.get(field, "")
    if not rr.get("enabled") or not cc.get("enabled") or not cand_raw or not ab.get("enabled", True):
        return []
    pool = [r for r in existing_records
            if "card_type" not in dom or not dd.get("match_same_card_type_only") or r.get("CardType") == dom["card_type"]]
    dyn = build_dynamic_aliases([r.get(field, "") for r in pool], ab)
    variants, hits = resolve_name(cand_raw, ab, dyn)
    if compare_name:                       # an edited or already-expanded name must not hide the abbreviation typed
        seen = {h["token"] for h in hits}
        hits = hits + [h for h in resolve_name(payload.get(field, ""), ab, dyn)[1] if h["token"] not in seen]
    min_len = ab["initialism_min_len"]
    out = []
    for r in pool:
        if r.get(key) in set(matched_ids):
            continue
        exist_words = resolve_name(r.get(field, ""), {**ab, "alias_map": {}, "token_map": {}})[0][0]
        (comp, ts, jw, pen, capped), cv = max(((_score(v, exist_words, dd, ab), v) for v in variants),
                                              key=lambda x: x[0][0])
        if not (rr["min_score"] <= comp < dd["similarity_threshold"]):
            continue
        # abbreviations that expanded to THIS record's name: they point the check at the record and give the
        # email-domain signal something to look for
        alias_tokens = [h["token"] for h in hits if h["source"] in ("ALIAS_MAP", "AUTO_INITIALISM")
                        and len(h["token"]) >= min_len and " ".join(exist_words) in h["expands_to"]]
        if rr.get("require_alias_expansion") and not alias_tokens:
            continue
        corr = corroborate({**payload, field: cand_raw}, r, alias_tokens, cc)
        out.append({"sap_id": r.get(key), "existing_name": r.get(field), "resolved_candidate_name": " ".join(cv),
                    "alias_hits": hits, "confidence_score": round(comp * 100, 1), "discriminator_penalty": pen,
                    "corroboration": corr, "tier": corr["tier"], "match_basis": "RELATED",
                    "verdict": rr["verdict_text"].get(corr["tier"], "")})
    return sorted(out, key=lambda m: m["confidence_score"], reverse=True)[:rr["max_results"]]


def build_dedup_pool(domain, sap_records, requests_db, config):
    """Dedup pool = SAP master records of the domain's table PLUS payloads of requests still pending
    (so two requesters cannot both create the same record). Pending requests use request_id as key."""
    dom = config["domains"][domain]
    open_states = set(config["state_machine"]["pending_states"]) | {"SUBMITTED"}
    pool = list(sap_records)
    for req in requests_db or []:
        if req.get("domain") == domain and req.get("status") in open_states:
            rec = {k: v for k, v in req["payload"].items() if not str(k).startswith("_")}
            rec.setdefault("CardType", dom.get("card_type"))
            rec[dom["key_field"]] = rec.get(dom["key_field"]) or req["request_id"]
            pool.append(rec)
    return pool


# ============================================================================ 2.2b evidence quorum
STRENGTH = {"WEAK": 0, "MEDIUM": 1, "STRONG": 2}     # CONFLICT signals are handled separately


def _domain(email):
    e = str(email or "")
    return e.split("@")[-1].strip().lower() if "@" in e else ""


def _domains_related(a, b):
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def _alnum(v):
    return re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()


def _digits(v):
    return re.sub(r"\D", "", str(v or ""))


def _addr_words(text, tm):
    s = re.sub(r"[^\w\s]", " ", str(text or "").lower())
    return [tm["address_token_map"].get(w, w) for w in s.split()]


def _person_words(text, tm):
    s = re.sub(r"[^\w\s]", " ", str(text or "").lower())
    return [w for w in s.split() if w not in set(tm["person_title_words"])]


def _signal_hit(s, payload, existing, alias_tokens, cc):
    """Return (fired, detail). Missing data on either side never fires."""
    kind, tm, free = s["kind"], cc["text_match"], set(cc["free_email_domains"])
    a, b = payload.get(s.get("candidate_field")), existing.get(s.get("existing_field"))
    if kind == "email_domain_contains_alias_token":
        da = _domain(a)
        return (bool(da) and da not in free and any(t in da for t in alias_tokens)), da or "-"
    if kind == "email_domain_match":
        da, db = _domain(a), _domain(b)
        return (da not in free and db not in free and _domains_related(da, db)), f"{da or '-'} vs {db or '-'}"
    if a in (None, "") or b in (None, ""):
        return False, "-"
    detail = f"{a} vs {b}"
    if kind == "field_equal":
        same = (_alnum(a) == _alnum(b)) if s.get("normalize") == "alnum_upper" else (str(a) == str(b))
        return same, detail
    if kind == "field_different":
        return str(a) != str(b), detail
    if kind == "near_match":             # typo-level difference only; identical IDs are the step-0 hard block
        na, nb = _alnum(a), _alnum(b)
        d = distance.DamerauLevenshtein.distance(na, nb)
        return (0 < d <= tm["tax_id_max_edit_distance"] and min(len(na), len(nb)) >= tm["tax_id_min_len"]), detail
    if kind == "text_similar":
        wa, wb = _addr_words(a, tm), _addr_words(b, tm)
        if min(len(wa), len(wb)) < tm["address_min_tokens"]:
            return False, detail
        return fuzz.token_sort_ratio(" ".join(wa), " ".join(wb)) / 100.0 >= tm["address_min_similarity"], detail
    if kind == "person_similar":
        wa, wb = _person_words(a, tm), _person_words(b, tm)
        if min(len(wa), len(wb)) < tm["person_min_tokens"]:
            return False, detail
        return fuzz.token_sort_ratio(" ".join(wa), " ".join(wb)) / 100.0 >= tm["person_min_similarity"], detail
    if kind == "phone_match":
        da, db, n = _digits(a), _digits(b), tm["phone_compare_last_digits"]
        return (len(da) >= n and len(db) >= n and da[-n:] == db[-n:]), detail
    return False, detail


def corroborate(payload, existing, alias_tokens, cc):
    """Deterministic non-name evidence, resolved by the quorum rule in config."""
    q, fired, conflicts, score = cc["quorum"], [], [], 0.0
    for s in cc["signals"]:
        hit, detail = _signal_hit(s, payload, existing, alias_tokens, cc)
        if not hit:
            continue
        rec = {"id": s["id"], "label": s["label"], "group": s["group"], "strength": s["strength"],
               "weight": s["weight"], "detail": detail}
        score += s["weight"]
        (conflicts if s["strength"] == "CONFLICT" else fired).append(rec)
    groups = {r["group"] for r in fired}
    non_weak = {r["group"] for r in fired if STRENGTH[r["strength"]] >= 1}
    if len(groups) >= q["min_agreeing_groups"] and len(non_weak) >= q["min_non_weak_groups"]:
        tier = "CONFIRMED"
    elif len(conflicts) >= q["contradiction_min_conflicts"] and not non_weak:
        tier = "CONTRADICTED"
    else:
        tier = "UNCONFIRMED"
    return {"score": round(score, 2), "signals": fired, "conflicts": conflicts,
            "agreeing_count": len(groups), "non_weak_count": len(non_weak), "tier": tier}


def assign_tiers(matches, payload, pool_by_id, hits, dd):
    """Adds tier + corroboration to each match, then resolves ambiguous aliases."""
    cc = dd.get("corroboration", {})
    if not cc.get("enabled"):
        return matches
    min_len = dd["abbreviation_resolution"]["initialism_min_len"]
    alias_tokens = [h["token"] for h in hits if h["source"] in ("ALIAS_MAP", "AUTO_INITIALISM") and len(h["token"]) >= min_len]
    for m in matches:
        if m.get("hard_block"):
            m["tier"] = "EXACT_ID"
            continue
        if m["match_basis"] in cc["apply_to_match_basis"]:
            m["corroboration"] = corroborate(payload, pool_by_id[m["sap_id"]], alias_tokens, cc)
            m["tier"] = m["corroboration"]["tier"]
        else:
            m["tier"] = cc["default_tier_otherwise"]
    alias_matches = [m for m in matches if m.get("match_basis") == "ALIAS_EXPANDED"]
    ambiguous = any(len(h["expands_to"]) > 1 for h in hits)
    if ambiguous and len({m["sap_id"] for m in alias_matches}) > 1:
        def agreeing(m):
            return (m.get("corroboration") or {}).get("agreeing_count", 0)
        confirmed = sorted((m for m in alias_matches if m["tier"] == "CONFIRMED"),
                           key=lambda m: (agreeing(m), (m.get("corroboration") or {}).get("score", 0.0)), reverse=True)
        margin, winner = cc["quorum"]["ambiguity_win_margin"], None
        if len(confirmed) == 1:
            winner = confirmed[0]
        elif len(confirmed) > 1 and agreeing(confirmed[0]) - agreeing(confirmed[1]) >= margin:
            winner = confirmed[0]
        for m in alias_matches:
            if winner is None:
                m["tier"] = "AMBIGUOUS_ALIAS"
            elif m is not winner:
                m["tier"] = "SUPERSEDED"
    return matches


def dup_review_required(matches, config, domain):
    dd = config["deduplication_rules"][config["domains"][domain]["sap_table"]]
    tiers = (dd.get("corroboration") or {}).get("tiers")
    if not tiers:
        # Tables without a tier configuration (OITM, OOCR): a hard block stops submission on its own,
        # any other name match is sent to MDM review (missing evidence never clears a match).
        return any(not m.get("hard_block") for m in matches)
    return any(tiers[m["tier"]]["requires_mdm_review"] for m in matches if m.get("tier") in tiers)


def evidence_for_role(corroboration, role, cc):
    """What a given role may see of the evidence: labels for everyone, compared values only for privileged roles."""
    full = role in cc["detail_visible_roles"]
    out = []
    for r in (corroboration or {}).get("signals", []) + (corroboration or {}).get("conflicts", []):
        item = {"id": r["id"], "label": r["label"], "strength": r["strength"]}
        if full:
            item["detail"] = r["detail"]
        out.append(item)
    return out


# ============================================================================ 2.3 routing
def _num(a):
    return None if a is None or str(a).strip() == "" else float(a)


OPS = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) is not None and _num(a) > b, "gte": lambda a, b: _num(a) is not None and _num(a) >= b,
    "lt": lambda a, b: _num(a) is not None and _num(a) < b, "lte": lambda a, b: _num(a) is not None and _num(a) <= b,
    "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b,
}


def _leaf(c, ctx):
    try:
        return OPS[c["op"]](ctx.get(c["field"]), c["value"])
    except (TypeError, ValueError):
        return False                       # a malformed value never satisfies a condition


def _eval(cond, ctx):
    ok_all = all(_leaf(c, ctx) for c in cond.get("all", []))
    any_list = cond.get("any")
    ok_any = True if not any_list else any(_leaf(c, ctx) for c in any_list)
    return ok_all and ok_any


def _typed_payload(domain, payload, config):
    """Coerce enum values to the type of the matching configured option (form widgets return strings),
    so GroupNum '3' routes the same as GroupNum 3."""
    out = dict(payload)
    for f in config["domains"].get(domain, {}).get("fields", []):
        if f.get("type") == "enum" and f["field"] in out:
            for o in f["options"]:
                if str(o) == str(out[f["field"]]):
                    out[f["field"]] = o
    return out


def route_request(domain, payload, config, dup_review=False):
    ctx = {**_typed_payload(domain, payload, config), "_domain": domain, "_dup_review": dup_review}
    hits = [r for r in sorted(config["routing_matrix"], key=lambda r: r["priority"])
            if r.get("enabled", True) is not False and _eval(r["condition"], ctx)]      # off = switched off on the dashboard
    rule = next((r for r in hits if r.get("kind", "route") != "add"), None)             # the first route that fits wins
    if rule is None:
        raise ValueError("No routing rule matched; routing_matrix must end with a catch-all rule.")
    chain = [rule["route_to"]] + list(rule.get("then") or [])
    also = []
    for extra in (r for r in hits if r.get("kind") == "add"):                           # 'also require' rules all apply
        also.append(extra["id"])
        chain += [s for s in [extra["route_to"]] + list(extra.get("then") or []) if s not in chain]
    out = {"route_to": rule["route_to"], "approver_role": rule["approver_role"], "rule_id": rule["id"]}
    if len(chain) > 1:                                 # further approvers, in order, after the first one signs off
        out["then"] = chain[1:]
    if also:
        out["also"] = also
    return out


# ============================================================================ state machine
ROUTE_TARGET, APPROVER_PLACEHOLDER, SAME_STATE = "<routing_matrix>", "<approver_role_by_state>", "<same_state>"
SYSTEM_ROLE = "SYSTEM"
ACTION_EVENTS = {"SUBMIT": "SUBMITTED", "ROUTE": "ROUTED", "APPROVE": "APPROVED", "REJECT": "REJECTED",
                 "SYNC": "SYNCED", "REASSIGN": "SLA_ACTION"}


def now_iso(config, now=None):
    """Timezone-aware ISO-8601 timestamp in config.meta.timezone (now is injectable)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ZoneInfo(config["meta"]["timezone"])).isoformat()


def system_user(config):
    return {"email": config["sla"]["system_sender"], "display": "System", "role": SYSTEM_ROLE}


def approver_email_for_role(role, config):
    return next((u["email"] for u in config["users"] if u["role"] == role), None)


def create_request(domain, payload, user, config, now=None):
    role = config["roles"].get(user.get("role"), {})
    if domain not in role.get("can_submit", []):
        raise PermissionError(f"Role {user.get('role')} may not submit {domain} requests.")
    dom, sm = config["domains"][domain], config["state_machine"]
    ts = now_iso(config, now)
    clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
    clean["U_ReqStatus"] = sm["initial"]
    clean.setdefault("U_Requester", user["email"])
    req = {"request_id": f"REQ-{uuid.uuid4().hex[:8].upper()}", "domain": domain, "sap_table": dom["sap_table"],
           "record_name": clean.get(dom["record_name_field"]), "payload": clean, "status": sm["initial"],
           "requester_email": user["email"], "current_approver_role": None, "current_approver_email": None,
           "created_at": ts, "pending_since": None, "last_updated_at": ts, "routing_rule_id": None,
           "dup_matches": [], "dup_requester_answer": None, "dup_override_justification": None, "history": [],
           "remaining_chain": [], "approval_path": []}
    for rule in config["sla"]["rules"]:
        req[f"sla_{rule['id']}_done"] = False
    return req


def _allowed_roles(tr, status, sm):
    return {sm["approver_role_by_state"].get(status) if r == APPROVER_PLACEHOLDER else r for r in tr["actor_roles"]}


def allowed_actions(request, user, config):
    """Actions this user may perform on the request in its current state."""
    sm, out = config["state_machine"], []
    for tr in sm["transitions"]:
        if request["status"] in tr["from"] and user.get("role") in _allowed_roles(tr, request["status"], sm):
            out.append(tr["action"])
    return out


def transition(request, action, user, config, comment=None, *, now=None, audit_log=None):
    """Apply a state-machine action. Raises PermissionError when the actor's role may not perform it,
    ValueError when the action is not valid from the current state."""
    sm = config["state_machine"]
    tr = next((t for t in sm["transitions"] if t["action"] == action), None)
    if tr is None:
        raise ValueError(f"Unknown action {action}.")
    cur = request["status"]
    if cur not in tr["from"]:
        raise ValueError(f"{action} is not allowed from {cur}.")
    if user.get("role") not in _allowed_roles(tr, cur, sm):
        raise PermissionError(f"Role {user.get('role')} may not {action} a request in {cur}.")
    if action == "SUBMIT" and str(user.get("email", "")).lower() != str(request["requester_email"]).lower():
        raise PermissionError("Only the requester may submit their own request.")

    ts, detail = now_iso(config, now), None
    if tr["to"] == ROUTE_TARGET:
        dup = dup_review_required(request.get("dup_matches") or [], config, request["domain"])
        route = route_request(request["domain"], request["payload"], config, dup_review=dup)
        new = route["route_to"]
        request["routing_rule_id"] = route["rule_id"]
        request["current_approver_role"] = route["approver_role"]
        request["current_approver_email"] = approver_email_for_role(route["approver_role"], config)
        request["remaining_chain"] = list(route.get("then", []))
        request["approval_path"] = [new] + list(route.get("then", []))      # every approver, in order, for display
        detail = {"rule_id": route["rule_id"], "route_to": new, "dup_review": dup}
    elif tr["to"] == SAME_STATE:
        new = cur
    else:
        new = tr["to"]

    chain_step = action == "APPROVE" and bool(request.get("remaining_chain"))
    if chain_step:                                     # an approval that hands the request to the next approver
        new = request["remaining_chain"].pop(0)

    pending = set(sm["pending_states"])
    if new in pending and (cur not in pending or chain_step):
        request["pending_since"] = ts                  # ENTERING a pending state, or a new approver's turn starts
        if chain_step:
            role = sm["approver_role_by_state"][new]
            request["current_approver_role"] = role
            request["current_approver_email"] = approver_email_for_role(role, config)
            for rule in config["sla"]["rules"]:
                request[f"sla_{rule['id']}_done"] = False
    if new not in pending:
        request["current_approver_role"] = None
        request["current_approver_email"] = None
    request["status"] = new
    request["payload"]["U_ReqStatus"] = new
    request["last_updated_at"] = ts
    request["history"].append({"ts": ts, "actor": user.get("email"), "action": action, "from": cur, "to": new,
                               "comment": comment})
    if audit_log is not None:
        log_event(audit_log, user, ACTION_EVENTS[action], request["request_id"],
                  detail or {"from": cur, "to": new, "comment": comment}, config=config, now=now)
    return request


def update_request(request, changes, user, config, comment=None, now=None):
    """Edit payload fields. Never touches pending_since, so edits do not reset the SLA clock."""
    if request["status"] in config["state_machine"]["terminal"]:
        raise ValueError(f"Request {request['request_id']} is closed ({request['status']}).")
    is_requester = str(user.get("email", "")).lower() == str(request["requester_email"]).lower()
    can_decide = request["status"] in config["roles"].get(user.get("role"), {}).get("can_decide_states", [])
    if not (is_requester or can_decide):
        raise PermissionError(f"{user.get('email')} may not edit request {request['request_id']}.")
    ts = now_iso(config, now)
    request["payload"].update({k: v for k, v in changes.items() if not str(k).startswith("_")})
    request["record_name"] = request["payload"].get(config["domains"][request["domain"]]["record_name_field"])
    request["last_updated_at"] = ts
    request["history"].append({"ts": ts, "actor": user.get("email"), "action": "EDIT", "from": request["status"],
                               "to": request["status"], "comment": comment})
    return request


# ============================================================================ audit
_AUDIT_DROP_KEYS = {"detail", "payload", "corroboration", "explanation", "alias_hits", "existing_name",
                    "resolved_candidate_name"}


def dup_audit_detail(match):
    """Audit-safe summary of one duplicate match: sap_id, tier and fired signal IDs only."""
    corr = match.get("corroboration") or {}
    return {"sap_id": match.get("sap_id"), "tier": match.get("tier"),
            "signal_ids": [s["id"] for s in corr.get("signals", []) + corr.get("conflicts", [])]}


def _scrub(obj, banned):
    if isinstance(obj, dict):
        if "sap_id" in obj and ("tier" in obj or "corroboration" in obj or "match_basis" in obj):
            return dup_audit_detail(obj)
        return {k: _scrub(v, banned) for k, v in obj.items() if k not in banned}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, banned) for v in obj]
    return obj


def log_event(audit_log, actor, event, request_id, detail, *, config=None, now=None):
    """Append an audit entry {ts, actor_email, actor_role, event, request_id, detail}.
    Only events in config.audit.log_events are accepted. Record field values and duplicate evidence
    values are stripped from detail; duplicate matches are reduced to sap_id, tier and signal IDs."""
    config = config or _default_config()
    if event not in config["audit"]["log_events"]:
        raise ValueError(f"Event {event} is not in config.audit.log_events.")
    banned = set(_AUDIT_DROP_KEYS)
    for dom in config["domains"].values():
        banned |= {f["field"] for f in dom["fields"]}
    for dd in config["deduplication_rules"].values():
        for s in (dd.get("corroboration") or {}).get("signals", []):
            banned |= {s[k] for k in ("candidate_field", "existing_field") if k in s}
    entry = {"ts": now_iso(config, now), "actor_email": (actor or {}).get("email"),
             "actor_role": (actor or {}).get("role"), "event": event, "request_id": request_id,
             "detail": _scrub(detail, banned)}
    entry["prev_hash"] = audit_log[-1]["hash"] if audit_log else AUDIT_GENESIS
    entry["hash"] = _entry_hash(entry)
    audit_log.append(entry)
    return entry


AUDIT_GENESIS = "START"
_HASHED = ("ts", "actor_email", "actor_role", "event", "request_id", "detail", "prev_hash")


def _entry_hash(entry):
    """SHA-256 over the entry's own content and the hash of the entry before it (a chain, like a ledger)."""
    body = json.dumps({k: entry.get(k) for k in _HASHED}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def verify_audit_chain(audit_log):
    """(True, None) when every entry still matches its hash and follows the one before it, otherwise (False, index) of the
    first entry that was changed, removed, reordered or inserted. This detects edits to the log; it cannot stop someone who
    can rewrite the whole log and recompute every hash, which needs the latest hash kept somewhere else (see README)."""
    prev = AUDIT_GENESIS
    for i, e in enumerate(audit_log):
        if e.get("prev_hash") != prev or e.get("hash") != _entry_hash(e):
            return False, i
        prev = e["hash"]
    return True, None
