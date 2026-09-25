"""CiteFlow — turn research into trusted PR content. Run: streamlit run app.py"""
import base64
import csv
import hashlib
import hmac
import html
import io
import json
import os
import re
import shutil
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

import cards
import pipeline as pl
import store
import video as vid

HERE = Path(__file__).parent
st.set_page_config(page_title="CiteFlow", page_icon=str(HERE / "mark_green.png"), layout="wide",
                   initial_sidebar_state="collapsed")
ss = st.session_state

# ------------------------------------------------------------------ configuration (hidden from users)


def _clean(s):
    s = str(s or "").strip().strip('"\'“”‘’').strip()
    return "" if s.startswith("paste-your") else s


def secret(name):
    val = ""
    try:
        if name in st.secrets:
            val = st.secrets[name]
    except Exception:
        pass
    return _clean(val) or _clean(os.environ.get(name, ""))


def team_accounts():
    """Accounts from [users] in secrets.toml (email = "password"). Falls back to one demo account."""
    try:
        if "users" in st.secrets:
            return {k.lower(): str(v) for k, v in dict(st.secrets["users"]).items()}
    except Exception:
        pass
    return {"demo@citeflow.app": "citeflow2026"}


GEMINI_KEY, PEXELS_KEY = secret("GEMINI_API_KEY"), secret("PEXELS_API_KEY")


@st.cache_resource(show_spinner=False)
def model_chain(key):
    try:
        found = pl.list_flash_models(key)
    except Exception:
        found = []
    chain = found or ["gemini-3.8-flash", "gemini-2.5-flash"]
    full = [m for m in chain if "lite" not in m][:3]
    lite = [m for m in chain if "lite" in m][:2]
    return (full + lite + ["gemini-flash-latest", "gemini-flash-lite-latest"])[:7]


def llm():
    chain = model_chain(GEMINI_KEY)
    return pl.LLM(GEMINI_KEY, chain[0], fallbacks=chain[1:])


@st.cache_data(show_spinner=False)
def b64(name):
    return base64.b64encode((HERE / name).read_bytes()).decode()

# ------------------------------------------------------------------ styling

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap');
:root { --deep:#2b4c40; --green:#3d6d5c; --sage:#4f8e78; --gold:#e4b752; --gold2:#dda526; --cream:#ebc97e;
        --ink:#1f2a26; --muted:#5f6b66; --line:#e4e1d6; --bg:#f7f5ef; --card:#ffffff;
        --ok:#3d7a62; --warn:#c98a12; --bad:#b4442c; --review:#a07d1c; }
html, body, [class*="css"], .stApp, button, input, textarea, select { font-family:'Plus Jakarta Sans',system-ui,sans-serif !important; }
.stApp { background:var(--bg); color:var(--ink); }
#MainMenu, header[data-testid="stHeader"], footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], [data-testid="collapsedControl"] { display:none !important; }
.block-container { padding-top:18px !important; max-width:1200px; }

/* top bar */
.st-key-topbar { background:var(--deep); border-radius:16px; padding:10px 18px 10px 22px; margin-bottom:34px;
                 box-shadow:0 10px 30px rgba(43,76,64,.18); }
.st-key-topbar [data-testid="stHorizontalBlock"] { align-items:center; }
.st-key-topbar img.cf-logo { height:38px; display:block; }
.st-key-topbar .stButton > button { background:transparent !important; border:none !important; color:#dfe8e3 !important;
                 font-weight:600 !important; border-radius:10px !important; padding:6px 14px !important; }
.st-key-topbar .stButton > button:hover { background:rgba(255,255,255,.08) !important; color:#fff !important; }
.st-key-topbar .stButton > button p { color:inherit !important; font-size:14.5px; }
.st-key-topbar .stButton > button[kind="primary"] { background:var(--gold) !important; color:var(--deep) !important; }
.cf-user { color:#cfe0d8; font-size:13px; text-align:right; line-height:1.2; }
.cf-user b { color:#fff; font-weight:600; }

/* hero */
.cf-eyebrow { display:inline-block; font-size:12px; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
              color:var(--green); border:1px solid #cfd9d3; background:#eef3ef; border-radius:6px; padding:5px 10px; margin-bottom:18px; }
.cf-hero h1 { font-size:48px; line-height:1.06; font-weight:800; letter-spacing:-1.4px; color:var(--deep); margin:0 0 16px; }
.cf-hero h1 em { font-style:normal; color:var(--green); box-shadow:inset 0 -12px 0 rgba(228,183,82,.55); }
.cf-hero p { font-size:18px; color:var(--muted); max-width:560px; margin:0 0 26px; line-height:1.55; }
.cf-points { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; max-width:600px; }
.cf-point { border-top:3px solid var(--gold); padding-top:10px; font-size:13.5px; color:var(--muted); }
.cf-point b { display:block; color:var(--deep); font-size:14.5px; margin-bottom:2px; }

/* cards & text */
[data-testid="stVerticalBlockBorderWrapper"] { border-radius:14px !important; background:var(--card); border-color:var(--line) !important; }
.cf-card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px 24px; }
.cf-h2 { font-size:26px; font-weight:800; color:var(--deep); letter-spacing:-.5px; margin:0 0 4px; }
.cf-lead { color:var(--muted); font-size:15.5px; margin:0 0 20px; }
.vp-kpis { display:grid; grid-template-columns:1.3fr 1fr 1fr 1fr; gap:14px; margin:8px 0 20px; }
.vp-kpi { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 18px; }
.vp-kpi .l { font-size:13px; color:var(--muted); } .vp-kpi .v { font-size:30px; font-weight:800; color:var(--deep); letter-spacing:-.6px; }
.vp-score { display:flex; align-items:center; gap:16px; }
.vp-headline { font-family:'Source Serif 4',Georgia,serif; font-size:32px; font-weight:600; color:var(--deep); line-height:1.18; margin:4px 0 8px; }
.vp-sub { color:var(--muted); font-size:15.5px; margin-bottom:8px; }
.vp-release { font-family:'Source Serif 4',Georgia,serif; font-size:18px; line-height:1.72; color:#26312c; }
.vp-release p { margin:0 0 14px; }
.cl { border-radius:3px; padding:1px 1px; cursor:help; }
.cl-SUPPORTED { background:#e7f1ec; border-bottom:2px solid var(--ok); }
.cl-EXAGGERATED { background:#fbf0d8; border-bottom:2px solid var(--warn); }
.cl-UNSUPPORTED { background:#f8e3dc; border-bottom:2px solid var(--bad); }
.cl-NEEDS_REVIEW, .cl-UNCHECKED { background:#f6f0da; border-bottom:2px dashed var(--review); }
.cl-ACCEPTED { background:#eef0ec; border-bottom:2px solid #9aa89f; }
.vp-legend { display:flex; gap:16px; font-size:13px; color:var(--muted); margin-top:4px; flex-wrap:wrap; }
.vp-dot { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; vertical-align:middle; }
.vp-badge { display:inline-block; font-size:11px; font-weight:700; letter-spacing:.06em; border-radius:5px; padding:3px 8px; text-transform:uppercase; }
.b-SUPPORTED { color:#2f6450; background:#e7f1ec; } .b-EXAGGERATED { color:#8a5a00; background:#fbf0d8; }
.b-UNSUPPORTED { color:#8f2f1b; background:#f8e3dc; } .b-NEEDS_REVIEW, .b-UNCHECKED { color:#7a5d0f; background:#f6f0da; }
.b-ACCEPTED { color:#4f5c55; background:#eef0ec; }
.vp-quote { border-left:3px solid var(--gold); background:#fbf8ef; padding:10px 14px; border-radius:0 8px 8px 0; font-size:14px; color:#3b4540; margin:8px 0; }
.vp-post-h { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.vp-avatar { width:38px; height:38px; border-radius:50%; background:var(--deep); color:var(--gold); display:grid; place-items:center; font-weight:700; font-size:14px; }
.vp-post-name { font-weight:700; font-size:14.5px; } .vp-post-meta { font-size:12px; color:var(--muted); }
.vp-post-text { white-space:pre-wrap; font-size:14.5px; line-height:1.55; margin-bottom:10px; }

/* tabs & controls */
.stTabs [data-baseweb="tab-list"] { gap:4px; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { height:42px; padding:0 14px; font-weight:600; color:var(--muted); background:transparent; }
.stTabs [aria-selected="true"] { color:var(--deep) !important; }
.stTabs [data-baseweb="tab-highlight"] { background:var(--gold) !important; height:3px; }
.stButton > button, .stDownloadButton > button, .stLinkButton > a { border-radius:9px !important; font-weight:600 !important; }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] { background:var(--green) !important; border-color:var(--green) !important; }
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover { background:var(--deep) !important; }
[data-testid="stFormSubmitButton"] button { background:var(--green) !important; border-color:var(--green) !important; border-radius:9px !important; }
[data-testid="stFormSubmitButton"] button p { color:#fff !important; font-weight:600; }
[data-testid="stForm"] { border-color:var(--line) !important; background:var(--card); border-radius:14px; }
.stButton > button[kind="primary"]:disabled { background:#cfd6d2 !important; border-color:#cfd6d2 !important; }
.stButton > button[kind="primary"] p, .stDownloadButton > button[kind="primary"] p { color:#fff !important; }
[data-testid="stFileUploaderDropzone"] { border-radius:12px; border:1.5px dashed #c9d3cd; background:#fbfaf6; }
.cf-foot { color:var(--muted); font-size:12.5px; text-align:center; padding:40px 0 20px; }

/* login */
.cf-login-side { background:var(--deep); border-radius:18px; padding:40px 38px; min-height:460px; color:#dfe8e3; }
.cf-login-side img { height:48px; margin-bottom:34px; }
.cf-login-side h2 { color:#fff; font-size:30px; line-height:1.15; font-weight:800; letter-spacing:-.6px; margin:0 0 14px; }
.cf-login-side p { color:#c3d3cb; font-size:15.5px; line-height:1.6; }
.cf-login-side li { margin:8px 0; font-size:14.5px; }
.cf-chan { display:flex; align-items:center; gap:12px; }
.cf-chan-ico { width:40px; height:40px; border-radius:10px; display:grid; place-items:center; color:#fff; font-weight:800; font-size:15px; }
.cf-status { font-size:12px; font-weight:700; padding:3px 8px; border-radius:5px; }
.cf-on { background:#e7f1ec; color:#2f6450; } .cf-off { background:#f1efe8; color:#8a8f8b; }
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------ login


def sign_in(user_id, name):
    ss.user, ss.user_name = user_id, name
    ss.page = "Studio"
    ss.channels = load_channels(user_id)
    st.rerun()


def login_screen():
    st.markdown("<div style='height:36px'></div>", unsafe_allow_html=True)
    left, right = st.columns([1.1, 1], gap="large")
    with left:
        st.markdown(f"""<div class="cf-login-side"><img src="data:image/png;base64,{b64('logo_gold.png')}">
<h2>Research news your university can stand behind.</h2>
<p>CiteFlow drafts press releases, social posts and videos from a paper, and links every claim back to the page it came from.</p>
<ul><li>Every claim traceable to the source</li><li>Every piece reviewed by a person</li><li>Every channel ready to publish</li></ul></div>""",
                    unsafe_allow_html=True)
    with right:
        t_in, t_up = st.tabs(["Sign in", "Create account"])
        with t_in:
            with st.form("login"):
                email = st.text_input("Email or username", placeholder="name@unideb.hu")
                pw = st.text_input("Password", type="password")
                ok = st.form_submit_button("Sign in", type="primary", width="stretch")
            if ok:
                ident = email.strip().lower()
                stored = team_accounts().get(ident)
                if stored is not None and hmac.compare_digest(stored, pw):
                    sign_in(ident, ident.split("@")[0].replace(".", " ").title())
                u = store.check_user(ident, pw)
                if u:
                    sign_in(u["username"], u["name"])
                time.sleep(0.6)
                st.error("Email/username or password is not correct.")
            if "demo@citeflow.app" in team_accounts():
                st.caption("Demo access for reviewers: **demo@citeflow.app** · password **citeflow2026**")
        with t_up:
            with st.form("signup"):
                name = st.text_input("Full name", placeholder="Anna Kovács")
                uid = st.text_input("Email or username", placeholder="anna.kovacs@unideb.hu", key="su_id")
                p1 = st.text_input("Password (min. 6 characters)", type="password", key="su_p1")
                p2 = st.text_input("Repeat password", type="password", key="su_p2")
                make = st.form_submit_button("Create account", type="primary", width="stretch")
            if make:
                if p1 != p2:
                    st.error("The two passwords do not match.")
                elif uid.strip().lower() in team_accounts():
                    st.error("This account already exists. Please sign in.")
                else:
                    err = store.create_user(uid, p1, name)
                    if err:
                        st.error(err)
                    else:
                        sign_in(uid.strip().lower(), name.strip() or uid.strip())

# ------------------------------------------------------------------ connected channels (saved per user)

CHANNELS = [
    ("linkedin", "LinkedIn", "in", "#0a66c2", "Company page URL", "https://www.linkedin.com/company/…"),
    ("facebook", "Facebook", "f", "#1877f2", "Page URL", "https://www.facebook.com/…"),
    ("instagram", "Instagram", "IG", "#c13584", "Account handle", "@unideb"),
    ("x", "X", "X", "#111111", "Account handle", "@unideb"),
    ("blog", "Blog / news site", "B", "#3d6d5c", "News page or blog URL", "https://unideb.hu/news"),
    ("youtube", "YouTube", "▶", "#e62117", "Channel URL", "https://www.youtube.com/@…"),
]


def load_channels(user):
    return store.get_channels(user)


def save_channels(user, data):
    try:
        store.save_channels(user, data)
    except OSError:
        pass

# ------------------------------------------------------------------ top bar

PAGES = ["Studio", "Projects", "Insights", "Channels", "Account"]


def topbar():
    with st.container(key="topbar"):
        cols = st.columns([2.0, 0.85, 0.9, 0.85, 0.9, 0.85, 1.35], vertical_alignment="center")
        cols[0].markdown(f"<img class='cf-logo' src='data:image/png;base64,{b64('logo_gold.png')}'>",
                         unsafe_allow_html=True)
        for c, name in zip(cols[1:6], PAGES):
            if c.button(name, key=f"nav_{name}", type="primary" if ss.page == name else "secondary",
                        width="stretch"):
                ss.page = name
                st.rerun()
        cols[6].markdown(f"<div class='cf-user'>Signed in as<br><b>{html.escape(ss.get('user_name') or ss.user)}</b></div>",
                         unsafe_allow_html=True)

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
            why = (r.get("explanation") or "; ".join(r.get("rule_flags", []))) if r else ""
            tip = html.escape(f"{v.replace('_', ' ').title()}: {why}" if r else "")
            spans.append(f'<span class="cl cl-{v}" title="{tip}">{html.escape(s)}</span>')
        out.append("<p>" + " ".join(spans) + "</p>")
    return "".join(out)


def badge(v):
    return f'<span class="vp-badge b-{v}">{v.replace("_", " ")}</span>'


def donut(ok, total):
    pct = 0 if total == 0 else ok / total
    c = 2 * 3.1416 * 26
    color = "#3d7a62" if pct >= .85 else "#c98a12" if pct >= .6 else "#b4442c"
    return (f'<svg width="64" height="64" viewBox="0 0 64 64"><circle cx="32" cy="32" r="26" fill="none" stroke="#ece9df" stroke-width="8"/>'
            f'<circle cx="32" cy="32" r="26" fill="none" stroke="{color}" stroke-width="8" stroke-linecap="round" '
            f'stroke-dasharray="{c * pct:.1f} {c:.1f}" transform="rotate(-90 32 32)"/>'
            f'<text x="32" y="37" text-anchor="middle" font-size="15" font-weight="800" fill="#2b4c40">{round(pct * 100)}%</text></svg>')


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
            st.image(proof(p.pid, r["text"]), width="stretch")
            st.caption(f"Highlighted: the passage the checker relied on (page {p.page}, {p.pid}).")


def mark_accepted():
    acc = set(ss.get("accepted", []))
    for r in ss.results:
        if r["text"] in acc and r["verdict"] in pl.FLAGGED:
            r["verdict"] = "ACCEPTED"


def recheck():
    ss.results = pl.verify(llm(), ss.passages, pl.build_items(ss.content.headline, ss.release, ss.content.scenes, ss.posts, ss.content.visual))
    mark_accepted()
    make_cards()
    ss.pop("video", None)
    ss.pop("anims", None)


def replace_sentence(text, old, new):
    pattern = r"\s+".join(re.escape(w) for w in old.split())
    out, n = re.subn(pattern, lambda m: new, text, count=1)
    if n and not new:
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r" +\n", "\n", out).strip()
    return out, bool(n)


POST_OF = {"LinkedIn": "linkedin", "Facebook": "facebook", "Instagram": "instagram", "X": "x"}


def edit_claim(r, new):
    """Replace (or remove, if new == '') one flagged sentence wherever it appears. Returns True if changed."""
    part, old = r["part"], r["text"]
    c = ss.content
    if part == "Press release":
        ss.release, ok = replace_sentence(ss.release, old, new)
        return ok
    if part in POST_OF:
        k = POST_OF[part]
        ss.posts[k], ok = replace_sentence(ss.posts[k], old, new)
        return ok
    if part == "Headline":
        if new:
            ss.content = c.model_copy(update={"headline": new})
        return bool(new)
    if part == "Video":
        idx = int(r["id"][1:]) - 1
        if 0 <= idx < len(c.scenes):
            scenes = [s.model_copy() for s in c.scenes]
            if new:
                scenes[idx] = scenes[idx].model_copy(update={"narration": new})
            else:
                scenes.pop(idx)
            ss.content = c.model_copy(update={"scenes": scenes})
            return True
        return False
    if part == "Image text":
        vis = c.visual
        if r["id"] == "K0":
            vis = vis.model_copy(update={"stat_value": "", "stat_label": ""} if not new else {"stat_value": "", "stat_label": new})
        else:
            idx = int(r["id"][1:]) - 1
            pts = list(vis.key_points)
            if 0 <= idx < len(pts):
                if new:
                    pts[idx] = new
                else:
                    pts.pop(idx)
            vis = vis.model_copy(update={"key_points": pts})
        ss.content = c.model_copy(update={"visual": vis})
        return True
    return False


# ------------------------------------------------------------------ projects (saved per user)


def ser_results(res):
    return [dict(r, evidence=[p.pid for p in r["evidence"]]) for r in res]


def de_results(sres, passages):
    pm = {p.pid: p for p in passages}
    return [dict(r, evidence=[pm[x] for x in r.get("evidence", []) if x in pm]) for r in sres]


def visual_now():
    return pl.safe_visual(ss.content.visual, ss.get("results", []))


def img_bytes(im, fmt="PNG"):
    b = io.BytesIO()
    if fmt == "JPEG":
        im.convert("RGB").save(b, "JPEG", quality=90, optimize=True)
    else:
        im.save(b, "PNG", optimize=False)
    return b.getvalue()


def card_bytes(name):
    """PNG (for display/download) and base64 JPEG (for the publish button), encoded once per image."""
    cache = ss.setdefault("card_cache", {})
    if name not in cache:
        im = ss.cards[name]
        cache[name] = (img_bytes(im), base64.b64encode(img_bytes(im, "JPEG")).decode())
    return cache[name]


def make_cards():
    fig0 = ss.figures[0].image if ss.figures else None
    v, c = visual_now(), ss.content
    ss.cards = {"square": cards.post_image(v, c.card_title, c.institution, fig0 if not v["key_points"] else None, (1080, 1080)),
                "wide": cards.post_image(v, c.card_title, c.institution, fig0, (1200, 675)),
                "portrait": cards.post_image(v, c.card_title, c.institution, fig0 if not v["key_points"] else None, (1080, 1350))}
    ss.pop("carousel", None)
    ss.pop("carousel_zip", None)
    ss.pop("card_cache", None)
    ss.pop("anims", None)


def current_content():
    """The content as the user currently sees it (including manual edits)."""
    return ss.content.model_copy(update={"press_release": ss.release, "posts": pl.Posts(**ss.posts)})


def persist():
    """Save the open project to the user's project list."""
    if "results" not in ss or "pid" not in ss:
        return
    ok, total = pl.score(ss.results)
    state = {"content": current_content().model_dump(), "release": ss.release, "posts": ss.posts, "lang": ss.lang,
             "doc_name": ss.get("doc_name", ""), "doc_title": ss.get("doc_title", ""), "person": ss.get("person", ""),
             "results": ser_results(ss.results), "chat": ss.get("chat", []), "accepted": ss.get("accepted", []),
             "plan": ss.plan.model_dump() if ss.get("plan") else None,
             "hype": [ss.hype[0], ser_results(ss.hype[1])] if ss.get("hype") else None}
    meta = {"headline": ss.content.headline, "doc_name": ss.get("doc_name", ""), "lang": ss.lang,
            "score": f"{ok}/{total}", "created": ss.get("created", time.strftime("%Y-%m-%d %H:%M"))}
    sig = hashlib.sha1(json.dumps([ss.pid, state, meta, (ss.get("video") or [None])[0]], sort_keys=True,
                                  default=str).encode()).hexdigest()
    if ss.get("last_saved") == sig:          # nothing changed since the last save
        return
    ss.last_saved = sig
    try:
        video_file = None
        if ss.get("video"):
            d = store.project_path(ss.user, ss.pid)
            for src in ss.video[:2]:
                dst = os.path.join(d, os.path.basename(src))
                if os.path.abspath(src) != os.path.abspath(dst) and os.path.exists(src):
                    shutil.copyfile(src, dst)
            state["video"] = [os.path.basename(ss.video[0]), os.path.basename(ss.video[1]), ss.video[2], ss.video[3]]
        store.save_project(ss.user, ss.pid, meta, state, pdf_bytes=ss.get("pdf_bytes"), video_path=video_file)
    except OSError:
        pass


PROJECT_KEYS = ["pid", "created", "pdf_bytes", "passages", "doc_title", "figures", "doc_name", "content", "release",
                "lang", "posts", "results", "cards", "person", "photo", "video", "hype", "proofs", "plan", "chat",
                "accepted", "carousel", "anims", "carousel_zip", "card_cache", "last_saved", "pkg"]


def clear_project():
    for k in PROJECT_KEYS:
        ss.pop(k, None)


def open_project(pid):
    state, pdf, _ = store.load_project(ss.user, pid)
    if state is None or pdf is None:
        st.error("This project could not be opened (its files are missing).")
        return False
    cd = dict(state.get("content") or {})
    cd.setdefault("visual", {"kicker": "Research news", "stat_value": "", "stat_label": "", "key_points": [], "cta": ""})
    for sc in cd.get("scenes", []):
        sc.setdefault("wants_figure", False)
        sc.setdefault("stock_query", "")
    try:
        content = pl.Content.model_validate(cd)
    except Exception:
        st.error("This project was saved by an older version of CiteFlow and cannot be opened. "
                 "Please create it again from the paper.")
        return False
    clear_project()
    ss.pid, ss.pdf_bytes, ss.proofs = pid, pdf, {}
    ss.passages, ss.doc_title = pl.extract_passages(pdf)
    ss.figures = pl.extract_figures(pdf)
    ss.content = content
    ss.accepted = state.get("accepted", [])
    ss.release, ss.posts, ss.lang = state["release"], state["posts"], state["lang"]
    ss.doc_name, ss.person, ss.photo = state.get("doc_name", ""), state.get("person", ""), None
    ss.results = de_results(state["results"], ss.passages)
    ss.chat = state.get("chat", [])
    if state.get("plan"):
        ss.plan = pl.PublishPlan.model_validate(state["plan"])
    if state.get("hype"):
        ss.hype = (state["hype"][0], de_results(state["hype"][1], ss.passages))
    if state.get("video"):
        d = store.project_path(ss.user, pid)
        mp4, srt = os.path.join(d, state["video"][0]), os.path.join(d, state["video"][1])
        if os.path.exists(mp4) and os.path.exists(srt):
            ss.video = (mp4, srt, state["video"][2], state["video"][3])
    make_cards()
    return True


def page_projects():
    st.markdown("<div class='cf-h2'>Projects</div><div class='cf-lead'>Every paper you turned into content, saved to your account.</div>",
                unsafe_allow_html=True)
    projects = store.list_projects(ss.user)
    if not projects:
        st.markdown("<div class='cf-card' style='color:var(--muted)'>No projects yet. Create your first one in the Studio.</div>",
                    unsafe_allow_html=True)
        if st.button("Go to Studio", type="primary"):
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
            c2.markdown(f"<span class='vp-badge b-SUPPORTED'>{html.escape(m.get('score', ''))} claims supported</span>",
                        unsafe_allow_html=True)
            if c3.button("Open", key=f"open_{m['id']}", width="stretch", type="primary"):
                with st.spinner("Opening project…"):
                    if open_project(m["id"]):
                        ss.page = "Studio"
                        st.rerun()
            if c4.button("Delete", key=f"del_{m['id']}", width="stretch"):
                ss.confirm_delete = m["id"]
            if ss.get("confirm_delete") == m["id"]:
                st.warning("Delete this project permanently?")
                d1, d2, _ = st.columns([1, 1, 4])
                if d1.button("Yes, delete", key=f"yes_{m['id']}", type="primary"):
                    store.delete_project(ss.user, m["id"])
                    if ss.get("pid") == m["id"]:
                        clear_project()
                    ss.pop("confirm_delete", None)
                    st.rerun()
                if d2.button("Cancel", key=f"no_{m['id']}"):
                    ss.pop("confirm_delete", None)
                    st.rerun()

# ------------------------------------------------------------------ channels


def page_channels():
    st.markdown("<div class='cf-h2'>Channels</div><div class='cf-lead'>Connect the accounts your team publishes to. "
                "CiteFlow uses them to address each post and open the right place to publish.</div>", unsafe_allow_html=True)
    data = ss.setdefault("channels", {})
    cols = st.columns(2, gap="medium")
    for k, (key, label, ico, color, field, ph) in enumerate(CHANNELS):
        with cols[k % 2]:
            with st.container(border=True):
                on = bool(data.get(key))
                st.markdown(f"<div class='cf-chan'><div class='cf-chan-ico' style='background:{color}'>{ico}</div>"
                            f"<div style='flex:1'><b>{label}</b><br><span style='color:var(--muted);font-size:13px'>{field}</span></div>"
                            f"<span class='cf-status {'cf-on' if on else 'cf-off'}'>{'CONNECTED' if on else 'NOT CONNECTED'}</span></div>",
                            unsafe_allow_html=True)
                val = st.text_input(field, value=data.get(key, ""), placeholder=ph, key=f"ch_{key}",
                                    label_visibility="collapsed")
                b1, b2 = st.columns(2)
                if b1.button("Save", key=f"save_{key}", width="stretch", type="primary"):
                    data[key] = val.strip()
                    save_channels(ss.user, data)
                    st.rerun()
                if on and b2.button("Disconnect", key=f"dis_{key}", width="stretch"):
                    data[key] = ""
                    save_channels(ss.user, data)
                    st.rerun()
    st.caption("Publishing mode: CiteFlow opens each platform with the approved post and image ready, so a person "
               "publishes it. Fully automatic posting needs each platform's app review (Meta, LinkedIn, X) and is on the roadmap.")

# ------------------------------------------------------------------ account


def page_account():
    st.markdown("<div class='cf-h2'>Account</div>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown(f"**{html.escape(ss.get('user_name') or ss.user)}**  \n<span style='color:var(--muted)'>{html.escape(ss.user)}</span>",
                    unsafe_allow_html=True)
        n = len(store.list_projects(ss.user))
        conn = sum(1 for v in ss.get("channels", {}).values() if v)
        st.markdown(f"<span style='color:var(--muted)'>{n} project(s) saved · {conn} channel(s) connected</span>",
                    unsafe_allow_html=True)
        st.markdown("**How CiteFlow works**  \nAI drafts the content and checks every claim against the paper. "
                    "Deterministic rules double-check numbers. A person approves before anything is published.")
        if st.button("Sign out", type="primary"):
            for k in list(ss.keys()):
                del ss[k]
            st.rerun()

# ------------------------------------------------------------------ studio


def page_studio():
    left, right = st.columns([1.05, 1], gap="large")
    with left:
        st.markdown("""
<div class="cf-hero">
  <div class="cf-eyebrow">For university communications teams</div>
  <h1>Turn research into <em>trusted</em> PR content.</h1>
  <p>Upload a paper. CiteFlow drafts the press release, social posts and video, and links every claim back to the page it came from.</p>
</div>
<div class="cf-points">
  <div class="cf-point"><b>Traceable</b>Every claim linked to its source page</div>
  <div class="cf-point"><b>Human-reviewed</b>Nothing goes out without approval</div>
  <div class="cf-point"><b>Channel-ready</b>Release, four platforms, video</div>
</div>""", unsafe_allow_html=True)
    with right:
        with st.container(border=True):
            pdf = st.file_uploader("Research paper (PDF)", type=["pdf"])
            c1, c2 = st.columns(2)
            lang = c1.selectbox("Language", ["English", "Hungarian"])
            tone = c2.selectbox("Audience", ["General public", "Students", "Industry partners"])
            with st.expander("Add a researcher (optional)"):
                person = st.text_input("Name and title", placeholder="Dr. Anna Kovács")
                photo_file = st.file_uploader("Photo (only with the person's consent)", type=["jpg", "jpeg", "png"])
            go = st.button("Create verified content", type="primary", width="stretch", disabled=not pdf)

    if go:
        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
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
                                       pl.build_items(ss.content.headline, ss.release, ss.content.scenes, ss.posts, ss.content.visual))
                make_cards()
                ss.person = person.strip()
                ss.photo = Image.open(photo_file).convert("RGB") if photo_file else None
                for k in ("video", "hype", "plan"):
                    ss.pop(k, None)
                ss.chat = []
                ss.accepted = []
                ss.pid, ss.created = store.new_project_id(), time.strftime("%Y-%m-%d %H:%M")
                persist()
                status.update(label="Done", state="complete")
        except Exception as e:
            msg = str(e)
            if any(t in msg for t in pl.TRANSIENT):
                st.warning("Google's AI service is very busy right now. Please wait a minute and click "
                           "**Create verified content** again.")
            else:
                st.error(f"Something went wrong: {e}")

    if "results" in ss:
        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
        results_view()


SHARE_HELP = {
    "x": "Opens X with the post already filled in. Attach the image if you like, then press Post.",
    "linkedin": "Opens LinkedIn with the post filled in; the text is also copied and the image saved. Add the image, then Post.",
    "facebook": "Copies the post and saves the image, then opens Facebook. Paste (Ctrl+V), add the image, then Post.",
    "instagram": "Copies the caption and saves the image, then opens Instagram. Create a post, choose the image, paste the caption.",
}

BRAND = {"linkedin": ("#0a66c2", "in"), "facebook": ("#1877f2", "f"), "instagram": ("#c13584", "IG"),
         "x": ("#111111", "X"), "blog": ("#3d6d5c", "B")}


def share_button(key, label, text, url, img_b64="", filename="citeflow.jpg", height=48):
    """One click: copies the post text, downloads the image (if any) and opens the platform in a new tab."""
    color = BRAND.get(key, ("#3d6d5c", ""))[0]
    payload = json.dumps({"t": text, "u": url, "i": img_b64, "f": filename}).replace("</", "<\\/")
    components.html(f"""
<button id="b" style="width:100%;height:40px;border:none;border-radius:9px;background:{color};color:#fff;
 font:600 14px 'Plus Jakarta Sans',system-ui,sans-serif;cursor:pointer">{html.escape(label)}</button>
<script>
const P = {payload};
const b = document.getElementById('b');
b.onclick = () => {{
  let ok = false;
  const ta = document.createElement('textarea'); ta.value = P.t; ta.style.position='fixed'; ta.style.opacity='0';
  document.body.appendChild(ta); ta.select();
  try {{ ok = document.execCommand('copy'); }} catch (e) {{}}
  ta.remove();
  if (navigator.clipboard) {{ navigator.clipboard.writeText(P.t).catch(() => {{}}); }}
  if (P.i) {{ const a = document.createElement('a'); a.href = 'data:image/jpeg;base64,' + P.i; a.download = P.f;
             document.body.appendChild(a); a.click(); a.remove(); }}
  window.open(P.u, '_blank', 'noopener');
  b.textContent = P.i ? 'Text copied + image saved ✓' : 'Text copied ✓';
  setTimeout(() => {{ b.textContent = {json.dumps(label)}; }}, 4000);
}};
</script>""", height=height)


def share_url(key, text, chans):
    q = urllib.parse.quote(text)
    own = str(chans.get(key, "") or "")
    if key == "x":
        return f"https://x.com/intent/post?text={q}"
    if key == "linkedin":
        return f"https://www.linkedin.com/feed/?shareActive=true&text={q}"
    if key == "facebook":
        return own if own.startswith("http") else "https://www.facebook.com/"
    if key == "instagram":
        return "https://www.instagram.com/"
    return own if own.startswith("http") else "about:blank"


REFINE_TARGETS = ["Everything", "Headline", "Press release", "LinkedIn post", "Facebook post", "Instagram post",
                  "X post", "Video script"]
QUICK = ["Shorter & punchier", "Warmer tone", "More formal", "Add a call to action", "Simpler explanation"]
QUICK_FULL = {"Shorter & punchier": "Make everything shorter and punchier.",
              "Warmer tone": "Use a warmer, more human tone.",
              "More formal": "Make it more formal, suitable for policymakers and partners.",
              "Add a call to action": "Add a clear call to action at the end of the release and each post.",
              "Simpler explanation": "Explain the method more simply, for a non-expert reader."}


def changed_parts(old, new):
    parts = []
    if old.headline != new.headline or old.subheadline != new.subheadline:
        parts.append("headline")
    if old.press_release.strip() != new.press_release.strip():
        parts.append("press release")
    for k, label in (("linkedin", "LinkedIn"), ("facebook", "Facebook"), ("instagram", "Instagram"), ("x", "X")):
        if getattr(old.posts, k).strip() != getattr(new.posts, k).strip():
            parts.append(f"{label} post")
    if [s.narration for s in old.scenes] != [s.narration for s in new.scenes]:
        parts.append("video script")
    return parts


def apply_feedback(feedback, target):
    engine = llm()
    before = current_content()
    after = pl.revise_content(engine, ss.passages, before, feedback, "" if target == "Everything" else target)
    ss.content = after
    ss.release, ss.posts = after.press_release, after.posts.model_dump()
    ss.results = pl.verify(engine, ss.passages, pl.build_items(after.headline, ss.release, after.scenes, ss.posts, after.visual))
    make_cards()
    parts = changed_parts(before, after)
    if "video script" in parts:
        ss.pop("video", None)
    ok, total = pl.score(ss.results)
    msg = (f"Updated the {', '.join(parts)}." if parts else "I looked at it again, but nothing needed to change.") + \
          f" Re-checked every claim: {ok} of {total} supported by the paper."
    ss.chat.append({"role": "assistant", "content": msg})


def refine_view():
    ss.setdefault("chat", [])
    st.markdown("**Tell CiteFlow what to change**  \n<span style='color:var(--muted);font-size:14px'>Give feedback in your own "
                "words. CiteFlow rewrites the content and checks every claim again.</span>", unsafe_allow_html=True)
    box = st.container(border=True, height=360)
    with box:
        if not ss.chat:
            st.markdown("<span style='color:var(--muted)'>No feedback yet. Try one of the suggestions below, or write your own.</span>",
                        unsafe_allow_html=True)
        for m in ss.chat:
            with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else str(HERE / "mark_green.png")):
                st.markdown(m["content"])
    qcols = st.columns(len(QUICK))
    quick = None
    for c, q in zip(qcols, QUICK):
        if c.button(q, key=f"quick_{q}", width="stretch"):
            quick = q
    with st.form("feedback", clear_on_submit=True):
        c1, c2 = st.columns([1, 3])
        target = c1.selectbox("Apply to", REFINE_TARGETS)
        text = c2.text_area("Your feedback", placeholder="e.g. The LinkedIn post sounds too stiff. Start with the problem "
                                                         "doctors face and end with a question.", height=90)
        sent = st.form_submit_button("Rework content", type="primary")
    fb = QUICK_FULL[quick] if quick else (text.strip() if sent else "")
    if sent and not text.strip():
        st.warning("Write your feedback first.")
    if fb:
        ss.chat.append({"role": "user", "content": fb + ("" if quick or target == "Everything" else f"  \n*Applies to: {target}*")})
        with st.spinner("Reworking the content and checking every claim again…"):
            try:
                apply_feedback(fb, "Everything" if quick else target)
            except Exception as e:
                busy = any(t in str(e) for t in pl.TRANSIENT)
                ss.chat.append({"role": "assistant", "content": "Google's AI service is busy right now. Please send the feedback again in a minute."
                                if busy else f"Sorry, that did not work: {e}"})
        persist()
        st.rerun()


ACTION_HELP = ("Use faithful version = replace the sentence with the accurate wording from the paper. "
               "Keep = you take responsibility for it as written. Remove = delete the sentence.")


def attention_view():
    """Everything the checker flagged, across all outputs, with a decision for each."""
    todo = [r for r in ss.results if r["verdict"] in pl.FLAGGED]
    if not todo:
        acc = sum(1 for r in ss.results if r["verdict"] == "ACCEPTED")
        st.success("Nothing needs your attention. Every claim is supported by the paper"
                   + (f" or accepted by you ({acc})." if acc else "."), icon=":material/verified:")
        return
    with st.container(border=True, key="attention"):
        h1, h2, h3 = st.columns([3, 1.1, 1.1], vertical_alignment="center")
        h1.markdown(f"**{len(todo)} sentence(s) need your decision**  \n<span style='color:var(--muted);font-size:13.5px'>"
                    f"{ACTION_HELP}</span>", unsafe_allow_html=True)
        fixable = [r for r in todo if r.get("rewrite")]
        if h2.button(f"Fix all ({len(fixable)})", type="primary", width="stretch", disabled=not fixable, key="fix_all",
                     help="Replace every sentence that has a faithful version, then check again."):
            with st.spinner("Applying the faithful versions and checking again…"):
                for r in fixable:
                    edit_claim(r, r["rewrite"].strip())
                safe_recheck()
            st.rerun()
        if h3.button("Keep all remaining", width="stretch", key="keep_all",
                     help="Accept every flagged sentence that has no faithful version, as written."):
            acc = ss.setdefault("accepted", [])
            acc.extend(r["text"] for r in todo if not r.get("rewrite"))
            mark_accepted()
            persist()
            st.rerun()
        for r in todo:
            st.markdown("<hr style='margin:10px 0;border:none;border-top:1px solid #ece8dd'>", unsafe_allow_html=True)
            c1, c2 = st.columns([3, 1.3], vertical_alignment="top")
            with c1:
                st.markdown(f"{badge(r['verdict'])} <span style='color:var(--muted);font-size:13px'>&nbsp;{r['part']}</span>",
                            unsafe_allow_html=True)
                st.markdown(f"<div style='font-size:15px;margin:4px 0'><s style='color:#8f2f1b'>{html.escape(r['text'])}</s></div>"
                            if r.get("rewrite") else f"<div style='font-size:15px;margin:4px 0'>{html.escape(r['text'])}</div>",
                            unsafe_allow_html=True)
                if r.get("rewrite"):
                    st.markdown(f"<div style='font-size:15px;color:#2f6450'>→ {html.escape(r['rewrite'])}</div>", unsafe_allow_html=True)
                why = r.get("explanation") or "; ".join(r.get("rule_flags", []))
                if why:
                    st.caption(why)
            with c2:
                if r.get("rewrite") and st.button("Use faithful version", key=f"fix_{r['id']}", type="primary", width="stretch"):
                    with st.spinner("Updating and checking again…"):
                        edit_claim(r, r["rewrite"].strip())
                        safe_recheck()
                    st.rerun()
                if st.button("Keep as is", key=f"keep_{r['id']}", width="stretch"):
                    ss.setdefault("accepted", []).append(r["text"])
                    mark_accepted()
                    persist()
                    st.rerun()
                if r["part"] != "Headline" and st.button("Remove", key=f"rm_{r['id']}", width="stretch"):
                    with st.spinner("Removing and checking again…"):
                        edit_claim(r, "")
                        safe_recheck()
                    st.rerun()


def safe_recheck():
    try:
        recheck()
    except Exception as e:
        st.session_state["recheck_error"] = str(e)
    persist()


@st.fragment
def extras_view(key, card):
    """Carousel (Instagram, LinkedIn) and animated version of a post. Runs on its own, so it never slows the page."""
    c = ss.content
    if key in ("instagram", "linkedin"):
        with st.expander("Carousel (swipe post)"):
            if "carousel" in ss or st.button("Show carousel", key=f"showcar_{key}", width="stretch"):
                carousel_body(key, c)
    with st.expander("Animated version (MP4)"):
        anim_body(key, card, c)


def carousel_body(key, c):
    if "carousel" not in ss:
        with st.spinner("Designing the carousel…"):
            slides = cards.carousel(visual_now(), c.card_title, c.institution, [f.image for f in ss.figures[:1]])
            ss.carousel = [img_bytes(im) for im in slides]
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for i, png in enumerate(ss.carousel, 1):
                    z.writestr(f"slide_{i:02d}.png", png)
            ss.carousel_zip = buf.getvalue()
    cols = st.columns(3)
    for i, png in enumerate(ss.carousel):
        cols[i % 3].image(png, width="stretch")
    st.download_button(f"Download {len(ss.carousel)} slides", ss.carousel_zip, f"citeflow_carousel_{key}.zip",
                       "application/zip", width="stretch", key=f"car_{key}")


def anim_body(key, card, c):
    anims = ss.setdefault("anims", {})
    size = {"wide": (1200, 675), "portrait": (1080, 1350)}.get(card, (1080, 1080))
    if key not in anims:
        if not st.button("Create animation", key=f"anim_{key}", width="stretch"):
            return
        with st.spinner("Animating the post…"):
            fig0 = ss.figures[0].image if ss.figures else None
            v = visual_now()
            path = os.path.join(tempfile.mkdtemp(), f"citeflow_{key}_animated.mp4")
            cards.animated_post(v, c.card_title, c.institution,
                                fig0 if (card == "wide" or not v["key_points"]) else None, path, size=size)
            with open(path, "rb") as fh:
                anims[key] = (path, fh.read())
    if key in anims:
        path, data = anims[key]
        st.video(data, loop=True, autoplay=True, muted=True)
        st.download_button("Download animation", data, os.path.basename(path), "video/mp4",
                           width="stretch", key=f"dla_{key}")


@st.fragment
def fact_check_view(flagged):
    """Runs on its own: switching filters or opening page views does not reload the whole page."""
    res = ss.results
    only = st.toggle("Show only claims that need attention", value=flagged)
    for r in res:
        if r["verdict"] == "NOT_A_CLAIM" or (only and r["verdict"] == "SUPPORTED"):
            continue
        with st.container(border=True):
            st.markdown(f"{badge(r['verdict'])} <span style='color:#5f6b66;font-size:13px'>&nbsp;{r['part']} · {r['id']}</span>",
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


def build_package(content, res):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("press_release.txt", f"{content.headline}\n{content.subheadline}\n\n{ss.release}\n")
        for key, text in ss.posts.items():
            z.writestr(f"post_{key}.txt", text)
        for name in ss.cards:
            z.writestr(f"image_{name}.png", card_bytes(name)[0])
        rows = io.StringIO()
        w = csv.writer(rows)
        w.writerow(["id", "part", "text", "verdict", "issue", "explanation", "faithful_version", "source", "rule_flags"])
        for r in res:
            w.writerow([r["id"], r["part"], r["text"], r["verdict"], r.get("issue_type", ""), r.get("explanation", ""),
                        r.get("rewrite", ""), " | ".join(f"p.{p.page} {p.pid}" for p in r["evidence"]),
                        "; ".join(r.get("rule_flags", []))])
        z.writestr("verification_report.csv", "\ufeff" + rows.getvalue())
        if ss.get("video"):
            z.write(ss.video[0], os.path.basename(ss.video[0]))
            z.write(ss.video[1], os.path.basename(ss.video[1]))
    return buf.getvalue()


def results_view():
    if not all(k in ss.get("cards", {}) for k in ("square", "wide", "portrait")):
        make_cards()
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
  <div class="vp-kpi"><div class="l">Need attention</div><div class="v" style="color:{'#b4442c' if flagged else '#3d7a62'}">{flagged}</div></div>
  <div class="vp-kpi"><div class="l">Source passages checked</div><div class="v">{len(ss.passages)}</div></div>
  <div class="vp-kpi"><div class="l">Figures found in paper</div><div class="v">{len(ss.figures)}</div></div>
</div>""", unsafe_allow_html=True)

    if ss.get("recheck_error"):
        err = ss.pop("recheck_error")
        st.warning("The change was applied, but the re-check could not run because Google's AI service is busy. "
                   "Use **Edit and re-check** in a minute." if any(t in err for t in pl.TRANSIENT) else f"Re-check failed: {err}")
    t_rel, t_soc, t_vid, t_ref, t_chk, t_str, t_exp = st.tabs(
        ["Press release", "Social posts", "Video", "Refine with feedback", "Fact check", "Stress test", "Approve & export"])

    with t_ref:
        refine_view()

    with t_rel:
        attention_view()
        with st.container(border=True):
            st.markdown(f"<div class='vp-sub'>{html.escape(content.institution)}</div>"
                        f"<div class='vp-headline'>{html.escape(content.headline)}</div>"
                        f"<div class='vp-sub'>{html.escape(content.subheadline)}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='vp-release'>{paragraphs_html(res, 'Press release', ss.release)}</div>",
                        unsafe_allow_html=True)
            st.markdown("""<div class="vp-legend"><span><i class="vp-dot" style="background:#3d7a62"></i>Supported</span>
<span><i class="vp-dot" style="background:#c98a12"></i>Exaggerated</span><span><i class="vp-dot" style="background:#b4442c"></i>Unsupported</span>
<span><i class="vp-dot" style="background:#a07d1c"></i>Needs review</span><span>Hover any sentence to see why.</span></div>""",
                        unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("**See the proof**  \n<span style='color:#5f6b66;font-size:14px'>Pick any sentence to see where it comes from in the paper.</span>",
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
                ("instagram", "Instagram", "portrait"), ("x", "X", "wide")]
        words = [w for w in content.institution.replace(",", " ").split() if w[:1].isupper() and w.lower() not in ("of", "the", "and")]
        initials = "".join(w[0] for w in words[:2]).upper() or "CF"
        chans = ss.get("channels", {})
        if not any(chans.values()):
            st.info("Tip: connect your accounts under **Channels** so each post is addressed to the right page.")
        for k, (key, label, card) in enumerate(nets):
            text = ss.posts[key]
            bad = [r for r in res if r["part"] == label and r["verdict"] in pl.FLAGGED]
            state = "SUPPORTED" if not bad else ("EXAGGERATED" if any(r["verdict"] in ("EXAGGERATED", "UNSUPPORTED") for r in bad) else "NEEDS_REVIEW")
            with cols[k % 2]:
                with st.container(border=True):
                    st.markdown(f"""<div class="vp-post-h"><div class="vp-avatar">{html.escape(initials)}</div>
<div><div class="vp-post-name">{html.escape(chans.get(key) or content.institution)}</div><div class="vp-post-meta">{label} · {'connected' if chans.get(key) else 'draft'}</div></div>
<div style="margin-left:auto">{badge(state)}</div></div>
<div class="vp-post-text">{html.escape(text)}</div>""", unsafe_allow_html=True)
                    st.image(card_bytes(card)[0], width="stretch")
                    for r in bad:
                        st.caption(f"⚠ {r['text']} — {r.get('explanation', '')}")
                    with_image = key in ("instagram", "facebook", "linkedin")
                    png, jpg64 = card_bytes(card)
                    share_button(key, f"Publish on {label}", text, share_url(key, text, chans),
                                 jpg64 if with_image else "", f"citeflow_{key}.jpg")
                    st.caption(SHARE_HELP[key])
                    b2, b3 = st.columns(2)
                    b2.download_button("Image", card_bytes(card)[0], f"citeflow_{key}.png", "image/png",
                                       width="stretch", key=f"img_{key}")
                    with b3.popover("Copy text", width="stretch"):
                        st.code(text, language=None, wrap_lines=True)
                    extras_view(key, card)

        with st.container(border=True):
            st.markdown(f"<div class='vp-post-h'><div class='vp-avatar'>B</div><div><div class='vp-post-name'>"
                        f"{html.escape(chans.get('blog') or 'Blog / news site')}</div><div class='vp-post-meta'>Web article · ready to paste</div></div></div>",
                        unsafe_allow_html=True)
            article = (f"<h1>{html.escape(content.headline)}</h1>\n<p><em>{html.escape(content.subheadline)}</em></p>\n" +
                       "\n".join(f"<p>{html.escape(p.strip())}</p>" for p in ss.release.split("\n\n") if p.strip()))
            bb1, bb2 = st.columns(2)
            bb1.download_button("Download article (HTML)", article, "citeflow_article.html", "text/html", width="stretch")
            with bb2:
                if str(chans.get("blog", "")).startswith("http"):
                    plain = f"{content.headline}\n\n{content.subheadline}\n\n{ss.release}"
                    share_button("blog", "Publish on blog", plain, chans["blog"])
                else:
                    st.caption("Connect your blog under **Channels** to publish it in one click.")

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
                                f"<span style='color:#5f6b66'>{extra}</span></div>", unsafe_allow_html=True)
                fmt = st.radio("Format", list(vid.FORMATS), horizontal=True)
                if st.button("Render video", type="primary", width="stretch", disabled=not scenes):
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
                                       width="stretch")
            else:
                st.markdown("<div class='cf-card' style='text-align:center;color:#5f6b66;padding:80px 20px'>"
                            "Your video preview appears here.</div>", unsafe_allow_html=True)

    with t_chk:
        fact_check_view(bool(flagged))

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
                persist()
                sig = (ss.get("last_saved"), (ss.get("video") or [None])[0])
                if ss.get("pkg", (None,))[0] != sig:
                    ss.pkg = (sig, build_package(content, res))
                st.download_button("Download approved package", ss.pkg[1], "citeflow_package.zip",
                                   "application/zip", type="primary", width="stretch")

    persist()


# ------------------------------------------------------------------ insights (publishing plan)

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
    st.markdown("<div class='cf-h2'>Insights</div><div class='cf-lead'>When to post, where, and with which hashtags — "
                "planned for the story you are working on.</div>", unsafe_allow_html=True)
    if "content" not in ss:
        st.markdown("<div class='cf-card' style='color:var(--muted)'>Open or create a project first, then come back for its publishing plan.</div>",
                    unsafe_allow_html=True)
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("Go to Studio", type="primary"):
            ss.page = "Studio"
            st.rerun()
        if c2.button("Open a project"):
            ss.page = "Projects"
            st.rerun()
        return
    st.markdown(f"<div class='vp-sub'>Project</div><div class='vp-headline' style='font-size:24px'>{html.escape(ss.content.headline)}</div>",
                unsafe_allow_html=True)
    label = "Refresh plan" if ss.get("plan") else "Create publishing plan"
    if st.button(label, type="primary"):
        with st.spinner("Planning the launch week…"):
            try:
                ss.plan = pl.publish_plan(llm(), current_content())
                persist()
                st.rerun()
            except Exception as e:
                if any(t in str(e) for t in pl.TRANSIENT):
                    st.warning("Google's AI service is busy right now. Please try again in a minute.")
                else:
                    st.error(f"Something went wrong: {e}")
    plan = ss.get("plan")
    if not plan:
        return
    st.caption("AI recommendations based on typical engagement patterns for each platform and audience, tailored to this "
               "research. They are guidance, not live platform analytics. Times are local (CET).")
    st.markdown(f"<div class='cf-card' style='margin:6px 0 16px'><b>Audience.</b> {html.escape(plan.audience_summary)}</div>",
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
    head = "".join(f"<th style='font-weight:600;color:#5f6b66;font-size:13px'>{d[:3]}</th>" for d in DAYS)
    st.markdown(f"<div class='cf-card' style='overflow-x:auto'><b>Best days and times</b><table style='width:100%;border-collapse:collapse;margin-top:8px'>"
                f"<tr><th></th>{head}<th style='text-align:left;padding-left:12px;font-weight:600;color:#5f6b66;font-size:13px'>Best times</th></tr>{rows}</table></div>",
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
                st.markdown(f"<div style='font-size:14px;margin-top:8px'><b>Format:</b> {html.escape(p.format_tip)}</div>"
                            f"<div style='font-size:13.5px;color:var(--muted);margin-top:4px'>{html.escape(p.why)}</div>",
                            unsafe_allow_html=True)
                with st.popover("Copy hashtags", width="stretch"):
                    st.code(" ".join(p.hashtags), language=None, wrap_lines=True)

    c1, c2 = st.columns([1.4, 1], gap="medium")
    with c1:
        with st.container(border=True):
            st.markdown("**Launch week**")
            for s in plan.schedule:
                st.markdown(f"<div style='display:flex;gap:14px;padding:7px 0;border-bottom:1px solid #eeebe2;font-size:14px'>"
                            f"<div style='min-width:120px;font-weight:700;color:#2b4c40'>{html.escape(s.day)} {html.escape(s.time)}</div>"
                            f"<div style='min-width:90px;color:#5f6b66'>{html.escape(s.platform)}</div><div>{html.escape(s.action)}</div></div>",
                            unsafe_allow_html=True)
    with c2:
        with st.container(border=True):
            st.markdown("**Angles to connect with**")
            for t in plan.trend_angles:
                st.markdown(f"- {t}")
            st.markdown("**Avoid**")
            for t in plan.avoid:
                st.markdown(f"- {t}")


# ------------------------------------------------------------------ app

if not GEMINI_KEY:
    st.error("CiteFlow is not configured yet. Add GEMINI_API_KEY to .streamlit/secrets.toml (see README).")
    st.stop()

if "user" not in ss:
    login_screen()
else:
    ss.setdefault("page", "Studio")
    topbar()
    if ss.page not in PAGES:
        ss.page = "Studio"
    {"Studio": page_studio, "Projects": page_projects, "Insights": page_insights, "Channels": page_channels,
     "Account": page_account}[ss.page]()

st.markdown("<div class='cf-foot'>CiteFlow · AI drafts and verifies, people approve · DEIK.AI Challenge 2026</div>",
            unsafe_allow_html=True)
