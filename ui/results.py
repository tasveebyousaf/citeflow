"""The Studio results: release, posts, images, video, fact check, refinement and export."""

import csv
import html
import io
import json
import os
import tempfile
import urllib.parse
import zipfile

import streamlit as st

import cards
import pipeline as pl
import video as vid
from ui.core import HERE, PEXELS_KEY, ai_problem, allow, badge, checker, donut, img_bytes, llm, log_error, ss, verify_now
from ui.i18n import choose, tr
from ui.project import (
    POST_OF,
    card_bytes,
    current_content,
    make_cards,
    mark_accepted,
    persist,
    proof,
    replace_sentence,
    safe_recheck,
    visual_now,
)


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
            label = "" if v in ("SUPPORTED", "NOT_A_CLAIM") else f'<span class="sr-only"> ({v.replace("_", " ").lower()})</span>'
            spans.append(f'<span class="cl cl-{v}" title="{tip}">{html.escape(s)}{label}</span>')
        out.append("<p>" + " ".join(spans) + "</p>")
    return "".join(out)


def proof_viewer(r, key):
    """Show the claim's evidence as highlighted pages from the paper."""
    if not r["evidence"]:
        st.caption(tr("No supporting passage was found in the paper."))
        return
    tabs = st.tabs([f"Page {p.page}" for p in r["evidence"]]) if len(r["evidence"]) > 1 else [st.container()]
    for t, p in zip(tabs, r["evidence"], strict=True):
        with t:
            st.image(proof(p.pid, r["text"]), width="stretch")
            st.caption(tr('Highlighted: the passage the checker relied on (page {0}, {1}).').format(p.page, p.pid))


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
    st.iframe(f"""
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
  b.textContent = P.i ? {json.dumps(tr('Text copied + image saved ✓'))} : {json.dumps(tr('Text copied ✓'))};
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
    ss.results = verify_now(pl.build_items(after.headline, ss.release, after.scenes, ss.posts, after.visual), writer=engine)
    make_cards()
    parts = changed_parts(before, after)
    if "video script" in parts:
        ss.pop("video", None)
    ok, total = pl.score(ss.results)
    msg = (tr("Updated the {0}.").format(", ".join(tr(p) for p in parts)) if parts
           else tr("I looked at it again, but nothing needed to change.")) + " " + \
        tr("Re-checked every claim: {0} of {1} supported by the paper.").format(ok, total)
    ss.chat.append({"role": "assistant", "content": msg})


def refine_view():
    ss.setdefault("chat", [])
    st.markdown(tr("**Tell CiteFlow what to change**  \n<span style='color:var(--muted);font-size:14px'>Give feedback in your own "
                "words. CiteFlow rewrites the content and checks every claim again.</span>"), unsafe_allow_html=True)
    box = st.container(border=True, height=360)
    with box:
        if not ss.chat:
            st.markdown(tr("<span style='color:var(--muted)'>No feedback yet. Try one of the suggestions below, or write your own.</span>"),
                        unsafe_allow_html=True)
        for m in ss.chat:
            with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else str(HERE / "mark_green.png")):
                st.markdown(m["content"])
    qcols = st.columns(len(QUICK))
    quick = None
    for c, q in zip(qcols, QUICK, strict=True):
        if c.button(tr(q), key=f"quick_{q}", width="stretch"):
            quick = q
    with st.form("feedback", clear_on_submit=True):
        c1, c2 = st.columns([1, 3])
        target = choose(c1.selectbox, tr("Apply to"), REFINE_TARGETS)
        text = c2.text_area(tr("Your feedback"), max_chars=1000, placeholder=tr("e.g. The LinkedIn post sounds too stiff. Start with the problem "
                                                         "doctors face and end with a question."), height=90)
        sent = st.form_submit_button(tr("Rework content"), type="primary")
    fb = QUICK_FULL[quick] if quick else (text.strip() if sent else "")
    if sent and not text.strip():
        st.warning(tr("Write your feedback first."))
    if fb and not allow("run"):
        fb = ""
    if fb:
        shown = tr(quick) if quick else fb
        ss.chat.append({"role": "user", "content": shown + ("" if quick or target == "Everything"
                                                            else "  \n*" + tr("Applies to: {0}").format(tr(target)) + "*")})
        request("feedback", fb=fb, target="Everything" if quick else target)


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
        h1.markdown(tr("**{0} sentence(s) need your decision**  \n<span style='color:var(--muted);font-size:13.5px'>{1}</span>").format(len(todo), tr(ACTION_HELP)), unsafe_allow_html=True)
        fixable = [r for r in todo if r.get("rewrite")]
        if h2.button(tr('Fix all ({0})').format(len(fixable)), type="primary", width="stretch", disabled=not fixable, key="fix_all",
                     help=tr("Replace every sentence that has a faithful version, then check again.")):
            request("fix_all")
        if h3.button(tr("Keep all remaining"), width="stretch", key="keep_all",
                     help=tr("Accept every flagged sentence that has no faithful version, as written.")):
            acc = ss.setdefault("accepted", [])
            acc.extend(r["text"] for r in todo if not r.get("rewrite"))
            mark_accepted()
            persist()
            st.rerun()
        for r in todo:
            st.markdown("<hr style='margin:10px 0;border:none;border-top:1px solid #ece8dd'>", unsafe_allow_html=True)
            c1, cp, c2 = st.columns([3, 1.7, 1.3], vertical_alignment="top")
            with c1:
                statement_view(r)
            with cp:
                type_panel(r)
            with c2:
                if not r.get("rewrite") and can_rewrite(r) and st.button(
                        tr("Write faithful version"), key=f"write_{r['id']}", type="primary", width="stretch",
                        help=tr("Rewrite this sentence at the strength the paper supports, then check it again.")):
                    request("write", rid=r["id"])
                if r.get("rewrite") and st.button(tr("Use faithful version"), key=f"fix_{r['id']}", type="primary", width="stretch"):
                    request("fix", rid=r["id"])
                if st.button(tr("Keep as is"), key=f"keep_{r['id']}", width="stretch"):
                    ss.setdefault("accepted", []).append(r["text"])
                    mark_accepted()
                    persist()
                    st.rerun()
                if r["part"] != "Headline" and st.button(tr("Remove"), key=f"rm_{r['id']}", width="stretch"):
                    request("remove", rid=r["id"])


@st.fragment
def extras_view(key, card):
    """Carousel (Instagram, LinkedIn) and animated version of a post. Runs on its own, so it never slows the page."""
    c = ss.content
    if key in ("instagram", "linkedin"):
        with st.expander(tr("Carousel (swipe post)")):
            if "carousel" in ss or st.button(tr("Show carousel"), key=f"showcar_{key}", width="stretch"):
                carousel_body(key, c)
    with st.expander(tr("Animated version (MP4)")):
        anim_body(key, card, c)


def carousel_body(key, c):
    if "carousel" not in ss:
        with st.spinner(tr("Designing the carousel…")):
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
    st.download_button(tr('Download {0} slides').format(len(ss.carousel)), ss.carousel_zip, f"citeflow_carousel_{key}.zip",
                       "application/zip", width="stretch", key=f"car_{key}")


def anim_body(key, card, c):
    anims = ss.setdefault("anims", {})
    size = {"wide": (1200, 675), "portrait": (1080, 1350)}.get(card, (1080, 1080))
    if key not in anims:
        if not st.button(tr("Create animation"), key=f"anim_{key}", width="stretch"):
            return
        with st.spinner(tr("Animating the post…")):
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
        st.download_button(tr("Download animation"), data, os.path.basename(path), "video/mp4",
                           width="stretch", key=f"dla_{key}")


# Labels for flags that are not one of the four measured patterns (descriptive, derived from the checker's reason).
OTHER_LABELS = (
    ("contradict", "Reported result → contradicted"),
    ("opposite", "Reported result → contradicted"),
    ("number", "Exact figure → altered figure"),
    ("figure", "Exact figure → altered figure"),
    ("round", "Exact figure → altered figure"),
    ("percent", "Exact figure → altered figure"),
    ("limitation", "Qualified result → caveat removed"),
    ("caveat", "Qualified result → caveat removed"),
    ("attribut", "One group's result → misattributed"),
    ("credited", "One group's result → misattributed"),
    ("wrong method", "One group's result → misattributed"),
    ("wrong group", "One group's result → misattributed"),
    ("hype", "Measured result → hyped"),
    ("breakthrough", "Measured result → hyped"),
    ("revolution", "Measured result → hyped"),
    ("inflat", "Measured result → hyped"),
)


def distortion_label(r):
    """(label, measured) for a flagged result: one of the four tested patterns, or a descriptive label for other flags."""
    if r.get("types"):
        return pl.DISTORTION_LABELS.get(r["types"][0], r["types"][0]), True
    verdict = r.get("verdict", "")
    if verdict == "UNCHECKED":
        return "Not checked → check again", False
    flags = " ".join(r.get("rule_flags", [])).lower()
    if verdict == "NEEDS_REVIEW" and "number" in flags:
        return "Exact figure → altered figure", False
    if verdict == "NEEDS_REVIEW" and "no valid source" in flags:
        return "No source passage found → needs review", False
    reason = f"{r.get('issue_type', '')} {r.get('explanation', '')}".lower()
    for word, label in OTHER_LABELS:
        if word in reason:
            return label, False
    if verdict == "UNSUPPORTED":
        return "Not in the paper → stated as fact", False
    return "Supported strength → overstated", False


def type_panel(r):
    """The 'Distortion type' panel: the label, and for the four measured patterns the paper's level (green) next to
    the claim's level (red)."""
    if r.get("verdict") not in pl.FLAGGED:
        return
    label, measured = distortion_label(r)
    levels = ""
    if measured:
        for sc, (ev, cl) in (r.get("levels") or {}).items():
            if pl.DISTORTION_OF[sc] in r["types"][:1] and ev and cl:
                levels += (f"<div class='cf-levels'><span class='cf-ev'>{html.escape(tr('Paper'))}: {html.escape(tr(pl.LEVEL_WORDS[ev]))}</span>"
                           f"<span class='cf-arrow'>↓</span>"
                           f"<span class='cf-cl'>{html.escape(tr('Claim'))}: {html.escape(tr(pl.LEVEL_WORDS[cl]))}</span></div>")
    also = [pl.DISTORTION_LABELS.get(t, t) for t in (r.get("types") or [])[1:]]
    extra = (f"<div class='cf-also'>{html.escape(tr('Also'))}: {html.escape(', '.join(tr(a) for a in also))}</div>" if also else "")
    st.markdown(f"<div class='cf-panel'><div class='cf-panel-h'>{html.escape(tr('Distortion type'))}</div>"
                f"<div class='cf-panel-l'>{html.escape(tr(label))}</div>{levels}{extra}</div>", unsafe_allow_html=True)


def statement_view(r):
    """Original sentence (crossed out in red when a faithful version exists), the faithful version in green, why."""
    st.markdown(tr("{0} <span style='color:var(--muted);font-size:13px'>&nbsp;{1}</span>").format(badge(r['verdict']), tr(r['part'])),
                unsafe_allow_html=True)
    if r.get("rewrite"):
        st.markdown(f"<div class='cf-orig'><s>{html.escape(r['text'])}</s></div>"
                    f"<div class='cf-fix'><span class='cf-fix-h'>{html.escape(tr('Faithful version'))}</span>{html.escape(r['rewrite'])}</div>",
                    unsafe_allow_html=True)
        rewrite_status(r)
    else:
        st.markdown(f"<div style='font-size:15px;margin:4px 0'>{html.escape(r['text'])}</div>", unsafe_allow_html=True)
    why = r.get("explanation") or "; ".join(r.get("rule_flags", []))
    if why:
        st.caption(why)


def rewrite_status(r):
    """Whether the faithful version passed the independent re-check."""
    if r.get("rewrite_verified") is True:
        st.caption("✓ " + tr("Re-checked by the independent checker: supported by the paper."))
    elif r.get("rewrite_verified") is False:
        st.caption("⚠ " + tr("The re-check did not confirm this version. Edit it before use."))


def can_rewrite(r):
    """The rewrite step fixes overclaims (a sentence stronger than its evidence), not invented facts or wrong numbers."""
    return bool(r.get("evidence")) and (bool(r.get("types")) or r.get("verdict") == "EXAGGERATED")


def write_faithful(r):
    """On demand: the writer rewrites one flagged sentence to the paper's strength; the checker re-checks it."""
    if not allow("run"):
        return
    try:
        pl.rewrite_flagged(llm(), checker(), ss.passages, [r])
    except Exception as e:
        log_error("faithful rewrite", e)
        st.warning(ai_problem(e) or tr("Sorry, that did not work: {0}").format(e))
        return
    if not r.get("rewrite"):
        st.warning(tr("No faithful version could be written for this sentence. Edit or remove it."))
    persist()


def distortion_list(results):
    """The distorted sentences of a text with their type, both levels and the verified faithful version."""
    for r in [r for r in results if r["verdict"] in pl.FLAGGED]:
        with st.container(border=True):
            c1, cp = st.columns([3, 1.7], vertical_alignment="top")
            with c1:
                statement_view(r)
            with cp:
                type_panel(r)


@st.fragment
def fact_check_view(flagged):
    """Runs on its own: switching filters or opening page views does not reload the whole page."""
    res = ss.results
    m = ss.get("models") or {}
    if m.get("checker"):
        same = m.get("writer") == m["checker"]
        st.caption(f"Written by {m.get('writer') or 'the writer model'} · checked by {m['checker']}"
                   + (" (same model: the independent checker was unavailable)" if same else " (independent checker)"))
    ocr_pages = sorted({p.page for p in ss.get("passages", []) if getattr(p, "ocr", False)})
    if ocr_pages:
        st.caption(tr('Pages {0} were scanned images and were read with text recognition (OCR). OCR can misread characters, so check numbers from these pages against the proof image.').format(', '.join(map(str, ocr_pages))))
    only = st.toggle(tr("Show only claims that need attention"), value=flagged)
    for r in res:
        if r["verdict"] == "NOT_A_CLAIM" or (only and r["verdict"] == "SUPPORTED"):
            continue
        with st.container(border=True):
            c1, cp = st.columns([3, 1.7], vertical_alignment="top")
            with c1:
                if r["verdict"] in pl.FLAGGED:
                    statement_view(r)
                else:
                    st.markdown(tr("{0} <span style='color:#5f6b66;font-size:13px'>&nbsp;{1} · {2}</span>").format(badge(r['verdict']), tr(r['part']), r['id']),
                                unsafe_allow_html=True)
                    st.markdown(f"**{html.escape(r['text'])}**")
            with cp:
                type_panel(r)
            for p in r["evidence"]:
                st.markdown(tr("<div class='vp-quote'><b>Paper, p. {0}</b> · {1}</div>").format(p.page, html.escape(p.text[:600])),
                            unsafe_allow_html=True)
            if r["evidence"] and st.toggle(tr("Show on the page"), key=f"pv_{r['id']}"):
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
        w.writerow(["id", "part", "text", "verdict", "distortion_type", "explanation", "faithful_version", "source", "rule_flags"])
        for r in res:
            w.writerow([r["id"], r["part"], r["text"], r["verdict"],
                        distortion_label(r)[0] if r["verdict"] in pl.FLAGGED else "", r.get("explanation", ""),
                        r.get("rewrite", ""), " | ".join(f"p.{p.page} {p.pid}" for p in r["evidence"]),
                        "; ".join(r.get("rule_flags", []))])
        z.writestr("verification_report.csv", "\ufeff" + rows.getvalue())
        if ss.get("video"):
            z.write(ss.video[0], os.path.basename(ss.video[0]))
            z.write(ss.video[1], os.path.basename(ss.video[1]))
    return buf.getvalue()


PENDING_LABELS = {
    "fix_all": "Applying the faithful versions…",
    "fix": "Applying the faithful version…",
    "remove": "Removing and checking again…",
    "write": "Writing a faithful version and checking it…",
    "edits": "Checking…",
    "feedback": "Reworking the content and checking every claim again…",
    "stress": "Writing a hyped version and checking it…",
}


def request(kind, **data):
    """Remember a slow action and reload. It then runs at the top of the page, before anything is drawn, so the
    page never shows the old and the new results at the same time while the work is in progress."""
    ss["pending_action"] = {"kind": kind, **data}
    st.rerun()


def apply_verified(r):
    """Uses a faithful version that already passed the independent re-check, without checking everything again
    (instant, no AI calls). Only for single sentences, so the sentence-by-sentence highlighting stays aligned."""
    new = (r.get("rewrite") or "").strip()
    if not new or r.get("rewrite_verified") is not True:
        return False
    if (r["part"] == "Press release" or r["part"] in POST_OF) and len(pl.split_sentences(new)) != 1:
        return False
    if not edit_claim(r, new):
        return False
    check = r.get("rewrite_check") or {}
    r.update(text=new, verdict="SUPPORTED", types=[], distortions=[], issue_type="", explanation="", rule_flags=[],
             rewrite="", rewrite_verified=None, levels=check.get("levels") or r.get("levels", {}), fixed=True)
    return True


def after_local_edit():
    """What a re-check would refresh, without the AI: images, video and the saved project."""
    make_cards()
    ss.pop("video", None)
    ss.pop("anims", None)
    persist()


def run_pending(slot):
    """Runs a remembered slow action inside a fixed slot at the top of the results, before they are drawn."""
    a = ss.pop("pending_action", None)
    if not a:
        return
    kind = a["kind"]
    r = next((x for x in ss.results if x["id"] == a.get("rid")), None) if a.get("rid") else None
    with slot.container(), st.spinner(tr(PENDING_LABELS[kind])):
        if kind == "fix_all":
            needs_check = False
            for x in [x for x in ss.results if x["verdict"] in pl.FLAGGED and x.get("rewrite")]:
                if not apply_verified(x):
                    edit_claim(x, x["rewrite"].strip())
                    needs_check = True
            safe_recheck() if needs_check else after_local_edit()
        elif kind == "fix" and r is not None:
            if apply_verified(r):
                after_local_edit()
            else:
                edit_claim(r, r["rewrite"].strip())
                safe_recheck()
        elif kind == "remove" and r is not None:
            edit_claim(r, "")
            safe_recheck()
        elif kind == "write" and r is not None:
            write_faithful(r)
        elif kind == "edits":
            ss.release = a["text"]
            safe_recheck()
        elif kind == "feedback":
            try:
                apply_feedback(a["fb"], a["target"])
            except Exception as e:
                log_error("rework content", e)
                friendly = ai_problem(e, "send the feedback again")
                ss.chat.append({"role": "assistant", "content": friendly or tr("Sorry, that did not work: {0}").format(e)})
            persist()
        elif kind == "stress":
            try:
                engine = llm()
                hyped = pl.hype_version(engine, ss.release)
                chk = checker()
                hres = pl.verify(chk, ss.passages, pl.build_items("", hyped, [], {}))
                try:
                    pl.rewrite_flagged(engine, chk, ss.passages, hres)
                except Exception as e:
                    log_error("faithful rewrite", e)
                ss.hype = (hyped, hres)
            except Exception as e:
                log_error("stress test", e)
                st.warning(ai_problem(e) or tr("Sorry, that did not work: {0}").format(e))


def results_view():
    run_pending(st.empty())                    # the slot exists on every run, so the layout never shifts
    if not all(k in ss.get("cards", {}) for k in ("square", "wide", "portrait")):
        make_cards()
    res, content = ss.results, ss.content
    ok, total = pl.score(res)
    cnt = {}
    for r in res:
        cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
    flagged = sum(cnt.get(k, 0) for k in pl.FLAGGED)

    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    st.markdown(tr('\n<div class="vp-kpis">\n  <div class="vp-kpi vp-score">{0}<div><div class="l">Claims supported by the paper</div>\n     <div class="v">{1} / {2}</div></div></div>\n  <div class="vp-kpi"><div class="l">Need attention</div><div class="v" style="color:{3}">{4}</div></div>\n  <div class="vp-kpi"><div class="l">Source passages checked</div><div class="v">{5}</div></div>\n  <div class="vp-kpi"><div class="l">Figures found in paper</div><div class="v">{6}</div></div>\n</div>').format(donut(ok, total), ok, total, '#b4442c' if flagged else '#3d7a62', flagged, len(ss.passages), len(ss.figures)), unsafe_allow_html=True)

    if ss.get("recheck_error"):
        err = ss.pop("recheck_error")
        friendly = ai_problem(RuntimeError(err), "use **Edit and re-check**")
        st.warning(tr("The change was applied, but the re-check could not run.") + " " + (friendly or tr("Re-check failed: {0}").format(err)))
    t_rel, t_soc, t_vid, t_ref, t_chk, t_str, t_exp = st.tabs(
        [tr("Press release"), tr("Social posts"), tr("Video"), tr("Refine with feedback"), tr("Fact check"), tr("Stress test"), tr("Approve & export")])

    with t_ref:
        refine_view()

    with t_rel:
        attention_view()
        with st.container(border=True):
            st.markdown(f"<div class='vp-sub'>{html.escape(content.institution)}</div>"
                        f"<div class='vp-headline'>{html.escape(content.headline)}</div>"
                        f"<div class='vp-sub'>{html.escape(content.subheadline)}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='vp-release' lang='{'hu' if ss.get('lang') == 'Hungarian' else 'en'}'>"
                        f"{paragraphs_html(res, 'Press release', ss.release)}</div>",
                        unsafe_allow_html=True)
            st.markdown(tr("""<div class="vp-legend"><span><i class="vp-line" style="border-bottom:2px solid #3d7a62"></i>Supported</span>
<span><i class="vp-line" style="border-bottom:4px double #c98a12"></i>Exaggerated</span><span><i class="vp-line" style="border-bottom:3px solid #b4442c"></i>Unsupported</span>
<span><i class="vp-line" style="border-bottom:2px dashed #a07d1c"></i>Needs review</span><span>Hover a sentence, or pick it in “See the proof”, to see why.</span></div>"""),
                        unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown(tr("**See the proof**  \n<span style='color:#5f6b66;font-size:14px'>Pick any sentence to see where it comes from in the paper.</span>"),
                        unsafe_allow_html=True)
            claims = [r for r in res if r["verdict"] != "NOT_A_CLAIM"]
            if claims:
                pick = st.selectbox(tr("Sentence"), range(len(claims)), label_visibility="collapsed",
                                    format_func=lambda i: f"{claims[i]['verdict'].replace('_', ' ').title()} · {claims[i]['part']} · {claims[i]['text'][:95]}")
                r = claims[pick]
                st.markdown(tr('{0} &nbsp;**{1}**').format(badge(r['verdict']), html.escape(r['text'])), unsafe_allow_html=True)
                if r.get("explanation"):
                    st.caption(r["explanation"])
                proof_viewer(r, "rel")
        with st.expander(tr("Edit and re-check")):
            edited = st.text_area(tr("Press release"), ss.release, height=340, label_visibility="collapsed")
            if st.button(tr("Re-check my edits")):
                request("edits", text=edited)

    with t_soc:
        cols = st.columns(2, gap="medium")
        nets = [("linkedin", "LinkedIn", "wide"), ("facebook", "Facebook", "wide"),
                ("instagram", "Instagram", "portrait"), ("x", "X", "wide")]
        words = [w for w in content.institution.replace(",", " ").split() if w[:1].isupper() and w.lower() not in ("of", "the", "and")]
        initials = "".join(w[0] for w in words[:2]).upper() or "CF"
        chans = ss.get("channels", {})
        if not any(chans.values()):
            st.info(tr("Tip: connect your accounts under **Channels** so each post is addressed to the right page."))
        for k, (key, label, card) in enumerate(nets):
            text = ss.posts[key]
            bad = [r for r in res if r["part"] == label and r["verdict"] in pl.FLAGGED]
            state = "SUPPORTED" if not bad else ("EXAGGERATED" if any(r["verdict"] in ("EXAGGERATED", "UNSUPPORTED") for r in bad) else "NEEDS_REVIEW")
            with cols[k % 2]:
                with st.container(border=True):
                    st.markdown(f"""<div class="vp-post-h"><div class="vp-avatar">{html.escape(initials)}</div>
<div><div class="vp-post-name">{html.escape(chans.get(key) or content.institution)}</div><div class="vp-post-meta">{label} · {tr('connected') if chans.get(key) else tr('draft')}</div></div>
<div style="margin-left:auto">{badge(state)}</div></div>
<div class="vp-post-text">{html.escape(text)}</div>""", unsafe_allow_html=True)
                    st.image(card_bytes(card)[0], width="stretch")
                    for r in bad:
                        st.caption(f"⚠ {r['text']} — {r.get('explanation', '')}")
                    with_image = key in ("instagram", "facebook", "linkedin")
                    png, jpg64 = card_bytes(card)
                    share_button(key, tr("Publish on {0}").format(label), text, share_url(key, text, chans),
                                 jpg64 if with_image else "", f"citeflow_{key}.jpg")
                    st.caption(tr(SHARE_HELP[key]))
                    b2, b3 = st.columns(2)
                    b2.download_button(tr("Image"), card_bytes(card)[0], f"citeflow_{key}.png", "image/png",
                                       width="stretch", key=f"img_{key}")
                    with b3.popover(tr("Copy text"), width="stretch"):
                        st.code(text, language=None, wrap_lines=True)
                    extras_view(key, card)

        with st.container(border=True):
            st.markdown(tr("<div class='vp-post-h'><div class='vp-avatar'>B</div><div><div class='vp-post-name'>{0}</div><div class='vp-post-meta'>Web article · ready to paste</div></div></div>").format(html.escape(chans.get('blog') or 'Blog / news site')),
                        unsafe_allow_html=True)
            article = (f"<h1>{html.escape(content.headline)}</h1>\n<p><em>{html.escape(content.subheadline)}</em></p>\n" +
                       "\n".join(f"<p>{html.escape(p.strip())}</p>" for p in ss.release.split("\n\n") if p.strip()))
            bb1, bb2 = st.columns(2)
            bb1.download_button(tr("Download article (HTML)"), article, "citeflow_article.html", "text/html", width="stretch")
            with bb2:
                if str(chans.get("blog", "")).startswith("http"):
                    plain = f"{content.headline}\n\n{content.subheadline}\n\n{ss.release}"
                    share_button("blog", tr("Publish on blog"), plain, chans["blog"])
                else:
                    st.caption(tr("Connect your blog under **Channels** to publish it in one click."))

    with t_vid:
        scenes = pl.safe_scenes(content.scenes, res, ss.figures)
        vc1, vc2 = st.columns([1, 1.4], gap="large")
        with vc1, st.container(border=True):
            st.markdown(f"**{html.escape(content.video_title)}**")
            st.caption(tr("Only verified lines are spoken. Flagged lines are replaced with their faithful version."))
            for i, s in enumerate(scenes, 1):
                icon = "✓" if s["status"] == "verified" else "↺"
                extra = " · paper figure" if s.get("figure") is not None else ""
                st.markdown(tr("<div style='font-size:14px;margin:6px 0'><b>{0} Scene {1}</b> — {2}<span style='color:#5f6b66'>{3}</span></div>").format(icon, i, html.escape(s['narration']), extra), unsafe_allow_html=True)
            fmt = choose(st.radio, tr("Format"), list(vid.FORMATS), horizontal=True)
            if st.button(tr("Render video"), type="primary", width="stretch", disabled=not scenes) and allow("run"):
                bar = st.progress(0.0, "Recording voice-over and finding footage…")
                try:
                    ss.video = vid.render_video(
                        content.video_title, content.institution, content.card_title, scenes, ss.figures,
                        ss.lang, tempfile.mkdtemp(), fmt=fmt, pexels_key=PEXELS_KEY, photo=ss.photo,
                        person_name=ss.person, progress=lambda x: bar.progress(x, "Rendering video…"))
                    bar.empty()
                except Exception as e:
                    log_error("render video", e)
                    st.error(tr('Video rendering failed: {0}').format(e))
        with vc2:
            if "video" in ss:
                mp4, srt, narrated, stock = ss.video
                st.video(mp4)
                if not narrated:
                    st.warning(tr("The voice-over service could not be reached, so this video is silent."))
                with open(mp4, "rb") as fh:
                    st.download_button(tr("Download video (MP4)"), fh.read(), os.path.basename(mp4), "video/mp4",
                                       width="stretch")
            else:
                st.markdown(tr("<div class='cf-card' style='text-align:center;color:#5f6b66;padding:80px 20px'>"
                            "Your video preview appears here.</div>"), unsafe_allow_html=True)

    with t_chk:
        fact_check_view(bool(flagged))

    with t_str:
        with st.container(border=True):
            st.markdown(tr("**Does the checker really catch hype?**"))
            st.caption(tr("CiteFlow writes an over-enthusiastic version of the release on purpose, then checks it with the same verifier."))
            if st.button(tr("Run stress test")) and allow("run"):
                request("stress")
            if "hype" in ss:
                hyped, hres = ss.hype
                hok, htot = pl.score(hres)
                st.markdown(tr("<div class='vp-kpis' style='grid-template-columns:1fr 1fr'><div class='vp-kpi vp-score'>{0}<div><div class='l'>Original release</div><div class='v'>{1} / {2}</div></div></div><div class='vp-kpi vp-score'>{3}<div><div class='l'>Hyped version</div><div class='v'>{4} / {5}</div></div></div></div>").format(donut(ok, total), ok, total, donut(hok, htot), hok, htot),
                            unsafe_allow_html=True)
                st.markdown(f"<div class='vp-release'>{paragraphs_html(hres, 'Press release', hyped)}</div>",
                            unsafe_allow_html=True)
                if any(r["verdict"] in pl.FLAGGED for r in hres):
                    st.markdown(tr("**What the checker found in the hyped version, and the faithful fix**"))
                    distortion_list(hres)

    with t_exp:
        with st.container(border=True):
            st.markdown(tr("**Human approval**"))
            st.caption(tr("CiteFlow drafts and verifies. A person approves before anything is published."))
            if flagged:
                st.warning(tr('{0} claim(s) still need attention. Fix them in the press release, or confirm below that you accept them.').format(flagged))
            a1 = st.checkbox(tr("I reviewed every flagged claim and its source passage"))
            a2 = st.checkbox(tr("Placeholder quotes will be replaced with real, approved quotes from the researchers"))
            if a1 and a2:
                persist()
                sig = (ss.get("last_saved"), (ss.get("video") or [None])[0])
                if ss.get("pkg", (None,))[0] != sig:
                    ss.pkg = (sig, build_package(content, res))
                st.download_button(tr("Download approved package"), ss.pkg[1], "citeflow_package.zip",
                                   "application/zip", type="primary", width="stretch")

    persist()
