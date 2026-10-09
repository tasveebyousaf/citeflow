"""Shared configuration: secrets, limits, AI clients, monitoring, look and navigation."""

import base64
import html
import io
import os
from pathlib import Path

import streamlit as st

import pipeline as pl
import store
from ui.i18n import tr

HERE = Path(__file__).resolve().parents[1]          # project folder (logos, config)
ss = st.session_state


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


DEMO_USER = "demo@citeflow.app"


def team_accounts():
    """Accounts from [users] in secrets.toml (email = "pbkdf2_sha256$..." hash, or a password).
    Falls back to one public demo account."""
    try:
        if "users" in st.secrets:
            return {k.lower(): str(v) for k, v in dict(st.secrets["users"]).items()}
    except Exception:
        pass
    return {DEMO_USER: "citeflow2026"}


LIMIT_DEFAULTS = {"user_daily_runs": 15, "demo_daily_runs": 60, "global_daily_runs": 300,
                  "user_daily_checks": 80, "demo_daily_checks": 250, "signups_per_ip_per_day": 3, "max_pdf_pages": 80,
                  "retention_days": 365, "demo_retention_days": 14}


def limit(name):
    try:
        if "limits" in st.secrets and name in st.secrets["limits"]:
            return int(st.secrets["limits"][name])
    except Exception:
        pass
    return LIMIT_DEFAULTS[name]


def client_ip():
    try:
        return st.context.ip_address or ""
    except Exception:
        return ""


def is_demo():
    return ss.get("user") == DEMO_USER


def allow(kind="run"):
    """Usage limit for expensive AI actions. kind: 'run' (generate, rework, stress test, plan, video) or 'check'."""
    who = "demo" if is_demo() else "user"
    counters = [(f"{kind}:{ss.get('user', '')}", limit(f"{who}_daily_{kind}s"))]
    if kind == "run":
        counters.append(("run:all", limit("global_daily_runs")))
    hit = store.use_quota(counters)
    if hit is None:
        return True
    if hit == "run:all":
        st.warning(tr("CiteFlow has reached its shared daily AI limit. Please try again tomorrow."))
    else:
        st.warning(tr("You have reached today's limit for this action. It resets at midnight. "
                   "(Limits protect the shared AI quota.)"))
    return False


GEMINI_KEY, PEXELS_KEY = secret("GEMINI_API_KEY"), secret("PEXELS_API_KEY")


@st.cache_resource(show_spinner=False)
def start_error_tracking(dsn):
    """Optional: sends crashes to Sentry when SENTRY_DSN is set in secrets (no personal data is sent)."""
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=dsn, send_default_pii=False, traces_sample_rate=0.0)
        return True
    except Exception:
        return False


SENTRY = start_error_tracking(secret("SENTRY_DSN")) if secret("SENTRY_DSN") else False


def admins():
    """Emails allowed to see the Admin page: ADMINS = ["you@unideb.hu"] in secrets."""
    try:
        raw = st.secrets.get("ADMINS", [])
    except Exception:
        raw = []
    if isinstance(raw, str):
        raw = raw.split(",")
    return {str(a).strip().lower() for a in raw if str(a).strip()}


def is_admin():
    return ss.get("user", "") in admins()


def pricing():
    """Optional prices per 1M tokens for cost estimates: [pricing] "model-name" = { input = 0.0, output = 0.0 }."""
    try:
        return {k: dict(v) for k, v in dict(st.secrets.get("pricing", {})).items()}
    except Exception:
        return {}


@st.cache_resource(show_spinner=False)
def model_chain(key):
    return pl.model_chain(key)


def monitor_hook():
    """Records every AI call (model, tokens, time, errors). Captures the user now, because calls run in worker threads."""
    user = ss.get("user", "")
    return lambda event: store.log_event({**event, "kind": "ai_call", "user": user})


def log_error(action, error):
    try:
        store.log_event({"kind": "error", "user": ss.get("user", ""), "action": action, "ok": False,
                         "detail": pl.redact(f"{type(error).__name__}: {error}")})
    except Exception:
        pass
    if SENTRY:
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(error)
        except Exception:
            pass


def llm():
    """The writer."""
    chain = model_chain(GEMINI_KEY)
    return pl.LLM(GEMINI_KEY, chain[0], fallbacks=chain[1:], on_call=monitor_hook())


def checker():
    """The fact-checker: a fixed, strong model (CHECKER_MODEL in secrets: auto, same or a model name).
    Falls back only to other full models, never Lite or "latest" aliases. CHECKER_STRICT = "true" disables fallback."""
    strict = (secret("CHECKER_STRICT") or "").lower() in {"1", "true", "yes"}
    chain = pl.checker_chain(model_chain(GEMINI_KEY), secret("CHECKER_MODEL") or "auto", strict=strict)
    return pl.LLM(GEMINI_KEY, chain[0], fallbacks=chain[1:], on_call=monitor_hook())


def ai_problem(error, action="try again"):
    """A friendly message for known AI service problems, or None for other errors."""
    if isinstance(error, pl.CreditsExhausted):
        return tr("The AI credits for this app are used up. Please contact the administrator.")
    if isinstance(error, pl.QuotaExhausted):
        return tr("The AI usage limit has been reached for every available model. Please try again later.")
    text = str(error)
    if "CreditsExhausted" in text:
        return tr("The AI credits for this app are used up. Please contact the administrator.")
    if "QuotaExhausted" in text:
        return tr("The AI usage limit has been reached for every available model. Please try again later.")
    kind = pl.classify_error(text)
    if kind == "credits":
        return tr("The AI credits for this app are used up. Please contact the administrator.")
    if kind in {"rate_limit", "daily_quota", "transient"}:
        return tr("Google's AI service is busy right now. Please wait a minute and {0}.").format(tr(action))
    return None


def verify_now(items, writer=None):
    """Checks items with the independent checker and remembers which models wrote and checked."""
    chk = checker()
    results = pl.verify(chk, ss.passages, items)
    try:                                   # faithful rewrites, re-checked; a failure here never blocks the results
        pl.rewrite_flagged(writer or llm(), chk, ss.passages, results)
    except Exception as e:
        log_error("faithful rewrite", e)
    used = sorted({r.get("checked_by") for r in results if r.get("checked_by")})
    ss.models = {"writer": getattr(writer, "model", None) or ss.get("models", {}).get("writer", ""),
                 "checker": ", ".join(used) or chk.model}
    return results


@st.cache_data(show_spinner=False)
def b64(name):
    return base64.b64encode((HERE / name).read_bytes()).decode()


CSS = """
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
/* each verdict has its own line pattern as well as colour, so it is not told by colour alone */
.cl-SUPPORTED { background:#e7f1ec; border-bottom:2px solid var(--ok); }
.cl-EXAGGERATED { background:#fbf0d8; border-bottom:4px double var(--warn); }
.cl-UNSUPPORTED { background:#f8e3dc; border-bottom:3px solid var(--bad); }
.cl-NEEDS_REVIEW, .cl-UNCHECKED { background:#f6f0da; border-bottom:2px dashed var(--review); }
.cl-ACCEPTED { background:#eef0ec; border-bottom:2px dotted #6b7a72; }
.vp-line { display:inline-block; width:22px; height:0; margin-right:6px; vertical-align:middle; }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); border:0; }
button:focus-visible, a:focus-visible, [role="tab"]:focus-visible { outline:3px solid var(--gold) !important; outline-offset:2px; }
.vp-legend { display:flex; gap:16px; font-size:13px; color:var(--muted); margin-top:4px; flex-wrap:wrap; }
.vp-dot { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; vertical-align:middle; }
.cf-type { display:inline-block; background:#f6ead0; color:#7a5a12; border-radius:999px; padding:2px 10px; font-size:12.5px; font-weight:700; margin:2px 4px 2px 0; }
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
.stButton > button[kind="primary"]:disabled { background:#dfe5e1 !important; border-color:#dfe5e1 !important; }
.stButton > button[kind="primary"]:disabled p { color:#4f5c55 !important; }
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
.cf-on { background:#e7f1ec; color:#2f6450; } .cf-off { background:#f1efe8; color:#5f6b66; }
</style>
"""


def inject_css():
    """The CiteFlow look (fonts, colours, top bar, cards). Called on every run."""
    st.markdown(CSS, unsafe_allow_html=True)


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


PAGES = ["Studio", "Projects", "Insights", "Channels", "Account"]


def pages():
    return PAGES + (["Admin"] if is_admin() else [])


def topbar():
    with st.container(key="topbar"):
        names = pages()
        cols = st.columns([2.0] + [0.87] * len(names) + [1.35], vertical_alignment="center")
        cols[0].markdown(f"<img class='cf-logo' alt='CiteFlow' src='data:image/png;base64,{b64('logo_gold.png')}'>",
                         unsafe_allow_html=True)
        for c, name in zip(cols[1:1 + len(names)], names, strict=True):
            if c.button(tr(name), key=f"nav_{name}", type="primary" if ss.page == name else "secondary",
                        width="stretch"):
                ss.page = name
                st.rerun()
        cols[-1].markdown(tr("<div class='cf-user'>Signed in as<br><b>{0}</b></div>").format(html.escape(ss.get('user_name') or ss.user)),
                         unsafe_allow_html=True)


def badge(v):
    return f'<span class="vp-badge b-{v}">{tr(v.replace("_", " "))}</span>'


def donut(ok, total):
    pct = 0 if total == 0 else ok / total
    c = 2 * 3.1416 * 26
    color = "#3d7a62" if pct >= .85 else "#c98a12" if pct >= .6 else "#b4442c"
    return (f'<svg width="64" height="64" viewBox="0 0 64 64"><circle cx="32" cy="32" r="26" fill="none" stroke="#ece9df" stroke-width="8"/>'
            f'<circle cx="32" cy="32" r="26" fill="none" stroke="{color}" stroke-width="8" stroke-linecap="round" '
            f'stroke-dasharray="{c * pct:.1f} {c:.1f}" transform="rotate(-90 32 32)"/>'
            f'<text x="32" y="37" text-anchor="middle" font-size="15" font-weight="800" fill="#2b4c40">{round(pct * 100)}%</text></svg>')


def img_bytes(im, fmt="PNG"):
    b = io.BytesIO()
    if fmt == "JPEG":
        im.convert("RGB").save(b, "JPEG", quality=90, optimize=True)
    else:
        im.save(b, "PNG", optimize=False)
    return b.getvalue()
