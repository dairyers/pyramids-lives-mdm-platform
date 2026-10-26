"""The help guide: every topic is well formed and points at a real page, and realistic questions (including typos and
different wording) land on the right topic. No AI is involved, so the same question always gives the same answer."""
import pytest

import engine_help as eh
from fixtures import CONFIG

DATA = eh.load_help()
INDEX = eh.Index(DATA)
TOPICS = {t["id"]: t for t in DATA["topics"]}
PAGES = {"home", "new", "requests", "insights", "waiting", "audit", "admin"}

# (what a user might type, topic that must be the top answer)
QUESTIONS = [
    ("how do I raise a new customer", "new_request"),
    ("i want to create a vendor", "new_request"),
    ("where do i add a supplier", "new_request"),
    ("set up a new cost centre", "new_request"),
    ("why can't I send my form, a field is blank", "required_fields"),
    ("which fields are mandatory", "required_fields"),
    ("why does it ask if this is the same company", "duplicate_question"),
    ("it says a similar customer already exists", "duplicate_question"),
    ("my request is blocked because of the tax number", "tax_stop"),
    ("how do I add a reason for the approver", "reason_note"),
    ("where is my request", "my_requests"),
    ("track the status of what I submitted", "my_requests"),
    ("what is waiting for me", "needs_me"),
    ("what do I need to do next", "needs_me"),
    ("how do I approve a request", "approve_reject"),
    ("where is the reject button", "approve_reject"),
    ("my request got rejected", "rejected_fix"),
    ("who approves a vendor request", "who_approves"),
    ("how does a request get created in SAP", "ready_for_sap"),
    ("which requests are overdue", "overdue"),
    ("who is slow to approve", "overdue"),
    ("when do reminder emails go out", "reminders"),
    ("change the reminder days", "change_reminders"),
    ("what does the orange banner mean", "notes_banner"),
    ("who can see the bank account number", "bank_details"),
    ("which roles can see what", "who_sees_what"),
    ("show me the audit log", "audit_trail"),
    ("who approved this and when", "audit_trail"),
    ("where is sales growth by country", "growth"),
    ("what should sales do first", "recommended_actions"),
    ("which orders are used in the forecast", "orders_vs_enquiries"),
    ("how do I download the numbers as csv", "download_numbers"),
    ("forecast demand for the next 12 months", "forecast"),
    ("which forecast model is most accurate", "forecast_models"),
    ("which customers are buying less", "churn"),
    ("who could we cross sell consumables to", "cross_sell"),
    ("how can I change the approval rules", "admin_rules"),
    ("give a person access to raise requests", "admin_people"),
    ("how do i add an approver for a new business unit", "admin_new_approver"),
    ("how do i add a new data domain", "admin_new_type"),
    ("the duplicate check is too strict", "admin_duplicates"),
    ("how do I undo a settings change", "undo_settings"),
    ("how do I switch role", "switch_role"),
    ("i cannot see the sales insights page", "cannot_see_page"),
    ("is the sap connection real", "limits"),
    # typos
    ("how do i aprove a requst", "approve_reject"),
    ("downlaod the csv", "download_numbers"),
    ("remnider days", "change_reminders"),
]


def test_every_topic_is_complete_and_unique():
    ids = [t["id"] for t in DATA["topics"]]
    assert len(ids) == len(set(ids))
    for t in DATA["topics"]:
        assert t["title"] and t["answer"] and t["words"], t["id"]
        assert t["page"] is None or t["page"] in PAGES, t["id"]
        assert (t["tab"] is None) or t["tab"] in {tab for r in CONFIG["roles"].values() for tab in r["tabs"]}, t["id"]
    assert all(s in TOPICS for s in DATA["suggested"])


def test_page_and_tab_agree_with_the_shell():
    import re
    import pathlib
    shell = (pathlib.Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    pairs = dict(re.findall(r'"(\w+)": \("(\w+)", "tabs\.', shell))        # page id -> config tab id
    for t in DATA["topics"]:
        if t["page"] in pairs:
            assert t["tab"] == pairs[t["page"]], t["id"]


@pytest.mark.parametrize("question,expected", QUESTIONS)
def test_question_lands_on_the_right_topic(question, expected):
    hits = INDEX.search(question)
    assert hits, f"no answer for: {question}"
    assert hits[0][1]["id"] == expected, [(round(s, 1), t["id"]) for s, t in hits]


# Different wording again, written after the topics were final: other word forms, other words with the same meaning.
# The guide shows up to three answers, so the right topic must be among them, and first most of the time.
PARAPHRASES = [
    ("registering a supplier", "new_request"), ("onboarding new clients", "new_request"), ("creating a cost-centre", "new_request"),
    ("what fields must be completed", "required_fields"), ("compulsory information", "required_fields"),
    ("duplicates warning", "duplicate_question"), ("duplicated companies", "duplicate_question"),
    ("approvals pending for me", "needs_me"), ("my outstanding tasks", "needs_me"),
    ("approving requests", "approve_reject"), ("rejecting a request", "approve_reject"),
    ("declined requests", "rejected_fix"), ("rejections", "rejected_fix"),
    ("delayed approvals", "overdue"), ("approvers who are late", "overdue"), ("escalations", "reminders"),
    ("reminding approvers", "reminders"), ("notifications timing", "reminders"),
    ("exporting the sales table", "download_numbers"), ("downloading data", "download_numbers"),
    ("forecasting", "forecast"), ("forecasted demand", "forecast"), ("predicting sales", "forecast"),
    ("customers leaving", "churn"), ("lost clients", "churn"), ("selling more to existing customers", "cross_sell"),
    ("changing roles", "admin_people"), ("editing permissions", "admin_people"),
    ("modifying workflow rules", "admin_rules"), ("approval routes", "admin_rules"),
    ("reverting changes", "undo_settings"), ("undoing edits", "undo_settings"),
    ("auditing", "audit_trail"), ("who did what", "audit_trail"), ("accessing logs", "audit_trail"),
    ("orders enquiry difference", "orders_vs_enquiries"), ("cancelled orders included", "orders_vs_enquiries"),
    ("bank account visibility", "bank_details"), ("sensitive data", "who_sees_what"),
    ("switching users", "switch_role"), ("hidden pages", "cannot_see_page"),
]


def test_other_wording_finds_the_topic_among_the_three_answers_shown():
    first = 0
    for question, expected in PARAPHRASES:
        ids = [t["id"] for _, t in INDEX.search(question)]
        assert expected in ids, (question, ids)
        first += ids[0] == expected
    assert first / len(PARAPHRASES) >= 0.8, first


def test_different_forms_of_a_word_become_one_token():
    t = lambda s: eh.tokens(s, DATA)                                       # noqa: E731
    assert t("approve") == t("approves") == t("approved") == t("approving") == t("approver") == t("approval")
    assert t("create") == t("creating") == t("created")
    assert t("download") == t("downloading") == t("export")
    assert t("escalation") == t("escalations") == t("reminder")            # a listed word also catches its other forms
    assert t("cost-centre") == t("cost center") == t("cost centers")


def test_similar_words_list_is_clean_and_editable():
    rows = [{"word": w, "same_as": s} for w, s in DATA["synonyms"].items()]
    assert eh.clean_synonyms(rows)[1] == []                                # the shipped list has no chains or repeats
    c = _trial()
    out = eh.set_synonyms(c, rows + [{"word": "Tea ", "same_as": "coffee"}])
    assert out == ["Added 'tea' = 'coffee'"] and c["help"]["synonyms"]["tea"] == "coffee"
    for bad in ([{"word": "a b", "same_as": "x"}], [{"word": "x", "same_as": "x"}],
                [{"word": "x", "same_as": "y"}, {"word": "x", "same_as": "z"}],
                [{"word": "x", "same_as": "y"}, {"word": "y", "same_as": "z"}]):
        with pytest.raises(ValueError):
            eh.set_synonyms(_trial(), bad)


def test_nonsense_gets_no_answer_and_same_question_same_answer():
    assert INDEX.search("purple elephant bicycle") == []
    assert INDEX.search("") == []
    assert [t["id"] for _, t in INDEX.search("approve a request")] == [t["id"] for _, t in INDEX.search("approve a request")]


def _rows(data):
    page = {pid: pid for pid in data["pages"]}
    return [{"id": t["id"], "title": t["title"], "words": list(t["words"]), "answer": t["answer"], "page": page.get(t["page"])}
            for t in data["topics"]]


def _trial():
    import copy
    return {"help": copy.deepcopy(DATA)}


def test_page_list_in_the_file_matches_the_shell_and_every_topic_uses_it():
    import pathlib
    import re
    shell = (pathlib.Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    pairs = dict(re.findall(r'"(\w+)": \("(\w+)", "tabs\.', shell))
    for pid, p in DATA["pages"].items():
        assert p["tab"] == pairs.get(pid)                                  # home has no tab and is not in PAGES
    assert all(t["page"] is None or t["page"] in DATA["pages"] for t in DATA["topics"])


def test_saving_unchanged_rows_changes_nothing():
    c = _trial()
    assert eh.set_topics(c, _rows(c["help"])) == []


def test_edit_add_and_remove_a_topic():
    c = _trial()
    rows = _rows(c["help"])
    rows[0]["answer"] = "A new answer."
    rows.append({"id": None, "title": "Order a coffee", "words": ["coffee", " tea "], "answer": "Ask the pantry.", "page": "home"})
    removed = rows.pop(1)["title"]
    out = eh.set_topics(c, rows)
    assert any(o.startswith("Edited") for o in out) and "Added 'Order a coffee'" in out and f"Removed '{removed}'" in out
    new = {t["id"]: t for t in c["help"]["topics"]}
    assert new["order_a_coffee"]["words"] == ["coffee", "tea"] and new["order_a_coffee"]["tab"] is None
    assert [h[1]["id"] for h in eh.Index(c["help"]).search("coffee")] == ["order_a_coffee"]


def test_bad_rows_are_refused_with_every_problem_named():
    c = _trial()
    rows = _rows(c["help"])
    rows[0]["title"] = rows[1]["title"]                                    # duplicate title
    rows[2]["answer"] = " "
    rows[3]["words"] = []
    rows[4]["page"] = "nowhere"
    with pytest.raises(ValueError) as e:
        eh.set_topics(c, rows)
    msg = str(e.value)
    assert "Titles must be different" in msg and "no answer" in msg and "at least one phrase" in msg and "does not exist" in msg
    assert c["help"] == DATA                                               # nothing changed
    with pytest.raises(ValueError):
        eh.set_topics(c, [])


def test_the_guide_reads_the_live_copy_and_falls_back_to_the_file():
    c = _trial()
    c["help"]["topics"][0]["title"] = "Changed title"
    assert eh.help_of(c)["topics"][0]["title"] == "Changed title"
    assert eh.help_of({})["topics"][0]["title"] == DATA["topics"][0]["title"]


def test_roles_are_taken_from_the_config():
    sales = TOPICS["growth"]
    assert eh.can_open(sales, "MDM_LEAD", CONFIG) and not eh.can_open(sales, "REQUESTER", CONFIG)
    assert "MDM Analyst" in eh.roles_with_tab(sales["tab"], CONFIG)
    assert eh.can_open(TOPICS["needs_me"], "REQUESTER", CONFIG)               # Home is open to everyone
