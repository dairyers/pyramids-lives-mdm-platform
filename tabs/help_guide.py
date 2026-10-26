"""The Help popover in the header bar: type a question, read the written answer, press Take me there.

Lookup only (engine_help); no AI and no network. A page the current role cannot open is explained rather than linked.
"""
import json

import streamlit as st

import engine_help as eh

QUERY_KEY = "help_q"


@st.cache_resource
def _built(content):
    """The search index for one version of the help topics (keyed by their content, so an edit gets a fresh index)."""
    return eh.Index(json.loads(content))


def index_for(data):
    return _built(json.dumps(data, sort_keys=True))


def _pick(query):
    st.session_state[QUERY_KEY] = query                       # callback: safe to set a widget's value here


def _go(page, on_go, query_key):
    st.session_state[query_key] = ""
    on_go(page)


def _answer(topic, role, config, on_go, ns, query_key):
    with st.container(border=True):
        st.markdown(f"**{topic['title']}**")
        st.write(topic["answer"])
        if topic["page"] is None:
            return
        if eh.can_open(topic, role, config):
            st.button("Take me there", key=f"{ns}_go_{topic['id']}", icon=":material/arrow_forward:",
                      on_click=_go, args=(topic["page"], on_go, query_key))
        else:
            st.caption("Your current role cannot open this page. Roles that can: "
                       + ", ".join(eh.roles_with_tab(topic["tab"], config)) + ".")


def show_answers(query, config, user, on_go, ns="help", query_key=QUERY_KEY):
    """Write the answers to `query` (up to three, with Take me there buttons). False when nothing matched."""
    hits = index_for(eh.help_of(config)).search(query)
    for _, topic in hits:
        _answer(topic, user["role"], config, on_go, ns, query_key)
    return bool(hits)


def render(config, user, on_go, popover_key):
    """The Help popover. on_go(page_id) opens a page and closes the popover (supplied by the shell)."""
    data = eh.help_of(config)
    with st.popover("Help", icon=":material/help:", width="stretch", key=popover_key):
        st.markdown("**Help guide**")
        st.caption(data["intro"])
        query = st.text_input("Your question", key=QUERY_KEY, label_visibility="collapsed",
                              placeholder="For example: where do I download the numbers?")
        if not query.strip():
            ui_topics = [t for t in data["topics"] if t["id"] in data.get("suggested", [])]
        else:
            if show_answers(query, config, user, on_go):
                return
            st.caption(data["no_match"])
            ui_topics = [t for t in data["topics"] if t["id"] in data.get("suggested", [])]
        for t in ui_topics:
            st.button(t["title"], key=f"help_s_{t['id']}", on_click=_pick, args=(t["title"],), width="stretch")
