"""Load and normalise the add-on options (/data/options.json)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, time
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
    "day_background": True,
    "only_in_art_mode": True,
    "wake_tv": True,
    "tv_mac": "",
    "restore_previous_art": True,
    "update_every_minutes": 1,
    "lead_seconds": 4,
    "schedule": [{"days": "mon-fri", "start": "07:00", "end": "08:30"}],
    "schedule_events": True,
    "schedule_event_minutes": 30,
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
    "clock_slots": [],
    "clock_slot_gradient": True,
    "clock_slot_countdown_minutes": 60,
    "hidden_enabled": True,
    "hidden_icons": "mdi:owl, mdi:ladybug, mdi:penguin, mdi:snail, mdi:bee, mdi:duck",
    "hidden_image": "",
    "hidden_color": "",  # empty = follow the theme
    "hidden_size": 5,
    "hidden_count": 1,
    "weather_entity": "",
    "events": [],
    "calendar_entities": [],
    "calendar_include_words": [],
    "calendar_exclude_words": [],
    "calendar_icon_rules": [],
    "holidays_country": "NL",
    "holidays_extra": True,
    "max_events": 4,
    "week_start": "monday",
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
    leave_before: int = 0  # minutes: 'need to leave' lead-in on the clock
    show: frozenset = frozenset(("list", "week"))  # list, week, countdown (see SHOW_OPTIONS)


# where an event shows up: the events list, the week strip, and/or as a countdown on the clock
# (label below the clock + band on the minute scale, coloured by the minutes left: the old 'time slots')
SHOW_OPTIONS = ("list", "week", "countdown")
DEFAULT_SHOW = "list,week"


def parse_show(value) -> frozenset:
    """'list,week,countdown' -> set.  Missing = list + week; an empty string = nowhere."""
    if value is None:
        value = DEFAULT_SHOW
    items = {p.strip().lower() for p in str(value).split(",") if p.strip()}
    unknown = items - set(SHOW_OPTIONS)
    if unknown:
        raise ValueError(f"unknown show option {', '.join(sorted(unknown))} (use {', '.join(SHOW_OPTIONS)})")
    return frozenset(items)


def migrate_options(options: dict) -> dict:
    """Old 'clock_slots' become events that only show as a countdown on the clock."""
    slots = (options or {}).get("clock_slots") or []
    if not slots:
        return options
    out = dict(options)
    events = list(out.get("events") or [])
    for sl in slots:
        ev = {"title": sl.get("label") or "", "when": sl.get("days") or "daily", "start": sl.get("start"),
              "end": sl.get("end") or sl.get("start"), "icon": sl.get("icon") or "mdi:clock-outline",
              "color": sl.get("color") or "#e0453a", "show": "countdown"}
        events.append({k: v for k, v in ev.items() if v not in (None, "")})
    out["events"], out["clock_slots"] = events, []
    return out


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


def _day(name: str) -> int:
    try:
        return DAY_ALIASES[name]
    except KeyError:
        raise ValueError(f"unknown day '{name}'") from None


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
            ia, ib = _day(a), _day(b)
            i = ia
            while True:
                days.add(i)
                if i == ib:
                    break
                i = (i + 1) % 7
        else:
            days.add(_day(part))
    return days


_NTH = {"1": 1, "1st": 1, "first": 1, "eerste": 1, "2": 2, "2nd": 2, "second": 2, "tweede": 2,
        "3": 3, "3rd": 3, "third": 3, "derde": 3, "4": 4, "4th": 4, "fourth": 4, "vierde": 4,
        "last": -1, "laatste": -1}


def parse_monthly(part: str):
    """'monthly 15' -> ('day', 15); 'monthly 1st wed' / 'monthly last fri' -> ('nth', n, weekday).
    Returns None when part is not a monthly rule; raises ValueError when it is but invalid."""
    m = re.fullmatch(r"(?:monthly|maandelijks)\s+(.+)", part.strip().lower())
    if not m:
        return None
    rest = m.group(1).split()
    if len(rest) == 1 and rest[0].isdigit():
        day = int(rest[0])
        if not 1 <= day <= 31:
            raise ValueError(f"day of month must be 1-31, not {day}")
        return ("day", day)
    if len(rest) == 2 and rest[0] in _NTH:
        return ("nth", _NTH[rest[0]], _day(rest[1]))
    raise ValueError(f"cannot understand '{part}', use e.g. 'monthly 15' or 'monthly 1st wed'")


def monthly_matches(rule, d: date) -> bool:
    import calendar

    last_day = calendar.monthrange(d.year, d.month)[1]
    if rule[0] == "day":
        return d.day == min(rule[1], last_day)  # 31 in a short month = last day
    _, n, weekday = rule
    if d.weekday() != weekday:
        return False
    if n == -1:
        return d.day + 7 > last_day
    return (d.day - 1) // 7 + 1 == n


def validate_when(value: str) -> str:
    """Check an event 'when' (weekdays, YYYY-MM-DD, MM-DD, a..b, joined with ';')."""
    for part in re.split(r"\s*;\s*", value.strip().lower()):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(\s*\.\.\s*\d{4}-\d{2}-\d{2})?", part):
            for d in re.findall(r"\d{4}-\d{2}-\d{2}", part):
                date.fromisoformat(d)
        elif m := re.fullmatch(r"(\d{2})-(\d{2})", part):
            date(2024, int(m.group(1)), int(m.group(2)))  # 2024: allows 02-29
        elif parse_monthly(part) is None:
            parse_days(part)
    return value


def _minutes(value) -> int:
    if value in (None, ""):
        return 0
    n = int(value)
    if not 0 <= n <= 240:
        raise ValueError(f"leave before must be 0-240 minutes, not {n}")
    return n


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
    options = json.loads(path.read_text()) if path.exists() else {}
    return build_config(options)


def build_config(options: dict, strict: bool = False) -> Config:
    """Options dict -> Config.  With strict, invalid entries raise ValueError
    (used when saving from the settings page); otherwise they are skipped."""
    raw = dict(DEFAULTS)
    raw.update({k: v for k, v in migrate_options(options or {}).items() if v is not None and k in DEFAULTS})
    cfg = Config(raw=raw)
    errors: list[str] = []

    def guard(where: str, fn):
        try:
            return fn()
        except (ValueError, KeyError) as exc:
            errors.append(f"{where}: {exc}")
            return None

    for i, w in enumerate(raw.get("schedule") or [], 1):
        item = guard(f"schedule {i}", lambda w=w: ScheduleWindow(
            parse_days(w.get("days", "daily")), parse_time(w.get("start")), parse_time(w.get("end"))))
        if item and item.start and item.end:
            cfg.schedule.append(item)
    for i, e in enumerate(raw.get("events") or [], 1):
        if not e.get("title"):
            continue
        item = guard(f"event '{e.get('title')}'", lambda e=e: EventRule(
            title=e["title"], when=validate_when(str(e.get("when", "daily"))), icon=e.get("icon") or "",
            start=parse_time(e.get("start")), end=parse_time(e.get("end")),
            highlight=bool(e.get("highlight", False)), color=e.get("color") or "",
            leave_before=_minutes(e.get("leave_before")), show=parse_show(e.get("show"))))
        if item:
            cfg.events.append(item)
    if strict and errors:
        raise ValueError("; ".join(errors))
    return cfg
