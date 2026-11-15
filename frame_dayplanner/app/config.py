"""Load and normalise the add-on options (/data/options.json)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("OPTIONS_FILE", DATA_DIR / "options.json"))

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_ALIASES = {
    # english
    "mon": 0, "monday": 0, "tue": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
    # dutch
    "ma": 0, "maandag": 0, "di": 1, "dinsdag": 1, "wo": 2, "woensdag": 2,
    "do": 3, "donderdag": 3, "vr": 4, "vrijdag": 4, "za": 5, "zaterdag": 5,
    "zo": 6, "zondag": 6,
}

DEFAULTS: dict = {
    "tv_host": "",
    "tv_port": 8002,
    "orientation": "portrait",
    "resolution": "4k",
    "matte": "none",
    "language": "nl",
    "theme": "paper",
    "only_in_art_mode": True,
    "restore_previous_art": True,
    "update_every_minutes": 1,
    "lead_seconds": 4,
    "schedule": [{"days": "mon-fri", "start": "07:00", "end": "08:30"}],
    "layout_portrait": [
        {"width": 80, "modules": "day, date, clock, weather, events, sum | word"},
        {"width": 20, "modules": "week"},
    ],
    "layout_landscape": [
        {"width": 42, "modules": "day, date, clock"},
        {"width": 44, "modules": "weather, events, sum | word"},
        {"width": 14, "modules": "week"},
    ],
    "clock_style": "analog",
    "clock_minute_numbers": False,
    "clock_event_arcs": True,
    "weather_entity": "",
    "events": [],
    "calendar_entities": [],
    "calendar_include_words": [],
    "calendar_exclude_words": [],
    "calendar_icon_rules": [],
    "holidays_country": "NL",
    "holidays_extra": True,
    "max_events": 4,
    "week_colors": "#2f6fd6, #3a9b4a, #f08a24, #f2c230, #e0453a, #3a3a3a, #3a3a3a",
    "sum_operations": "+",
    "sum_max": 10,
    "sum_terms": 2,
    "sum_missing": "answer",
    "word_patterns": "mkm",
    "word_max_letters": 4,
    "word_digraphs": True,
    "word_case": "lower",
    "word_show_sounds": False,
    "word_custom_list": [],
    "puzzle_refresh": "daily",
}


@dataclass
class ScheduleWindow:
    days: set[int]
    start: time
    end: time

    def contains(self, weekday: int, t: time) -> bool:
        if self.start <= self.end:
            return weekday in self.days and self.start <= t < self.end
        # window crosses midnight
        prev = (weekday - 1) % 7
        return (weekday in self.days and t >= self.start) or (prev in self.days and t < self.end)


@dataclass
class EventRule:
    title: str
    when: str
    icon: str = ""
    start: time | None = None
    end: time | None = None
    highlight: bool = False
    color: str = ""


@dataclass
class Column:
    width: float
    rows: list[list[tuple[str, float | None]]]  # each row: [(module, weight)]


@dataclass
class Config:
    raw: dict
    schedule: list[ScheduleWindow] = field(default_factory=list)
    events: list[EventRule] = field(default_factory=list)

    def __getattr__(self, item):
        try:
            return self.raw[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    @property
    def columns(self) -> list[Column]:
        key = "layout_portrait" if self.raw["orientation"] == "portrait" else "layout_landscape"
        return parse_layout(self.raw.get(key) or DEFAULTS[key])

    @property
    def size(self) -> tuple[int, int]:
        long_side, short_side = (3840, 2160) if self.raw["resolution"] == "4k" else (1920, 1080)
        if self.raw["orientation"] == "portrait":
            return short_side, long_side
        return long_side, short_side


def parse_time(value: str | None) -> time | None:
    if not value:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(value))
    if not m:
        raise ValueError(f"Invalid time '{value}', use HH:MM")
    h, mi = int(m.group(1)), int(m.group(2))
    if h == 24 and mi == 0:
        return time(23, 59, 59)
    return time(h, mi)


def parse_days(value: str) -> set[int]:
    """'mon-fri', 'ma,wo,vr', 'daily', 'weekend' -> set of weekday ints."""
    value = (value or "").strip().lower()
    if value in ("", "daily", "all", "every day", "elke dag", "dagelijks"):
        return set(range(7))
    if value in ("weekdays", "werkdagen", "doordeweeks"):
        return set(range(5))
    if value in ("weekend", "weekends"):
        return {5, 6}
    days: set[int] = set()
    for part in re.split(r"[,\s]+", value):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            ia, ib = DAY_ALIASES[a], DAY_ALIASES[b]
            i = ia
            while True:
                days.add(i)
                if i == ib:
                    break
                i = (i + 1) % 7
        else:
            days.add(DAY_ALIASES[part])
    return days


def parse_layout(columns: list[dict]) -> list[Column]:
    result = []
    for col in columns:
        rows = []
        for row in str(col.get("modules", "")).split(","):
            row = row.strip()
            if not row:
                continue
            items = []
            for item in row.split("|"):
                item = item.strip()
                if not item:
                    continue
                name, _, weight = item.partition(":")
                items.append((name.strip().lower(), float(weight) if weight else None))
            rows.append(items)
        result.append(Column(width=float(col.get("width", 50)), rows=rows))
    return result


def load_config(path: Path = OPTIONS_FILE) -> Config:
    raw = dict(DEFAULTS)
    if path.exists():
        raw.update({k: v for k, v in json.loads(path.read_text()).items() if v is not None})
    cfg = Config(raw=raw)
    for w in raw.get("schedule") or []:
        start, end = parse_time(w.get("start")), parse_time(w.get("end"))
        if start and end:
            cfg.schedule.append(ScheduleWindow(parse_days(w.get("days", "daily")), start, end))
    for e in raw.get("events") or []:
        if not e.get("title"):
            continue
        cfg.events.append(
            EventRule(
                title=e["title"],
                when=str(e.get("when", "daily")),
                icon=e.get("icon") or "",
                start=parse_time(e.get("start")),
                end=parse_time(e.get("end")),
                highlight=bool(e.get("highlight", False)),
                color=e.get("color") or "",
            )
        )
    return cfg
