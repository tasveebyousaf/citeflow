"""Explainer-style video: stock b-roll + paper figures + word-synced kinetic captions + voice-over.
Also renders social image cards."""
import asyncio
import glob
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FPS = 24
NAVY, INDIGO = (11, 22, 51), (36, 30, 92)
ACCENT, WHITE, MUTED = (255, 138, 92), (255, 255, 255), (190, 198, 220)
VOICES = {"English": ["en-US-AvaMultilingualNeural", "en-GB-SoniaNeural"],
          "Hungarian": ["hu-HU-NoemiNeural", "hu-HU-TamasNeural"]}
FORMATS = {"Landscape 16:9": (1280, 720), "Vertical 9:16": (720, 1280)}
TEXT = {
    "English": {"verified": "VERIFIED", "source": "SOURCE P.", "read": "Read the full study",
                "stock": "Illustrative footage · Pexels", "card": "Every claim checked against the published paper"},
    "Hungarian": {"verified": "ELLENŐRIZVE", "source": "FORRÁS: ", "read": "Olvasd el a teljes tanulmányt",
                  "stock": "Illusztráció · Pexels", "card": "Minden állítás ellenőrizve a publikált tanulmány alapján"},
}


def tx(lang, key):
    return TEXT.get(lang, TEXT["English"])[key]


def fit(draw, text, fnt, max_w):
    """Shorten text with an ellipsis so it fits on one line."""
    if draw.textlength(text, font=fnt) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=fnt) > max_w:
        text = text[:-1]
    return text.rstrip(" ,;") + "…"


def check_icon(draw, x, y, size, color):
    """Draw a check mark (fonts on some systems lack the ✓ glyph)."""
    s = size
    draw.line([(x, y + s * 0.55), (x + s * 0.38, y + s * 0.9), (x + s, y + s * 0.1)], fill=color, width=max(2, int(s / 5)))

# ------------------------------------------------------------------ fonts & helpers

_FC = {}


def font(size, bold=False):
    key = (size, bold)
    if key in _FC:
        return _FC[key]
    cands = (["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"] if bold else
             ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"])
    cands += sorted(glob.glob("/usr/share/fonts/**/Inter*%s*.[ot]tf" % ("Bold" if bold else "Regular"), recursive=True))
    cands += ["/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""), "/Library/Fonts/Arial.ttf"]
    f = None
    for c in cands:
        try:
            f = ImageFont.truetype(c, size)
            break
        except OSError:
            continue
    _FC[key] = f or ImageFont.load_default(size=size)
    return _FC[key]


def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def gradient(w, h, top=NAVY, bottom=INDIGO):
    t = np.linspace(0, 1, h)[:, None, None]
    arr = (np.array(top) * (1 - t) + np.array(bottom) * t).astype(np.uint8)
    return Image.fromarray(np.repeat(arr, w, axis=1), "RGB")


def blobs(w, h, t, seed=0):
    layer = Image.new("RGB", (w // 4, h // 4))
    d = ImageDraw.Draw(layer)
    rng = np.random.default_rng(seed)
    for k, col in enumerate([(255, 138, 92), (110, 92, 255), (64, 180, 255)]):
        cx = (rng.uniform(0, w // 4) + 20 * np.sin(t * 0.5 + k)) % (w // 4)
        cy = rng.uniform(0, h // 4) + 14 * np.cos(t * 0.4 + 2 * k)
        r = rng.uniform(0.18, 0.3) * min(w, h) / 4
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=tuple(int(c * 0.4) for c in col))
    return Image.blend(gradient(w, h), layer.filter(ImageFilter.GaussianBlur(20)).resize((w, h)), 0.6)


def cover(img, w, h, zoom=1.0):
    """Scale-to-fill crop with a zoom factor (for camera push-ins)."""
    s = max(w / img.width, h / img.height) * zoom
    nw, nh = int(img.width * s) + 1, int(img.height * s) + 1
    im = img.resize((nw, nh), Image.BILINEAR)
    x, y = (nw - w) // 2, (nh - h) // 2
    return im.crop((x, y, x + w, y + h))


def shade(w, h):
    """Dark vignette so text stays readable on footage."""
    a = np.zeros((h, w), np.float32)
    yy = np.linspace(0, 1, h)[:, None]
    a += 0.35 + 0.45 * (yy ** 1.6) + 0.25 * ((1 - yy) ** 3)
    a = np.clip(a, 0, 0.88)
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., :3] = (8, 12, 30)
    rgba[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(np.repeat(rgba, 1, axis=1), "RGBA")


def figure_card(fig, max_w, max_h):
    im = fig.copy()
    im.thumbnail((max_w - 28, max_h - 28))
    card = Image.new("RGB", (im.width + 28, im.height + 28), WHITE)
    card.paste(im, (14, 14))
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, card.width, card.height], 18, fill=255)
    sh = Image.new("L", (card.width + 80, card.height + 80), 0)
    ImageDraw.Draw(sh).rounded_rectangle([40, 40, 40 + card.width, 40 + card.height], 18, fill=170)
    return card, mask, sh.filter(ImageFilter.GaussianBlur(18))

# ------------------------------------------------------------------ stock footage (Pexels)


def pexels_clip(query, api_key, orientation, workdir, idx, min_side=720):
    import requests
    r = requests.get("https://api.pexels.com/videos/search",
                     params={"query": query, "orientation": orientation, "per_page": 6, "size": "medium"},
                     headers={"Authorization": api_key}, timeout=15)
    r.raise_for_status()
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
        files = [f for f in files if min(f["width"], f["height"]) >= min_side] or files
        if not files:
            continue
        f = min(files, key=lambda f: f["width"] * f["height"])
        path = os.path.join(workdir, f"broll{idx}.mp4")
        with requests.get(f["link"], stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(path, "wb") as fh:
                for chunk in resp.iter_content(1 << 16):
                    fh.write(chunk)
        return path
    return None

# ------------------------------------------------------------------ voice with word timings


async def _speak(text, voice, path):
    import edge_tts
    words = []
    com = edge_tts.Communicate(text, voice, rate="+4%", boundary="WordBoundary")
    with open(path, "wb") as fh:
        async for ch in com.stream():
            if ch["type"] == "audio":
                fh.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
    return words


def speak(text, lang, path):
    for v in VOICES.get(lang, VOICES["English"]):
        try:
            words = asyncio.run(_speak(text, v, path))
            if os.path.getsize(path) > 0:
                return words
        except Exception:
            continue
    return None


def even_timings(text, dur):
    ws = text.split()
    step = dur / max(len(ws), 1)
    return [(i * step, (i + 1) * step, w) for i, w in enumerate(ws)]

# ------------------------------------------------------------------ scene renderer


class Scene:
    def __init__(self, W, H, kind, dur, t0, total, **kw):
        self.W, self.H, self.kind, self.dur, self.t0, self.total = W, H, kind, dur, t0, total
        self.kw = kw
        self.vertical = H > W
        self.shade = shade(W, H)
        self.broll = kw.get("broll")            # moviepy clip or None
        self.words = kw.get("words") or []
        self.groups = self._group(self.words)
        fig = kw.get("figure")
        self.card = None
        if fig is not None:
            mw, mh = (int(W * 0.86), int(H * 0.36)) if self.vertical else (int(W * 0.46), int(H * 0.56))
            self.card, self.card_mask, self.card_sh = figure_card(fig, mw, mh)
        photo = kw.get("photo")
        self.photo = None
        if photo is not None:
            s = min(photo.size)
            p = photo.convert("RGB").crop(((photo.width - s) // 2, (photo.height - s) // 2,
                                           (photo.width + s) // 2, (photo.height + s) // 2)).resize((260, 260))
            m = Image.new("L", (260, 260), 0)
            ImageDraw.Draw(m).ellipse([0, 0, 260, 260], fill=255)
            self.photo, self.photo_mask = p, m

    @staticmethod
    def _group(words, n=5):
        return [words[i:i + n] for i in range(0, len(words), n)]

    def background(self, t):
        W, H = self.W, self.H
        z = 1.04 + 0.10 * (t / max(self.dur, 0.1))
        if self.broll is not None:
            tt = min(t, self.broll.duration - 0.05) if self.broll.duration > t else t % max(self.broll.duration - 0.05, 0.1)
            img = cover(Image.fromarray(self.broll.get_frame(tt)), W, H, z)
        else:
            img = cover(blobs(W, H, self.t0 + t, hash(self.kw.get("seed", 0)) % 1000), W, H, z)
        img = img.convert("RGBA")
        img.alpha_composite(self.shade)
        return img.convert("RGB")

    def frame(self, t):
        W, H = self.W, self.H
        img = self.background(t)
        d = ImageDraw.Draw(img)
        a = ease(t / 0.6)
        m = int(W * 0.07)

        if self.kw.get("broll") is not None:
            lab = tx(self.kw.get("lang", "English"), "stock")
            f = font(16)
            d.text((W - m - d.textlength(lab, font=f), m * 0.6), lab, font=f, fill=(200, 205, 220))

        if self.kind == "title":
            inst = self.kw.get("institution", "").upper()
            f_i = font(22 if not self.vertical else 24, True)
            d.text((m, H * 0.30 - 10 * (1 - a)), fit(d, inst, f_i, W - 2 * m), font=f_i, fill=ACCENT)
            words = self.kw.get("text", "").split()
            f = font(64 if not self.vertical else 70, True)
            lines = wrap(d, " ".join(words), f, W - 2 * m)
            y, k = H * 0.30 + 50, 0
            for ln in lines[:5]:
                x = m
                for w in ln.split():
                    appear = ease((t - 0.25 - 0.09 * k) / 0.35)
                    if appear > 0:
                        d.text((x, y + 24 * (1 - appear)), w, font=f,
                               fill=tuple(int(c * appear + 30 * (1 - appear)) for c in WHITE))
                    x += d.textlength(w + " ", font=f)
                    k += 1
                y += int(f.size * 1.18)
            d.rectangle([m, y + 16, m + int(W * 0.18 * ease((t - 0.8) / 0.7)), y + 22], fill=ACCENT)

        elif self.kind == "person" and self.photo is not None:
            px, py = (W - 260) // 2, int(H * 0.28 - 20 * (1 - a))
            d.ellipse([px - 6, py - 6, px + 266, py + 266], fill=ACCENT)
            img.paste(self.photo, (px, py), self.photo_mask)
            d = ImageDraw.Draw(img)
            for txt, f, col, dy in ((self.kw.get("name", ""), font(44, True), WHITE, 300),
                                    (self.kw.get("institution", ""), font(24), MUTED, 360)):
                txt = fit(d, txt, f, W - 2 * m)
                d.text(((W - d.textlength(txt, font=f)) / 2, py + dy), txt, font=f,
                       fill=tuple(int(c * a) for c in col))

        elif self.kind == "outro":
            f = font(56 if not self.vertical else 60, True)
            lines = wrap(d, self.kw.get("text", ""), f, W - 2 * m)[:3]
            y = H * 0.36
            for ln in lines:
                d.text(((W - d.textlength(ln, font=f)) / 2, y + 20 * (1 - a)), ln, font=f,
                       fill=tuple(int(c * a) for c in WHITE))
                y += int(f.size * 1.2)
            for txt, fz, col in ((tx(self.kw.get("lang", "English"), "read"), font(30, True), ACCENT),
                                 (self.kw.get("institution", ""), font(22), MUTED)):
                txt = fit(d, txt, fz, W - 2 * m)
                y += 30
                d.text(((W - d.textlength(txt, font=fz)) / 2, y), txt, font=fz, fill=tuple(int(c * a) for c in col))
                y += fz.size

        else:  # story scene
            tag, headline = self.kw.get("tag", ""), self.kw.get("text", "")
            hf = font(46 if not self.vertical else 54, True)
            if self.card is not None:
                ca = ease((t - 0.25) / 0.6)
                if self.vertical:
                    cx, cy = (W - self.card.width) // 2, int(H * 0.40 + 40 * (1 - ca))
                    text_w, ty = W - 2 * m, H * 0.16
                else:
                    cx, cy = int(W - m - self.card.width + 40 * (1 - ca)), (H - self.card.height) // 2 - 40
                    text_w, ty = int(W * 0.40), H * 0.22
                if ca > 0:
                    img.paste((0, 0, 0), (cx - 40, cy - 30), self.card_sh)
                    img.paste(self.card, (cx, cy), self.card_mask)
                    d = ImageDraw.Draw(img)
            else:
                text_w, ty = W - 2 * m, H * (0.22 if not self.vertical else 0.30)
            check_icon(d, m, ty - 43, 16, ACCENT)
            d.text((m + 24, ty - 44), tag, font=font(18, True), fill=ACCENT)
            y = ty
            for ln in wrap(d, headline, hf, text_w)[:4]:
                d.text((m - 30 * (1 - a), y), ln, font=hf, fill=tuple(int(c * a) for c in WHITE))
                y += int(hf.size * 1.2)
            self._captions(d, t)

        prog = (self.t0 + t) / self.total
        d.rectangle([0, H - 6, int(W * prog), H], fill=ACCENT)
        return np.array(img)

    def _captions(self, d, t):
        """Word-by-word karaoke captions synced to the voice-over."""
        if not self.groups:
            return
        g = self.groups[-1]
        for grp in self.groups:
            if t < grp[-1][1] + 0.05:
                g = grp
                break
        W, H = self.W, self.H
        f = font(40 if not self.vertical else 46, True)
        lines = wrap(d, " ".join(w[2] for w in g), f, W * 0.84)
        y = H * (0.80 if not self.vertical else 0.78)
        k = 0
        for ln in lines[:2]:
            lw = d.textlength(ln, font=f)
            x = (W - lw) / 2
            d.rounded_rectangle([x - 16, y - 8, x + lw + 16, y + f.size + 14], 12, fill=(0, 0, 0))
            for w in ln.split():
                if k < len(g):
                    s, e, _ = g[k]
                    col = ACCENT if s <= t < e + 0.05 else (WHITE if t >= e else (150, 155, 175))
                else:
                    col = WHITE
                d.text((x, y), w, font=f, fill=col)
                x += d.textlength(w + " ", font=f)
                k += 1
            y += f.size + 26

# ------------------------------------------------------------------ public API


def _srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def render_video(title, institution, outro_text, scenes, figures, lang, workdir, fmt="Landscape 16:9",
                 pexels_key="", photo=None, person_name="", progress=None):
    """scenes: dicts with screen, narration, pages, figure, stock_query. Returns (mp4, srt, narrated, used_stock)."""
    from moviepy import AudioFileClip, VideoClip, VideoFileClip, concatenate_videoclips, vfx

    W, H = FORMATS.get(fmt, FORMATS["Landscape 16:9"])
    orient = "portrait" if H > W else "landscape"
    os.makedirs(workdir, exist_ok=True)
    step = lambda x: progress(min(x, 0.99)) if progress else None

    plan, narrated, used_stock, opened = [], True, False, []

    def broll_for(q, i):
        nonlocal used_stock
        if not (pexels_key and q):
            return None
        try:
            p = pexels_clip(q, pexels_key, orient, workdir, i)
            if p:
                c = VideoFileClip(p, audio=False)
                opened.append(c)
                used_stock = True
                return c
        except Exception:
            return None
        return None

    first_q = scenes[0].get("stock_query") if scenes else ""
    plan.append(dict(kind="title", dur=3.4, text=title, broll=broll_for(first_q, 0)))
    if photo is not None and person_name.strip():
        plan.append(dict(kind="person", dur=3.0, photo=photo, name=person_name))
    for i, sc in enumerate(scenes, 1):
        path = os.path.join(workdir, f"voice{i}.mp3")
        words = speak(sc["narration"], lang, path)
        if words is not None and os.path.exists(path):
            audio = AudioFileClip(path)
            dur = audio.duration + 0.45
            words = words or even_timings(sc["narration"], audio.duration)
        else:
            narrated, audio = False, None
            dur = max(3.5, len(sc["narration"].split()) / 2.5)
            words = even_timings(sc["narration"], dur - 0.4)
        pages = ", ".join(str(p) for p in sc.get("pages", [])[:2])
        tag = f"{tx(lang, 'verified')} · {tx(lang, 'source')}{pages}" if pages else tx(lang, "verified")
        fig = figures[sc["figure"]].image if sc.get("figure") is not None else None
        plan.append(dict(kind="story", dur=dur, text=sc["screen"], tag=tag, figure=fig, words=words,
                         audio=audio, broll=broll_for(sc.get("stock_query", ""), i)))
        step(0.1 + 0.5 * i / max(len(scenes), 1))
    plan.append(dict(kind="outro", dur=3.6, text=outro_text, broll=plan[0]["broll"]))

    fade = 0.4
    total = sum(p["dur"] for p in plan) - fade * (len(plan) - 1)
    clips, srt, t = [], [], 0.0
    for k, p in enumerate(plan):
        kw = {x: p[x] for x in p if x not in ("kind", "dur", "audio")}
        sc = Scene(W, H, p["kind"], p["dur"], t, total, institution=institution, seed=k, lang=lang, **kw)
        clip = VideoClip(sc.frame, duration=p["dur"])
        if p.get("audio") is not None:
            clip = clip.with_audio(p["audio"])
        if k > 0:
            clip = clip.with_effects([vfx.CrossFadeIn(fade)])
        clips.append(clip)
        if p["kind"] == "story":
            srt.append(f"{len(srt) + 1}\n{_srt_time(t)} --> {_srt_time(t + p['dur'] - 0.3)}\n"
                       f"{' '.join(w[2] for w in p['words'])}\n")
        t += p["dur"] - fade

    video = concatenate_videoclips(clips, method="compose", padding=-fade)
    mp4 = os.path.join(workdir, f"citeflow_{orient}.mp4")
    step(0.7)
    video.write_videofile(mp4, fps=FPS, codec="libx264", audio_codec="aac", preset="veryfast",
                          threads=4, logger=None)
    video.close()
    for c in opened:
        try:
            c.close()
        except Exception:
            pass
    srt_path = os.path.join(workdir, f"citeflow_{orient}.srt")
    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(srt))
    if progress:
        progress(1.0)
    return mp4, srt_path, narrated, used_stock


def render_card(title, institution, figure=None, size=(1080, 1080), lang="English"):
    """Social image card: headline + real figure from the paper + verification line."""
    w, h = size
    img = blobs(w, h, 1.3, 7)
    d = ImageDraw.Draw(img)
    pad = int(w * 0.07)
    fi = font(int(w * 0.024), True)
    d.text((pad, pad), fit(d, institution.upper(), fi, w - 2 * pad), font=fi, fill=ACCENT)
    tf = font(int(w * (0.064 if h >= w else 0.05)), True)
    y = pad + int(h * 0.07)
    for ln in wrap(d, title, tf, w - 2 * pad)[:3]:
        d.text((pad, y), ln, font=tf, fill=WHITE)
        y += int(tf.size * 1.2)
    if figure is not None:
        top = y + int(h * 0.04)
        box_h, box_w = h - top - int(h * 0.13), w - 2 * pad
        if box_h > 120:
            card, mask, _ = figure_card(figure, box_w, box_h)
            img.paste(card, (pad + (box_w - card.width) // 2, top), mask)
    d = ImageDraw.Draw(img)
    fs = int(w * 0.022)
    check_icon(d, pad, h - int(h * 0.085) + fs * 0.15, fs * 0.85, ACCENT)
    d.text((pad + fs * 1.4, h - int(h * 0.085)), tx(lang, "card"), font=font(fs), fill=MUTED)
    d.rectangle([0, h - 8, w, h], fill=ACCENT)
    return img
