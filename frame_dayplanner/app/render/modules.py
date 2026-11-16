"""Planner modules.  Each module draws itself into a box.

To add a module: subclass Module, set `name` and `default_weight`, implement
`draw`, and add it to REGISTRY.  It can then be used in the layout options.
"""
from __future__ import annotations

import math
from datetime import timedelta

from .canvas import (bubble_color, draw_icon, draw_text_centered, fit_size, font, hex_to_rgb, mix,
                     readable_on, text_size, visible_on)

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


# Time-left colours, by minutes left until the deadline (end of a slot / start of an event).
# Example: at school 8:30 -> green until 8:15, orange 8:15-8:20, red 8:20-8:25, purple 8:25-8:30.
# Colours are blended smoothly around these points (in hue, so green -> orange passes yellow).
TIME_LEFT = [(17.5, "#3a9b4a"), (12.5, "#f08a24"), (7.5, "#e0453a"), (2.5, "#8e44ad")]


def _blend_hue(c1: str, c2: str, t: float):
    """Blend two colours along the shortest hue path (keeps them bright, no muddy browns)."""
    import colorsys

    h1, l1, s1 = colorsys.rgb_to_hls(*(v / 255 for v in hex_to_rgb(c1)))
    h2, l2, s2 = colorsys.rgb_to_hls(*(v / 255 for v in hex_to_rgb(c2)))
    dh = ((h2 - h1 + 0.5) % 1.0) - 0.5
    r, g, b = colorsys.hls_to_rgb((h1 + dh * t) % 1.0, l1 + (l2 - l1) * t, s1 + (s2 - s1) * t)
    return tuple(round(v * 255) for v in (r, g, b))


SOON_MINUTES = 60  # outer ring: only what happens within the upcoming hour


def time_left_color(minutes_left: float):
    """green (> 15 min) -> orange (15-10) -> red (10-5) -> purple (last 5 min), interpolated."""
    if minutes_left >= TIME_LEFT[0][0]:
        return hex_to_rgb(TIME_LEFT[0][1])
    for (m_hi, c_hi), (m_lo, c_lo) in zip(TIME_LEFT, TIME_LEFT[1:]):
        if minutes_left >= m_lo:
            return _blend_hue(c_hi, c_lo, (m_hi - minutes_left) / (m_hi - m_lo))
    return hex_to_rgb(TIME_LEFT[-1][1])


def day_color(ctx, weekday: int) -> str:
    """Colour of a weekday, lightened/darkened when it would vanish into the background."""
    colors = [c.strip() for c in str(ctx.cfg.week_colors).split(",") if c.strip()]
    colors += ["#888888"] * (7 - len(colors))
    return visible_on(colors[weekday], ctx.theme["bg"], ctx.theme["ink"])


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
        if not ctx.cfg.day_background:
            draw_text_centered(draw, box, text, ctx.theme["ink"], bold=True)
            return
        color = day_color(ctx, ctx.now.weekday())
        x0, y0, x1, y1 = box
        h = y1 - y0
        draw.rounded_rectangle(box, radius=h * 0.25, fill=color)
        draw_text_centered(draw, (x0 + h * 0.35, y0 + h * 0.14, x1 - h * 0.35, y1 - h * 0.14), text,
                           readable_on(color), bold=True)


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
    """Analog clock.

    The dial shows the current half day (AM or PM, decided on the 24h time):
    timed events and slots in that half are arcs on an outer ring (hour
    scale).  During a slot's own hour (e.g. 08:00-08:25 for a slot
    08:15-08:25) the slot is drawn on the minute scale instead, as a band
    just inside the rim where the minute hand points.
    """
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

        # automatic 'leave' slots (from events) only show up close to leaving; own slots whole half-day
        slots = [s for s in ctx.slots
                 if self._minute_mode(s, now) or self._upcoming(s, ctx)
                 or (s.source != "leave" and self._in_half(s, now))][:3]
        if slots:  # legend below the clock
            row_h = (y1 - y0) * 0.115
            legend_top = y1 - row_h * len(slots)
            for i, slot in enumerate(slots):
                self._legend_row(draw, (x0, legend_top + i * row_h, x1, legend_top + (i + 1) * row_h), slot, ctx)
            y1 = legend_top - (y1 - y0) * 0.02

        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        outer = min(x1 - x0, y1 - y0) / 2
        minute_slots = [s for s in slots if self._minute_mode(s, now)]
        # an event whose time to leave is on the minute band is drawn there completely,
        # not a second time on the hour scale
        on_band = [s.ref for s in minute_slots if s.source == "leave" and s.ref]
        hour_items = [e for e in ctx.events if e.timed and not any(e is b for b in on_band)] \
            if ctx.cfg.clock_event_arcs else []
        # leave slots are already drawn as the lead-in of their event's arc
        hour_items += [s for s in slots if s not in minute_slots and s.source != "leave"]
        hour_items = [e for e in hour_items if self._in_half(e, now) and self._soon(e, ctx)]
        ring = bool(hour_items)  # only reserve the outer ring when there is something on it
        r = outer * (0.83 if ring else 0.98)

        # face
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=th["bg"], outline=ink, width=max(2, int(r * 0.03)))
        ctx.blocked.append(("ellipse", (cx - outer, cy - outer, cx + outer, cy + outer)))
        bubbles = []  # drawn after ticks and numbers, so no tick runs through them
        for slot in minute_slots:
            bubbles += self._minute_band(draw, cx, cy, r, slot, ctx)
        if ring:
            self._hour_arcs(draw, cx, cy, r, outer, hour_items, ctx)

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
                          f"{m % 60:02d}", font=fm, fill=th["muted"], anchor="mm")

        if style == "both":
            fd = font(r * 0.13, bold=True)
            draw.text((cx, cy + r * 0.36), ctx.now.strftime("%H:%M"), font=fd, fill=th["muted"], anchor="mm")

        for bubble in bubbles:
            bubble()

        # hands
        minute = ctx.now.minute
        hour = ctx.now.hour % 12 + minute / 60
        self._hand(draw, cx, cy, hour * 30, r * 0.48, r * 0.075, th["hour"])
        self._hand(draw, cx, cy, minute * 6, r * 0.78, r * 0.045, th["minute"])
        cap = r * 0.065
        draw.ellipse((cx - cap, cy - cap, cx + cap, cy + cap), fill=ink)

    # ------------------------------------------------------------ helpers
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
    def _end(ev):
        return max(ev.end or ev.start, ev.start + timedelta(minutes=1))

    @staticmethod
    def _half(now):
        start = now.replace(hour=0 if now.hour < 12 else 12, minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=12)

    def _ring_from(self, ev):
        """Where an item starts on the outer ring: a time slot counts down the last 60 minutes
        to its end (school 8:00-8:30 -> from 7:30), an event from its 'leave' time or start."""
        if ev.source == "slot":
            return min(ev.start, self._end(ev) - timedelta(minutes=SOON_MINUTES))
        return getattr(ev, "leave", None) or ev.start

    def _in_half(self, ev, now) -> bool:
        """Still to come (or running) and within the current AM/PM half."""
        _h0, h1 = self._half(now)
        return self._end(ev) > now and self._ring_from(ev) < h1

    def _soon(self, ev, ctx) -> bool:
        """Show on the outer ring only within the upcoming 60 minutes: an event from 60 minutes
        before its start, a time slot from 60 minutes before its end, until it ends.
        (A long prep time only switches the planner on earlier, see the schedule.)"""
        deadline = self._end(ev) if ev.source == "slot" else ev.start
        return deadline - timedelta(minutes=SOON_MINUTES) <= ctx.now < self._end(ev)

    def _minute_mode(self, slot, now) -> bool:
        """From the full hour of the slot's start (24h) until it ends."""
        begin = slot.start.replace(minute=0, second=0, microsecond=0)
        if slot.source == "slot":  # and the countdown: the last 60 minutes before the end
            begin = min(begin, self._ring_from(slot))
        return begin <= now < self._end(slot)

    @staticmethod
    def _upcoming(slot, ctx) -> bool:
        limit = timedelta(minutes=int(ctx.cfg.clock_slot_countdown_minutes))
        return timedelta(0) < slot.start - ctx.now <= limit

    def _minute_band(self, draw, cx, cy, r, slot, ctx):
        rb = r * 0.97
        width = int(r * 0.13)
        bb = (cx - rb, cy - rb, cx + rb, cy + rb)
        begin = slot.start
        if slot.source == "slot" and ctx.now < slot.start:
            # countdown before the slot (school 8:00-8:30 from 7:30): only the part still to come
            begin = max(self._ring_from(slot), ctx.now)
        a0 = (begin.minute + begin.second / 60) * 6 - 90
        minutes = min(60.0, max(0.5, (self._end(slot) - begin).total_seconds() / 60))
        passed = 0.0
        if ctx.now > begin:
            passed = min(minutes, (ctx.now - begin).total_seconds() / 60)
        # small segments so the colour can follow the minutes left (green -> orange -> red -> purple)
        total = (self._end(slot) - begin).total_seconds() / 60
        steps = max(2, int(minutes * 4))
        for k in range(steps):
            m0, m1 = minutes * k / steps, minutes * (k + 1) / steps
            color = self._slot_color(slot, ctx, total - (m0 + m1) / 2)
            if m1 <= passed:  # the part that has already passed fades out
                color = mix(color, ctx.theme["bg"], 0.6)
            overlap = 0.4 if k < steps - 1 else 0
            draw.arc(bb, a0 + m0 * 6, a0 + m1 * 6 + overlap, fill=color, width=width)

        # leave slot: then a short piece of the event itself in its own colour, with its icon
        # exactly where it starts (where green -> red ends); a gap keeps the ring readable
        ev = slot.ref if slot.source == "leave" else None
        if ev:
            color = ev.color or ctx.theme["accent"]
            e0 = a0 + minutes * 6
            duration = ((ev.end - ev.start).total_seconds() / 60) if ev.end else 15
            ev_minutes = max(0.0, min(duration, 15, 55 - minutes))
            if ev_minutes >= 1:
                draw.arc(bb, e0 - 0.3, e0 + ev_minutes * 6, fill=color, width=width)
            ra = math.radians(e0)
            px, py = cx + rb * math.cos(ra), cy + rb * math.sin(ra)
            br = width * 0.95

            def bubble(px=px, py=py, br=br, color=color, ev=ev):
                draw.ellipse((px - br, py - br, px + br, py + br), fill=bubble_color(ev.icon, color),
                             outline=ctx.theme["bg"], width=max(2, int(width * 0.12)))
                draw_icon(draw, ev.icon, (px, py), br * 1.3, readable_on(color))
            return [bubble]
        if slot.source == "slot" and slot.icon:  # own slot: its icon at the deadline (the end)
            ra = math.radians(a0 + minutes * 6)
            px, py = cx + rb * math.cos(ra), cy + rb * math.sin(ra)
            br = width * 0.95
            color = self._slot_color(slot, ctx, 0)

            def bubble(px=px, py=py, br=br, color=color):
                draw.ellipse((px - br, py - br, px + br, py + br), fill=bubble_color(slot.icon, color),
                             outline=ctx.theme["bg"], width=max(2, int(width * 0.12)))
                draw_icon(draw, slot.icon, (px, py), br * 1.3, readable_on(color))
            return [bubble]
        return []

    @staticmethod
    def _slot_color(slot, ctx, minutes_left: float):
        """Colour of a slot with minutes_left until its end (the deadline)."""
        if not ctx.cfg.clock_slot_gradient:
            return slot.color or ctx.theme["accent"]
        return time_left_color(minutes_left)

    def _legend_row(self, draw, box, slot, ctx):
        x0, y0, x1, y1 = box
        th = ctx.theme
        h = y1 - y0
        now = ctx.now
        active = slot.start <= now < self._end(slot)
        color = slot.color or th["accent"]
        if active and ctx.cfg.clock_slot_gradient:  # pill follows the colour of the current minute
            color = self._slot_color(slot, ctx, (self._end(slot) - now).total_seconds() / 60)
        on_color = readable_on(color)
        text = f"{slot.start:%H:%M}"
        if slot.end and slot.end > slot.start:
            text += f"-{slot.end:%H:%M}"
        text += f"  {slot.title}"
        if not active and self._upcoming(slot, ctx):
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
        icon = slot.icon or "clock-outline"
        draw.ellipse((icx - ir, cy - ir, icx + ir, cy + ir), fill=on_color if active else bubble_color(icon, color))
        draw_icon(draw, icon, (icx, cy), ir * 1.3, color if active else on_color)
        draw.text((sx + 2 * ir + pad * 1.2, cy), text, font=f, fill=on_color if active else th["ink"], anchor="lm")

    def _hour_arcs(self, draw, cx, cy, r, outer, items, ctx):
        now = ctx.now
        _h0, h1 = self._half(now)
        ring_r = (r + outer) / 2 + outer * 0.005
        width = int((outer - r) * 0.62)
        bb = (cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r)
        angle = lambda t: (t.hour % 12 + t.minute / 60 + t.second / 3600) * 30 - 90  # noqa: E731

        def cap(ang, color):  # rounded end
            ra = math.radians(ang)
            px, py = cx + ring_r * math.cos(ra), cy + ring_r * math.sin(ra)
            draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=color)

        left = lambda t, deadline: (deadline - t).total_seconds() / 60  # noqa: E731

        def time_left_arc(t_from, t_to, deadline):
            """Arc from t_from to t_to coloured by the minutes left until deadline."""
            steps = max(2, int((t_to - t_from).total_seconds() / 60))
            for k in range(steps):
                t0 = t_from + (t_to - t_from) * k / steps
                t1 = t_from + (t_to - t_from) * (k + 1) / steps
                draw.arc(bb, angle(t0), angle(t1) + 0.3, fill=time_left_color(left(t0 + (t1 - t0) / 2, deadline)),
                         width=width)

        for ev in items:
            if not self._in_half(ev, now):
                continue
            color = ev.color or ctx.theme["accent"]
            end = min(self._end(ev), h1)
            first_cap = None
            # 'need to leave' lead-in, coloured by the minutes left until the event starts
            leave = getattr(ev, "leave", None)
            if leave and ev.start > now:
                lead_from, lead_to = max(leave, now), min(ev.start, h1)
                time_left_arc(lead_from, lead_to, ev.start)
                first_cap = (angle(lead_from), time_left_color(left(lead_from, ev.start)))
            if ev.start >= h1:  # only the lead-in falls in this half of the day
                if first_cap:
                    cap(*first_cap)
                continue
            start = max(self._ring_from(ev) if ev.source == "slot" else ev.start, now)
            a0 = angle(start)
            a1 = a0 + max((end - start).total_seconds() / 3600 * 30, 3)
            if ev.source == "slot" and ctx.cfg.clock_slot_gradient:
                # a time slot on the hour ring: the same time-left colours, deadline = end of the slot
                time_left_arc(start, max(end, start + timedelta(minutes=6)), self._end(ev))
                cap(a0, time_left_color(left(start, self._end(ev))))
                cap(a1, time_left_color(0))
            else:
                draw.arc(bb, a0, a1, fill=color, width=width)
                cap(*(first_cap or (a0, color)))
                cap(a1, color)
            if ev.source == "slot" and ev.start > start:  # slot: bubble on the slot itself, not on its countdown
                s0 = angle(min(ev.start, end))
                a0 = max(a0, min(s0, a1 - 3))
            ra = math.radians((a0 + a1) / 2)  # icon bubble in the middle of the event itself
            px, py = cx + ring_r * math.cos(ra), cy + ring_r * math.sin(ra)
            br = width * 1.15
            draw.ellipse((px - br, py - br, px + br, py + br), fill=bubble_color(ev.icon, color))
            draw_icon(draw, ev.icon, (px, py), br * 1.35, readable_on(color))


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
        bubble = bubble_color(ev.icon, ev.color or th["accent"])
        draw.ellipse((icx - ir, cy - ir, icx + ir, cy + ir), fill=bubble)
        draw_icon(draw, ev.icon, (icx, cy), ir * 1.25, readable_on(bubble))
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
    """Vertical strip of the coming 7 days, one colour per day, today highlighted.

    The order follows week_start (monday / sunday first, or today on top);
    every tile shows the next occurrence of that weekday, so on sunday the
    'ma' tile is tomorrow."""
    name, default_weight = "week", 1.0

    def draw(self, draw, box, ctx):
        x0, y0, x1, y1 = box
        th = ctx.theme
        days = ctx.week_days or [ctx.now.date() + timedelta(days=i) for i in range(7)]
        cell_h = (y1 - y0) / 7
        w = x1 - x0
        for i, day in enumerate(days):
            wd = day.weekday()
            cy0 = y0 + i * cell_h
            is_today = day == ctx.now.date()
            inset = 0 if is_today else w * 0.1
            box_i = (x0 + inset, cy0 + cell_h * 0.06, x1 - inset, cy0 + cell_h * 0.94)
            col = day_color(ctx, wd)
            fill = hex_to_rgb(col) if is_today else mix(col, th["bg"], 0.55)
            draw.rounded_rectangle(box_i, radius=min(w, cell_h) * 0.22, fill=fill)
            if is_today:
                draw.rounded_rectangle(box_i, radius=min(w, cell_h) * 0.22, outline=th["ink"],
                                       width=max(3, int(w * 0.03)))
            bx0, by0, bx1, by1 = box_i
            txt_col = readable_on(col) if is_today else th["ink"]
            label = ctx.strings["days_short"][wd]
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
