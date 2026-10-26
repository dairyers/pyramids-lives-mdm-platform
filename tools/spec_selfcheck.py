"""Independent cross-check of the dedup logic written in the spec (Sections 2.2 and 2.2b).
It extracts the reference Python straight from the spec text, runs the name table, the evidence
table and both ambiguous-alias fixtures, and exits 1 on any failure. Needs the real rapidfuzz.
Usage: python tools/spec_selfcheck.py [path-to-spec]"""
import re, json, sys, copy, pathlib
spec = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "action_items_execution_game_plan_v3.txt")
t = spec.read_text(encoding="utf-8")
cfg = json.loads(re.search(r"```json\n(\{\n  \"meta\".*?\n)```", t, re.S).group(1))

# pull python code blocks of section 2.2 and 2.2b straight out of the spec text
s22 = t[t.index("2.2 ABBREVIATION RESOLVER + FUZZY DEDUPLICATION"):t.index("2.2b EVIDENCE QUORUM")]
s22b = t[t.index("2.2b EVIDENCE QUORUM"):t.index("2.3 STRUCTURED ROUTING")]
code = "\n".join(re.findall(r"```python\n(.*?)```", s22, re.S) + re.findall(r"```python\n(.*?)```", s22b, re.S))
ns = {}
exec(compile(code, "spec_code", "exec"), ns)
check, dup_req, ev_role = ns["check_fuzzy_duplicates"], ns["dup_review_required"], ns["evidence_for_role"]

def rec(code, name, email=None, country="SG", group=100, tax=None, **kw):
    r = {"CardCode": code, "CardName": name, "CardType": "C", "LicTradNum": tax or ("T"+code[1:]+"XYZ0"),
         "GroupCode": group, "U_Country": country, "U_Entity": "SPD"}
    if email: r["E_Mail"] = email
    r.update(kw); return r

C10023 = rec("C10023", "National University Hospital Singapore", "procurement@nuhs-demo.example", tax="198400000X",
             Address="1 Demo Medical Drive", ZipCode="119999", CntctPrsn="Lim Mei Ling", Phone1="+65 6000 1234", U_GSTRegNo="M2-0000123-4")
C10031 = rec("C10031", "National University of Singapore", "admin@nus-demo.example")
C10045 = rec("C10045", "Singapore Management University", "ap@smu-demo.example")
MASTER = [C10023, C10031, C10045]
fails = 0
def expect(label, got, want):
    global fails
    ok = got == want
    fails += (not ok)
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f"   got={got} want={want}"))

def cand(name, email, country="SG", group=100, **kw):
    p = {"CardName": name, "E_Mail": email, "U_Country": country, "GroupCode": group, "LicTradNum": "ZZ9999999Q"}
    p.update(kw); return p

def tier_vs(master, payload, sap_id, config=cfg):
    ms = [m for m in check("CUSTOMER", payload, master, config) if m["sap_id"] == sap_id]
    return ms[0]["tier"] if ms else None

print("== evidence table (Section 2.2b) ==")
nu = "ap@nuhs-demo.example"
expect("3a Nat. Uni. + corp email", tier_vs(MASTER, cand("Nat. Uni. Hospital Singapore", nu), "C10023"), "CONFIRMED")
expect("3b NUHS + corp email",      tier_vs(MASTER, cand("NUHS", nu), "C10023"), "CONFIRMED")
expect("3d NUHS gmail",             tier_vs(MASTER, cand("NUHS", "someone@gmail.com"), "C10023"), "UNCONFIRMED")
expect("3e NUHS other co MY/102",   tier_vs(MASTER, cand("NUHS", "buyer@healthsupplies.example", "MY", 102), "C10023"), "CONTRADICTED")
expect("3f gmail + zip + contact",  tier_vs(MASTER, cand("NUHS", "someone@gmail.com", ZipCode="119999", CntctPrsn="Mei Ling Lim"), "C10023"), "CONFIRMED")
expect("3g gmail + tax typo",       tier_vs(MASTER, cand("NUHS", "someone@gmail.com", LicTradNum="198400001X"), "C10023"), "CONFIRMED")
expect("3h corp email, MY/102",     tier_vs(MASTER, cand("NUHS", nu, "MY", 102), "C10023"), "UNCONFIRMED")
expect("phone + 2 weak",            tier_vs(MASTER, cand("NUHS", "someone@gmail.com", Phone1="6000-1234"), "C10023"), "CONFIRMED")
expect("gmail only",                tier_vs(MASTER, cand("NUHS", "someone@gmail.com", country="", group=""), "C10023"), "UNCONFIRMED")
expect("GST equal + 2 weak",        tier_vs(MASTER, cand("NUHS", "someone@gmail.com", U_GSTRegNo="m2 0000123 4"), "C10023"), "CONFIRMED")
expect("zip only + 2 weak",         tier_vs(MASTER, cand("NUHS", "someone@gmail.com", ZipCode="119999"), "C10023"), "CONFIRMED")
expect("address abbrev + weak (single LOCATION grp + 2 weak)", tier_vs(MASTER, cand("NUHS", "someone@gmail.com", Address="1 demo medical dr"), "C10023"), "CONFIRMED")
expect("contact single name only (needs 2 words)", tier_vs(MASTER, cand("NUHS", "someone@gmail.com", CntctPrsn="Lim"), "C10023"), "UNCONFIRMED")
expect("postal + address = ONE group (+ different country conflict)", tier_vs(MASTER, cand("NUHS", "someone@gmail.com", "MY", 102, ZipCode="119999", Address="1 Demo Medical Drive"), "C10023"), "UNCONFIRMED")
ms = check("CUSTOMER", cand("National University of Singapore", "x@y.example"), MASTER, cfg)
expect("NUS vs C10023 not flagged", any(m["sap_id"]=="C10023" for m in ms), False)
expect("NUS vs C10031 flagged (fuzzy, CONFIRMED)", [m["tier"] for m in ms if m["sap_id"]=="C10031"], ["CONFIRMED"])
p3d = cand("NUHS","someone@gmail.com"); m3d = check("CUSTOMER", p3d, MASTER, cfg)
expect("dup_review_required 3d True", dup_req(m3d, cfg, "CUSTOMER"), True)
m3e = check("CUSTOMER", cand("NUHS","buyer@healthsupplies.example","MY",102), MASTER, cfg)
expect("dup_review_required 3e False", dup_req(m3e, cfg, "CUSTOMER"), False)

print("== ambiguous alias fixtures ==")
def amb_cfg():
    c = copy.deepcopy(cfg); ab = c["deduplication_rules"]["OCRD"]["abbreviation_resolution"]
    ab["alias_map"]["abc"] = ["alpha beta clinic", "apex biomedical centre"]; return c
M_abc = [rec("C20001","Alpha Beta Clinic","buyer@alphabeta.example",group=101), rec("C20002","Apex Biomedical Centre","buyer@apexbio.example",group=101)]
c = amb_cfg()
ms = check("CUSTOMER", cand("ABC","ap@unrelated.example",group=101), M_abc, c)
expect("abc unrelated -> both AMBIGUOUS", sorted((m["sap_id"], m["tier"]) for m in ms), [("C20001","AMBIGUOUS_ALIAS"),("C20002","AMBIGUOUS_ALIAS")])
ms = check("CUSTOMER", cand("ABC","ap@apexbio.example",group=101), M_abc, c)
expect("abc apexbio -> C20002 CONFIRMED / C20001 SUPERSEDED", sorted((m["sap_id"], m["tier"]) for m in ms), [("C20001","SUPERSEDED"),("C20002","CONFIRMED")])

def nuhs_cfg():
    c = copy.deepcopy(cfg); c["deduplication_rules"]["OCRD"]["abbreviation_resolution"]["alias_map"]["nuhs"] = ["national university hospital singapore","national university health system"]; return c
C10052 = rec("C10052","National University Health System","procurement@nuhs-demo.example",tax="199900000K",Address="2 Other Road Tower",ZipCode="119888")
M_n = [C10023, C10052]
ms = check("CUSTOMER", cand("NUHS", nu, ZipCode="119999"), M_n, nuhs_cfg())
expect("which NUHS + zip -> C10023 wins", sorted((m["sap_id"], m["tier"]) for m in ms), [("C10023","CONFIRMED"),("C10052","SUPERSEDED")])
ms = check("CUSTOMER", cand("NUHS", nu), M_n, nuhs_cfg())
expect("which NUHS no zip -> both AMBIGUOUS", sorted((m["sap_id"], m["tier"]) for m in ms), [("C10023","AMBIGUOUS_ALIAS"),("C10052","AMBIGUOUS_ALIAS")])
# default demo config + master that holds BOTH entities: master-derived initialisms alone must surface the ambiguity
ms = check("CUSTOMER", cand("NUHS", nu), M_n, cfg)
expect("master-derived ambiguity (demo alias_map has 1 expansion) -> both AMBIGUOUS", sorted((m["sap_id"], m["tier"]) for m in ms), [("C10023","AMBIGUOUS_ALIAS"),("C10052","AMBIGUOUS_ALIAS")])

print("== evidence_for_role disclosure ==")
corr = [m for m in m3d if m["sap_id"]=="C10023"][0]["corroboration"]
m3f = check("CUSTOMER", cand("NUHS","someone@gmail.com",ZipCode="119999",CntctPrsn="Mei Ling Lim"), MASTER, cfg)
corr_f = [m for m in m3f if m["sap_id"]=="C10023"][0]["corroboration"]
cc = cfg["deduplication_rules"]["OCRD"]["corroboration"]
expect("REQUESTER sees no detail", any("detail" in e for e in ev_role(corr_f, "REQUESTER", cc)), False)
expect("MDM_LEAD sees detail",     all("detail" in e for e in ev_role(corr_f, "MDM_LEAD", cc)), True)

print("== name table (Section 2.2), threshold from config ==")
SMU_SOL = rec("C99001", "Singapore Management University Solutions", "x@smusol-demo.example")
SMU_SOL_PTE = rec("C99002", "Singapore Management University Solutions Pte. Ltd.", "x@smusol-demo.example")
TTSH = rec("C99003", "Tan Tock Seng Hospital", "x@ttsh-demo.example")
SGH = rec("C99004", "Singapore General Hospital", "x@sgh-demo.example")
NHCS = rec("C99005", "National Heart Centre Singapore", "x@nhcs-demo.example")
pairs = [("Nat. Uni. Hospital Singapore",[C10023],True),("NUHS",[C10023],True),("N.U.H.S.",[C10023],True),("NUH",[C10023],True),
         ("Nat Univ Hosp",[C10023],True),("SMU Solutions",[SMU_SOL],True),("SMU Solutions Pte Ltd",[SMU_SOL_PTE],True),
         ("TTSH",[TTSH],True),("Tan Tock Seng Hosp",[TTSH],True),
         ("National University of Singapore",[C10023],False),("NUS",[C10023],False),("SMU Solutions",[C10045],False),
         ("Singapore Eye Hospital",[SGH],False),("National Heart Centre Singapore",[C10023],False),("Tock Seng Clinic",[TTSH],False)]
for name, pool, want in pairs:
    got = bool(check("CUSTOMER", cand(name, "someone@gmail.com"), pool, cfg))
    expect(f"flagged={want}: {name!r} vs {pool[0]['CardName']!r}", got, want)
# NHCS vs hospital: candidate is NHCS-named, existing is hospital
expect("NHCS vs hospital not flagged", bool(check("CUSTOMER", cand("National Heart Centre Singapore","a@b.example"), [C10023], cfg)), False)
print("\nFAILS:", fails)

print("== exact tax-ID hard block still wins ==")
ms = check("CUSTOMER", {"CardName": "Totally Different Name Ltd", "LicTradNum": "198400000x", "E_Mail": "a@b.example"}, MASTER, cfg)
expect("hard block with unrelated name", [(m["sap_id"], m["tier"], m["hard_block"]) for m in ms], [("C10023", "EXACT_ID", True)])
print("FAILS:", fails)
sys.exit(1 if fails else 0)
