"""Deterministic lint for config_rules.json (replaces the LLM 'Prompt 1' config lint).
Usage: python tools/check_config.py [path]   -> exit code 1 if any defect is found."""
import json, sys, pathlib

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "config_rules.json")
defects = []
def bad(where, msg): defects.append(f"{where}: {msg}")

try:
    cfg = json.loads(path.read_text(encoding="utf-8"))
except Exception as e:
    print(f"DEFECT $: not valid JSON ({e})"); sys.exit(1)

roles, tabs, domains = cfg["roles"], cfg["tabs"], cfg["domains"]
states = set(cfg["state_machine"]["pending_states"]) | {cfg["state_machine"]["initial"], "SUBMITTED", "APPROVED"} | set(cfg["state_machine"]["terminal"])

# roles referenced anywhere must exist in roles (SYSTEM is a pseudo-actor)
def need_role(where, r):
    if r not in roles and r != "SYSTEM" and not str(r).startswith("<") and r != "*":
        bad(where, f"role '{r}' is not defined in roles")
for i, r in enumerate(cfg["routing_matrix"]): need_role(f"routing_matrix[{i}].approver_role", r["approver_role"])
for st, r in cfg["state_machine"]["approver_role_by_state"].items(): need_role(f"state_machine.approver_role_by_state.{st}", r)
for i, tr in enumerate(cfg["state_machine"]["transitions"]):
    for r in tr["actor_roles"]: need_role(f"state_machine.transitions[{i}].actor_roles", r)
for i, u in enumerate(cfg["users"]): need_role(f"users[{i}].role", u["role"])
for r in cfg.get("delegates", {}): need_role("delegates", r)
for cls, c in cfg["security"]["classes"].items():
    for r, pol in c["policy_by_role"].items():
        need_role(f"security.classes.{cls}.policy_by_role", r)
for r in roles:
    for cls, c in cfg["security"]["classes"].items():
        if "*" not in c["policy_by_role"] and r not in c["policy_by_role"]:
            bad(f"security.classes.{cls}", f"no policy for role {r} (falls back to default_policy)")

# tabs
for r, rd in roles.items():
    for tb in rd["tabs"]:
        if tb not in tabs: bad(f"roles.{r}.tabs", f"tab '{tb}' is not defined in tabs")
for tb in cfg["security"].get("always_visible_tabs_in_demo", []):
    if tb not in tabs: bad("security.always_visible_tabs_in_demo", f"tab '{tb}' not in tabs")

# routing: catch-all last, unique ids/priorities, route_to is a known state
rm = sorted(cfg["routing_matrix"], key=lambda x: x["priority"])
if rm[-1]["condition"] != {"all": []}: bad("routing_matrix", "last rule by priority must be a catch-all {'all': []}")
if len({r["id"] for r in rm}) != len(rm): bad("routing_matrix", "duplicate rule ids")
if len({r["priority"] for r in rm}) != len(rm): bad("routing_matrix", "duplicate priorities")
for r in rm:
    if r["route_to"] not in states: bad(f"routing_matrix.{r['id']}", f"route_to '{r['route_to']}' is not a state")
    if cfg["state_machine"]["approver_role_by_state"].get(r["route_to"]) != r["approver_role"]:
        bad(f"routing_matrix.{r['id']}", f"approver_role does not match approver_role_by_state[{r['route_to']}]")

# security_class names, enum options vs labels, regex compile
import re
for dn, d in domains.items():
    for f in d["fields"]:
        w = f"domains.{dn}.{f['field']}"
        sc = f.get("security_class")
        if sc and sc not in cfg["security"]["classes"]: bad(w, f"security_class '{sc}' not under security.classes")
        if f.get("type") == "enum" and f["field"] in cfg["enum_labels"]:
            for o in f["options"]:
                if str(o) not in cfg["enum_labels"][f["field"]]: bad(w, f"enum option {o} has no label in enum_labels")
        if "regex" in f:
            try: re.compile(f["regex"])
            except re.error as e: bad(w, f"regex does not compile: {e}")

# SLA notify targets
resolvable = {"current_approver", "supervisor", "delegate"}
for r in cfg["sla"]["rules"]:
    for n in r["notify"]:
        if n not in resolvable: bad(f"sla.rules.{r['id']}.notify", f"'{n}' cannot be resolved")
for r in cfg["sla"]["watched_states"]:
    if r not in cfg["state_machine"]["pending_states"]: bad("sla.watched_states", f"'{r}' is not a pending state")

# corroboration
cc = cfg["deduplication_rules"]["OCRD"]["corroboration"]
cust = {f["field"] for f in domains["CUSTOMER"]["fields"]}
need_tiers = {"EXACT_ID", "CONFIRMED", "UNCONFIRMED", "CONTRADICTED", "AMBIGUOUS_ALIAS", "SUPERSEDED"}
if need_tiers - set(cc["tiers"]): bad("corroboration.tiers", f"missing {sorted(need_tiers - set(cc['tiers']))}")
for s in cc["signals"]:
    w = f"corroboration.signals.{s['id']}"
    if s["strength"] not in ("WEAK", "MEDIUM", "STRONG", "CONFLICT"): bad(w, f"bad strength {s['strength']}")
    for k in ("candidate_field", "existing_field"):
        if k in s and s[k] not in cust: bad(w, f"{k} '{s[k]}' is not a CUSTOMER field")
    if s["kind"] not in ("near_match","field_equal","field_different","email_domain_match","email_domain_contains_alias_token","text_similar","person_similar","phone_match"):
        bad(w, f"unknown kind {s['kind']}")
for r in cc["detail_visible_roles"]: need_role("corroboration.detail_visible_roles", r)

# unreachable routing rules (priority shadowed by an earlier catch-all)
for r in rm[:-1]:
    if r["condition"] == {"all": []}: bad(f"routing_matrix.{r['id']}", "catch-all before the last rule makes later rules unreachable")

if defects:
    print(f"{len(defects)} defect(s):"); [print(" -", d) for d in defects]; sys.exit(1)
print("config_rules.json: no defects")
