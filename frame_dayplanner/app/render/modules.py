"""Planner modules.  Each module draws itself into a box.

To add a module: subclass Module, set `name` and `default_weight`, implement
`draw`, and add it to REGISTRY.  It can then be used in the layout options.
"""
from __future__ import annotations

import math
from datetime import timedelta

from .canvas import (draw_icon, draw_text_centered, fit_size, font, hex_to_rgb, mix,
                     text_size)

WEATHER_ICONS = {
    "clear-night": ("weather-night", "#5b6c9a"),
    "cloudy": ("weather-cloudy", "#8d99a6"),
    "exceptional": ("alert-circle-outline", "#e0453a"),
    "fog": ("weather-fog", "#9aa3ab"),
    "hail": ("weather-hail", "#5aa0c8"),
    "lightning": ("weather-lightning", "#8b5cf6"),
    "lightning-rainy": ("weather-lightning-rainy", "#7b62d6"),
    "partlycloudy": ("weather-partly-cloudy", "#e8a33a"),
    "pouring": ("weather-pouring", "#2f6fd6"),
    "rainy": ("weather-rainy", "#3d7fd1"),
    "snowy": ("weather-snowy", "#7fb4e0"),
    "snowy-rainy": ("weather-snowy-rainy", "#6a9fd0"),
    "sunny": ("weather-sunny", "#f2a91d"),
    "windy": ("weather-windy", "#6aa39a"),
    "windy-variant": ("weather-windy-variant", "#6aa39a"),
}


def rounded_panel(draw, box, ctx, fill=None, radius=None):
    x0, y0, x1, y1 = box
    r = radius if radius is not None else min(x1 - x0, y1 - y0) * 0.18
    draw.rounded_rectangle(box, radius=r, fill=fill or ctx.theme["panel"])


class Module:
    name = ""
    default_weight = 1.0

    def weight(self, ctx) -> float:
        return self.default_weight

    def draw(self, draw, box, ctx) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class DayModule(Module):
    name, default_weight = "day", 1.0

    def draw(self, draw, box, ctx):
        text = ctx.strings["days"][ctx.now.weekday()].upper()
        draw_text_centered(draw, box, text, ctx.theme["ink"], bold=True)


class DateModule(Module):
    name, default_weight = "date", 0.7

    def draw(self, draw, box, ctx):
        text = f"{ctx.now.day} {ctx.strings['months'][ctx.now.month - 1]}"
        draw_text_centered(draw, box, text, ctx.theme["ink"])


class TimeModule(Module):
    """Digital time only (e.g. for a small extra clock)."""
    name, default_weight = "time", 1.2

    def draw(self, draw, box, ctx):
        draw_text_centered(draw, box, ctx.now.strftime("%H:%M"), ctx.theme["ink"], bold=True)


class ClockModule(Module):
    name, default_weight = "clock", 4.4

    def draw(self, draw, box, ctx):
        style = ctx.cfg.clock_style
        if style == "digital":
            draw_text_centered(draw, box, ctx.now.strftime("%H:%M"), ctx.theme["ink"], bold=True)
            return
        x0, y0, x1, y1 = box
        th = ctx.theme
        ink = th["ink"]
        now = ctx.now

        # labelled time slots get a legend below the clock
        slots = [s for s in ctx.slots if self._visible(s, now)][:3]
        if slots:
            row_h = (y1 - y0) * 0.115
            legend_top = y1 - row_h * len(slots)
            for i, slot in enumerate(slots):
                self._legend_row(draw, (x0, legend_top + i * row_h, x1, legend_top + (i + 1) * row_h), slot, ctx)
            y1 = legend_top - (y1 - y0) * 0.02

        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        outer = min(x1 - x0, y1 - y0) / 2
        arc_items = [e for e in ctx.events if e.timed] if ctx.cfg.clock_event_arcs else []
        arc_items += slots
        ring = ctx.cfg.clock_event_arcs or bool(ctx.cfg.slots)
        r = outer * (0.83 if ring else 0.98)

        # face
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=th["bg"], outline=ink, width=max(2, int(r * 0.03)))

        # 'time timer' wedge: minutes left until the next slot starts
        upcoming = self._upcoming(slots, ctx)
        if upcoming and ctx.cfg.clock_slot_timer:
            mins = math.ceil((upcoming.start - now).total_seconds() / 60)
            a0 = now.minute * 6 - 90
            rw = r * 0.955
            draw.pieslice((cx - rw, cy - rw, cx + rw, cy + rw), a0, a0 + mins * 6,
                          fill=mix(upcoming.color or th["accent"], th["bg"], 0.72))

        # arcs on an outer ring
        if ring:
            self._draw_arcs(draw, cx, cy, r, outer, arc_items, ctx)

        # ticks
        for i in range(60):
            a = math.radians(i * 6 - 90)
            major = i % 5 == 0
            r_in = r * (0.86 if major else 0.91)
            r_out = r * 0.95
            w = max(2, int(r * (0.022 if major else 0.009)))
            draw.line((cx + r_in * math.cos(a), cy + r_in * math.sin(a),
                       cx + r_out * math.cos(a), cy + r_out * math.sin(a)),
                      fill=ink if major else th["muted"], width=w)

        # numbers
        num_r = r * (0.66 if ctx.cfg.clock_minute_numbers else 0.72)
        f = font(r * (0.17 if ctx.cfg.clock_minute_numbers else 0.2), bold=True)
        for h in range(1, 13):
            a = math.radians(h * 30 - 90)
            draw.text((cx + num_r * math.cos(a), cy + num_r * math.sin(a)), str(h), font=f,
                      fill=ink, anchor="mm")
        if ctx.cfg.clock_minute_numbers:
            fm = font(r * 0.075)
            for m in range(5, 61, 5):
                a = math.radians(m * 6 - 90)
                draw.text((cx + r * 0.8 * math.cos(a), cy + r * 0.8 * math.sin(a)),
                          f"{m % 60:02d}" if m != 60 else "00", font=fm, fill=th["muted"], anchor="mm")

        if style == "both":
            fd = font(r * 0.13, bold=True)
            draw.text((cx, cy + r * 0.36), ctx.now.strftime("%H:%M"), font=fd, fill=th["muted"], anchor="mm")

        # hands
        minute = ctx.now.minute
        hour = ctx.now.hour % 12 + minute / 60
        self._hand(draw, cx, cy, hour * 30, r * 0.48, r * 0.075, th["hour"])
        self._hand(draw, cx, cy, minute * 6, r * 0.78, r * 0.045, th["minute"])
        cap = r * 0.065
        draw.ellipse((cx - cap, cy - cap, cx + cap, cy + cap), fill=ink)

    @staticmethod
    def _hand(draw, cx, cy, deg, length, width, color):
        a = math.radians(deg - 90)
        ex, ey = cx + length * math.cos(a), cy + length * math.sin(a)
        tail = length * 0.12
        sx, sy = cx - tail * math.cos(a), cy - tail * math.sin(a)
        w = int(width)
        draw.line((sx, sy, ex, ey), fill=color, width=w)
        for px, py in ((ex, ey), (sx, sy)):
            draw.ellipse((px - w / 2, py - w / 2, px + w / 2, py + w / 2), fill=color)

    @staticmethod
    def _visible(ev, now) -> bool:
        end = max(ev.end or ev.start, ev.start + timedelta(minutes=1))
        return end > now and ev.start < now + timedelta(hours=12)

    @staticmethod
    def _upcoming(slots, ctx):
        limit = timedelta(minutes=int(ctx.cfg.clock_slot_countdown_minutes))
        for s in sorted(slots, key=lambda s: s.start):
            if timedelta(0) < s.start - ctx.now <= limit:
                return s
        return None

    def _legend_row(self, draw, box, slot, ctx):
        x0, y0, x1, y1 = box
        th = ctx.theme
        h = y1 - y0
        now = ctx.now
        active = slot.start <= now < max(slot.end, slot.start + timedelta(minutes=1))
        color = slot.color or th["accent"]
        text = f"{slot.start:%H:%M}"
        if slot.end and slot.end > slot.start:
            text += f"-{slot.end:%H:%M}"
        text += f"  {slot.title}"
        if active:
            text += f"  {ctx.strings['now']}"
        elif self._upcoming([slot], ctx):
            n = math.ceil((slot.start - now).total_seconds() / 60)
            text += f"  ({ctx.strings['in_minutes'].format(n=n)})"
        pad = h * 0.12
        ir = (h - 2 * pad) / 2 * 0.8
        size = fit_size(draw, text, (x1 - x0) * 0.9 - 2 * ir - pad * 3, h * 0.55, bold=True)
        f = font(size, True)
        tw = text_size(draw, text, f)[0]
        total = 2 * ir + pad * 1.2 + tw
        sx = x0 + ((x1 - x0) - total) / 2
        cy = (y0 + y1) / 2
        pill = (sx - pad * 2, y0 + pad * 0.5, sx + total + pad * 2, y1 - pad * 0.5)
        draw.rounded_rectangle(pill, radius=(pill[3] - pill[1]) / 2, fill=color if active else th["panel"])
        icx = sx + ir
        draw.ellipse((icx - ir, cy - ir, icx + ir, cy + ir), fill="#ffffff" if active else color)
        draw_icon(draw, slot.icon or "clock-outline", (icx, cy), ir * 1.3, color if active else "#ffffff")
        draw.text((sx + 2 * ir + pad * 1.2, cy), text, font=f, fill="#ffffff" if active else th["ink"], anchor="lm")

    def _draw_arcs(self, draw, cx, cy, r, outer, items, ctx):
        now = ctx.now
        horizon = now + timedelta(hours=12)
        ring_r = (r + outer) / 2 + outer * 0.005
        width = int((outer - r) * 0.62)
        for ev in items:
            if not self._visible(ev, now):
                continue
            start = max(ev.start, now)
            end = min(max(ev.end or ev.start, ev.start), horizon - timedelta(minutes=1))
            a0 = (start.hour % 12 + start.minute / 60) * 30 - 90
            span = (end - start).total_seconds() / 3600 * 30
            a1 = a0 + max(span, 3)
            bb = (cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r)
            draw.arc(bb, a0, a1, fill=ev.color, width=width)
            # rounded ends
            for ang in (a0, a1):
                ra = math.radians(ang)
                px, py = cx + ring_r * math.cos(ra), cy + ring_r * math.sin(ra)
                draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=ev.color)
            # icon in a bubble at the middle of the arc
            ra = math.radians((a0 + a1) / 2)
            px, py = cx + ring_r * math.cos(ra), cy + ring_r * math.sin(ra)
            br = width * 1.15
            draw.ellipse((px - br, py - br, px + br, py + br), fill=ev.color)
            draw_icon(draw, ev.icon, (px, py), br * 1.35, "#ffffff")


class WeatherModule(Module):
    name, default_weight = "weather", 1.5

    def weight(self, ctx):
        return self.default_weight if ctx.weather else 0

    def draw(self, draw, box, ctx):
        x0, y0, x1, y1 = box
        n = len(ctx.weather)
        cw = (x1 - x0) / n
        h = y1 - y0
        th = ctx.theme
        label_size = fit_size(draw, max((p["label"] for p in ctx.weather), key=len), cw * 0.8, h * 0.16)
        for i, p in enumerate(ctx.weather):
            bx0 = x0 + i * cw
            box_i = (bx0 + cw * 0.06, y0, bx0 + cw * 0.94, y1)
            if p.get("current"):
                rounded_panel(draw, box_i, ctx)
            dim = 0.6 if p.get("past") else 0
            icon, color = WEATHER_ICONS.get(p.get("condition") or "", ("weather-cloudy", "#8d99a6"))
            ink = mix(th["ink"], th["bg"], dim)
            cxm = bx0 + cw / 2
            draw.text((cxm, y0 + h * 0.13), p["label"], font=font(label_size), fill=mix(th["muted"], th["bg"], dim), anchor="mm")
            if p.get("condition"):
                draw_icon(draw, icon, (cxm, y0 + h * 0.5), h * 0.48, mix(color, th["bg"], dim))
            else:
                draw.text((cxm, y0 + h * 0.5), "–", font=font(h * 0.3), fill=ink, anchor="mm")
            if p.get("temperature") is not None:
                draw.text((cxm, y0 + h * 0.86), f"{round(p['temperature'])}°", font=font(h * 0.2, True),
                          fill=ink, anchor="mm")


class EventsModule(Module):
    name, default_weight = "events", 1.0

    def _visible(self, ctx):
        return ctx.events[: int(ctx.cfg.max_events)]

    def weight(self, ctx):
        evs = self._visible(ctx)
        return sum(1.35 if e.highlight else 0.85 for e in evs) * self.default_weight

    def draw(self, draw, box, ctx):
        evs = self._visible(ctx)
        if not evs:
            return
        x0, y0, x1, y1 = box
        units = [1.35 if e.highlight else 0.85 for e in evs]
        unit_h = (y1 - y0) / sum(units)
        y = y0
        th = ctx.theme
        # font sizes: equal for all non-highlight rows / all highlight rows
        for ev, u in zip(evs, units):
            h = unit_h * u
            row = (x0, y, x1, y + h)
            self._row(draw, row, ev, ctx)
            y += h

    def _row(self, draw, box, ev, ctx):
        x0, y0, x1, y1 = box
        h = y1 - y0
        th = ctx.theme
        pad = h * 0.1
        ir = (h - 2 * pad) / 2
        cy = (y0 + y1) / 2
        text = ev.title.upper() if ev.highlight else ev.title
        if ev.timed and ev.start.date() == ctx.now.date():
            text = f"{ev.start:%H:%M}  {text}"
        # measure text first to center icon+text as a group
        max_text_w = (x1 - x0) - 2 * ir - pad * 3
        size = fit_size(draw, text, max_text_w, h * (0.62 if ev.highlight else 0.5), bold=ev.highlight)
        f = font(size, ev.highlight)
        tw = text_size(draw, text, f)[0]
        ir = min(ir, size * 0.75)
        total = 2 * ir + pad * 1.5 + tw
        sx = x0 + ((x1 - x0) - total) / 2
        icx = sx + ir
        draw.ellipse((icx - ir, cy - ir, icx + ir, cy + ir), fill=ev.color or th["accent"])
        draw_icon(draw, ev.icon, (icx, cy), ir * 1.25, "#ffffff")
        draw.text((sx + 2 * ir + pad * 1.5, cy), text, font=f, fill=th["ink"], anchor="lm")


class SumModule(Module):
    name, default_weight = "sum", 1.3

    def draw(self, draw, box, ctx):
        x0, y0, x1, y1 = box
        rounded_panel(draw, box, ctx)
        pw, ph = (x1 - x0) * 0.1, (y1 - y0) * 0.18
        draw_text_centered(draw, (x0 + pw, y0 + ph, x1 - pw, y1 - ph), ctx.sum_text, ctx.theme["ink"], bold=True)


class WordModule(Module):
    name, default_weight = "word", 1.3

    def draw(self, draw, box, ctx):
        x0, y0, x1, y1 = box
        rounded_panel(draw, box, ctx)
        pw, ph = (x1 - x0) * 0.12, (y1 - y0) * 0.16
        if ctx.cfg.word_show_sounds and ctx.word_sounds:
            mid = y0 + (y1 - y0) * 0.62
            draw_text_centered(draw, (x0 + pw, y0 + ph, x1 - pw, mid), ctx.word, ctx.theme["ink"], bold=True)
            draw_text_centered(draw, (x0 + pw, mid, x1 - pw, y1 - ph * 0.8), " - ".join(ctx.word_sounds),
                               ctx.theme["muted"])
        else:
            draw_text_centered(draw, (x0 + pw, y0 + ph, x1 - pw, y1 - ph), ctx.word, ctx.theme["ink"], bold=True)


class WeekModule(Module):
    """Vertical week strip, one colour per day, today highlighted."""
    name, default_weight = "week", 1.0

    def draw(self, draw, box, ctx):
        x0, y0, x1, y1 = box
        th = ctx.theme
        colors = [c.strip() for c in str(ctx.cfg.week_colors).split(",")]
        colors += ["#888888"] * (7 - len(colors))
        today = ctx.now.weekday()
        cell_h = (y1 - y0) / 7
        w = x1 - x0
        for i in range(7):
            cy0 = y0 + i * cell_h
            is_today = i == today
            inset = 0 if is_today else w * 0.1
            box_i = (x0 + inset, cy0 + cell_h * 0.06, x1 - inset, cy0 + cell_h * 0.94)
            col = colors[i]
            fill = hex_to_rgb(col) if is_today else mix(col, th["bg"], 0.55)
            draw.rounded_rectangle(box_i, radius=min(w, cell_h) * 0.22, fill=fill)
            if is_today:
                draw.rounded_rectangle(box_i, radius=min(w, cell_h) * 0.22, outline=th["ink"],
                                       width=max(3, int(w * 0.03)))
            bx0, by0, bx1, by1 = box_i
            txt_col = "#ffffff" if is_today else th["ink"]
            label = ctx.strings["days_short"][i]
            icons = ctx.week_icons[i] if ctx.week_icons else []
            if icons:
                label_box = (bx0, by0 + (by1 - by0) * 0.08, bx1, by0 + (by1 - by0) * 0.55)
            else:
                label_box = (bx0, by0 + (by1 - by0) * 0.2, bx1, by1 - (by1 - by0) * 0.2)
            size = fit_size(draw, "ma", (bx1 - bx0) * 0.7, label_box[3] - label_box[1], bold=True)
            draw_text_centered(draw, label_box, label, txt_col, bold=True, size=size)
            if icons:
                icons = icons[:3]
                isz = min((bx1 - bx0) * 0.8 / len(icons), (by1 - by0) * 0.34)
                total = isz * len(icons)
                for k, (ic, colr) in enumerate(icons):
                    icx = (bx0 + bx1 - total) / 2 + isz * (k + 0.5)
                    draw_icon(draw, ic, (icx, by0 + (by1 - by0) * 0.74), isz * 0.95,
                              txt_col if is_today else colr)


class SpacerModule(Module):
    name, default_weight = "spacer", 0.3

    def draw(self, draw, box, ctx):
        pass


class LineModule(Module):
    name, default_weight = "line", 0.1

    def draw(self, draw, box, ctx):
        x0, y0, x1, y1 = box
        cy = (y0 + y1) / 2
        draw.line((x0 + (x1 - x0) * 0.1, cy, x1 - (x1 - x0) * 0.1, cy), fill=ctx.theme["line"],
                  width=max(2, int((y1 - y0) * 0.08)))


REGISTRY: dict[str, Module] = {
    m.name: m for m in (DayModule(), DateModule(), TimeModule(), ClockModule(), WeatherModule(),
                        EventsModule(), SumModule(), WordModule(), WeekModule(), SpacerModule(),
                        LineModule())
}
