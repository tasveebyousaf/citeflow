"""Sign-in and account creation."""

import time

import streamlit as st

import store
from ui.core import DEMO_USER, b64, client_ip, limit, load_channels, ss, team_accounts
from ui.i18n import language_picker, tr


def sign_in(user_id, name):
    ss.user, ss.user_name = user_id, name
    ss.page = "Studio"
    ss.channels = load_channels(user_id)
    st.rerun()


PRIVACY_NOTE = """**What CiteFlow stores:** your name, email or username, a salted hash of your password (never the password
itself), the papers you upload, the content CiteFlow creates from them and the channel links you save.

**Why:** only to provide the service: to show your projects again and to address your posts.

**Who processes it:** uploaded papers are sent to Google's Gemini API to write and check the content, and video
narration text is sent to Microsoft's text-to-speech service. Stock footage searches use short keywords only.
Nothing is sold or used for advertising.

**How long:** projects that are not changed for {retention} days are deleted automatically ({demo} days for the shared
demo account). Your account stays until you delete it.

**Your control:** you can delete any project, or delete your whole account and all its data on the Account page.
Upload only papers you are allowed to share, and photos of people only with their consent.
Questions: contact the CiteFlow team."""


def privacy_note():
    return tr(PRIVACY_NOTE).format(retention=limit("retention_days"), demo=limit("demo_retention_days"))


def login_screen():
    _, lang_col = st.columns([5, 1])
    with lang_col:
        language_picker("lang_login")
    left, right = st.columns([1.1, 1], gap="large")
    with left:
        st.markdown(tr('<div class="cf-login-side"><img alt="CiteFlow" src="data:image/png;base64,{0}">\n<h2>Research news your university can stand behind.</h2>\n<p>CiteFlow drafts press releases, social posts and videos from a paper, and links every claim back to the page it came from.</p>\n<ul><li>Every claim traceable to the source</li><li>Every piece reviewed by a person</li><li>Every channel ready to publish</li></ul></div>').format(b64('logo_gold.png')),
                    unsafe_allow_html=True)
    with right:
        t_in, t_up = st.tabs([tr("Sign in"), tr("Create account")])
        with t_in:
            with st.form("login"):
                email = st.text_input(tr("Email or username"), placeholder=tr("name@unideb.hu"))
                pw = st.text_input(tr("Password"), type="password")
                ok = st.form_submit_button(tr("Sign in"), type="primary", width="stretch")
            if ok:
                ident = email.strip().lower()[:80]
                ip_key = f"ip:{client_ip()}" if client_ip() else ""
                wait = store.locked_for(ident, ip_key)
                if wait:
                    st.error(tr('Too many failed attempts. Please try again in {0} minute(s).').format(wait // 60 + 1))
                else:
                    stored = team_accounts().get(ident)
                    if stored is not None and store.check_hash_string(stored, pw):
                        store.clear_failures(ident)
                        sign_in(ident, ident.split("@")[0].replace(".", " ").title())
                    u = store.check_user(ident, pw) if stored is None else None
                    if u:
                        store.clear_failures(ident)
                        sign_in(u["username"], u["name"])
                    store.record_failure(ident, ip_key)
                    time.sleep(0.6)
                    st.error(tr("Email/username or password is not correct."))
            if DEMO_USER in team_accounts():
                st.caption(tr("Demo access for reviewers: **demo@citeflow.app** · password **citeflow2026**. "
                           "The demo account is shared, so please do not upload confidential papers with it."))
        with t_up:
            with st.form("signup"):
                name = st.text_input(tr("Full name"), placeholder=tr("Anna Kovács"), max_chars=80)
                uid = st.text_input(tr("Email or username"), placeholder=tr("anna.kovacs@unideb.hu"), key="su_id", max_chars=40)
                p1 = st.text_input(tr("Password (min. 8 characters, a letter and a number)"), type="password", key="su_p1",
                                   max_chars=128)
                p2 = st.text_input(tr("Repeat password"), type="password", key="su_p2")
                agree = st.checkbox(tr("I have read the privacy note below and agree that CiteFlow stores my account "
                                    "and projects to provide the service."))
                make = st.form_submit_button(tr("Create account"), type="primary", width="stretch")
            if make:
                ip = client_ip()
                if p1 != p2:
                    st.error(tr("The two passwords do not match."))
                elif not agree:
                    st.error(tr("Please accept the privacy note to create an account."))
                elif uid.strip().lower() in team_accounts():
                    st.error(tr("This account already exists. Please sign in."))
                elif ip and store.usage_today(f"signup:{ip}") >= limit("signups_per_ip_per_day"):
                    st.error(tr("Too many accounts were created from this network today. Please try again tomorrow."))
                else:
                    err = store.create_user(uid, p1, name)
                    if err:
                        st.error(err)
                    else:
                        if ip:
                            store.use_quota([(f"signup:{ip}", 10 ** 6)])
                        sign_in(uid.strip().lower(), name.strip()[:80] or uid.strip())
            with st.expander(tr("Privacy note")):
                st.markdown(privacy_note())
