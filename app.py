"""CiteFlow — turn research into trusted PR content. Run: streamlit run app.py"""
import csv
import html
import io
import os
import tempfile
import urllib.parse
import zipfile

import streamlit as st
from PIL import Image

import pipeline as pl
import video as vid

st.set_page_config(page_title="CiteFlow", page_icon="◆", layout="wide", initial_sidebar_state="collapsed")
ss = st.session_state

# ------------------------------------------------------------------ configuration (hidden from users)


def secret(name):
    val = ""
    try:
        if name in st.secrets:
            val = str(st.secrets[name])
    except Exception:
        pass
    clean = lambda s: "" if s.strip().strip('"\'“”‘’').strip().startswith("paste-your") else s.strip().strip('"\'“”‘’').strip()
    return clean(val) or clean(os.environ.get(name, ""))


GEMINI_KEY, PEXELS_KEY = secret("GEMINI_API_KEY"), secret("PEXELS_API_KEY")


@st.cache_resource(show_spinner=False)
def model_chain(key):
    try:
        found = pl.list_flash_models(key)
    except Exception:
        found = []
    return (found or ["gemini-3.8-flash", "gemini-2.5-flash"])[:4]


def llm():
    chain = model_chain(GEMINI_KEY)
    return pl.LLM(GEMINI_KEY, chain[0], fallbacks=chain[1:])

# ------------------------------------------------------------------ styling

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root { --navy:#0B1633; --ink:#1B2440; --muted:#667085; --line:#E6E8F0; --bg:#F6F7FB; --accent:#FF7A45;
        --ok:#12B76A; --warn:#F79009; --bad:#F04438; --review:#CA8504; }
html, body, [class*="css"], .stApp { font-family:'Inter',system-ui,sans-serif !important; color:var(--ink); }
.stApp { background:var(--bg); }
#MainMenu, header[data-testid="stHeader"], footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], [data-testid="collapsedControl"] { display:none !important; }
.block-container { padding-top:0 !important; max-width:1180px; }
.vp-nav { display:flex; align-items:center; justify-content:space-between; padding:22px 0 18px; border-bottom:1px solid var(--line); margin-bottom:34px; }
.vp-logo { display:flex; align-items:center; gap:10px; font-weight:800; font-size:20px; color:var(--navy); letter-spacing:-.3px; }
.vp-mark { width:30px; height:30px; border-radius:9px; background:linear-gradient(135deg,#FF7A45,#6C5CE7); display:grid; place-items:center; color:#fff; font-size:16px; }
.vp-navlinks { color:var(--muted); font-size:14px; display:flex; gap:26px; }
.vp-hero h1 { font-size:46px; line-height:1.08; font-weight:800; letter-spacing:-1.2px; color:var(--navy); margin:0 0 14px; }
.vp-hero h1 span { background:linear-gradient(90deg,#FF7A45,#6C5CE7); -webkit-background-clip:text; color:transparent; }
.vp-hero p { font-size:18px; color:var(--muted); max-width:620px; margin:0 0 26px; }
.vp-pill { display:inline-block; font-size:12px; font-weight:600; color:#6C5CE7; background:#EFEDFF; border-radius:99px; padding:5px 12px; margin-bottom:16px; }
.vp-card { background:#fff; border:1px solid var(--line); border-radius:18px; padding:22px 24px; box-shadow:0 1px 2px rgba(16,24,40,.04),0 8px 24px rgba(16,24,40,.04); }
.vp-steps { display:flex; gap:10px; margin:6px 0 2px; flex-wrap:wrap; }
.vp-step { font-size:13px; color:var(--muted); background:#fff; border:1px solid var(--line); border-radius:99px; padding:6px 12px; }
.vp-kpis { display:grid; grid-template-columns:1.3fr 1fr 1fr 1fr; gap:14px; margin:8px 0 20px; }
.vp-kpi { background:#fff; border:1px solid var(--line); border-radius:16px; padding:16px 18px; }
.vp-kpi .l { font-size:13px; color:var(--muted); } .vp-kpi .v { font-size:30px; font-weight:800; color:var(--navy); letter-spacing:-.6px; }
.vp-score { display:flex; align-items:center; gap:16px; }
.vp-headline { font-size:30px; font-weight:800; color:var(--navy); letter-spacing:-.6px; line-height:1.15; margin:4px 0 6px; }
.vp-sub { color:var(--muted); font-size:16px; margin-bottom:6px; }
.vp-release { font-size:16.5px; line-height:1.75; }
.vp-release p { margin:0 0 14px; }
.cl { border-radius:4px; padding:1px 2px; cursor:help; }
.cl-SUPPORTED { background:#E7F8EF; border-bottom:2px solid var(--ok); }
.cl-EXAGGERATED { background:#FFF4E5; border-bottom:2px solid var(--warn); }
.cl-UNSUPPORTED { background:#FEECEB; border-bottom:2px solid var(--bad); }
.cl-NEEDS_REVIEW, .cl-UNCHECKED { background:#FEF7DB; border-bottom:2px solid var(--review); }
.vp-legend { display:flex; gap:16px; font-size:13px; color:var(--muted); margin-top:4px; flex-wrap:wrap; }
.vp-dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; vertical-align:middle; }
.vp-badge { display:inline-block; font-size:11.5px; font-weight:700; letter-spacing:.3px; border-radius:99px; padding:3px 10px; }
.b-SUPPORTED { color:#067647; background:#E7F8EF; } .b-EXAGGERATED { color:#B54708; background:#FFF4E5; }
.b-UNSUPPORTED { color:#B42318; background:#FEECEB; } .b-NEEDS_REVIEW, .b-UNCHECKED { color:#A15C07; background:#FEF7DB; }
.vp-quote { border-left:3px solid #6C5CE7; background:#F7F6FF; padding:10px 14px; border-radius:0 10px 10px 0; font-size:14px; color:#344054; margin:8px 0; }
.vp-post-h { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.vp-avatar { width:38px; height:38px; border-radius:50%; background:linear-gradient(135deg,#0B1633,#6C5CE7); color:#fff; display:grid; place-items:center; font-weight:700; font-size:14px; }
.vp-post-name { font-weight:700; font-size:14.5px; } .vp-post-meta { font-size:12px; color:var(--muted); }
.vp-post-text { white-space:pre-wrap; font-size:14.5px; line-height:1.55; margin-bottom:10px; }
.stTabs [data-baseweb="tab-list"] { gap:6px; background:#EEF0F6; padding:5px; border-radius:12px; width:fit-content; }
.stTabs [data-baseweb="tab"] { height:38px; border-radius:9px; padding:0 16px; font-weight:600; color:var(--muted); background:transparent; }
.stTabs [aria-selected="true"] { background:#fff !important; color:var(--navy) !important; box-shadow:0 1px 3px rgba(16,24,40,.1); }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display:none; }
.stButton > button, .stDownloadButton > button, .stLinkButton > a { border-radius:10px !important; font-weight:600 !important; }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] { background:var(--navy) !important; border-color:var(--navy) !important; }
.stButton > button[kind="primary"]:disabled { background:#D0D5DD !important; border-color:#D0D5DD !important; color:#fff !important; }
.stButton > button[kind="primary"] p, .stDownloadButton > button[kind="primary"] p { color:#fff !important; }
[data-testid="stFileUploaderDropzone"] { border-radius:14px; border:1.5px dashed #C7CBE0; background:#FAFBFF; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius:16px !important; background:#fff; }
.vp-foot { color:var(--muted); font-size:12.5px; text-align:center; padding:40px 0 20px; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="vp-nav">
  <div class="vp-logo"><div class="vp-mark">◆</div>CiteFlow</div>
  <div class="vp-navlinks"><span>Press release</span><span>Social</span><span>Video</span><span>Fact check</span></div>
</div>""", unsafe_allow_html=True)

if not GEMINI_KEY:
    st.error("CiteFlow is not configured yet. Add GEMINI_API_KEY to .streamlit/secrets.toml (see README).")
    st.stop()

# ------------------------------------------------------------------ hero + input

left, right = st.columns([1.05, 1], gap="large")
with left:
    st.markdown("""
<div class="vp-hero">
  <div class="vp-pill">For university communications teams</div>
  <h1>Turn research into <span>trusted</span> PR content.</h1>
  <p>Every claim traceable. Every piece human-reviewed. Every channel ready to publish.</p>
</div>
<div class="vp-steps"><span class="vp-step">1 · Upload</span><span class="vp-step">2 · Draft</span>
<span class="vp-step">3 · Verify every claim</span><span class="vp-step">4 · Approve &amp; share</span></div>
""", unsafe_allow_html=True)
with right:
    with st.container(border=True):
        pdf = st.file_uploader("Research paper (PDF)", type=["pdf"])
        c1, c2 = st.columns(2)
        lang = c1.selectbox("Language", ["English", "Hungarian"])
        tone = c2.selectbox("Audience", ["General public", "Students", "Industry partners"])
        with st.expander("Add a researcher (optional)"):
            person = st.text_input("Name and title", placeholder="Dr. Anna Kovács")
            photo_file = st.file_uploader("Photo (only with the person's consent)", type=["jpg", "jpeg", "png"])
        go = st.button("Create verified content", type="primary", use_container_width=True, disabled=not pdf)

if go:
    try:
        with st.status("Reading the paper…", expanded=False) as status:
            data = pdf.getvalue()
            ss.pdf_bytes = data
            ss.proofs = {}
            ss.passages, ss.doc_title = pl.extract_passages(data)
            ss.figures = pl.extract_figures(data)
            ss.doc_name = pdf.name
            status.update(label="Writing the story…")
            engine = llm()
            ss.content = pl.generate_content(engine, ss.passages, f"{lang}; audience: {tone}")
            ss.release, ss.lang = ss.content.press_release, lang
            ss.posts = ss.content.posts.model_dump()
            status.update(label="Checking every claim against the paper…")
            ss.results = pl.verify(engine, ss.passages,
                                   pl.build_items(ss.content.headline, ss.release, ss.content.scenes, ss.posts))
            fig0 = ss.figures[0].image if ss.figures else None
            ss.cards = {"square": vid.render_card(ss.content.card_title, ss.content.institution, fig0, lang=lang),
                        "wide": vid.render_card(ss.content.card_title, ss.content.institution, fig0, (1200, 675), lang=lang)}
            ss.person = person.strip()
            ss.photo = Image.open(photo_file).convert("RGB") if photo_file else None
            for k in ("video", "hype"):
                ss.pop(k, None)
            status.update(label="Done", state="complete")
    except Exception as e:
        st.error(f"Something went wrong: {e}")

# ------------------------------------------------------------------ helpers


def paragraphs_html(results, part, text):
    """Show text with each sentence highlighted by its verdict, keeping paragraphs."""
    rs = [r for r in results if r["part"] == part]
    out, i = [], 0
    for para in [p for p in text.split("\n\n") if p.strip()]:
        spans = []
        for s in pl.split_sentences(para):
            r = rs[i] if i < len(rs) else None
            i += 1
            v = r["verdict"] if r else "NOT_A_CLAIM"
            tip = html.escape(f"{v.replace('_', ' ').title()}: {r.get('explanation', '')}" if r else "")
            spans.append(f'<span class="cl cl-{v}" title="{tip}">{html.escape(s)}</span>')
        out.append("<p>" + " ".join(spans) + "</p>")
    return "".join(out)


def badge(v):
    return f'<span class="vp-badge b-{v}">{v.replace("_", " ")}</span>'


def donut(ok, total):
    pct = 0 if total == 0 else ok / total
    c = 2 * 3.1416 * 26
    color = "#12B76A" if pct >= .85 else "#F79009" if pct >= .6 else "#F04438"
    return (f'<svg width="64" height="64" viewBox="0 0 64 64"><circle cx="32" cy="32" r="26" fill="none" stroke="#EEF0F6" stroke-width="8"/>'
            f'<circle cx="32" cy="32" r="26" fill="none" stroke="{color}" stroke-width="8" stroke-linecap="round" '
            f'stroke-dasharray="{c * pct:.1f} {c:.1f}" transform="rotate(-90 32 32)"/>'
            f'<text x="32" y="37" text-anchor="middle" font-size="15" font-weight="800" fill="#0B1633">{round(pct * 100)}%</text></svg>')


def proof(pid, claim=""):
    """Cached highlighted page image for a passage id (and the claim's numbers)."""
    cache = ss.setdefault("proofs", {})
    key = (pid, claim)
    if key not in cache:
        pm = {p.pid: p for p in ss.passages}
        cache[key] = pl.proof_image(ss.pdf_bytes, pm[pid], claim=claim)
    return cache[key]


def proof_viewer(r, key):
    """Show the claim's evidence as highlighted pages from the paper."""
    if not r["evidence"]:
        st.caption("No supporting passage was found in the paper.")
        return
    tabs = st.tabs([f"Page {p.page}" for p in r["evidence"]]) if len(r["evidence"]) > 1 else [st.container()]
    for t, p in zip(tabs, r["evidence"]):
        with t:
            st.image(proof(p.pid, r["text"]), use_container_width=True)
            st.caption(f"Highlighted: the passage the checker relied on (page {p.page}, {p.pid}).")


def recheck():
    ss.results = pl.verify(llm(), ss.passages, pl.build_items(ss.content.headline, ss.release, ss.content.scenes, ss.posts))
    ss.pop("video", None)

# ------------------------------------------------------------------ results

if "results" in ss:
    res, content = ss.results, ss.content
    ok, total = pl.score(res)
    cnt = {}
    for r in res:
        cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
    flagged = sum(cnt.get(k, 0) for k in pl.FLAGGED)

    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    st.markdown(f"""
<div class="vp-kpis">
  <div class="vp-kpi vp-score">{donut(ok, total)}<div><div class="l">Claims supported by the paper</div>
     <div class="v">{ok} / {total}</div></div></div>
  <div class="vp-kpi"><div class="l">Need attention</div><div class="v" style="color:{'#F04438' if flagged else '#12B76A'}">{flagged}</div></div>
  <div class="vp-kpi"><div class="l">Source passages checked</div><div class="v">{len(ss.passages)}</div></div>
  <div class="vp-kpi"><div class="l">Figures found in paper</div><div class="v">{len(ss.figures)}</div></div>
</div>""", unsafe_allow_html=True)

    t_rel, t_soc, t_vid, t_chk, t_str, t_exp = st.tabs(
        ["Press release", "Social posts", "Video", "Fact check", "Stress test", "Approve & export"])

    with t_rel:
        with st.container(border=True):
            st.markdown(f"<div class='vp-sub'>{html.escape(content.institution)}</div>"
                        f"<div class='vp-headline'>{html.escape(content.headline)}</div>"
                        f"<div class='vp-sub'>{html.escape(content.subheadline)}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='vp-release'>{paragraphs_html(res, 'Press release', ss.release)}</div>",
                        unsafe_allow_html=True)
            st.markdown("""<div class="vp-legend"><span><i class="vp-dot" style="background:#12B76A"></i>Supported</span>
<span><i class="vp-dot" style="background:#F79009"></i>Exaggerated</span><span><i class="vp-dot" style="background:#F04438"></i>Unsupported</span>
<span><i class="vp-dot" style="background:#CA8504"></i>Needs review</span><span>Hover any sentence to see why.</span></div>""",
                        unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("**See the proof**  \n<span style='color:#667085;font-size:14px'>Pick any sentence to see where it comes from in the paper.</span>",
                        unsafe_allow_html=True)
            claims = [r for r in res if r["verdict"] != "NOT_A_CLAIM"]
            if claims:
                pick = st.selectbox("Sentence", range(len(claims)), label_visibility="collapsed",
                                    format_func=lambda i: f"{claims[i]['verdict'].replace('_', ' ').title()} · {claims[i]['part']} · {claims[i]['text'][:95]}")
                r = claims[pick]
                st.markdown(f"{badge(r['verdict'])} &nbsp;**{html.escape(r['text'])}**", unsafe_allow_html=True)
                if r.get("explanation"):
                    st.caption(r["explanation"])
                proof_viewer(r, "rel")
        with st.expander("Edit and re-check"):
            edited = st.text_area("Press release", ss.release, height=340, label_visibility="collapsed")
            if st.button("Re-check my edits"):
                with st.spinner("Checking…"):
                    ss.release = edited
                    recheck()
                st.rerun()

    with t_soc:
        cols = st.columns(2, gap="medium")
        nets = [("linkedin", "LinkedIn", "wide"), ("facebook", "Facebook", "wide"),
                ("instagram", "Instagram", "square"), ("x", "X", "wide")]
        initials = "".join(w[0] for w in content.institution.split()[:2] if w).upper() or "UD"
        for k, (key, label, card) in enumerate(nets):
            text = ss.posts[key]
            bad = [r for r in res if r["part"] == label and r["verdict"] in pl.FLAGGED]
            state = "SUPPORTED" if not bad else ("EXAGGERATED" if any(r["verdict"] in ("EXAGGERATED", "UNSUPPORTED") for r in bad) else "NEEDS_REVIEW")
            with cols[k % 2]:
                with st.container(border=True):
                    st.markdown(f"""<div class="vp-post-h"><div class="vp-avatar">{html.escape(initials)}</div>
<div><div class="vp-post-name">{html.escape(content.institution)}</div><div class="vp-post-meta">{label} · draft</div></div>
<div style="margin-left:auto">{badge(state)}</div></div>
<div class="vp-post-text">{html.escape(text)}</div>""", unsafe_allow_html=True)
                    st.image(ss.cards[card], use_container_width=True)
                    for r in bad:
                        st.caption(f"⚠ {r['text']} — {r.get('explanation', '')}")
                    b1, b2, b3 = st.columns(3)
                    q = urllib.parse.quote(text)
                    links = {"x": ("Post on X", f"https://x.com/intent/post?text={q}"),
                             "linkedin": ("Open LinkedIn", f"https://www.linkedin.com/feed/?shareActive=true&text={q}"),
                             "facebook": ("Open Facebook", "https://www.facebook.com/"),
                             "instagram": ("Open Instagram", "https://www.instagram.com/")}
                    b1.link_button(links[key][0], links[key][1], use_container_width=True)
                    buf = io.BytesIO()
                    ss.cards[card].save(buf, "PNG")
                    b2.download_button("Image", buf.getvalue(), f"citeflow_{key}.png", "image/png",
                                       use_container_width=True, key=f"img_{key}")
                    with b3.popover("Copy text", use_container_width=True):
                        st.code(text, language=None, wrap_lines=True)

    with t_vid:
        scenes = pl.safe_scenes(content.scenes, res, ss.figures)
        vc1, vc2 = st.columns([1, 1.4], gap="large")
        with vc1:
            with st.container(border=True):
                st.markdown(f"**{html.escape(content.video_title)}**")
                st.caption("Only verified lines are spoken. Flagged lines are replaced with their faithful version.")
                for i, s in enumerate(scenes, 1):
                    icon = "✓" if s["status"] == "verified" else "↺"
                    extra = " · paper figure" if s.get("figure") is not None else ""
                    st.markdown(f"<div style='font-size:14px;margin:6px 0'><b>{icon} Scene {i}</b> — {html.escape(s['narration'])}"
                                f"<span style='color:#667085'>{extra}</span></div>", unsafe_allow_html=True)
                fmt = st.radio("Format", list(vid.FORMATS), horizontal=True)
                if st.button("Render video", type="primary", use_container_width=True, disabled=not scenes):
                    bar = st.progress(0.0, "Recording voice-over and finding footage…")
                    try:
                        ss.video = vid.render_video(
                            content.video_title, content.institution, content.card_title, scenes, ss.figures,
                            ss.lang, tempfile.mkdtemp(), fmt=fmt, pexels_key=PEXELS_KEY, photo=ss.photo,
                            person_name=ss.person, progress=lambda x: bar.progress(x, "Rendering video…"))
                        bar.empty()
                    except Exception as e:
                        st.error(f"Video rendering failed: {e}")
        with vc2:
            if "video" in ss:
                mp4, srt, narrated, stock = ss.video
                st.video(mp4)
                if not narrated:
                    st.warning("The voice-over service could not be reached, so this video is silent.")
                with open(mp4, "rb") as fh:
                    st.download_button("Download video (MP4)", fh.read(), os.path.basename(mp4), "video/mp4",
                                       use_container_width=True)
            else:
                st.markdown("<div class='vp-card' style='text-align:center;color:#667085;padding:80px 20px'>"
                            "Your video preview appears here.</div>", unsafe_allow_html=True)

    with t_chk:
        only = st.toggle("Show only claims that need attention", value=bool(flagged))
        for r in res:
            if r["verdict"] == "NOT_A_CLAIM" or (only and r["verdict"] == "SUPPORTED"):
                continue
            with st.container(border=True):
                st.markdown(f"{badge(r['verdict'])} <span style='color:#667085;font-size:13px'>&nbsp;{r['part']} · {r['id']}</span>",
                            unsafe_allow_html=True)
                st.markdown(f"**{html.escape(r['text'])}**")
                if r.get("explanation"):
                    st.caption((r["issue_type"].capitalize() + " — " if r.get("issue_type") else "") + r["explanation"])
                for f in r.get("rule_flags", []):
                    st.caption(f"Rule check: {f}")
                if r.get("rewrite"):
                    st.markdown(f"<div class='vp-quote'><b>Faithful version:</b> {html.escape(r['rewrite'])}</div>",
                                unsafe_allow_html=True)
                for p in r["evidence"]:
                    st.markdown(f"<div class='vp-quote'><b>Paper, p. {p.page}</b> · {html.escape(p.text[:600])}</div>",
                                unsafe_allow_html=True)
                if r["evidence"] and st.toggle("Show on the page", key=f"pv_{r['id']}"):
                    proof_viewer(r, r["id"])

    with t_str:
        with st.container(border=True):
            st.markdown("**Does the checker really catch hype?**")
            st.caption("CiteFlow writes an over-enthusiastic version of the release on purpose, then checks it with the same verifier.")
            if st.button("Run stress test"):
                with st.spinner("Writing a hyped version and checking it…"):
                    engine = llm()
                    hyped = pl.hype_version(engine, ss.release)
                    ss.hype = (hyped, pl.verify(engine, ss.passages, pl.build_items("", hyped, [], {})))
            if "hype" in ss:
                hyped, hres = ss.hype
                hok, htot = pl.score(hres)
                st.markdown(f"<div class='vp-kpis' style='grid-template-columns:1fr 1fr'>"
                            f"<div class='vp-kpi vp-score'>{donut(ok, total)}<div><div class='l'>Original release</div><div class='v'>{ok} / {total}</div></div></div>"
                            f"<div class='vp-kpi vp-score'>{donut(hok, htot)}<div><div class='l'>Hyped version</div><div class='v'>{hok} / {htot}</div></div></div></div>",
                            unsafe_allow_html=True)
                st.markdown(f"<div class='vp-release'>{paragraphs_html(hres, 'Press release', hyped)}</div>",
                            unsafe_allow_html=True)

    with t_exp:
        with st.container(border=True):
            st.markdown("**Human approval**")
            st.caption("CiteFlow drafts and verifies. A person approves before anything is published.")
            if flagged:
                st.warning(f"{flagged} claim(s) still need attention. Fix them in the press release, or confirm below that you accept them.")
            a1 = st.checkbox("I reviewed every flagged claim and its source passage")
            a2 = st.checkbox("Placeholder quotes will be replaced with real, approved quotes from the researchers")
            if a1 and a2:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("press_release.txt", f"{content.headline}\n{content.subheadline}\n\n{ss.release}\n")
                    for key, text in ss.posts.items():
                        z.writestr(f"post_{key}.txt", text)
                    for name, im in ss.cards.items():
                        b = io.BytesIO()
                        im.save(b, "PNG")
                        z.writestr(f"image_{name}.png", b.getvalue())
                    rows = io.StringIO()
                    w = csv.writer(rows)
                    w.writerow(["id", "part", "text", "verdict", "issue", "explanation", "faithful_version", "source", "rule_flags"])
                    for r in res:
                        w.writerow([r["id"], r["part"], r["text"], r["verdict"], r.get("issue_type", ""), r.get("explanation", ""),
                                    r.get("rewrite", ""), " | ".join(f"p.{p.page} {p.pid}" for p in r["evidence"]),
                                    "; ".join(r.get("rule_flags", []))])
                    z.writestr("verification_report.csv", "﻿" + rows.getvalue())
                    if "video" in ss:
                        z.write(ss.video[0], os.path.basename(ss.video[0]))
                        z.write(ss.video[1], os.path.basename(ss.video[1]))
                st.download_button("Download approved package", buf.getvalue(), "citeflow_package.zip",
                                   "application/zip", type="primary", use_container_width=True)

st.markdown("<div class='vp-foot'>CiteFlow · AI drafts and verifies, people approve · DEIK.AI Challenge 2026</div>",
            unsafe_allow_html=True)
