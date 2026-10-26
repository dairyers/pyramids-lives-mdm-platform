"""Fixture master for the MDM and benchmark tests (spec Sections 2.2, 2.2b and 3.1).

C10023 carries the full spec 3.1 seed. Records marked fixture-only are NOT in
data/sap_master_mock.json: the SMU Solutions entities, C10052 National University Health System
and C20001 / C20002 for the ambiguous alias 'abc'. All values are fake demo data.
"""
import copy

from engine_mdm import load_config

CONFIG = load_config()


def customer(code, name, email, country="SG", group=100, tax=None, entity="SPD", **extra):
    rec = {"CardCode": code, "CardName": name, "CardType": "C", "LicTradNum": tax or f"T{code[1:]}0000K",
           "GroupCode": group, "U_Entity": entity, "U_Country": country, "E_Mail": email}
    rec.update(extra)
    return rec


C10023 = customer("C10023", "National University Hospital Singapore", "procurement@nuhs-demo.example",
                  tax="198400000X", Address="1 Demo Medical Drive", ZipCode="119999", CntctPrsn="Lim Mei Ling",
                  Phone1="+65 6000 1234", U_GSTRegNo="M2-0000123-4")
C10031 = customer("C10031", "National University of Singapore", "admin@nus-demo.example")
C10045 = customer("C10045", "Singapore Management University", "ap@smu-demo.example")
C10007 = customer("C10007", "Singapore General Hospital", "purchasing@sgh-demo.example")
C10012 = customer("C10012", "Tan Tock Seng Hospital", "purchasing@ttsh-demo.example")
C10018 = customer("C10018", "National Heart Centre Singapore", "purchasing@nhcs-demo.example")

# fixture-only records
SMU_SOLUTIONS = customer("C10061", "Singapore Management University Solutions", "ap@smusol-demo.example", entity="QTS")
SMU_SOLUTIONS_PTE = customer("C10062", "Singapore Management University Solutions Pte. Ltd.", "ap@smusol-demo.example",
                             entity="QTS")
C10052 = customer("C10052", "National University Health System", "procurement@nuhs-demo.example", tax="199900000K",
                  Address="2 Other Road Tower", ZipCode="119888")
C20001 = customer("C20001", "Alpha Beta Clinic", "buyer@alphabeta.example", group=101)
C20002 = customer("C20002", "Apex Biomedical Centre", "buyer@apexbio.example", group=101)

FIXTURE_MASTER = [C10023, C10031, C10045]
FIXTURE_ONLY = [SMU_SOLUTIONS, SMU_SOLUTIONS_PTE, C10052, C20001, C20002]
ALL_FIXTURES = {r["CardCode"]: r for r in [C10023, C10031, C10045, C10007, C10012, C10018] + FIXTURE_ONLY}


def candidate(name, email="someone@gmail.com", country="SG", group=100, tax="202600777K", **extra):
    """A complete, valid CUSTOMER payload; pass country=None / group=None to leave a field out."""
    p = {"CardName": name, "LicTradNum": tax, "GroupCode": group, "E_Mail": email, "CreditLine": 20000.0,
         "GroupNum": 1, "U_Entity": "SPD", "U_Country": country, "U_Requester": "marcus@livesgroup.com"}
    p = {k: v for k, v in p.items() if v is not None}
    p.update(extra)
    return p


def config_with_alias(config, key, expansions):
    c = copy.deepcopy(config)
    c["deduplication_rules"]["OCRD"]["abbreviation_resolution"]["alias_map"][key] = list(expansions)
    return c


def abc_config(config=CONFIG):
    return config_with_alias(config, "abc", ["alpha beta clinic", "apex biomedical centre"])


def which_nuhs_config(config=CONFIG):
    return config_with_alias(config, "nuhs", ["national university hospital singapore",
                                              "national university health system"])
