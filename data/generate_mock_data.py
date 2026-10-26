"""Seeded mock-data generator (spec Sections 3.1 and 4.1).

Writes, next to this file:
  sap_master_mock.json            existing OCRD (customers + vendors), OITM and OOCR records
  regional_orders.csv             PO history (analytics.regional_view.mock_history_months) with noise rows and
                                  injected test cases
  expected_analytics_checks.json  CardCodes of every injected analytics test case + the short series key
  scenarios.json                  the five scenario payloads of spec 3.1 (variants 3a to 3h)
  dedup_labelled_pairs.json       labelled SAME / DIFFERENT pairs for the dedup benchmark

Usage: python data/generate_mock_data.py
All bank accounts, SWIFT codes, tax IDs, phone numbers and addresses are fake demo values.
"""
import json
import pathlib

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = HERE.parent / "config_rules.json"
SEED = 2026
DEMO_DATE = pd.Timestamp("2026-10-28")          # submission date; history ends the month before
SHORT_HISTORY_MONTHS = 10                        # < recent + baseline window (15)
SHORT_SERIES_MONTHS = 10                         # a short history: the forecast still works from forecast.min_points (6) months
CHURN_TARGET_DELTAS = [-0.45, -0.50, -0.55]      # 40-60% below baseline (spec 4.1)
DECLINER_TARGET_DELTAS = [-0.15, -0.17]          # declining, but above the -0.20 churn threshold
NOISE_SHARE = 0.12

# ----------------------------------------------------------------------------- SAP master seed
# (code, name, country, entity, group, email domain, extra optional fields)
NAMED_CUSTOMERS = [
    ("C10023", "National University Hospital Singapore", "SG", "SPD", 100, "nuhs-demo.example",
     {"E_Mail": "procurement@nuhs-demo.example", "LicTradNum": "198400000X", "Address": "1 Demo Medical Drive",
      "ZipCode": "119999", "CntctPrsn": "Lim Mei Ling", "Phone1": "+65 6000 1234", "U_GSTRegNo": "M2-0000123-4"}),
    ("C10031", "National University of Singapore", "SG", "QTS", 100, "nus-demo.example",
     {"E_Mail": "admin@nus-demo.example"}),
    ("C10045", "Singapore Management University", "SG", "QTG", 100, "smu-demo.example",
     {"E_Mail": "ap@smu-demo.example"}),
    ("C10007", "Singapore General Hospital", "SG", "SPD", 100, "sgh-demo.example",
     {"Address": "7 Demo Hospital Avenue", "ZipCode": "169999", "CntctPrsn": "Tan Wei Ming", "Phone1": "+65 6000 2001"}),
    ("C10012", "Tan Tock Seng Hospital", "SG", "QTS", 100, "ttsh-demo.example",
     {"Address": "11 Demo Jalan Tan Tock Seng", "ZipCode": "308999", "Phone1": "+65 6000 3001"}),
    ("C10018", "National Heart Centre Singapore", "SG", "SPD", 100, "nhcs-demo.example",
     {"ZipCode": "169998", "CntctPrsn": "Ong Hui Min"}),
    ("C10003", "Changi General Hospital", "SG", "QTG", 100, "cgh-demo.example", {"ZipCode": "529999"}),
    ("C10005", "Khoo Teck Puat Hospital", "SG", "QTS", 100, "ktph-demo.example", {"Phone1": "+65 6000 5001"}),
]
OTHER_CUSTOMERS = [
    ("C10001", "Orchid Medical Clinic", "SG", "SPD", 101),
    ("C10002", "Harbourfront Diagnostics Pte Ltd", "SG", "QTS", 101),
    ("C10004", "Kallang Biolab Pte Ltd", "SG", "SPD", 101),
    ("C10006", "Novena Specialist Centre", "SG", "QTG", 101),
    ("C10008", "Raffles Place Medical Group", "SG", "SPD", 101),
    ("C10009", "Tampines Family Clinic", "SG", "QTS", 101),
    ("C10010", "Lumina Diagnostics Sdn Bhd", "MY", "SPD", 101),
    ("C10011", "Penang Valley Hospital", "MY", "SPD", 100),
    ("C10013", "Klang Medical Supplies Sdn Bhd", "MY", "QTS", 102),
    ("C10014", "Seri Kembangan Clinic", "MY", "QTS", 101),
    ("C10015", "Johor Bahru Pathology Lab Sdn Bhd", "MY", "QTG", 101),
    ("C10016", "Ipoh Heritage Hospital", "MY", "QTG", 100),
    ("C10017", "Siam Precision Lab Co Ltd", "TH", "SPD", 101),
    ("C10019", "Bangkok Riverside Hospital", "TH", "SPD", 100),
    ("C10020", "Chiang Mai Wellness Clinic", "TH", "QTS", 101),
    ("C10021", "Phuket Coastal Medical Centre", "TH", "QTS", 101),
    ("C10022", "Thonburi Diagnostic Services", "TH", "QTG", 101),
    ("C10024", "Jakarta Harapan Hospital", "ID", "SPD", 100),
    ("C10025", "Surabaya Prima Laboratorium", "ID", "SPD", 101),
    ("C10026", "Bandung Sehat Clinic", "ID", "QTS", 101),
    ("C10027", "Medan Utama Healthcare", "ID", "QTS", 101),
    ("C10028", "Bali Nusa Medical Supplies", "ID", "QTG", 102),
    ("C10029", "Saigon Lotus Hospital", "VN", "SPD", 100),
    ("C10030", "Hanoi Red River Clinic", "VN", "SPD", 101),
    ("C10032", "Da Nang Coastal Lab", "VN", "SPD", 101),
    ("C10033", "Hue Imperial Medical Centre", "VN", "QTS", 101),
    ("C10034", "Mekong Trading Supplies", "VN", "QTS", 102),
    ("C10035", "Can Tho Delta Healthcare", "VN", "QTG", 101),
]
COUNTRY_PHONE = {"SG": "+65", "MY": "+60", "TH": "+66", "ID": "+62", "VN": "+84"}

# Analytics roles of specific customers (spec 4.1 injected test cases)
CHURNERS = ["C10001", "C10016", "C10026"]
DECLINERS = ["C10022", "C10030"]
CROSS_SELL_GAPS = ["C10004", "C10013", "C10021", "C10027", "C10032"]
SHORT_HISTORY = ["C10009"]
SHORT_SERIES_CUSTOMER = "C10035"                 # the only QTG/VN customer; buys C only recently
SHORT_SERIES_PRODUCT = "C"

VENDORS = [
    ("V20001", "Demo Diagnostics Manufacturing GmbH", "QTS", "SG"),
    ("V20002", "Sample Imaging Systems Ltd", "SPD", "SG"),
    ("V20003", "Placeholder Reagents Asia Pte Ltd", "SPD", "SG"),
    ("V20004", "Mock Consumables Sdn Bhd", "QTG", "MY"),
    ("V20005", "Test Logistics Thailand Co Ltd", "QTS", "TH"),
    ("V20006", "Fictional Lab Equipment PT", "SPD", "ID"),
    ("V20007", "Example Cold Chain Vietnam", "QTG", "VN"),
    ("V20008", "Dummy Medical Plastics Pte Ltd", "QTS", "SG"),
]

ITEMS = [
    ("Equipment", ["Hematology Analyzer HX-500", "Chemistry Analyzer CA-200", "Portable Ultrasound Scanner US-3",
                   "Patient Monitor PM-12", "Infusion Pump IP-7", "Benchtop Centrifuge CF-24",
                   "ECG Machine 12-Lead", "Autoclave Steriliser 50L"]),
    ("IVD", ["Glucose Reagent Kit GL-100", "HbA1c Test Cartridge", "Troponin I Rapid Test", "CRP Reagent Pack",
             "Lipid Panel Reagent Set", "Dengue NS1 Antigen Test", "Urinalysis Strip Kit",
             "Influenza A/B Rapid Test", "Blood Typing Reagent Set", "Thyroid TSH Assay Kit"]),
    ("Consumable", ["Nitrile Examination Gloves M", "Blood Collection Tube 4ml", "Sterile Syringe 5ml",
                    "Pipette Tips 200ul", "Specimen Container 60ml", "Alcohol Swab Pad", "Surgical Face Mask 3-Ply"]),
]
ITEM_GROUP = {"Equipment": 100, "IVD": 101, "Consumable": 102}
ITEM_UOM = {"Equipment": "Unit", "IVD": "Kit", "Consumable": "Box"}
ITEM_PREFIX = {"Equipment": "EQ", "IVD": "IVD", "Consumable": "CON"}
ITEM_PRICE = {"Equipment": (8000, 40000), "IVD": (60, 400), "Consumable": (5, 60)}

COST_CENTERS = [
    ("CC-1001", "Corporate Headquarters", "SPD", "priya@livesgroup.com"),
    ("CC-2001", "Equipment Field Service - SPD", "SPD", "priya@livesgroup.com"),
    ("CC-2002", "Equipment Field Service - QTS", "QTS", "weijie@livesgroup.com"),
    ("CC-3001", "Consumables Distribution - QTG", "QTG", "weijie@livesgroup.com"),
    ("CC-4010", "IVD Regional Sales - QTS", "QTS", "weijie@livesgroup.com"),
    ("CC-5001", "Regulatory Affairs Office", "SPD", "ra.officer@livesgroup.com"),
    ("CC-6001", "Finance Shared Services", "QTS", "weijie@livesgroup.com"),
    ("CC-7001", "Warehouse Operations Singapore", "QTG", "priya@livesgroup.com"),
]

# Fixture-only records (live in tests/fixtures.py, referenced here by code only)
FIXTURE_SMU_SOLUTIONS = "C10061"           # Singapore Management University Solutions
FIXTURE_SMU_SOLUTIONS_PTE = "C10062"       # Singapore Management University Solutions Pte. Ltd.


def _slug(name):
    return "".join(ch for ch in name.lower() if ch.isalnum())[:14]


def build_master(rng, cfg):
    synced = cfg["analytics"]["noise_filter"]["status_in"][0]
    customers = []
    for code, name, country, entity, group, domain, extra in NAMED_CUSTOMERS:
        rec = {"CardCode": code, "CardName": name, "CardType": "C",
               "LicTradNum": f"T{code[1:]}{rng.integers(1000, 9999)}K", "GroupCode": group,
               "U_Entity": entity, "U_Country": country, "E_Mail": f"purchasing@{domain}",
               "CreditLine": float(rng.choice([50000, 100000, 250000])), "GroupNum": int(rng.choice([1, 2])),
               "U_ReqStatus": synced}
        rec.update(extra)
        customers.append(rec)
    for code, name, country, entity, group in OTHER_CUSTOMERS:
        rec = {"CardCode": code, "CardName": name, "CardType": "C",
               "LicTradNum": f"T{code[1:]}{rng.integers(1000, 9999)}K", "GroupCode": group,
               "U_Entity": entity, "U_Country": country, "E_Mail": f"orders@{_slug(name)}-demo.example",
               "CreditLine": float(rng.choice([10000, 25000, 40000])), "GroupNum": int(rng.choice([-1, 1, 2])),
               "U_ReqStatus": synced}
        if rng.random() < 0.5:
            rec["Phone1"] = f"{COUNTRY_PHONE[country]} 7{rng.integers(100, 999)} {rng.integers(1000, 9999)}"
        if rng.random() < 0.4:
            rec["ZipCode"] = str(rng.integers(10000, 99999))
        customers.append(rec)

    vendors = []
    for i, (code, name, entity, country) in enumerate(VENDORS, start=1):
        vendors.append({"CardCode": code, "CardName": name, "CardType": "S",
                        "LicTradNum": f"VAT{code[1:]}{rng.integers(100, 999)}X",
                        "E_Mail": f"ar@{_slug(name)}-demo.example", "U_Country": country,
                        "BankCode": f"Example Test Bank {i}", "BankAcct": f"99900{i:02d}{rng.integers(1000, 9999)}",
                        "SwiftCode": f"TEST{country}{'X' * 2}", "GroupNum": int(rng.choice([1, 2, 3])),
                        "U_Entity": entity, "U_ReqStatus": synced})

    items = []
    for cat, names in ITEMS:
        for j, name in enumerate(names, start=1):
            lo, hi = ITEM_PRICE[cat]
            item = {"ItemCode": f"ITM-{ITEM_PREFIX[cat]}-{j:03d}", "ItemName": name, "ItmsGrpCod": ITEM_GROUP[cat],
                    "U_Category": cat, "InvntryUom": ITEM_UOM[cat], "U_Entity": str(rng.choice(["SPD", "QTS", "QTG"])),
                    "LastPurPrc": round(float(rng.uniform(lo, hi)), 2), "U_ReqStatus": synced}
            if cat != "Equipment":
                item["U_RegLicenseNo"] = f"HSA-{ITEM_PREFIX[cat]}-2025-{rng.integers(1000, 9999)}"
            items.append(item)

    cost_centers = [{"PrcCode": c, "PrcName": n, "U_Entity": e, "U_BudgetMgr": m, "U_ReqStatus": synced}
                    for c, n, e, m in COST_CENTERS]
    return {"meta": {"generated_by": "data/generate_mock_data.py", "seed": SEED,
                     "note": "All identifiers, bank accounts, SWIFT codes, phones and addresses are fake demo values."},
            "OCRD": customers + vendors, "OITM": items, "OOCR": cost_centers}


# ----------------------------------------------------------------------------- order history
def build_orders(rng, cfg, customers):
    lines = list(cfg["analytics"]["product_lines"].keys())            # A, B, C
    nf = cfg["analytics"]["noise_filter"]
    months = pd.date_range(end=DEMO_DATE.to_period("M").to_timestamp() - pd.offsets.MonthBegin(1),
                           periods=cfg["analytics"]["regional_view"]["mock_history_months"], freq="MS")
    n = len(months)
    t = np.arange(n)
    level_range = {lines[0]: (4, 10), lines[1]: (30, 80), lines[2]: (60, 200)}
    price_range = {lines[0]: (8000, 25000), lines[1]: (150, 400), lines[2]: (15, 60)}
    phase = {(c, l): rng.uniform(0, 2 * np.pi) for c in COUNTRY_PHONE for l in lines}

    qty = {}                                                           # (CardCode, line) -> float array (nan = inactive)
    for cust in customers:
        code, country = cust["CardCode"], cust["U_Country"]
        trend = rng.uniform(0.0, 0.01)
        for l in lines:
            if code in CROSS_SELL_GAPS and l == lines[2]:
                continue                                               # never buys the consequent product
            lo, hi = level_range[l]
            base = rng.uniform(lo, hi)
            season = 1 + 0.12 * np.sin(2 * np.pi * t / 12 + phase[(country, l)])
            noise = rng.normal(1.0, 0.06, n)
            q = base * (1 + trend * t) * season * noise
            if code in SHORT_HISTORY:
                q[: n - SHORT_HISTORY_MONTHS] = np.nan
            if code == SHORT_SERIES_CUSTOMER and l == SHORT_SERIES_PRODUCT:
                q[: n - SHORT_SERIES_MONTHS] = np.nan
            qty[(code, l)] = q

    churn_cfg = cfg["analytics"]["churn"]
    recent, baseline = churn_cfg["recent_window_months"], churn_cfg["baseline_window_months"]
    targets = dict(zip(CHURNERS, CHURN_TARGET_DELTAS)) | dict(zip(DECLINERS, DECLINER_TARGET_DELTAS))
    for code, delta in targets.items():
        tot = sum(np.nan_to_num(q) for (c, _), q in qty.items() if c == code)
        base_avg = tot[n - recent - baseline: n - recent].mean()
        rec_avg = tot[n - recent:].mean()
        factor = (1 + delta) * base_avg / rec_avg
        for (c, l), q in qty.items():
            if c == code:
                q[n - recent:] *= factor

    names = {c["CardCode"]: c for c in customers}
    unit_price = {(c, l): rng.uniform(*price_range[l]) for (c, l) in qty}
    rows = []
    for (code, l), q in qty.items():
        cust = names[code]
        for i, m in enumerate(months):
            if np.isnan(q[i]):
                continue
            quantity = max(int(round(q[i])), 1)
            rows.append({"CardCode": code, "CardName": cust["CardName"], "U_Entity": cust["U_Entity"],
                         "U_Country": cust["U_Country"], "U_ProductLine": l, "Quantity": quantity,
                         "LineTotal_USD": round(quantity * unit_price[(code, l)] * rng.uniform(0.97, 1.03), 2),
                         "DocDate": (m + pd.Timedelta(days=int(rng.integers(0, 28)))).date().isoformat(),
                         "RecordType": nf["record_type"], "U_ReqStatus": str(rng.choice(nf["status_in"]))})

    noise_specs = [("INQUIRY", ["OPEN", nf["status_in"][0]]), ("DRAFT_PO", ["DRAFT"]),
                   ("CANCELLED_PO", ["CANCELLED", nf["status_in"][1]]), (nf["record_type"], ["PENDING", "REJECTED"])]
    for idx in rng.choice(len(rows), size=int(len(rows) * NOISE_SHARE), replace=False):
        src = dict(rows[idx])
        rtype, statuses = noise_specs[int(rng.integers(0, len(noise_specs)))]
        src["RecordType"], src["U_ReqStatus"] = rtype, str(rng.choice(statuses))
        src["Quantity"] = int(src["Quantity"] * rng.uniform(0.5, 3.0)) + 1
        rows.append(src)

    df = pd.DataFrame(rows).sort_values(["DocDate", "CardCode", "U_ProductLine"], kind="stable").reset_index(drop=True)
    df.insert(0, "DocEntry", np.arange(100001, 100001 + len(df)))
    cols = ["DocEntry", "CardCode", "CardName", "U_Entity", "U_Country", "U_ProductLine",
            "Quantity", "LineTotal_USD", "DocDate", "RecordType", "U_ReqStatus"]
    return df[cols], months


def build_expected_checks(customers, months):
    short = next(c for c in customers if c["CardCode"] == SHORT_SERIES_CUSTOMER)
    return {"history_start": months[0].date().isoformat(), "history_end_month": months[-1].date().isoformat(),
            "as_of": (months[-1] + pd.offsets.MonthEnd(0)).date().isoformat(),
            "churners": CHURNERS, "churner_target_deltas": dict(zip(CHURNERS, CHURN_TARGET_DELTAS)),
            "near_threshold_decliners": DECLINERS,
            "decliner_target_deltas": dict(zip(DECLINERS, DECLINER_TARGET_DELTAS)),
            "cross_sell_gaps": CROSS_SELL_GAPS, "short_history_customers": SHORT_HISTORY,
            "short_series": {"U_Entity": short["U_Entity"], "U_Country": short["U_Country"],
                             "U_ProductLine": SHORT_SERIES_PRODUCT, "months": SHORT_SERIES_MONTHS}}


# ----------------------------------------------------------------------------- scenarios (spec 3.1)
S1_PAYLOAD = {"ItemName": "Lab Consumable Diagnostic Reagent", "ItmsGrpCod": 101, "U_Category": "IVD",
              "InvntryUom": "Box", "U_RegLicenseNo": "", "U_Entity": "SPD", "LastPurPrc": 85.0}
# Street address, postal code, contact person and phone are mandatory on every customer and vendor request. The values
# below are deliberately different from the seeded record they are compared with, so each example keeps its
# original duplicate-evidence outcome (the variants that test a postal code / contact match override them).
CONTACT_BLOCK = {"Address": "88 Example Street", "ZipCode": "238888", "CntctPrsn": "Alex Tan", "Phone1": "+65 6123 4567"}
S2_PAYLOAD = {"CardName": "SMU Solutions", "LicTradNum": "201912345Z", "GroupCode": 100,
              "E_Mail": "procurement@smusolutions.example", "CreditLine": 75000, "GroupNum": 3,
              "BankCode": "Example Bank", "BankAcct": "0123456789", "SwiftCode": "EXMPSGSG",
              "U_Entity": "QTS", "U_Country": "SG", "U_Requester": "marcus@livesgroup.com",
              **CONTACT_BLOCK}
S3_BASE = {"CardName": "NUHS", "LicTradNum": "202600777K", "GroupCode": 100, "E_Mail": "ap@nuhs-demo.example",
           "CreditLine": 20000.0, "GroupNum": 1, "U_Entity": "SPD", "U_Country": "SG",
           "U_Requester": "marcus@livesgroup.com", **CONTACT_BLOCK}
S3_VARIANTS = {
    "3a": ("Nat. Uni. Hospital Singapore, corporate email",
           {"CardName": "Nat. Uni. Hospital Singapore", "E_Mail": "ap@nuhs-demo.example"},
           {"C10023": {"flagged": True, "match_basis": "ALIAS_EXPANDED", "tier": "CONFIRMED"}}, True),
    "3b": ("NUHS, corporate email", {"E_Mail": "ap@nuhs-demo.example"},
           {"C10023": {"flagged": True, "match_basis": "ALIAS_EXPANDED", "tier": "CONFIRMED"}}, True),
    "3c": ("National University of Singapore (negative control vs the hospital)",
           {"CardName": "National University of Singapore", "E_Mail": "procurement@nus-demo.example"},
           {"C10023": {"flagged": False}, "C10031": {"flagged": True, "match_basis": "FUZZY", "tier": "CONFIRMED"}},
           True),
    "3d": ("NUHS, free-mail address only", {"E_Mail": "someone@gmail.com"},
           {"C10023": {"flagged": True, "match_basis": "ALIAS_EXPANDED", "tier": "UNCONFIRMED"}}, True),
    "3e": ("NUHS from another company in Malaysia",
           {"E_Mail": "buyer@healthsupplies.example", "U_Country": "MY", "GroupCode": 102},
           {"C10023": {"flagged": True, "match_basis": "ALIAS_EXPANDED", "tier": "CONTRADICTED"}}, False),
    "3f": ("NUHS, free mail, postal code and contact person",
           {"E_Mail": "someone@gmail.com", "ZipCode": "119999", "CntctPrsn": "Mei Ling Lim"},
           {"C10023": {"flagged": True, "match_basis": "ALIAS_EXPANDED", "tier": "CONFIRMED"}}, True),
    "3g": ("NUHS, free mail, tax ID one digit off", {"E_Mail": "someone@gmail.com", "LicTradNum": "198400001X"},
           {"C10023": {"flagged": True, "match_basis": "ALIAS_EXPANDED", "tier": "CONFIRMED"}}, True),
    "3h": ("NUHS, corporate email but Malaysia / distributor group",
           {"E_Mail": "ap@nuhs-demo.example", "U_Country": "MY", "GroupCode": 102},
           {"C10023": {"flagged": True, "match_basis": "ALIAS_EXPANDED", "tier": "UNCONFIRMED"}}, True),
}


def build_scenarios(cfg):
    req_if = cfg["messages"]["required_if"].format(label="Regulatory License No", field="U_RegLicenseNo",
                                                   dep_value="IVD")
    variants = {}
    for vid, (desc, overrides, expected, dup_review) in S3_VARIANTS.items():
        variants[vid] = {"id": vid, "description": desc, "payload": {**S3_BASE, **overrides},
                         "expected": {"matches": expected, "dup_review_required": dup_review}}
    return {
        "S1": {"id": "S1", "title": "Amy's Incomplete Lab Consumable Request", "source": "Appendix B & C",
               "persona_email": "amy@livesgroup.com", "domain": "ITEM", "payload": S1_PAYLOAD,
               "fix": {"U_RegLicenseNo": "HSA-IVD-2026-0881"},
               "expected": {"errors": [req_if], "after_fix_rule_id": "R04", "after_fix_status": "PENDING_REGULATORY",
                            "visible_to": ["REGULATORY_OFFICER"], "not_visible_to": ["FINANCE_APPROVER"]},
               "comparison": {"old_way": "18-day email bounce-back", "new_way": "blocked in 0 seconds"}},
        "S2": {"id": "S2", "title": "Wei Jie's Overlooked High-Credit Customer", "source": "Appendix B",
               "persona_email": "marcus@livesgroup.com", "approver_email": "weijie@livesgroup.com",
               "release_email": "ap.officer@livesgroup.com", "domain": "CUSTOMER", "payload": S2_PAYLOAD,
               "expected": {"resolved_name": "singapore management university solutions", "flagged": False,
                            "rule_id": "R03", "status": "PENDING_FINANCE",
                            "full_bank_account_only_for": "PAYMENTS_OFFICER",
                            "full_bank_account_only_in_states": ["APPROVED", "SYNCED_TO_SAP"]}},
        "S3": {"id": "S3", "title": "Priya's Duplicate National University Hospital", "source": "Appendix B & Section 4",
               "persona_email": "marcus@livesgroup.com", "reviewer_email": "priya@livesgroup.com",
               "domain": "CUSTOMER", "existing_code": "C10023", "base_payload": S3_BASE, "variants": variants},
        "S4": {"id": "S4", "title": "Marcus's Misrouted New Business Unit Request", "source": "Appendix B",
               "persona_email": "marcus@livesgroup.com", "domain": "COST_CENTER",
               "payload": {"PrcCode": "CC-9001", "PrcName": "New BU Regional Direct", "U_Entity": "QTG",
                           "U_BudgetMgr": "marcus@livesgroup.com"},
               "expected": {"valid": True, "flagged": False, "rule_id": "R99", "status": "PENDING_MDM",
                            "visible_to": ["MDM_LEAD"], "not_visible_to": ["FINANCE_APPROVER", "REGULATORY_OFFICER"]}},
        "S5": {"id": "S5", "title": "The 18-Day Bottleneck Simulator", "source": "Appendix C",
               "persona_email": "supervisor@livesgroup.com",
               "secondary_emails": ["weijie@livesgroup.com", "priya@livesgroup.com"],
               "uses_scenario": "S2", "start_state": "PENDING_FINANCE", "slider_days": [0, 2, 4, 8, 18],
               "expected": {"fired_by_day": {str(d): [r["action"] for r in cfg["sla"]["rules"]
                                                      if r["after_hours"] <= d * 24]
                                             for d in [0, 2, 4, 8, 18]},
                            "without_engine_day_18": "still unseen"}},
    }


# ----------------------------------------------------------------------------- dedup labelled pairs
def _cand(name, email="someone@gmail.com", country="SG", group=100, tax="202600777K", **extra):
    p = {"CardName": name, "LicTradNum": tax, "GroupCode": group, "E_Mail": email, "CreditLine": 20000.0,
         "GroupNum": 1, "U_Entity": "SPD", "U_Country": country, "U_Requester": "marcus@livesgroup.com"}
    for k in [k for k, v in list(p.items()) if v is None]:
        del p[k]
    p.update(extra)
    return p


def _pair(pid, source, candidate, existing, label, expected_tier=None, negative_control=False, note=""):
    return {"pair_id": pid, "source": source, "candidate": candidate, "existing_code": existing, "label": label,
            "expected_tier": expected_tier, "negative_control": negative_control, "note": note}


def build_labelled_pairs():
    nu, gm = "ap@nuhs-demo.example", "someone@gmail.com"
    name_table = [
        ("Nat. Uni. Hospital Singapore", "C10023", True), ("NUHS", "C10023", True), ("N.U.H.S.", "C10023", True),
        ("NUH", "C10023", True), ("Nat Univ Hosp", "C10023", True), ("SMU Solutions", FIXTURE_SMU_SOLUTIONS, True),
        ("SMU Solutions Pte Ltd", FIXTURE_SMU_SOLUTIONS_PTE, True), ("TTSH", "C10012", True),
        ("Tan Tock Seng Hosp", "C10012", True),
        ("National University of Singapore", "C10023", False), ("NUS", "C10023", False),
        ("SMU Solutions", "C10045", False), ("Singapore Eye Hospital", "C10007", False),
        ("National Heart Centre Singapore", "C10023", False), ("Tock Seng Clinic", "C10012", False),
    ]
    pairs = [_pair(f"N{i:02d}", "spec 2.2 name table", _cand(n), code, "SAME" if same else "DIFFERENT",
                   negative_control=not same)
             for i, (n, code, same) in enumerate(name_table, start=1)]

    evidence = [
        ("Nat. Uni. Hospital Singapore", dict(email=nu), "SAME", "CONFIRMED", False, "3a"),
        ("NUHS", dict(email=nu), "SAME", "CONFIRMED", False, "3b"),
        ("NUHS", dict(email=gm), "SAME", "UNCONFIRMED", False, "3d: two weak groups only"),
        ("NUHS", dict(email="buyer@healthsupplies.example", country="MY", group=102), "DIFFERENT", "CONTRADICTED",
         True, "3e: another company"),
        ("NUHS", dict(email=gm, ZipCode="119999", CntctPrsn="Mei Ling Lim"), "SAME", "CONFIRMED", False, "3f"),
        ("NUHS", dict(email=gm, tax="198400001X"), "SAME", "CONFIRMED", False, "3g: tax ID typo"),
        ("NUHS", dict(email=nu, country="MY", group=102), "DIFFERENT", "UNCONFIRMED", False,
         "3h: regional sister company; a separate customer record, flagged for review by design"),
        ("NUHS", dict(email=gm, Phone1="6000-1234"), "SAME", "CONFIRMED", False, "phone + country + group"),
        ("NUHS", dict(email=gm, country=None, group=None), "SAME", "UNCONFIRMED", False, "gmail, no other fields"),
        ("NUHS", dict(email=gm, U_GSTRegNo="m2 0000123 4"), "SAME", "CONFIRMED", False, "GST + country + group"),
        ("NUHS", dict(email=gm, ZipCode="119999"), "SAME", "CONFIRMED", False, "postal code + country + group"),
        ("NUHS", dict(email=gm, Address="1 demo medical dr"), "SAME", "CONFIRMED", False,
         "abbreviated address + country + group"),
    ]
    for i, (name, kw, label, tier, neg, note) in enumerate(evidence, start=1):
        pairs.append(_pair(f"E{i:02d}", "spec 2.2b evidence table", _cand(name, **kw), "C10023", label, tier, neg, note))
    pairs.append(_pair("E13", "spec 2.2b evidence table", _cand("National University of Singapore", "x@y.example"),
                       "C10031", "SAME", "CONFIRMED", False, "plain fuzzy: it IS that customer"))
    pairs.append(_pair("E14", "spec 2.2b evidence table", _cand("National University of Singapore", "x@y.example"),
                       "C10023", "DIFFERENT", None, True, "NUS must not match the hospital"))

    extras = [
        ("Singapore Genral Hospital", dict(email="purchasing@sgh-demo.example"), "C10007", "SAME", "typo"),
        ("SGH", dict(email=gm), "C10007", "SAME", "acronym, free mail"),
        ("Khoo Teck Puat Hosp", dict(email="stores@ktph-demo.example"), "C10005", "SAME", "token abbreviation"),
        ("National Heart Center Singapore", dict(email="finance@nhcs-demo.example"), "C10018", "SAME",
         "US spelling of centre"),
        ("Lumina Diagnostic Sdn Bhd", dict(email="orders@luminadiagnost-demo.example", country="MY", group=101),
         "C10010", "SAME", "singular/plural typo"),
        ("Changi General Clinic", dict(email="admin@changiclinic.example", group=101), "C10003", "DIFFERENT",
         "clinic vs hospital look-alike"),
        ("Tan Tock Seng Pharma", dict(email="sales@ttspharma.example", group=102), "C10012", "DIFFERENT",
         "pharma vs hospital look-alike"),
        ("National University Polyclinic", dict(email="ops@nupoly.example"), "C10023", "DIFFERENT",
         "polyclinic vs hospital look-alike"),
        ("Bangkok Riverside Clinic", dict(email="info@bkkriverclinic.example", country="TH", group=101), "C10019",
         "DIFFERENT", "clinic vs hospital look-alike"),
        ("Saigon Lotus Trading", dict(email="sales@saigonlotustrading.example", country="VN", group=102), "C10029",
         "DIFFERENT", "trading company vs hospital look-alike"),
    ]
    for i, (name, kw, code, label, note) in enumerate(extras, start=1):
        pairs.append(_pair(f"X{i:02d}", "extra", _cand(name, **kw), code, label, None, False, note))
    return pairs


def main():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rng = np.random.default_rng(SEED)
    master = build_master(rng, cfg)
    customers = [r for r in master["OCRD"] if r["CardType"] == "C"]
    orders, months = build_orders(rng, cfg, customers)
    outputs = {
        "sap_master_mock.json": master,
        "expected_analytics_checks.json": build_expected_checks(customers, months),
        "scenarios.json": build_scenarios(cfg),
        "dedup_labelled_pairs.json": build_labelled_pairs(),
    }
    for fname, obj in outputs.items():
        (HERE / fname).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    orders.to_csv(HERE / "regional_orders.csv", index=False)

    pairs = outputs["dedup_labelled_pairs.json"]
    print(f"sap_master_mock.json: {len(customers)} customers, {len(master['OCRD']) - len(customers)} vendors, "
          f"{len(master['OITM'])} items, {len(master['OOCR'])} cost centers")
    print(f"regional_orders.csv: {len(orders)} rows, {orders['CardCode'].nunique()} customers, "
          f"{months[0].date()} .. {months[-1].date()}")
    print("  RecordType counts:", orders["RecordType"].value_counts().to_dict())
    print(f"expected_analytics_checks.json: churners {CHURNERS}, decliners {DECLINERS}, "
          f"cross-sell gaps {CROSS_SELL_GAPS}, short history {SHORT_HISTORY}, "
          f"short series {outputs['expected_analytics_checks.json']['short_series']}")
    print(f"scenarios.json: {', '.join(outputs['scenarios.json'])} (S3 variants "
          f"{', '.join(outputs['scenarios.json']['S3']['variants'])})")
    print(f"dedup_labelled_pairs.json: {len(pairs)} pairs "
          f"({sum(p['label'] == 'SAME' for p in pairs)} SAME, {sum(p['label'] == 'DIFFERENT' for p in pairs)} DIFFERENT, "
          f"{sum(p['negative_control'] for p in pairs)} negative controls)")


if __name__ == "__main__":
    main()
