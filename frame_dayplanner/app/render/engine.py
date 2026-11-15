"""Compose the planner image from the configured layout."""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image, ImageDraw

from ..config import Config
from ..events import Event
from .canvas import THEMES
from .modules import REGISTRY

_LOGGER = logging.getLogger(__name__)


@dataclass
class RenderContext:
    cfg: Config
    now: datetime
    strings: dict
    weather: list[dict] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    week_icons: list[list[tuple[str, str]]] = field(default_factory=list)
    sum_text: str = ""
    word: str = ""
    word_sounds: list[str] = field(default_factory=list)

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
    return img


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()
