"""Drawing helpers: fonts, Material Design Icons, text fitting, themes."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from PIL import ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

THEMES = {
    "paper": {"bg": "#f5eedf", "ink": "#2b2622", "muted": "#8f8476", "line": "#ddd1bb",
              "panel": "#ece2cf", "accent": "#e0453a", "hour": "#d6423a", "minute": "#2f5fb8"},
    "white": {"bg": "#ffffff", "ink": "#1f1f1f", "muted": "#8a8a8a", "line": "#e2e2e2",
              "panel": "#f2f2f2", "accent": "#e0453a", "hour": "#d6423a", "minute": "#2f5fb8"},
    "dark": {"bg": "#1c1e23", "ink": "#f1ede4", "muted": "#9a968e", "line": "#3a3d44",
             "panel": "#272a31", "accent": "#ff6b5e", "hour": "#ff6b5e", "minute": "#6ea0ff"},
}


@lru_cache(maxsize=256)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "Andika-Bold.ttf" if bold else "Andika-Regular.ttf"
    return ImageFont.truetype(str(FONT_DIR / name), max(6, int(size)))


@lru_cache(maxsize=128)
def icon_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / "materialdesignicons-webfont.ttf"), max(6, int(size)))


@lru_cache(maxsize=1)
def _codepoints() -> dict[str, int]:
    return json.loads((FONT_DIR / "mdi_codepoints.json").read_text())


def icon_char(name: str) -> str | None:
    name = (name or "").strip().lower()
    if name.startswith("mdi:"):
        name = name[4:]
    cp = _codepoints().get(name)
    return chr(cp) if cp else None


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt) -> tuple[int, int, int, int]:
    """Return (width, height, x_offset, y_offset) of the ink box."""
    l, t, r, b = draw.textbbox((0, 0), text, font=fnt)
    return r - l, b - t, l, t


def fit_size(draw, text: str, max_w: float, max_h: float, bold=False, start: int | None = None,
             cap_height=True) -> int:
    """Largest font size where text fits in max_w x max_h.

    With cap_height the height is measured on 'Hg' so differently shaped words
    on the same line get the same size.
    """
    lo, hi = 6, int(start or max_h * 1.6)
    probe = "Hg" if cap_height else text
    while lo < hi:
        mid = (lo + hi + 1) // 2
        f = font(mid, bold)
        w = text_size(draw, text, f)[0]
        h = text_size(draw, probe, f)[1]
        if w <= max_w and h <= max_h:
            lo = mid
        else:
            hi = mid - 1
    return lo


def draw_text_centered(draw, box, text, fill, bold=False, max_size=None, size=None, anchor_x="center"):
    x0, y0, x1, y1 = box
    size = size or fit_size(draw, text, x1 - x0, y1 - y0, bold, start=max_size)
    if max_size:
        size = min(size, max_size)
    f = font(size, bold)
    w = text_size(draw, text, f)[0]
    cy = (y0 + y1) / 2
    if anchor_x == "left":
        x = x0
    elif anchor_x == "right":
        x = x1 - w
    else:
        x = (x0 + x1 - w) / 2
    # 'lm' anchor = left / middle (vertical middle of ascender..descender)
    draw.text((x, cy), text, font=f, fill=fill, anchor="lm")
    return size


def draw_icon(draw, name: str, center: tuple[float, float], size: float, fill) -> bool:
    ch = icon_char(name)
    if not ch:
        return False
    draw.text(center, ch, font=icon_font(int(size)), fill=fill, anchor="mm")
    return True


def hex_to_rgb(value: str, fallback=(120, 120, 120)) -> tuple[int, int, int]:
    v = (value or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def mix(c1, c2, amount: float):
    a, b = hex_to_rgb(c1) if isinstance(c1, str) else c1, hex_to_rgb(c2) if isinstance(c2, str) else c2
    return tuple(int(a[i] + (b[i] - a[i]) * amount) for i in range(3))
