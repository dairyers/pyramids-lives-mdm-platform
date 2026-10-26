"""Dedup benchmark (spec Section 3.2): precision / recall on data/dedup_labelled_pairs.json.

Each pair is run against its named existing record only. FLAGGED = the record's match has a tier a
person or a block will see (CONFIRMED, UNCONFIRMED, AMBIGUOUS_ALIAS, and the EXACT_ID hard block);
CONTRADICTED, SUPERSEDED and no match are NOT FLAGGED. Run with -s to keep the printed table.
Fails if a negative-control pair is flagged or a pair with an expected_tier gets another tier.
Scores are never asserted.
"""
import json
import pathlib

import engine_mdm as em
from fixtures import CONFIG, FIXTURE_ONLY

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAIRS = json.loads((ROOT / "data" / "dedup_labelled_pairs.json").read_text(encoding="utf-8"))
MASTER = json.loads((ROOT / "data" / "sap_master_mock.json").read_text(encoding="utf-8"))
RECORDS = {**{r["CardCode"]: r for r in FIXTURE_ONLY}, **{r["CardCode"]: r for r in MASTER["OCRD"]}}
FLAGGED_TIERS = {"EXACT_ID", "CONFIRMED", "UNCONFIRMED", "AMBIGUOUS_ALIAS"}
NO_MATCH = "NO_MATCH"


def run_pair(pair):
    existing = RECORDS[pair["existing_code"]]
    matches = em.check_fuzzy_duplicates("CUSTOMER", pair["candidate"], [existing], CONFIG)
    m = next((x for x in matches if x["sap_id"] == pair["existing_code"]), None)
    tier = m["tier"] if m else None
    return {**pair, "tier": tier, "flagged": tier in FLAGGED_TIERS,
            "existing_name": existing["CardName"], "match_basis": m["match_basis"] if m else None}


def benchmark():
    results = [run_pair(p) for p in PAIRS]
    tp = [r for r in results if r["flagged"] and r["label"] == "SAME"]
    fp = [r for r in results if r["flagged"] and r["label"] == "DIFFERENT"]
    fn = [r for r in results if not r["flagged"] and r["label"] == "SAME"]
    tn = [r for r in results if not r["flagged"] and r["label"] == "DIFFERENT"]
    precision = len(tp) / (len(tp) + len(fp)) if tp or fp else 0.0
    recall = len(tp) / (len(tp) + len(fn)) if tp or fn else 0.0
    return results, {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall}


def markdown_table(results):
    lines = ["| Pair | Candidate | Existing record | Label | Tier | Flagged |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['pair_id']} | {r['candidate']['CardName']} | {r['existing_code']} {r['existing_name']} "
                     f"| {r['label']} | {r['tier'] or NO_MATCH} | {'yes' if r['flagged'] else 'no'} |")
    return "\n".join(lines)


def _line(r):
    return (f"  {r['pair_id']}: '{r['candidate']['CardName']}' vs {r['existing_code']} '{r['existing_name']}' "
            f"label={r['label']} tier={r['tier'] or NO_MATCH} ({r['note'] or r['source']})")


def test_dedup_benchmark():
    results, s = benchmark()
    print(f"\n\nDEDUP BENCHMARK: {len(results)} labelled pairs "
          f"(TP {len(s['tp'])}, FP {len(s['fp'])}, FN {len(s['fn'])}, TN {len(s['tn'])})")
    print(f"Precision: {s['precision']:.1%}   Recall: {s['recall']:.1%}")
    print("Misses (SAME, not flagged):")
    print("\n".join(_line(r) for r in s["fn"]) or "  none")
    print("False alarms (DIFFERENT, flagged):")
    print("\n".join(_line(r) for r in s["fp"]) or "  none")
    print("\n" + markdown_table(results) + "\n")

    flagged_controls = [r["pair_id"] for r in results if r["negative_control"] and r["flagged"]]
    wrong_tier = [(r["pair_id"], r["expected_tier"], r["tier"]) for r in results
                  if r["expected_tier"] is not None and r["tier"] != r["expected_tier"]]
    assert not flagged_controls, f"negative controls flagged: {flagged_controls}"
    assert not wrong_tier, f"pairs with an unexpected tier (id, expected, got): {wrong_tier}"


def test_labelled_pairs_cover_spec_tables():
    sources = [p["source"] for p in PAIRS]
    assert sources.count("spec 2.2 name table") == 15
    assert sources.count("spec 2.2b evidence table") >= 10
    extras = [p for p in PAIRS if p["source"] == "extra"]
    assert sum(p["label"] == "SAME" for p in extras) == 5 and sum(p["label"] == "DIFFERENT" for p in extras) == 5
    assert all(p["existing_code"] in RECORDS for p in PAIRS)
