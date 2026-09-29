"""Projects page."""

import html

import streamlit as st

import store
from ui.core import is_demo, ss
from ui.i18n import tr
from ui.project import clear_project, open_project


def page_projects():
    st.markdown(tr("<div class='cf-h2'>Projects</div><div class='cf-lead'>Every paper you turned into content, saved to your account.</div>"),
                unsafe_allow_html=True)
    projects = store.list_projects(ss.user)
    if not projects:
        st.markdown(tr("<div class='cf-card' style='color:var(--muted)'>No projects yet. Create your first one in the Studio.</div>"),
                    unsafe_allow_html=True)
        if st.button(tr("Go to Studio"), type="primary"):
            ss.page = "Studio"
            st.rerun()
        return
    for m in projects:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([4, 1.3, 0.9, 0.9], vertical_alignment="center")
            is_open = ss.get("pid") == m["id"]
            c1.markdown(f"**{html.escape(m.get('headline', 'Untitled'))}**  \n"
                        f"<span style='color:var(--muted);font-size:13.5px'>{html.escape(m.get('doc_name', ''))} · "
                        f"{html.escape(m.get('created', ''))} · {html.escape(m.get('lang', ''))}"
                        f"{' · <b>open now</b>' if is_open else ''}</span>", unsafe_allow_html=True)
            c2.markdown(tr("<span class='vp-badge b-SUPPORTED'>{0} claims supported</span>").format(html.escape(m.get('score', ''))),
                        unsafe_allow_html=True)
            if c3.button(tr("Open"), key=f"open_{m['id']}", width="stretch", type="primary"):
                with st.spinner(tr("Opening project…")):
                    if open_project(m["id"]):
                        ss.page = "Studio"
                        st.rerun()
            if c4.button(tr("Delete"), key=f"del_{m['id']}", width="stretch", disabled=is_demo(),
                         help="Projects of the shared demo account cannot be deleted." if is_demo() else None):
                ss.confirm_delete = m["id"]
            if ss.get("confirm_delete") == m["id"]:
                st.warning(tr("Delete this project permanently?"))
                d1, d2, _ = st.columns([1, 1, 4])
                if d1.button(tr("Yes, delete"), key=f"yes_{m['id']}", type="primary"):
                    store.delete_project(ss.user, m["id"])
                    if ss.get("pid") == m["id"]:
                        clear_project()
                    ss.pop("confirm_delete", None)
                    st.rerun()
                if d2.button(tr("Cancel"), key=f"no_{m['id']}"):
                    ss.pop("confirm_delete", None)
                    st.rerun()
