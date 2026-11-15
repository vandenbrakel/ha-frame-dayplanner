"""Procedural background textures (paper, kraft, chalkboard).

The texture is a grey 'grain' image centred on 128 that is added to the
finished frame (lighter above 128, darker below), so it also shows on the
clock, tiles and text, like ink printed on real paper.  Everything is
generated here from a fixed seed, so there are no image files and the
texture looks the same after a restart.  Results are cached per size.
"""
from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

# strengths in grey levels (0-255 scale)
STYLES = {
    # blotch: big soft clouds, mottle: smaller clouds, grain: fine noise, fibres/specks: per megapixel,
    # vignette: darker edges, smear: horizontal chalk wipe marks (lighter only)
    "paper": {"blotch": 5, "mottle": 3, "grain": 4, "fibres": 30, "fibre_amp": 10, "specks": 12, "vignette": 12, "smear": 0},
    "kraft": {"blotch": 8, "mottle": 5, "grain": 7, "fibres": 90, "fibre_amp": 18, "specks": 30, "vignette": 16, "smear": 0},
    "chalk": {"blotch": 5, "mottle": 3, "grain": 5, "fibres": 0, "fibre_amp": 0, "specks": 0, "vignette": 18, "smear": 6},
}


def _random(size: tuple[int, int], rng: random.Random) -> Image.Image:
    return Image.frombytes("L", size, rng.randbytes(size[0] * size[1]))


def _scaled(layer: Image.Image, amp: float) -> Image.Image:
    """Re-centre a layer on 128 with a standard deviation of amp grey levels."""
    st = ImageStat.Stat(layer)
    mean, std = st.mean[0], st.stddev[0] or 1
    return layer.point(lambda v: max(0, min(255, round(128 + (v - mean) * amp / std))))


def _clouds(size, cell: float, rng) -> Image.Image:
    w, h = max(2, int(size[0] / cell)), max(2, int(size[1] / cell))
    return _random((w, h), rng).resize(size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(cell / 2))


def _add(base: Image.Image, layer: Image.Image) -> Image.Image:
    """base + (layer - 128)."""
    pos = layer.point(lambda v: max(0, v - 128))
    neg = layer.point(lambda v: max(0, 128 - v))
    return ImageChops.subtract(ImageChops.add(base, pos), neg)


@lru_cache(maxsize=4)
def grain(style: str, size: tuple[int, int]) -> Image.Image:
    """Greyscale texture centred on 128 for the given style and frame size."""
    p = STYLES[style]
    W, H = size
    rng = random.Random(f"texture-{style}-{W}x{H}")
    unit = min(W, H) / 1080  # 1.0 at 1080p, 2.0 at 4K
    tex = Image.new("L", size, 128)

    if p["blotch"]:
        tex = _add(tex, _scaled(_clouds(size, 220 * unit, rng), p["blotch"]))
    if p["mottle"]:
        tex = _add(tex, _scaled(_clouds(size, 45 * unit, rng), p["mottle"]))
    if p["smear"]:  # wide, flat streaks: chalk wiped with a cloth
        w, h = max(2, int(W / (420 * unit))), max(2, int(H / (70 * unit)))
        streaks = _random((w, h), rng).resize(size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(28 * unit))
        streaks = _scaled(streaks, p["smear"]).point(lambda v: max(128, v))  # only lighter
        tex = _add(tex, streaks)
    if p["grain"]:
        # grain at half resolution: looks the same from a distance, keeps the JPEG much smaller
        half = (max(2, W // 2), max(2, H // 2))
        fine = _random(half, rng).filter(ImageFilter.GaussianBlur(0.5)).resize(size, Image.BILINEAR)
        tex = _add(tex, _scaled(fine, p["grain"]))

    mp = W * H / 1e6
    d = ImageDraw.Draw(tex)
    for _ in range(int(p["fibres"] * mp)):  # short curly fibres, lighter or darker
        x, y = rng.uniform(0, W), rng.uniform(0, H)
        step, angle = rng.uniform(4, 12) * unit, rng.uniform(0, 2 * math.pi)
        pts = [(x, y)]
        for _i in range(6):
            angle += rng.uniform(-0.5, 0.5)
            x, y = x + step * math.cos(angle), y + step * math.sin(angle)
            pts.append((x, y))
        tone = 128 + rng.choice((-1, 1)) * rng.uniform(0.4, 1) * p["fibre_amp"]
        d.line(pts, fill=int(tone), width=max(1, round(rng.uniform(0.6, 1.5) * unit)))
    for _ in range(int(p["specks"] * mp)):  # tiny dark specks
        x, y, r = rng.uniform(0, W), rng.uniform(0, H), rng.uniform(0.6, 1.6) * unit
        d.ellipse((x - r, y - r, x + r, y + r), fill=int(128 - rng.uniform(15, 35)))
    tex = tex.filter(ImageFilter.GaussianBlur(0.4 * unit))

    if p["vignette"]:  # slightly darker towards the edges
        v = Image.radial_gradient("L").resize(size, Image.BICUBIC)  # 0 in the centre .. 255 in the corners
        k = p["vignette"] / 145
        tex = _add(tex, v.point(lambda x: 128 - round(max(0, x - 110) * k)))
    return tex


def apply(img: Image.Image, style: str) -> Image.Image:
    """Add the texture to a finished RGB frame."""
    g = grain(style, img.size)
    pos = g.point(lambda v: max(0, v - 128)).convert("RGB")
    neg = g.point(lambda v: max(0, 128 - v)).convert("RGB")
    return ImageChops.subtract(ImageChops.add(img, pos), neg)
