"""Collect data (weather, events, puzzles) and build the render context."""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, timedelta

from . import puzzles
from .config import Config
from .events import events_for_day
from .ha import HAClient
from .i18n import t
from .render.engine import RenderContext

_LOGGER = logging.getLogger(__name__)

# (start hour, end hour, representative hour)
PERIODS = [(6, 12, 9), (12, 18, 14), (18, 24, 19)]
WEATHER_TTL = 15 * 60
CALENDAR_TTL = 5 * 60


class Planner:
    def __init__(self, ha: HAClient) -> None:
        self.ha = ha
        self._forecast: list[dict] = []
        self._current: dict | None = None
        self._weather_at = 0.0
        self._weather_memo: dict[tuple[date, int], dict] = {}
        self._calendar: list[dict] = []
        self._calendar_key: tuple = ()
        self._calendar_at = 0.0

    # ------------------------------------------------------------------ data
    async def refresh(self, cfg: Config, now: datetime, force=False) -> None:
        mono = _time.monotonic()
        if cfg.weather_entity and self.ha.available and (force or mono - self._weather_at > WEATHER_TTL):
            self._current = await self.ha.state(cfg.weather_entity)
            self._forecast = await self.ha.forecast(cfg.weather_entity)
            self._weather_at = mono
        if cfg.calendar_entities and self.ha.available:
            week_start = datetime.combine(now.date() - timedelta(days=now.weekday()), datetime.min.time(), now.tzinfo)
            key = (tuple(cfg.calendar_entities), week_start)
            if force or key != self._calendar_key or mono - self._calendar_at > CALENDAR_TTL:
                items: list[dict] = []
                for ent in cfg.calendar_entities:
                    items += await self.ha.calendar(ent, week_start, week_start + timedelta(days=7))
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
                target = datetime.combine(now.date(), datetime.min.time(), now.tzinfo).replace(hour=rep)
                best, best_d = None, None
                for f in self._forecast:
                    try:
                        ft = datetime.fromisoformat(f["datetime"]).astimezone(now.tzinfo)
                    except (KeyError, ValueError):
                        continue
                    d = abs((ft - target).total_seconds())
                    if best_d is None or d < best_d:
                        best, best_d = f, d
                if best and best_d < 12 * 3600:
                    p["condition"], p["temperature"] = best.get("condition"), best.get("temperature")
            if p["condition"]:
                self._weather_memo[key] = {"condition": p["condition"], "temperature": p["temperature"]}
            elif key in self._weather_memo:
                p.update(self._weather_memo[key])
            out.append(p)
        # forget old days
        self._weather_memo = {k: v for k, v in self._weather_memo.items() if k[0] >= now.date()}
        return out

    # --------------------------------------------------------------- context
    def build(self, cfg: Config, now: datetime, session_id: str = "") -> RenderContext:
        strings = t(cfg.language)
        tz = now.tzinfo
        events = events_for_day(cfg, now.date(), tz, self._calendar)
        week_start = now.date() - timedelta(days=now.weekday())
        week_icons = []
        for i in range(7):
            d = week_start + timedelta(days=i)
            evs = events_for_day(cfg, d, tz, self._calendar)
            week_icons.append([(e.icon, e.color) for e in evs if e.icon])
        seed = puzzles.seed_for(now, cfg.puzzle_refresh, session_id)
        word = puzzles.pick_word(cfg, seed)
        return RenderContext(
            cfg=cfg,
            now=now,
            strings=strings,
            weather=self._weather_periods(cfg, now, strings),
            events=events,
            week_icons=week_icons,
            sum_text=puzzles.make_sum(cfg, seed),
            word=word,
            word_sounds=puzzles.sounds(word),
        )
