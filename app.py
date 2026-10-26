"""Lives Group MDM & Sales Insights - Streamlit shell (spec Sections 2.4 and 3.0).

The page area shows only what a user of the real product would see: a top navigation of plain-named pages, built
from engine_security.visible_tabs() for whoever is signed in. The demo controls sit in the header bar (a role
switch and the Case Synopsis stories) and, while a story runs, a guide bar fixed to the bottom of the window.
Identity is read only through get_current_user(), so Microsoft Entra ID SSO can replace the role switch with a
single-function change.
"""
import importlib

import streamlit as st

import engine_security as es
from tabs import autopilot, common, demo_data, help_guide, ui

# page id -> (config tab id, module, icon). Titles come from page_title(); the order is the navigation order.
PAGES = {
    "new": ("INTAKE", "tabs.tab_intake", ":material/add_circle:"),
    "requests": ("INBOX", "tabs.tab_inbox", ":material/inbox:"),
    "insights": ("ANALYTICS", "tabs.tab_analytics", ":material/insights:"),
    "waiting": ("SLA_BOARD", "tabs.tab_sla_board", ":material/schedule:"),
    "audit": ("AUDIT", "tabs.tab_audit", ":material/fact_check:"),
    "admin": ("ADMIN", "tabs.tab_admin", ":material/tune:"),
}
POPOVER_KEY = "popgen"
REQUEST_TITLES = {"OWN": "Requests", "QUEUE": "Requests", "RELEASE": "Requests", "ALL": "Requests"}
STATIC_TITLES = {"new": "New request", "insights": "Sales insights",
                 "waiting": "Waiting times", "audit": "Audit trail", "admin": "Platform settings"}


def get_current_user(config):
    """The single identity read. Production: replace with the verified Entra ID token's user."""
    return es.get_user(st.session_state.current_user_email, config)


def page_title(page_id, config, user):
    if page_id == "requests":
        return REQUEST_TITLES.get(config["roles"][user["role"]]["row_scope"], "Requests")
    return STATIC_TITLES[page_id]


# ----------------------------------------------------------------------------- demo controls (header)
def _close_toggles():
    """A new key makes a popover a new element, so it comes back closed after an action."""
    st.session_state[POPOVER_KEY] = st.session_state.get(POPOVER_KEY, 0) + 1


def _become(config, email):
    common.switch_user(st.session_state, config, email)
    ui.go(st.session_state, "home")
    _close_toggles()


def _pick_role(config, widget_key):
    entry = next(e for e in config["demo"]["roles"] if e["label"] == st.session_state[widget_key])
    _become(config, entry["email"] if "email" in entry else entry["functions"][0]["email"])


def _pick_function(config, widget_key, entry_label):
    entry = next(e for e in config["demo"]["roles"] if e["label"] == entry_label)
    _become(config, next(f["email"] for f in entry["functions"] if f["label"] == st.session_state[widget_key]))


def _launch_auto(config, sids):
    gen = st.session_state.get(POPOVER_KEY, 0)
    autopilot.launch(st.session_state, config, sids)
    st.session_state[POPOVER_KEY] = gen + 1


def _reset(config):
    gen = st.session_state.get(POPOVER_KEY, 0)
    common.reset_demo(st.session_state)
    st.session_state[POPOVER_KEY] = gen + 1
    ui.go(st.session_state, "home")


def current_role_choice(user, config):
    """The (role entry, function entry or None) of the demo role list that matches who is acting now."""
    users = {u["email"]: u for u in config["users"]}
    for entry in config["demo"]["roles"]:
        for fn in entry.get("functions") or [None]:
            if users[(fn or entry)["email"]]["role"] == user["role"]:
                return entry, fn
    return None, None


def role_toggle(config, user):
    """Switch role. Generic roles only: the named people from the brief live under the stories."""
    roles = config["demo"]["roles"]
    labels = [r["label"] for r in roles]
    entry, fn = current_role_choice(user, config)
    current = entry["label"] if entry else labels[0]
    named = user["email"] not in {(f or r)["email"] for r in roles for f in (r.get("functions") or [None])}
    head = f"Role: {ui.short_role(current)}" + (f" ({fn['label']})" if fn else "") + (f" as {user['display']}" if named else "")
    with st.popover(head, icon=":material/swap_horiz:", width="stretch",
                    key=f"role_pop_{st.session_state.get(POPOVER_KEY, 0)}"):
        st.markdown("**Switch role**")
        # The key carries who is acting, so a story that changes person gives the dropdown a fresh identity instead
        # of leaving the browser holding the previous one (which would switch back on the next click).
        key = f"role_{user['email']}"
        st.selectbox("Role", labels, index=labels.index(current), key=key, label_visibility="collapsed",
                     on_change=_pick_role, args=(config, key))
        if entry and entry.get("functions"):
            names = [f["label"] for f in entry["functions"]]
            st.caption("Which approval step?")
            fkey = f"fn_{user['email']}"
            st.selectbox("Approval step", names, index=names.index(fn["label"]) if fn else 0, key=fkey,
                         label_visibility="collapsed", on_change=_pick_function, args=(config, fkey, entry["label"]))
        st.caption(f"{user.get('persona', '')}")
        st.caption("Demo only. In production, Microsoft Entra ID sign-in decides who you are.")


def story_toggle(config):
    with st.popover("Case Synopsis stories", icon=":material/menu_book:", width="stretch",
                    key=f"story_pop_{st.session_state.get(POPOVER_KEY, 0)}"):
        st.markdown("**Hands-free stories**")
        st.caption("Each story plays by itself on the real screens, keying in every field. In real life every account is tied to "
                   "one role and sees only that role's pages, requests and fields. To show the whole journey, the demo moves "
                   "between roles with the demo role switch, so a real user would only ever see the part shown for their "
                   "own role. Flags explain how it works, the security features and live checks.")
        st.button("Play all stories and role tours", key="auto_all", on_click=_launch_auto, args=(config, autopilot.story_ids()),
                  type="primary", width="stretch")
        for sid in autopilot.story_ids():
            st.button(autopilot.story_label(sid), key=f"auto_{sid}", on_click=_launch_auto, args=(config, [sid]), width="stretch")
        st.divider()
        st.button("Reset demo", key="demo_reset", on_click=_reset, args=(config,), width="stretch")


def _help_go(page):
    ui.go(st.session_state, page)
    _close_toggles()


def _begin_tour():
    st.session_state["tour_begun"] = True


def _splash():
    """Blank start page: the prototype's name and one button. Nothing else renders until the tour begins."""
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.markdown("<div style='height:22vh'></div>", unsafe_allow_html=True)
        ui.page_header("Lives Group MDM & Sales Insights", "Prototype")
        st.button("Begin tour", key="begin_tour", type="primary", on_click=_begin_tour)


def main():
    st.set_page_config(page_title="Lives Group MDM", page_icon=":material/hub:", layout="wide",
                       initial_sidebar_state="collapsed")
    config = common.load_config()
    common.init_state(st.session_state, config)
    if not st.session_state.get("demo_seeded"):                      # sample requests at different stages: no empty pages
        st.session_state["demo_seeded"] = True
        demo_data.seed(st.session_state, config)
    user = get_current_user(config)
    ui.apply_style()
    if not st.session_state.get("tour_begun"):            # splash page above the app, like a login page
        _splash()
        st.stop()
    if st.session_state.get("story"):
        st.markdown("<style>.block-container{padding-bottom:13rem !important}</style>", unsafe_allow_html=True)

    def runner(module_name):
        def run():
            importlib.import_module(module_name).render(config, st.session_state, get_current_user(config))
        return run

    allowed = es.visible_tabs(user["role"], config)
    pages = {"home": st.Page(runner("tabs.tab_home"), title="Home", icon=":material/home:", url_path="home", default=True)}
    for pid, (tab_id, module, icon) in PAGES.items():
        if tab_id not in allowed:
            continue
        pages[pid] = st.Page(runner(module), title=page_title(pid, config, user), icon=icon, url_path=pid)

    nav = st.navigation(list(pages.values()), position="top")
    target = st.session_state.pop("goto", None)
    if target in pages and pages[target] is not nav:
        st.switch_page(pages[target])

    with st.container(key="demo_bar"):                    # fixed into the header bar by CSS
        left, middle, right = st.columns([5, 5, 3])
        with left:
            role_toggle(config, user)
        with middle:
            story_toggle(config)
        with right:
            help_guide.render(config, user, _help_go, f"help_pop_{st.session_state.get(POPOVER_KEY, 0)}")
    nav.run()
    autopilot.inject(st.session_state)                    # invisible loader for the hands-free player
    st.markdown("<div class='lv-footer'>Prototype with demo data. SAP and Outlook are simulated.</div>", unsafe_allow_html=True)
    ui.demo_guide(st.session_state)                       # fixed to the bottom of the window by CSS


if __name__ == "__main__":
    main()
