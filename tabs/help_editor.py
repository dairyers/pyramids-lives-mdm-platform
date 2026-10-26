"""Platform settings, Help guide tab: the IT / System Administrator edits the help topics without touching a file. Each
row is a topic: its title, the phrases people might type, the written answer and the page it can open. Rows can be added
and removed. A question box tries the draft before it is applied; Apply goes through the same checked, audited and
undoable path as every other setting. A second table lists similar words (supplier = vendor). The stopwords, the suffix list and the ranking numbers stay in
data/help_topics.json."""
import pandas as pd
import streamlit as st

import engine_help as eh
from tabs import help_guide, ui

SEPARATOR = "; "
WORD, MEANS = "Word people type", "Treat it as"
TITLE, PHRASES, ANSWER, OPENS ="Topic", "Phrases people might type (separate with ;)", "Answer", "Opens page"


def _frame(data):
    label = {pid: lab for pid, lab in eh.page_choices(data)}
    return pd.DataFrame([{"id": t["id"], TITLE: t["title"], PHRASES: SEPARATOR.join(t["words"]), ANSWER: t["answer"],
                          OPENS: label[t["page"]]} for t in data["topics"]])


def _rows(frame, data):
    """Editor rows back to topic rows (blank cells arrive as None)."""
    page = {lab: pid for pid, lab in eh.page_choices(data)}
    return [{"id": r.get("id") if isinstance(r.get("id"), str) else None, "title": r[TITLE],
             "words": [w for w in (r[PHRASES] or "").split(";")], "answer": r[ANSWER],
             "page": page.get(r[OPENS])} for r in frame.to_dict("records")]


def _try_it(data, draft_topics, draft_synonyms):
    ui.section("Try a question", "See which topic would answer it, using your draft rather than what is live.")
    question = st.text_input("Question to try", key="help_try", placeholder="For example: how do I change a reminder?",
                             label_visibility="collapsed")
    if not question.strip():
        return
    trial = {**data, "topics": draft_topics, "synonyms": draft_synonyms}
    hits = help_guide.index_for(trial).search(question)
    if not hits:
        st.caption("No topic would answer that. The user would see the suggested topics instead.")
        return
    for rank, (score, topic) in enumerate(hits, 1):
        st.markdown(f"{rank}. **{topic['title']}**" + ("  (the answer shown first)" if rank == 1 else ""))


def render(config, state, user, commit):
    data = eh.help_of(config)
    ui.section("Help guide", "The topics behind the Help button. Edit the wording, add a topic or remove one.",
               tip="Each topic is a question people ask, the phrases they might type, the answer and the page it can open. The "
                   "guide only returns what is written here: it is not an AI and cannot make anything up. Changes apply to "
                   "this session, are written to the audit trail and can be undone. Download the rules to keep them.")
    gen = state.get("admin_gen", 0)
    labels = [lab for _, lab in eh.page_choices(data)]
    frame = _frame(data)
    edited = ui.editor(frame, hide_index=True, num_rows="dynamic", key=f"adm_help_{gen}",
                       column_order=[TITLE, PHRASES, ANSWER, OPENS],
                       column_config={"id": None,
                                      OPENS: st.column_config.SelectboxColumn(OPENS, options=labels, default=labels[-1], required=True),
                                      TITLE: st.column_config.TextColumn(TITLE, required=True),
                                      ANSWER: st.column_config.TextColumn(ANSWER, width="large", required=True)})
    rows = _rows(edited, data)
    topics, errors = eh.clean_rows(rows, data)
    for e in errors:
        st.warning(e, icon=":material/warning:")
    st.caption(f"{len(topics)} topic(s). Answers are shown exactly as written. If a role cannot open the page, the guide says "
               "which roles can.")
    if st.button("Apply help topics", key="adm_apply_help", type="primary", disabled=bool(errors)):
        commit(state, user, "Help guide", lambda c: eh.set_topics(c, rows))

    ui.section("Similar words", "Words that should be understood as another word, so different wording finds the same topic.",
               tip="Endings are handled automatically (approve, approving and approver count as one word), and small typos are "
                   "repaired. Use this list for different words with the same meaning, such as supplier and vendor. Put each "
                   "word on its own row; the right-hand word is the one the topics use.")
    syn_frame = pd.DataFrame({WORD: list(data["synonyms"]), MEANS: list(data["synonyms"].values())})
    syn_edit = ui.editor(syn_frame, hide_index=True, num_rows="dynamic", key=f"adm_syn_{gen}",
                         column_config={WORD: st.column_config.TextColumn(WORD, required=True),
                                        MEANS: st.column_config.TextColumn(MEANS, required=True)})
    syn_rows = [{"word": r[WORD], "same_as": r[MEANS]} for r in syn_edit.to_dict("records")]
    synonyms, syn_errors = eh.clean_synonyms(syn_rows)
    for e in syn_errors:
        st.warning(e, icon=":material/warning:")
    st.caption(f"{len(synonyms)} similar word(s).")
    if st.button("Apply similar words", key="adm_apply_syn", type="primary", disabled=bool(syn_errors)):
        commit(state, user, "Help guide, similar words", lambda c: eh.set_synonyms(c, syn_rows))
    if not errors and not syn_errors:
        _try_it(data, topics, synonyms)
