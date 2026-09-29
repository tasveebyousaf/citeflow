"""Account page."""

import html

import streamlit as st

import store
from ui.core import ss, team_accounts
from ui.i18n import language_picker, tr
from ui.login import privacy_note


def page_account():
    head, lang_col = st.columns([4, 1], vertical_alignment="bottom")
    head.markdown(tr("<div class='cf-h2'>Account</div>"), unsafe_allow_html=True)
    with lang_col:
        st.caption(tr("Interface language"))
        language_picker("lang_account")
    with st.container(border=True):
        st.markdown(f"**{html.escape(ss.get('user_name') or ss.user)}**  \n<span style='color:var(--muted)'>{html.escape(ss.user)}</span>",
                    unsafe_allow_html=True)
        n = len(store.list_projects(ss.user))
        conn = sum(1 for v in ss.get("channels", {}).values() if v)
        st.markdown(tr("<span style='color:var(--muted)'>{0} project(s) saved · {1} channel(s) connected</span>").format(n, conn),
                    unsafe_allow_html=True)
        st.markdown(tr("**How CiteFlow works**  \nAI drafts the content and checks every claim against the paper. "
                    "Deterministic rules double-check numbers. A person approves before anything is published."))
        if st.button(tr("Sign out"), type="primary"):
            for k in list(ss.keys()):
                del ss[k]
            st.rerun()
    with st.container(border=True):
        st.markdown(tr("**Privacy and your data**"))
        st.markdown(privacy_note())
        if ss.user in team_accounts():
            st.caption(tr("This is a team or demo account managed by the CiteFlow administrators, so it cannot be deleted here."))
        elif store.is_registered(ss.user):
            with st.expander(tr("Delete my account and all my data")):
                st.warning(tr("This permanently deletes your account, all projects, uploaded papers, videos and channel links."))
                with st.form("delete_account"):
                    pw = st.text_input(tr("Type your password to confirm"), type="password")
                    really = st.form_submit_button(tr("Delete everything"), type="primary")
                if really:
                    if store.check_user(ss.user, pw):
                        store.delete_user(ss.user)
                        for k in list(ss.keys()):
                            del ss[k]
                        st.rerun()
                    else:
                        st.error(tr("The password is not correct."))
