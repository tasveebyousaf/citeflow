"""CiteFlow pipeline: source document -> PR content -> claim-by-claim verification."""
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pymupdf
from PIL import Image
from pydantic import BaseModel

# ---------------------------------------------------------------- source text

@dataclass
class Passage:
    pid: str
    page: int
    text: str
    rects: list = None   # PDF coordinates of the text blocks (for highlighting)


@dataclass
class Figure:
    page: int
    image: Image.Image


def extract_passages(pdf_bytes: bytes, max_chars: int = 700) -> tuple[list[Passage], str]:
    """Split a PDF into numbered passages (id = P<page>-<n>). Drops the reference list."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    title = (doc.metadata or {}).get("title") or ""
    passages: list[Passage] = []
    stop = False
    for pno, page in enumerate(doc, start=1):
        if stop:
            break
        blocks = [b for b in page.get_text("blocks") if b[6] == 0]
        n, buf, rects = 0, "", []
        for b in blocks:
            t = re.sub(r"\s+", " ", b[4]).strip()
            if not t:
                continue
            if re.fullmatch(r"(references|bibliography|irodalomjegyzék)", t.lower()):
                stop = True
                break
            if len(buf) + len(t) > max_chars and buf:
                n += 1
                passages.append(Passage(f"P{pno}-{n}", pno, buf.strip(), rects))
                buf, rects = "", []
            buf += " " + t
            rects.append(tuple(b[:4]))
        if buf.strip():
            n += 1
            passages.append(Passage(f"P{pno}-{n}", pno, buf.strip(), rects))
    return passages, title


def extract_figures(pdf_bytes: bytes, limit: int = 12) -> list[Figure]:
    """Real figures from the paper (raster images of reasonable size; banners/logos skipped)."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    figs, seen = [], set()
    for pno, page in enumerate(doc, start=1):
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                pix = pymupdf.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
            except Exception:
                continue
            w, h = pix.width, pix.height
            if w < 400 or h < 250 or w / h > 3.2 or h / w > 2.2:
                continue
            try:
                im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            except Exception:
                continue
            figs.append(Figure(pno, im))
            if len(figs) >= limit:
                return figs
    return figs


def proof_image(pdf_bytes: bytes, passage: Passage, zoom: float = 2.0, crop: bool = True,
                claim: str = "") -> Image.Image:
    """Render the passage's page with the supporting text highlighted like a marker pen."""
    from PIL import ImageDraw
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[passage.page - 1]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rects = passage.rects or []
    for x0, y0, x1, y1 in rects:
        d.rounded_rectangle([x0 * zoom - 4, y0 * zoom - 3, x1 * zoom + 4, y1 * zoom + 3], 6, fill=(255, 221, 0, 95))
        d.rectangle([x0 * zoom - 14, y0 * zoom - 3, x0 * zoom - 9, y1 * zoom + 3], fill=(255, 122, 69, 255))
    # outline the claim's exact numbers where they appear inside the passage
    if claim and rects:
        area = pymupdf.Rect(min(r[0] for r in rects), min(r[1] for r in rects),
                            max(r[2] for r in rects), max(r[3] for r in rects))
        for num in {n for n in NUM_RE.findall(re.sub(r"#\w+", "", claim))}:
            for variant in {num, num.replace(",", "."), num.replace(".", ",")}:
                for hit in page.search_for(variant, clip=area):
                    d.rounded_rectangle([hit.x0 * zoom - 5, hit.y0 * zoom - 4, hit.x1 * zoom + 5, hit.y1 * zoom + 4],
                                        5, outline=(240, 68, 56, 255), width=4)
    img = Image.alpha_composite(img, layer).convert("RGB")
    if crop and rects:
        top = max(0, int(min(r[1] for r in rects) * zoom) - 160)
        bottom = min(img.height, int(max(r[3] for r in rects) * zoom) + 160)
        img = img.crop((0, top, img.width, bottom))
    return img


def passages_block(passages: list[Passage], limit_chars: int = 150_000) -> str:
    out, total = [], 0
    for p in passages:
        line = f"[{p.pid}] {p.text}"
        total += len(line)
        if total > limit_chars:
            break
        out.append(line)
    return "\n".join(out)

# ---------------------------------------------------------------- LLM schemas

class Scene(BaseModel):
    on_screen_text: str
    narration: str
    wants_figure: bool
    stock_query: str


class Posts(BaseModel):
    linkedin: str
    facebook: str
    instagram: str
    x: str


class Content(BaseModel):
    headline: str
    subheadline: str
    institution: str
    card_title: str
    press_release: str
    posts: Posts
    video_title: str
    scenes: list[Scene]


class Verdict(BaseModel):
    item_id: str
    verdict: str
    evidence_ids: list[str]
    issue_type: str
    explanation: str
    suggested_rewrite: str


class Report(BaseModel):
    items: list[Verdict]


class Hyped(BaseModel):
    press_release: str

# ---------------------------------------------------------------- prompts

GEN_PROMPT = """You are the best science writer in a university communications office. You write for real people,
not for other scientists: warm, concrete, curious, never corporate. Base everything ONLY on the source document below.
Output language: {lang}.

Accuracy rules (non-negotiable):
- Every factual statement must be supported by the source. Never add facts, numbers, dates, funding or impact that are not in it.
- Keep claim strength equal to the source: no causal claims from correlations, no simulation/lab/small-sample results
  presented as real-world or clinical, no "assists experts" turned into "replaces experts". Keep the key limitation.
- Never invent quotes. Where a quote belongs, write exactly: [Quote from the researchers to be added after approval]

Style:
- Open with a human hook: the everyday problem, who it affects, why it matters. Then what the team did, what they found,
  what it could mean, and what comes next. Short sentences. Explain jargon in plain words.
- headline: max 12 words, specific and intriguing, no hype words ("breakthrough", "revolutionary").
- institution: the lead institution and faculty as named in the source (short).
- card_title: max 7 words for an image card.
- press_release: 300-420 words, plain paragraphs separated by blank lines, no markdown.
- posts.linkedin: 80-140 words, professional but personal, line breaks, 3 hashtags at the end.
- posts.facebook: 50-90 words, friendly, a question to the reader, 2 hashtags.
- posts.instagram: 40-80 words, vivid, emoji allowed (max 3), 4-6 hashtags at the end.
- posts.x: max 260 characters including 1-2 hashtags.
- scenes: 5-6 scenes for a 45-60 second video, total narration max 120 words, spoken style.
  Scene 1 is the hook. Last scene says what comes next. on_screen_text: max 7 words, punchy.
  wants_figure: true if a figure/diagram from the paper would illustrate the scene well.
  stock_query: 2-4 ENGLISH words describing concrete, filmable background footage for the scene
  (e.g. "microscope laboratory", "drone flying forest", "solar panels sunset"). No brand names, no specific real people,
  no computer screens, code, text or charts (they look fake as stock footage).

SOURCE DOCUMENT (numbered passages):
{source}
"""

VERIFY_PROMPT = """You are an independent, strict fact-checker for a university press office.
You did not write the text below. Check every item against the numbered source passages.

For each item return:
- item_id: copy exactly.
- verdict: one of SUPPORTED, EXAGGERATED, UNSUPPORTED, NOT_A_CLAIM
  SUPPORTED   = the source states this with the same strength.
  EXAGGERATED = the source supports only a weaker or narrower version: causal language for non-causal results; simulation, lab or small-sample results presented as real-world or general; "assists" turned into "replaces/detects/cures"; certainty inflated ("proves", "breakthrough", "first"); important limitation removed; numbers rounded upward or misattributed.
  UNSUPPORTED = not found in the source, or contradicts it.
  NOT_A_CLAIM = no checkable factual content (hooks that only pose a question, greetings, placeholders, calls to action, hashtags, contact info).
- evidence_ids: up to 3 passage ids (like P3-2). Empty only if nothing relevant exists.
- issue_type: for EXAGGERATED/UNSUPPORTED a short label ("overgeneralisation", "causal overclaim", "removed limitation", "number mismatch", "not in source", "certainty inflation"); otherwise "".
- explanation: one sentence citing what the source actually says.
- suggested_rewrite: for EXAGGERATED/UNSUPPORTED a faithful replacement in the same language and tone (or "" if it should be deleted); otherwise "".

Be strict: when in doubt between SUPPORTED and EXAGGERATED, choose EXAGGERATED. In particular:
- Words of certainty ("proved", "proves", "shows for certain", "bizonyították", "bizonyítja") for empirical results are EXAGGERATED unless the source uses equally strong wording.
- A result attributed to the wrong method, group or subset (e.g. a range that applies to all methods credited to one method) is EXAGGERATED.
- Rounded or widened numbers ("11-15%" for "11.1-14.7%", "up to" added) are EXAGGERATED.
- Write explanation in the same language as the item.

ITEMS TO CHECK:
{items}

SOURCE DOCUMENT (numbered passages):
{source}
"""

HYPE_PROMPT = """Rewrite the press release below the way an over-enthusiastic PR writer might: add typical hype
(e.g. "breakthrough", causal claims, real-world or clinical impact, removed limitations, rounded-up numbers).
Keep the same language and similar length. This is used to test an automatic fact-checker.

PRESS RELEASE:
{text}
"""

# ---------------------------------------------------------------- LLM client

TRANSIENT = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL", "overloaded", "high demand",
             "timed out", "Timeout", "Connection")


class LLM:
    """Gemini client: low thinking for speed, retries on temporary errors, fallback to other models."""

    def __init__(self, api_key: str, model: str, fallbacks: list[str] | None = None):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.models = [model] + [m for m in (fallbacks or []) if m != model]
        self.model = model
        self.fast = True
        self.log: list[str] = []

    def _config(self, model: str, schema, temperature: float):
        from google.genai import types
        kw = dict(response_mime_type="application/json", response_schema=schema, temperature=temperature)
        if self.fast:
            m = re.search(r"gemini-(\d+)", model)
            major = int(m.group(1)) if m else 0
            if major >= 3:
                kw["thinking_config"] = types.ThinkingConfig(thinking_level="low")
            elif "2.5-flash" in model:
                kw["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(**kw)

    def _once(self, model: str, prompt: str, schema, temperature: float):
        resp = self.client.models.generate_content(
            model=model, contents=prompt, config=self._config(model, schema, temperature))
        if getattr(resp, "parsed", None) is not None:
            return resp.parsed
        return schema.model_validate(json.loads(resp.text))

    def json_call(self, prompt: str, schema, temperature: float = 0.2, fast: bool = True):
        self.fast = fast
        last = None
        for model in self.models:
            attempt = 0
            while attempt < 3:
                try:
                    out = self._once(model, prompt, schema, temperature)
                    self.model = model
                    return out
                except Exception as e:
                    last, msg = e, str(e)
                    if self.fast and "thinking" in msg.lower():
                        self.fast = False          # model does not accept the speed setting
                        continue
                    if "404" in msg or "NOT_FOUND" in msg:
                        self.log.append(f"{model}: not available")
                        break
                    if not any(t in msg for t in TRANSIENT):
                        raise
                    attempt += 1
                    self.log.append(f"{model}: busy, retry {attempt}")
                    time.sleep(2 * attempt)
        raise last


def rank_model(name: str):
    """Newest version first; prefer stable, non-lite models."""
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    ver = float(m.group(1)) if m else 0.0
    return (ver, "lite" not in name, "preview" not in name and "exp" not in name, -len(name))


def list_flash_models(api_key: str) -> list[str]:
    from google import genai
    client = genai.Client(api_key=api_key)
    names = []
    for m in client.models.list():
        name = m.name.replace("models/", "")
        actions = getattr(m, "supported_actions", None) or []
        if "generateContent" in actions and "gemini" in name and "flash" in name \
                and not any(x in name for x in ("tts", "image", "live", "audio", "embedding", "robotics")):
            names.append(name)
    return sorted(set(names), key=rank_model, reverse=True)

# ---------------------------------------------------------------- steps

def generate_content(llm: LLM, passages, lang: str) -> Content:
    return llm.json_call(GEN_PROMPT.format(lang=lang, source=passages_block(passages)), Content, 0.7)


def hype_version(llm: LLM, text: str) -> str:
    return llm.json_call(HYPE_PROMPT.format(text=text), Hyped, 0.9).press_release


def split_sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n\s*\n|\n(?=\S)", text.strip()):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÖŐÚÜŰ\"'„(\[])", para)
        out.extend(p.strip() for p in parts if p.strip())
    return out


POST_KEYS = [("linkedin", "L", "LinkedIn"), ("facebook", "F", "Facebook"), ("instagram", "I", "Instagram"), ("x", "X", "X")]


def build_items(headline: str, press_release: str, scenes, posts: dict | None = None) -> list[dict]:
    items = []
    if headline.strip():
        items.append({"id": "H1", "part": "Headline", "text": headline.strip()})
    for i, s in enumerate(split_sentences(press_release), 1):
        items.append({"id": f"R{i}", "part": "Press release", "text": s})
    for key, prefix, label in POST_KEYS:
        for i, s in enumerate(split_sentences((posts or {}).get(key, "")), 1):
            items.append({"id": f"{prefix}{i}", "part": label, "text": s})
    for i, sc in enumerate(scenes or [], 1):
        items.append({"id": f"V{i}", "part": "Video", "text": sc.narration.strip()})
    return items


NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?!\d)|\d+(?:[.,]\d+)?")


def _norm_num(s: str) -> str:
    if re.fullmatch(r"\d{1,3}(,\d{3})+", s):  # 1,000 -> 1000 (thousands separator)
        s = s.replace(",", "")
    s = s.replace(",", ".")
    return s.rstrip("0").rstrip(".") if "." in s else s


def number_check(claim: str, evidence_text: str) -> list[str]:
    """Deterministic rule: every number in a claim must appear in the cited evidence."""
    claim = re.sub(r"#\w+", "", claim)
    ev = {_norm_num(n) for n in NUM_RE.findall(evidence_text)}
    return [n for n in NUM_RE.findall(claim) if _norm_num(n) not in ev]


def _verify_chunk(llm: LLM, source: str, items: list[dict]):
    item_block = "\n".join(f"[{it['id']}] {it['text']}" for it in items)
    return llm.json_call(VERIFY_PROMPT.format(items=item_block, source=source), Report, 0.0, fast=False).items


def verify(llm: LLM, passages, items: list[dict]) -> list[dict]:
    """Checks items in parallel groups (release / posts / video) for speed."""
    source = passages_block(passages)
    groups: dict[str, list[dict]] = {}
    for it in items:
        g = "release" if it["id"][0] in "HR" else "video" if it["id"][0] == "V" else "posts"
        groups.setdefault(g, []).append(it)
    verdicts = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for part in ex.map(lambda g: _verify_chunk(llm, source, g), groups.values()):
            verdicts.extend(part)
    by_id = {v.item_id.strip("[] "): v for v in verdicts}
    pmap = {p.pid: p for p in passages}
    results = []
    for it in items:
        v = by_id.get(it["id"])
        r = dict(it)
        if v is None:
            r.update(verdict="UNCHECKED", evidence=[], issue_type="not checked",
                     explanation="The checker returned no verdict for this item.", rewrite="", rule_flags=[])
            results.append(r)
            continue
        verdict = v.verdict.strip().upper().replace(" ", "_")
        if verdict not in {"SUPPORTED", "EXAGGERATED", "UNSUPPORTED", "NOT_A_CLAIM"}:
            verdict = "UNCHECKED"
        ev = [pmap[e.strip("[] ")] for e in v.evidence_ids if e.strip("[] ") in pmap]
        flags = []
        # Rule layer: the AI proposes, deterministic rules double-check.
        if verdict == "SUPPORTED" and not ev:
            flags.append("no valid source passage cited")
        if verdict == "SUPPORTED" and ev:
            missing = number_check(it["text"], " ".join(p.text for p in ev))
            if missing:
                flags.append("number(s) not found in cited passages: " + ", ".join(missing))
        if flags and verdict == "SUPPORTED":
            verdict = "NEEDS_REVIEW"
        r.update(verdict=verdict, evidence=ev, issue_type=v.issue_type, explanation=v.explanation,
                 rewrite=v.suggested_rewrite, rule_flags=flags)
        results.append(r)
    return results


FLAGGED = {"EXAGGERATED", "UNSUPPORTED", "NEEDS_REVIEW", "UNCHECKED"}


def score(results: list[dict]) -> tuple[int, int]:
    claims = [r for r in results if r["verdict"] != "NOT_A_CLAIM"]
    return len([r for r in claims if r["verdict"] == "SUPPORTED"]), len(claims)


def safe_scenes(scenes, results, figures: list[Figure]) -> list[dict]:
    """Narration used in the video: verified text; flagged text replaced by its faithful rewrite, else dropped.
    Each scene gets the paper figure closest to the page its evidence comes from."""
    by_id = {r["id"]: r for r in results}
    used, out = set(), []
    for i, sc in enumerate(scenes, 1):
        r = by_id.get(f"V{i}")
        narr, status = sc.narration.strip(), "verified"
        if r and r["verdict"] in FLAGGED:
            if r.get("rewrite"):
                narr, status = r["rewrite"].strip(), "corrected"
            else:
                continue
        screen = sc.on_screen_text.strip()
        if status == "corrected":
            words = narr.split()
            screen = " ".join(words[:7]) + ("…" if len(words) > 7 else "")
        pages = sorted({p.page for p in (r["evidence"] if r else [])})
        fig = None
        if sc.wants_figure and figures:
            target = pages[0] if pages else 1
            cands = sorted((abs(f.page - target), k) for k, f in enumerate(figures) if k not in used)
            if cands:
                fig = cands[0][1]
                used.add(fig)
        out.append({"screen": screen, "narration": narr, "status": status, "pages": pages, "figure": fig,
                    "stock_query": sc.stock_query})
    return out
