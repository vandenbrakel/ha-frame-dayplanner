"""Compose the planner image from the configured layout."""
from __future__ import annotations

import io
import logging
import random
import re
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date, datetime

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..config import Config
from ..events import Event
from .canvas import THEMES, emoji_sprite, emoji_value, icon_char, icon_font, mix
from .modules import REGISTRY

_LOGGER = logging.getLogger(__name__)


@dataclass
class RenderContext:
    cfg: Config
    now: datetime
    strings: dict
    weather: list[dict] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    slots: list[Event] = field(default_factory=list)
    week_icons: list[list[tuple[str, str]]] = field(default_factory=list)
    week_days: list[date] = field(default_factory=list)
    blocked: list[tuple[str, tuple]] = field(default_factory=list)  # areas the hidden figure must avoid
    sum_text: str = ""
    word: str = ""
    word_sounds: list[str] = field(default_factory=list)
    seed: str = ""

    @property
    def theme(self) -> dict:
        return THEMES.get(self.cfg.theme, THEMES["paper"])


def render(ctx: RenderContext) -> Image.Image:
    W, H = ctx.cfg.size
    img = Image.new("RGB", (W, H), ctx.theme["bg"])
    draw = ImageDraw.Draw(img)
    unit = min(W, H)
    pad = unit * 0.05
    gutter = unit * 0.035
    row_gap = unit * 0.022

    columns = ctx.cfg.columns
    total_w = sum(c.width for c in columns) or 1
    avail_w = W - 2 * pad - gutter * (len(columns) - 1)
    x = pad
    for ci, col in enumerate(columns):
        cw = avail_w * col.width / total_w
        # resolve rows -> weights, dropping empty modules
        rows = []
        for row in col.rows:
            items = [(REGISTRY[n], w) for n, w in row if n in REGISTRY]
            unknown = [n for n, _ in row if n not in REGISTRY]
            if unknown:
                _LOGGER.warning("Unknown module(s) in layout: %s", ", ".join(unknown))
            if not items:
                continue
            weight = max((w if w is not None else m.weight(ctx)) for m, w in items)
            if weight > 0:
                rows.append((items, weight))
        if rows:
            avail_h = H - 2 * pad - row_gap * (len(rows) - 1)
            total = sum(w for _, w in rows)
            y = pad
            for items, weight in rows:
                rh = avail_h * weight / total
                iw = (cw - gutter * (len(items) - 1)) / len(items)
                for k, (mod, _w) in enumerate(items):
                    ix = x + k * (iw + gutter)
                    try:
                        mod.draw(draw, (ix, y, ix + iw, y + rh), ctx)
                    except Exception:  # never let one module kill the frame
                        _LOGGER.exception("Module %s failed", mod.name)
                y += rh + row_gap
        x += cw
        if ci < len(columns) - 1:
            lx = x + gutter / 2
            draw.line((lx, pad, lx, H - pad), fill=ctx.theme["line"], width=max(2, int(unit * 0.003)))
            x += gutter
    if ctx.cfg.hidden_enabled:
        try:
            draw_hidden(img, ctx)
        except Exception:
            _LOGGER.exception("Hidden figure failed")
    if ctx.theme.get("texture"):  # last, so the figure search above still sees a flat background
        from .texture import apply

        img = apply(img, ctx.theme["texture"])
    return img


@lru_cache(maxsize=4)
def _load_image(path: str, mtime: float) -> Image.Image:
    return Image.open(path).convert("RGBA")


LEGACY_HIDDEN_COLOR = "#e0453a"  # default before 0.7.3; treated as 'follow the theme'


def hidden_color(ctx: RenderContext):
    """Fixed colour from the settings, or the theme's ink (looks like part of the drawing)."""
    value = (ctx.cfg.hidden_color or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{6}", value) and value != LEGACY_HIDDEN_COLOR:
        return value
    return mix(ctx.theme["ink"], ctx.theme["bg"], 0.12)


def _hidden_sprite(ctx: RenderContext, rng: random.Random, size: int) -> Image.Image | None:
    """Own picture (hidden_image) or one of the hidden_icons, as RGBA sprite."""
    path = (ctx.cfg.hidden_image or "").strip()
    if path:
        p = Path(path)
        if p.is_dir():
            files = sorted(f for f in p.iterdir() if f.suffix.lower() in (".png", ".webp", ".gif", ".jpg", ".jpeg"))
            p = rng.choice(files) if files else p
        if p.is_file():
            src = _load_image(str(p), p.stat().st_mtime).copy()
            src.thumbnail((size, size), Image.LANCZOS)
            return src
        _LOGGER.warning("hidden_image %s not found, using an icon", path)
    icons = [i.strip() for i in str(ctx.cfg.hidden_icons).split(",") if icon_char(i.strip()) or emoji_value(i)]
    if not icons:
        return None
    choice = rng.choice(icons)
    if emoji_value(choice):
        return emoji_sprite(emoji_value(choice), size).copy()
    ch = icon_char(choice)
    sprite = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(sprite).text((size / 2, size / 2), ch, font=icon_font(size), fill=hidden_color(ctx),
                                anchor="mm")
    return sprite


def draw_hidden(img: Image.Image, ctx: RenderContext) -> None:
    """Hide one (or more) small figures in empty space; same spot all day.

    The figure never overlaps an element: anything drawn (text, tiles,
    panels) or registered in ctx.blocked (e.g. the clock face) is off-limits.
    When no spot is big enough the figure shrinks a little; if it still
    doesn't fit anywhere it is left out.
    """
    W, H = img.size
    unit = min(W, H)
    size = max(16, int(unit * float(ctx.cfg.hidden_size) / 100))
    rng = random.Random(f"hidden-{ctx.seed}")
    occupied = _occupied_mask(img, ctx)
    for n in range(max(0, int(ctx.cfg.hidden_count))):
        angle, flip = rng.uniform(-25, 25), rng.random() < 0.5
        for factor in (1.0, 0.8, 0.65, 0.5):
            sprite = _hidden_sprite(ctx, random.Random(f"hidden-sprite-{ctx.seed}-{n}"), int(size * factor))
            if sprite is None:
                return
            sprite = sprite.rotate(angle, resample=Image.BICUBIC, expand=True)
            if flip:
                sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)
            spot = _free_spot(occupied, sprite, rng, unit)
            if spot:
                img.paste(sprite, spot, sprite)
                ImageDraw.Draw(occupied).rectangle(
                    (spot[0], spot[1], spot[0] + sprite.width, spot[1] + sprite.height), fill=255)
                break
        else:
            _LOGGER.debug("No free spot for the hidden figure")


def _occupied_mask(img: Image.Image, ctx: RenderContext) -> Image.Image:
    """255 where something is drawn or blocked, 0 where it's plain background."""
    bg = Image.new("RGB", img.size, ctx.theme["bg"])
    diff = ImageChops.difference(img, bg).convert("L")
    mask = diff.point(lambda v: 255 if v > 4 else 0)
    d = ImageDraw.Draw(mask)
    for shape, box in ctx.blocked:
        (d.ellipse if shape == "ellipse" else d.rectangle)(box, fill=255)
    return mask


def _free_spot(occupied: Image.Image, sprite: Image.Image, rng: random.Random, unit: int):
    """Random top-left position where the sprite (plus a small gap) only covers background."""
    F = 12  # work on a reduced grid, fast enough for 4K
    W, H = occupied.size
    small = occupied.resize((W // F, H // F), Image.BOX).point(lambda v: 255 if v else 0)
    gap = max(1, int(unit * 0.008) // F + 1)
    k = max(sprite.width, sprite.height) // F + 1 + 2 * gap
    k += 1 - k % 2  # odd kernel
    grown = small.filter(ImageFilter.MaxFilter(k))  # 0 = a k*k window around it is free
    edge = k // 2 + max(1, int(unit * 0.012) // F)
    sw, sh = small.size
    free = [(x, y) for y in range(edge, sh - edge) for x in range(edge, sw - edge)
            if grown.getpixel((x, y)) == 0]
    if not free:
        return None
    cx, cy = rng.choice(free)
    return int(cx * F + F / 2 - sprite.width / 2), int(cy * F + F / 2 - sprite.height / 2)


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    """quality 90 for flat themes; textured frames use 85 (grain is expensive to store)."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()
