"""The administrator decides who sees redacted values: the choice changes what the security engine returns for that role
and nothing else, and a bad request is refused."""
import copy

import pytest

import engine_security as es
import engine_settings as es_set
from fixtures import CONFIG

RECORD = {"CreditLine": 75000, "BankAcct": "0123456789"}


def view(config, role, status="APPROVED"):
    return es.apply_field_security(RECORD, "CUSTOMER", role, config, status=status)


def test_the_auditor_sees_credit_terms_masked_until_the_administrator_allows_them():
    assert view(CONFIG, "AUDITOR")[1]["CreditLine"] == "MASKED"
    trial = copy.deepcopy(CONFIG)
    assert es_set.set_field_policy(trial, "FINANCIAL_TERMS", "AUDITOR", "FULL")
    v, report = view(trial, "AUDITOR")
    assert report["CreditLine"] == "FULL" and v["CreditLine"] == 75000
    assert report["BankAcct"] == "MASKED"                       # the other kind of value did not change
    assert view(CONFIG, "AUDITOR")[1]["CreditLine"] == "MASKED"  # the shipped settings were not touched


def test_other_roles_are_unaffected_by_a_change_for_one_role():
    trial = copy.deepcopy(CONFIG)
    es_set.set_field_policy(trial, "FINANCIAL_TERMS", "AUDITOR", "FULL")
    for role in CONFIG["roles"]:
        if role != "AUDITOR":
            assert view(trial, role)[1] == view(CONFIG, role)[1]


@pytest.mark.parametrize("level", ["HIDDEN", "MASKED", "FULL"])
def test_every_level_can_be_chosen_and_is_what_the_engine_returns(level):
    trial = copy.deepcopy(CONFIG)
    es_set.set_field_policy(trial, "BANK_DETAILS", "SUPERVISOR", level)
    assert view(trial, "SUPERVISOR")[1]["BankAcct"] == level


def test_a_state_tied_policy_becomes_the_plain_level_chosen():
    trial = copy.deepcopy(CONFIG)
    es_set.set_field_policy(trial, "BANK_DETAILS", "PAYMENTS_OFFICER", "MASKED")
    assert view(trial, "PAYMENTS_OFFICER", "APPROVED")[1]["BankAcct"] == "MASKED"


def test_choosing_what_a_role_already_has_changes_nothing():
    assert es_set.set_field_policy(copy.deepcopy(CONFIG), "FINANCIAL_TERMS", "AUDITOR", "MASKED") == []


@pytest.mark.parametrize("args", [("GENERAL", "AUDITOR", "FULL"), ("FINANCIAL_TERMS", "NO_SUCH_ROLE", "FULL"),
                                  ("FINANCIAL_TERMS", "AUDITOR", "EVERYTHING")])
def test_bad_requests_are_refused(args):
    with pytest.raises(ValueError):
        es_set.set_field_policy(copy.deepcopy(CONFIG), *args)


def test_email_never_carries_bank_details_whatever_the_administrator_chooses():
    trial = copy.deepcopy(CONFIG)
    es_set.set_field_policy(trial, "BANK_DETAILS", "AUDITOR", "FULL")
    assert es.apply_field_security(RECORD, "CUSTOMER", "AUDITOR", trial, status="APPROVED", channel="EMAIL")[1]["BankAcct"] == "HIDDEN"
