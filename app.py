"""CiteFlow — turn research into trusted PR content. Run: streamlit run app.py

This file only starts the app; the interface lives in the ui/ package:
  ui/core.py         configuration, secrets, limits, AI clients, monitoring, look, navigation
  ui/project.py      the open project: checking, decisions, images, saving and reopening
  ui/results.py      Studio results: release, posts, images, video, fact check, refinement, export
  ui/login.py        sign-in and account creation
  ui/page_*.py       one module per page (Studio, Projects, Insights, Channels, Account, Admin)
"""
import time

import streamlit as st

import store
from ui.core import DEMO_USER, GEMINI_KEY, HERE, inject_css, limit, log_error, pages, ss, topbar
from ui.i18n import tr
from ui.login import login_screen
from ui.page_account import page_account
from ui.page_admin import page_admin
from ui.page_channels import page_channels
from ui.page_insights import page_insights
from ui.page_projects import page_projects
from ui.page_studio import page_studio

st.set_page_config(page_title="CiteFlow", page_icon=str(HERE / "mark_green.png"), layout="wide",
                   initial_sidebar_state="collapsed")
inject_css()

if not GEMINI_KEY:
    st.error("CiteFlow is not configured yet. Add GEMINI_API_KEY to .streamlit/secrets.toml (see README).")
    st.stop()


@st.cache_resource(show_spinner=False, max_entries=2)
def apply_retention(day):
    """Runs at most once a day per server: deletes projects past the retention period."""
    try:
        n = store.purge_old_projects(limit("retention_days"), {DEMO_USER: limit("demo_retention_days")})
        store.log_event({"kind": "retention", "action": "delete old projects", "ok": True, "detail": f"{n} deleted"})
        return n
    except Exception as e:
        log_error("retention", e)
        return 0


apply_retention(time.strftime("%Y-%m-%d"))

PAGE_VIEWS = {"Studio": page_studio, "Projects": page_projects, "Insights": page_insights,
              "Channels": page_channels, "Account": page_account, "Admin": page_admin}

if "user" not in ss:
    login_screen()
else:
    ss.setdefault("page", "Studio")
    topbar()
    if ss.page not in pages():
        ss.page = "Studio"
    PAGE_VIEWS[ss.page]()

st.markdown(tr("<div class='cf-foot'>CiteFlow · AI drafts and verifies, people approve · DEIK.AI Challenge 2026</div>"),
            unsafe_allow_html=True)
