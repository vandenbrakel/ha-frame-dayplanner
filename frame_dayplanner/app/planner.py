"""Collect data (weather, events, puzzles) and build the render context."""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, timedelta

from . import puzzles
from .config import Config
from .events import Event, events_for_day
from .ha import HAClient
from .i18n import t
from .render.engine import RenderContext

_LOGGER = logging.getLogger(__name__)

# (start hour, end hour, representative hour)
PERIODS = [(6, 12, 9), (12, 18, 14), (18, 24, 19)]
WEATHER_TTL = 15 * 60
CALENDAR_TTL = 5 * 60


def week_days(cfg: Config, today: date) -> list[date]:
    """Dates for the week strip: weekdays in the configured order (monday /
    sunday first, or today first), each the next occurrence from today."""
    first = {"monday": 0, "sunday": 6}.get(cfg.week_start, today.weekday())
    order = [(first + i) % 7 for i in range(7)]
    return [today + timedelta(days=(wd - today.weekday()) % 7) for wd in order]


class Planner:
    def __init__(self, ha: HAClient) -> None:
        self.ha = ha
        self._forecast: list[dict] = []
        self._current: dict | None = None
        self._weather_at = 0.0
        self._weather_memo: dict[tuple[date, int], dict] = {}
        self.weather_info: dict = {"entity": None}
        self._history: list[dict] = []
        self._weather_entity: str | None = None
        self._calendar: list[dict] = []
        self._calendar_key: tuple = ()
        self._calendar_at = 0.0

    # ------------------------------------------------------------------ data
    async def refresh(self, cfg: Config, now: datetime, force=False) -> None:
        mono = _time.monotonic()
        if cfg.weather_entity != self._weather_entity:
            self._weather_entity, self._weather_at = cfg.weather_entity, 0.0
            self._current, self._forecast, self._history = None, [], []
            self._weather_memo.clear()
        if cfg.weather_entity and self.ha.available and (force or mono - self._weather_at > WEATHER_TTL):
            self._current = await self.ha.state(cfg.weather_entity)
            midnight = datetime.combine(now.date(), datetime.min.time(), now.tzinfo)
            self._history = await self.ha.history(cfg.weather_entity, midnight, now)
            kind, self._forecast, errors = await self.ha.forecast(cfg.weather_entity)
            self._weather_at = mono
            self.weather_info = {
                "entity": cfg.weather_entity,
                "found": self._current is not None,
                "state": (self._current or {}).get("state"),
                "forecast_type": kind or None,
                "forecast_entries": len(self._forecast),
                "errors": errors,
            }
            if not self._current:
                _LOGGER.warning("Weather entity %s not found in Home Assistant", cfg.weather_entity)
            elif not self._forecast:
                _LOGGER.warning("Weather %s has no forecast: %s", cfg.weather_entity, "; ".join(errors))
            else:
                _LOGGER.info("Weather %s: %s, %d %s forecast entries", cfg.weather_entity,
                             self._current.get("state"), len(self._forecast), kind)
        if cfg.calendar_entities and self.ha.available:
            # covers the week strip (which may start before today) and the next days
            week_start = datetime.combine(now.date(), datetime.min.time(), now.tzinfo)  # today + 7 days
            key = (tuple(cfg.calendar_entities), week_start)
            if force or key != self._calendar_key or mono - self._calendar_at > CALENDAR_TTL:
                items: list[dict] = []
                for ent in cfg.calendar_entities:
                    items += await self.ha.calendar(
                        ent, week_start, datetime.combine(now.date() + timedelta(days=8), datetime.min.time(), now.tzinfo))
                self._calendar, self._calendar_key, self._calendar_at = items, key, mono

    def _weather_periods(self, cfg: Config, now: datetime, strings: dict) -> list[dict]:
        if not cfg.weather_entity:
            return []
        out = []
        for i, (h0, h1, rep) in enumerate(PERIODS):
            p = {"label": strings["periods"][i], "condition": None, "temperature": None,
                 "past": now.hour >= h1, "current": h0 <= now.hour < h1}
            key = (now.date(), i)
            if p["current"] and self._current:
                p["condition"] = self._current.get("state")
                p["temperature"] = self._current.get("attributes", {}).get("temperature")
            elif not p["past"]:
                best = self._pick_forecast(now, rep, evening=i == 2)
                if best:
                    p["condition"], p["temperature"] = best.get("condition"), best.get("temperature")
            if p["condition"]:
                self._weather_memo[key] = {"condition": p["condition"], "temperature": p["temperature"]}
            elif key in self._weather_memo:
                p.update(self._weather_memo[key])
            elif p["past"]:
                past = self._from_history(now, rep)
                if past:
                    p.update(past)
            out.append(p)
        # forget old days
        self._weather_memo = {k: v for k, v in self._weather_memo.items() if k[0] >= now.date()}
        return out

    def _from_history(self, now: datetime, rep_hour: int) -> dict | None:
        """What the weather entity said at rep_hour today (for periods already past)."""
        target = datetime.combine(now.date(), datetime.min.time(), now.tzinfo).replace(hour=rep_hour)
        entries = []
        for st in self._history:
            if st.get("state") in (None, "unknown", "unavailable"):
                continue
            try:  # last_updated also moves when only the temperature changes
                at = datetime.fromisoformat(st.get("last_updated") or st.get("last_changed"))
            except (TypeError, ValueError):
                continue
            entries.append((at, st))
        entries.sort(key=lambda e: e[0])
        best = None
        for at, st in entries:
            if at <= target or best is None:
                best = st
            if at > target:
                break
        if not best:
            return None
        return {"condition": best["state"], "temperature": best.get("attributes", {}).get("temperature")}

    def _pick_forecast(self, now: datetime, rep_hour: int, evening: bool) -> dict | None:
        """Best forecast entry for today at rep_hour (works for hourly, twice_daily, daily)."""
        target = datetime.combine(now.date(), datetime.min.time(), now.tzinfo).replace(hour=rep_hour)
        best, best_score = None, None
        for f in self._forecast:
            try:
                ft = datetime.fromisoformat(f["datetime"]).astimezone(now.tzinfo)
            except (KeyError, ValueError, TypeError):
                continue
            score = abs((ft - target).total_seconds())
            if "is_daytime" in f and f["is_daytime"] == evening:
                score += 6 * 3600  # twice_daily: prefer day for morning/afternoon, night for evening
            if ft.date() != now.date():
                score += 24 * 3600  # daily: prefer today's entry
            if best_score is None or score < best_score:
                best, best_score = f, score
        return best if best_score is not None and best_score < 30 * 3600 else None

    def event_windows(self, cfg: Config, now: datetime) -> list[tuple[datetime, datetime, str]]:
        """(begin, end, title) for timed events today: prep time before (leaving / start) until the end."""
        if not cfg.schedule_events:
            return []
        prep = timedelta(minutes=int(cfg.schedule_event_minutes))
        out = []
        for d in (now.date() - timedelta(days=1), now.date()):  # yesterday: events running past midnight
            for ev in events_for_day(cfg, d, now.tzinfo, self._calendar):
                if ev.start and ev.end:
                    out.append(((ev.leave or ev.start) - prep, ev.end, ev.title))
        return out

    # --------------------------------------------------------------- context
    def build(self, cfg: Config, now: datetime, session_id: str = "") -> RenderContext:
        strings = t(cfg.language)
        tz = now.tzinfo
        events = events_for_day(cfg, now.date(), tz, self._calendar)
        days = week_days(cfg, now.date())
        week_icons = []
        for i in range(7):
            d = days[i]
            evs = events_for_day(cfg, d, tz, self._calendar)
            week_icons.append([(e.icon, e.color) for e in evs if e.icon])
        slots = []
        for d in (now.date(), now.date() + timedelta(days=1)):
            for sl in cfg.slots:
                if d.weekday() not in sl.days:
                    continue
                start = datetime.combine(d, sl.start, tz)
                end = datetime.combine(d, sl.end, tz)
                if end < start:
                    end += timedelta(days=1)
                slots.append(Event(sl.label, d, sl.icon or "clock-outline", sl.color or "#e0453a", start, end, False, "slot"))
        # events with 'leave before' get an automatic slot from leaving time until the start
        for ev in events:
            if ev.leave and ev.start:
                label = strings["leave_label"].format(title=ev.title)
                slots.append(Event(label, ev.day, ev.icon, ev.color, ev.leave, ev.start, False, "leave"))
        slots.sort(key=lambda s: s.start)
        seed = puzzles.seed_for(now, cfg.puzzle_refresh, session_id)
        word = puzzles.pick_word(cfg, seed)
        return RenderContext(
            cfg=cfg,
            now=now,
            strings=strings,
            weather=self._weather_periods(cfg, now, strings),
            events=events,
            slots=slots,
            seed=seed,
            week_icons=week_icons,
            week_days=days,
            sum_text=puzzles.make_sum(cfg, seed),
            word=word,
            word_sounds=puzzles.sounds(word),
        )
