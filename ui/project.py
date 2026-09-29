"""The open project: checking, decisions on flagged sentences, images, saving and reopening."""

import base64
import hashlib
import json
import os
import re
import shutil
import time

import streamlit as st

import cards
import pipeline as pl
import store
from ui.core import allow, img_bytes, log_error, ss, verify_now
from ui.i18n import tr


def proof(pid, claim=""):
    """Cached highlighted page image for a passage id (and the claim's numbers)."""
    cache = ss.setdefault("proofs", {})
    key = (pid, claim)
    if key not in cache:
        pm = {p.pid: p for p in ss.passages}
        cache[key] = img_bytes(pl.proof_image(ss.pdf_bytes, pm[pid], claim=claim, zoom=1.6))
        if len(cache) > 12:                       # keep memory small: forget the oldest page views
            cache.pop(next(iter(cache)))
    return cache[key]


def mark_accepted():
    acc = set(ss.get("accepted", []))
    for r in ss.results:
        if r["text"] in acc and r["verdict"] in pl.FLAGGED:
            r["verdict"] = "ACCEPTED"


def recheck():
    ss.results = verify_now(pl.build_items(ss.content.headline, ss.release, ss.content.scenes, ss.posts, ss.content.visual))
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


def ser_results(res):
    return [dict(r, evidence=[p.pid for p in r["evidence"]]) for r in res]


def de_results(sres, passages):
    pm = {p.pid: p for p in passages}
    return [dict(r, evidence=[pm[x] for x in r.get("evidence", []) if x in pm]) for r in sres]


def visual_now():
    return pl.safe_visual(ss.content.visual, ss.get("results", []))


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
             "hype": [ss.hype[0], ser_results(ss.hype[1])] if ss.get("hype") else None,
             "models": ss.get("models") or {}}
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
                "accepted", "carousel", "anims", "carousel_zip", "card_cache", "last_saved", "pkg", "models"]


def clear_project():
    for k in PROJECT_KEYS:
        ss.pop(k, None)


def open_project(pid):
    state, pdf, _ = store.load_project(ss.user, pid)
    if state is None or pdf is None:
        st.error(tr("This project could not be opened (its files are missing)."))
        return False
    cd = dict(state.get("content") or {})
    cd.setdefault("visual", {"kicker": "Research news", "stat_value": "", "stat_label": "", "key_points": [], "cta": ""})
    for sc in cd.get("scenes", []):
        sc.setdefault("wants_figure", False)
        sc.setdefault("stock_query", "")
    try:
        content = pl.Content.model_validate(cd)
    except Exception:
        st.error(tr("This project was saved by an older version of CiteFlow and cannot be opened. "
                 "Please create it again from the paper."))
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
    ss.models = state.get("models") or {}
    if state.get("hype"):
        ss.hype = (state["hype"][0], de_results(state["hype"][1], ss.passages))
    if state.get("video"):
        d = store.project_path(ss.user, pid)
        mp4, srt = os.path.join(d, state["video"][0]), os.path.join(d, state["video"][1])
        if os.path.exists(mp4) and os.path.exists(srt):
            ss.video = (mp4, srt, state["video"][2], state["video"][3])
    make_cards()
    return True


def safe_recheck():
    if not allow("check"):
        persist()
        return
    try:
        recheck()
    except Exception as e:
        log_error("re-check", e)
        st.session_state["recheck_error"] = str(e)
    persist()
