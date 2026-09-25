"""Designed social images: single posts, carousels and short animated posts, in the CiteFlow palette.
Every element can be revealed over time (t) so the same layout also renders as an animation."""
import os

import numpy as np
from PIL import Image, ImageDraw

from video import ease, figure_card, fit, font, wrap

DEEP, GREEN, SAGE = (0x2b, 0x4c, 0x40), (0x3d, 0x6d, 0x5c), (0x4f, 0x8e, 0x78)
GOLD, GOLD2, CREAM = (0xe4, 0xb7, 0x52), (0xdd, 0xa5, 0x26), (0xf7, 0xf1, 0xe1)
WHITE, INK, MIST = (255, 255, 255), (0x1f, 0x2a, 0x26), (0xc9, 0xd8, 0xd0)


def _mix(c, bg, a):
    a = max(0.0, min(1.0, a))
    return tuple(int(bg[i] + (c[i] - bg[i]) * a) for i in range(3))


def _reveal(t, start, dur=0.45):
    """0..1 progress of an element that appears at `start` seconds; None t means fully shown."""
    return 1.0 if t is None else ease((t - start) / dur)


def _decor(d, w, h, t):
    """Thin gold arcs and leaf strokes echoing the CiteFlow logo."""
    a = _reveal(t, 0.0, 0.8)
    col = _mix(GOLD, DEEP, 0.55 * a)
    r = int(min(w, h) * 0.42)
    cx, cy = int(w * 0.92), int(h * 0.10)
    d.arc([cx - r, cy - r, cx + r, cy + r], 95, 250, fill=col, width=3)
    r2 = int(r * 0.72)
    d.arc([cx - r2, cy - r2, cx + r2, cy + r2], 100, 240, fill=_mix(GOLD, DEEP, 0.3 * a), width=2)
    for k in range(3):                                 # leaf strokes
        x0, y0 = int(w * 0.06) + k * 16, int(h * 0.96) - k * 26
        d.line([(x0, y0), (x0 + 60, y0 - 70)], fill=_mix(SAGE, DEEP, 0.8 * a), width=3)


def _pill(d, x, y, text, fnt, bg, fg, pad=(16, 8)):
    tw = d.textlength(text, font=fnt)
    d.rounded_rectangle([x, y, x + tw + 2 * pad[0], y + fnt.size + 2 * pad[1]], (fnt.size + 2 * pad[1]) // 2, fill=bg)
    d.text((x + pad[0], y + pad[1] - 2), text, font=fnt, fill=fg)
    return x + tw + 2 * pad[0]


def _point_box(d, x, y, w, n, text, fnt, a):
    """Cream text box with a gold number badge; returns its height."""
    lines = wrap(d, text, fnt, w - 110)[:3]
    h = max(86, 34 + len(lines) * int(fnt.size * 1.25))
    y += int(26 * (1 - a))
    d.rounded_rectangle([x, y, x + w, y + h], 18, fill=_mix(CREAM, DEEP, a))
    cr = 24
    cx, cy = x + 44, y + h // 2
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=_mix(GOLD, DEEP, a))
    nf = font(int(fnt.size * 1.05), True)
    s = str(n)
    d.text((cx - d.textlength(s, font=nf) / 2, cy - nf.size * 0.62), s, font=nf, fill=_mix(DEEP, DEEP, a))
    ty = y + (h - len(lines) * int(fnt.size * 1.25)) // 2 - 2
    for ln in lines:
        d.text((x + 90, ty), ln, font=fnt, fill=_mix(INK, DEEP, a))
        ty += int(fnt.size * 1.25)
    return h


def _stat(d, x, y, value, label, w, a, big=120):
    if not value:
        return 0
    vf = font(big, True)
    while d.textlength(value, font=vf) > w * 0.9 and vf.size > 48:
        vf = font(vf.size - 8, True)
    d.text((x, y + 20 * (1 - a)), value, font=vf, fill=_mix(GOLD, DEEP, a))
    lf = font(int(big * 0.26), True)
    ly = y + int(vf.size * 1.08)
    for ln in wrap(d, label.upper(), lf, w)[:2]:
        d.text((x, ly), ln, font=lf, fill=_mix(WHITE, DEEP, a))
        ly += int(lf.size * 1.3)
    return ly - y


def post_image(v, title, institution, figure=None, size=(1080, 1080), t=None):
    """One designed post. v: dict from pipeline.safe_visual. t: seconds for animation (None = final frame)."""
    w, h = size
    img = Image.new("RGB", size, DEEP)
    d = ImageDraw.Draw(img)
    _decor(d, w, h, t)
    wide = w > h * 1.3
    pad = int(w * 0.075)
    # kicker + institution
    a = _reveal(t, 0.1)
    kf = font(int(min(w, h) * 0.026), True)
    x = _pill(d, pad, pad + int(10 * (1 - a)), (v.get("kicker") or "RESEARCH").upper()[:32], kf,
              _mix(GOLD, DEEP, a), _mix(DEEP, DEEP, a))
    inf = font(int(min(w, h) * 0.022))
    inst_w = (int(w * 0.5) - x - 14) if wide else (w - x - pad - 14)
    d.text((x + 14, pad + 8), fit(d, institution, inf, inst_w), font=inf, fill=_mix(MIST, DEEP, a))
    # title
    a = _reveal(t, 0.35)
    col_w = int(w * (0.48 if wide else 1.0)) - pad
    tf = font(int(min(w, h) * (0.068 if not wide else 0.07)), True)
    y = pad + int(min(w, h) * 0.1)
    for ln in wrap(d, title, tf, col_w)[:3]:
        d.text((pad - 30 * (1 - a), y), ln, font=tf, fill=_mix(WHITE, DEEP, a))
        y += int(tf.size * 1.12)
    d.rectangle([pad, y + 12, pad + int(90 * _reveal(t, 0.7)), y + 18], fill=GOLD)
    y += 44
    points = v.get("key_points") or []
    if wide:
        # left: stat; right: figure or points
        _stat(d, pad, y, v.get("stat_value", ""), v.get("stat_label", ""), col_w, _reveal(t, 0.9), big=int(h * 0.16))
        rx, rw = int(w * 0.52), int(w * 0.48) - pad
        if figure is not None:
            a = _reveal(t, 1.1)
            card, mask, _ = figure_card(figure, rw, h - 2 * pad)
            img.paste(card, (rx + int(30 * (1 - a)), (h - card.height) // 2), mask)
        else:
            pf = font(int(h * 0.036), True)
            py = pad + 10
            for i, k in enumerate(points[:3], 1):
                py += _point_box(d, rx, py, rw, i, k, pf, _reveal(t, 0.9 + 0.35 * i)) + 16
    else:
        sh = _stat(d, pad, y, v.get("stat_value", ""), v.get("stat_label", ""), w - 2 * pad, _reveal(t, 0.9),
                   big=int(w * 0.105))
        y += sh + (24 if sh else 0)
        room = h - y - int(h * 0.14)
        if figure is not None and room > h * 0.3 and not points:
            a = _reveal(t, 1.1)
            card, mask, _ = figure_card(figure, w - 2 * pad, room)
            img.paste(card, (pad + (w - 2 * pad - card.width) // 2, y + int(30 * (1 - a))), mask)
        else:
            pf = font(int(w * 0.029), True)
            for i, k in enumerate(points[:3], 1):
                if y > h - int(h * 0.25):
                    break
                y += _point_box(d, pad, y, w - 2 * pad, i, k, pf, _reveal(t, 0.9 + 0.35 * i)) + 12
    # call to action
    a = _reveal(t, 2.2)
    cf = font(int(min(w, h) * 0.03), True)
    cta = (v.get("cta") or "").strip()
    if cta:
        _pill(d, pad, h - pad - cf.size - 14 + int(12 * (1 - a)), cta + "  →", cf, _mix(WHITE, DEEP, a), _mix(DEEP, DEEP, a))
    d.rectangle([0, h - 8, w, h], fill=GOLD)
    return img


def carousel(v, title, institution, figures=(), size=(1080, 1350)):
    """Cover slide, one slide per key fact, a figure slide (if any) and a closing slide."""
    w, h = size
    slides = [post_image(dict(v, key_points=[]), title, institution, None, size)]
    # "swipe" hint on the cover
    d = ImageDraw.Draw(slides[0])
    sf = font(30, True)
    d.text((w - 210, h - 100), "Swipe  →", font=sf, fill=GOLD)
    pts = v.get("key_points") or []
    for i, k in enumerate(pts, 1):
        img = Image.new("RGB", size, DEEP)
        d = ImageDraw.Draw(img)
        _decor(d, w, h, None)
        pad = 90
        nf = font(260, True)
        d.text((pad - 8, 120), f"0{i}", font=nf, fill=_mix(GOLD, DEEP, 0.9))
        tf = font(66, True)
        y = 470
        for ln in wrap(d, k, tf, w - 2 * pad)[:6]:
            d.text((pad, y), ln, font=tf, fill=WHITE)
            y += 84
        d.rounded_rectangle([pad, y + 30, pad + 120, y + 38], 4, fill=GOLD)
        d.text((pad, h - 110), fit(d, institution, font(26), w - 2 * pad), font=font(26), fill=MIST)
        d.text((w - pad - 120, h - 110), f"{i + 1}/{len(pts) + 2 + (1 if figures else 0)}", font=font(26, True), fill=GOLD)
        d.rectangle([0, h - 8, w, h], fill=GOLD)
        slides.append(img)
    if figures:
        img = Image.new("RGB", size, CREAM)
        d = ImageDraw.Draw(img)
        card, mask, _ = figure_card(figures[0], w - 140, h - 360)
        img.paste(card, ((w - card.width) // 2, 200), mask)
        d.text((70, 80), "FROM THE RESEARCH", font=font(34, True), fill=GREEN)
        y = 200 + card.height + 50
        for ln in wrap(d, title, font(52, True), w - 140)[:3]:
            d.text((70, y), ln, font=font(52, True), fill=DEEP)
            y += 64
        d.rectangle([0, h - 8, w, h], fill=GOLD)
        slides.append(img)
    end = Image.new("RGB", size, GREEN)
    d = ImageDraw.Draw(end)
    _decor(d, w, h, None)
    tf = font(80, True)
    y = 420
    for ln in wrap(d, (v.get("cta") or "Read the full study"), tf, w - 180)[:3]:
        d.text((90, y), ln, font=tf, fill=WHITE)
        y += 96
    _pill(d, 90, y + 50, "Follow for more research news", font(32, True), GOLD, DEEP)
    d.text((90, h - 110), fit(d, institution, font(28), w - 180), font=font(28), fill=CREAM)
    d.rectangle([0, h - 8, w, h], fill=GOLD)
    slides.append(end)
    return slides


def animated_post(v, title, institution, figure, path, size=(1080, 1080), seconds=6.0, fps=24):
    """Short MP4 where the post's elements appear one after another (for Reels, Stories, X, LinkedIn)."""
    from moviepy import VideoClip
    clip = VideoClip(lambda t: np.array(post_image(v, title, institution, figure, size, t=t)), duration=seconds)
    clip.write_videofile(path, fps=fps, codec="libx264", audio=False, preset="veryfast", threads=4, logger=None)
    clip.close()
    return path
