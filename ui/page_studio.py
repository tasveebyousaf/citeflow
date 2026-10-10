"""Studio page (upload and create)."""

import time

import streamlit as st
from PIL import Image

import pipeline as pl
import store
from ui.core import ai_problem, allow, is_demo, limit, llm, log_error, ss, verify_now
from ui.i18n import choose, tr
from ui.project import make_cards, persist
from ui.results import results_view


def page_studio():
    left, right = st.columns([1.05, 1], gap="large")
    with left:
        st.markdown(tr("""
<div class="cf-hero">
  <div class="cf-eyebrow">For university communications teams</div>
  <h1>Turn research into <em>trusted</em> PR content.</h1>
  <p>Upload a paper. CiteFlow drafts the press release, social posts and video, and links every claim back to the page it came from.</p>
</div>
<div class="cf-points">
  <div class="cf-point"><b>Traceable</b>Every claim linked to its source page</div>
  <div class="cf-point"><b>Human-reviewed</b>Nothing goes out without approval</div>
  <div class="cf-point"><b>Channel-ready</b>Release, four platforms, video</div>
</div>"""), unsafe_allow_html=True)
    with right, st.container(border=True):
        pdf = st.file_uploader(tr("Research paper (PDF)"), type=["pdf"])
        c1, c2 = st.columns(2)
        lang = choose(c1.selectbox, tr("Content language"), ["English", "Hungarian"])
        tone = choose(c2.selectbox, tr("Audience"), ["General public", "Students", "Industry partners"])
        with st.expander(tr("Add a researcher (optional)")):
            person = st.text_input(tr("Name and title"), placeholder=tr("Dr. Anna Kovács"), max_chars=100)
            photo_file = st.file_uploader(tr("Photo (only with the person's consent)"), type=["jpg", "jpeg", "png"])
        go = st.button(tr("Create verified content"), type="primary", width="stretch", disabled=not pdf)
        if is_demo():
            st.caption(tr("You are using the shared demo account: other reviewers can see projects created here."))

    if go:
        problem = pl.check_pdf(pdf.getvalue(), limit("max_pdf_pages"))
        if problem:
            st.error(problem)
            go = False
        elif not allow("run"):
            go = False

    # A fixed slot for progress and errors, present on every run: the page keeps the same layout, so new results
    # replace the old ones in place and the page never shows two copies while work is running.
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    slot = st.empty()
    if go:
        try:
            with slot.container(), st.status(tr("Reading the paper…"), expanded=False) as status:
                data = pdf.getvalue()
                ss.pdf_bytes = data
                ss.proofs = {}
                if pl.looks_scanned(data):
                    status.update(label=tr("This PDF is scanned: reading it with text recognition (about 5–10 s per page)…"))
                ss.passages, ss.doc_title = pl.extract_passages(data)
                if not ss.passages:
                    raise ValueError("No readable text was found in this PDF.")
                ss.figures = pl.extract_figures(data)
                ss.doc_name = pdf.name
                status.update(label=tr("Writing the story…"))
                engine = llm()
                ss.content = pl.generate_content(engine, ss.passages, f"{lang}; audience: {tone}")
                ss.release, ss.lang = ss.content.press_release, lang
                ss.posts = ss.content.posts.model_dump()
                status.update(label=tr("Checking every claim against the paper…"))
                ss.results = verify_now(pl.build_items(ss.content.headline, ss.release, ss.content.scenes, ss.posts,
                                                       ss.content.visual), writer=engine)
                make_cards()
                ss.person = person.strip()
                ss.photo = Image.open(photo_file).convert("RGB") if photo_file else None
                for k in ("video", "hype", "plan"):
                    ss.pop(k, None)
                ss.chat = []
                ss.accepted = []
                ss.pid, ss.created = store.new_project_id(), time.strftime("%Y-%m-%d %H:%M")
                persist()
                status.update(label=tr("Done"), state="complete")
        except Exception as e:
            log_error("create content", e)
            friendly = ai_problem(e, "click **Create verified content** again")
            with slot.container():
                if friendly:
                    st.warning(friendly)
                else:
                    st.error(tr('Something went wrong: {0}').format(e))

    if "results" in ss:
        results_view()
