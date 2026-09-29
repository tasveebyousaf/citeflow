"""Insights page (publishing plan)."""

import html

import streamlit as st

import pipeline as pl
from ui.core import allow, llm, log_error, ss
from ui.i18n import tr
from ui.project import current_content, persist
from ui.results import BRAND

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def day_index(s):
    s = (s or "").strip().lower()
    for i, d in enumerate(DAYS):
        if s.startswith(d[:3].lower()):
            return i
    hu = ["hét", "ked", "sze", "csü", "pén", "szo", "vas"]
    for i, d in enumerate(hu):
        if s.startswith(d):
            return i
    return None


def page_insights():
    st.markdown(tr("<div class='cf-h2'>Insights</div><div class='cf-lead'>When to post, where, and with which hashtags — "
                "planned for the story you are working on.</div>"), unsafe_allow_html=True)
    if "content" not in ss:
        st.markdown(tr("<div class='cf-card' style='color:var(--muted)'>Open or create a project first, then come back for its publishing plan.</div>"),
                    unsafe_allow_html=True)
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button(tr("Go to Studio"), type="primary"):
            ss.page = "Studio"
            st.rerun()
        if c2.button(tr("Open a project")):
            ss.page = "Projects"
            st.rerun()
        return
    st.markdown(tr("<div class='vp-sub'>Project</div><div class='vp-headline' style='font-size:24px'>{0}</div>").format(html.escape(ss.content.headline)),
                unsafe_allow_html=True)
    label = "Refresh plan" if ss.get("plan") else "Create publishing plan"
    if st.button(label, type="primary") and allow("run"):
        with st.spinner(tr("Planning the launch week…")):
            try:
                ss.plan = pl.publish_plan(llm(), current_content())
                persist()
                st.rerun()
            except Exception as e:
                log_error("publishing plan", e)
                if any(t in str(e) for t in pl.TRANSIENT):
                    st.warning(tr("Google's AI service is busy right now. Please try again in a minute."))
                else:
                    st.error(tr('Something went wrong: {0}').format(e))
    plan = ss.get("plan")
    if not plan:
        return
    st.caption(tr("AI recommendations based on typical engagement patterns for each platform and audience, tailored to this "
               "research. They are guidance, not live platform analytics. Times are local (CET)."))
    st.markdown(tr("<div class='cf-card' style='margin:6px 0 16px'><b>Audience.</b> {0}</div>").format(html.escape(plan.audience_summary)),
                unsafe_allow_html=True)

    # week heat-map: which days each platform should be used
    by_name = {p.platform.lower(): p for p in plan.platforms}
    rows = ""
    for key, label in (("linkedin", "LinkedIn"), ("facebook", "Facebook"), ("instagram", "Instagram"), ("x", "X")):
        p = next((v for k, v in by_name.items() if k.startswith(key) or (key == "x" and k in ("x", "twitter", "x (twitter)"))), None)
        best = {day_index(d) for d in (p.best_days if p else [])}
        color = BRAND[key][0]
        cells = "".join(
            f"<td style='text-align:center;padding:8px'><div style='height:26px;border-radius:6px;background:"
            f"{color if i in best else '#efece3'};opacity:{1 if i in best else 1}'></div></td>" for i in range(7))
        times = html.escape(", ".join(p.best_times)) if p else ""
        rows += f"<tr><td style='padding:8px 12px;font-weight:700'>{label}</td>{cells}<td style='padding:8px 12px;color:#5f6b66;font-size:13px'>{times}</td></tr>"
    head = "".join(f"<th style='font-weight:600;color:#5f6b66;font-size:13px'>{tr(d[:3])}</th>" for d in DAYS)
    st.markdown(tr("<div class='cf-card' style='overflow-x:auto'><b>Best days and times</b><table style='width:100%;border-collapse:collapse;margin-top:8px'><tr><th></th>{0}<th style='text-align:left;padding-left:12px;font-weight:600;color:#5f6b66;font-size:13px'>Best times</th></tr>{1}</table></div>").format(head, rows),
                unsafe_allow_html=True)

    cols = st.columns(2, gap="medium")
    for i, p in enumerate(plan.platforms):
        with cols[i % 2]:
            with st.container(border=True):
                st.markdown(f"**{html.escape(p.platform)}**  \n<span style='color:var(--muted);font-size:13.5px'>"
                            f"{html.escape(', '.join(p.best_days))} · {html.escape(', '.join(p.best_times))}</span>",
                            unsafe_allow_html=True)
                chips = " ".join(f"<span style='display:inline-block;background:#eef3ef;color:#2b4c40;border-radius:5px;"
                                 f"padding:3px 8px;margin:2px;font-size:13px;font-weight:600'>{html.escape(h)}</span>" for h in p.hashtags)
                st.markdown(chips, unsafe_allow_html=True)
                st.markdown(tr("<div style='font-size:14px;margin-top:8px'><b>Format:</b> {0}</div><div style='font-size:13.5px;color:var(--muted);margin-top:4px'>{1}</div>").format(html.escape(p.format_tip), html.escape(p.why)),
                            unsafe_allow_html=True)
                with st.popover(tr("Copy hashtags"), width="stretch"):
                    st.code(" ".join(p.hashtags), language=None, wrap_lines=True)

    c1, c2 = st.columns([1.4, 1], gap="medium")
    with c1:
        with st.container(border=True):
            st.markdown(tr("**Launch week**"))
            for s in plan.schedule:
                st.markdown(f"<div style='display:flex;gap:14px;padding:7px 0;border-bottom:1px solid #eeebe2;font-size:14px'>"
                            f"<div style='min-width:120px;font-weight:700;color:#2b4c40'>{html.escape(s.day)} {html.escape(s.time)}</div>"
                            f"<div style='min-width:90px;color:#5f6b66'>{html.escape(s.platform)}</div><div>{html.escape(s.action)}</div></div>",
                            unsafe_allow_html=True)
    with c2, st.container(border=True):
        st.markdown(tr("**Angles to connect with**"))
        for t in plan.trend_angles:
            st.markdown(f"- {t}")
        st.markdown(tr("**Avoid**"))
        for t in plan.avoid:
            st.markdown(f"- {t}")
