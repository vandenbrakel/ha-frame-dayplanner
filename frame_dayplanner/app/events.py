"""Event sources: configured (weekly / custom) events, public holidays,
extra Dutch 'kid' days and Home Assistant calendars."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import Config, EventRule, monthly_matches, parse_days, parse_monthly

_LOGGER = logging.getLogger(__name__)

PALETTE = ["#2f6fd6", "#3a9b4a", "#f08a24", "#8e5bd6", "#e0453a", "#1f9aa8", "#d6458f"]


@dataclass
class Event:
    title: str
    day: date
    icon: str = ""
    color: str = ""
    start: datetime | None = None
    end: datetime | None = None
    highlight: bool = False
    source: str = "custom"
    leave: datetime | None = None  # time to leave (before start)

    @property
    def timed(self) -> bool:
        return self.start is not None


def color_for(title: str) -> str:
    return PALETTE[sum(map(ord, title.lower())) % len(PALETTE)]


# --------------------------------------------------------------------------
# Configured events
# --------------------------------------------------------------------------
def rule_matches(rule: EventRule, d: date) -> bool:
    when = rule.when.strip().lower()
    for part in re.split(r"\s*;\s*", when):
        if m := re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s*\.\.\s*(\d{4}-\d{2}-\d{2})", part):
            if date.fromisoformat(m.group(1)) <= d <= date.fromisoformat(m.group(2)):
                return True
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", part):
            if d == date.fromisoformat(part):
                return True
        elif m := re.fullmatch(r"(\d{2})-(\d{2})", part):
            if (d.month, d.day) == (int(m.group(1)), int(m.group(2))):
                return True
        elif part.startswith(("monthly", "maandelijks")):
            try:
                if monthly_matches(parse_monthly(part), d):
                    return True
            except ValueError:
                _LOGGER.warning("Event '%s': cannot understand when='%s'", rule.title, rule.when)
        else:
            try:
                if d.weekday() in parse_days(part):
                    return True
            except (KeyError, ValueError):
                _LOGGER.warning("Event '%s': cannot understand when='%s'", rule.title, rule.when)
    return False


def configured_events(cfg: Config, d: date, tz) -> list[Event]:
    out = []
    for rule in cfg.events:
        if not rule_matches(rule, d):
            continue
        start = datetime.combine(d, rule.start, tz) if rule.start else None
        end = datetime.combine(d, rule.end, tz) if rule.end else None
        if start and not end:
            end = start + timedelta(hours=1)
        leave = start - timedelta(minutes=rule.leave_before) if start and rule.leave_before else None
        out.append(Event(rule.title, d, rule.icon or "calendar-star", rule.color or color_for(rule.title),
                         start, end, rule.highlight, "custom", leave))
    return out


# --------------------------------------------------------------------------
# Holidays
# --------------------------------------------------------------------------
def _nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _extra_days(year: int, lang: str) -> dict[date, tuple[str, str]]:
    nl = lang == "nl"
    return {
        date(year, 2, 14): ("Valentijnsdag" if nl else "Valentine's Day", "heart"),
        _nth_weekday(year, 5, 6, 2): ("Moederdag" if nl else "Mother's Day", "flower"),
        _nth_weekday(year, 6, 6, 3): ("Vaderdag" if nl else "Father's Day", "tie"),
        date(year, 10, 4): ("Dierendag" if nl else "Animal Day", "paw"),
        date(year, 11, 11): ("Sint-Maarten" if nl else "St. Martin's Day", "candle"),
        date(year, 12, 5): ("Sinterklaas" if nl else "Sinterklaas", "gift"),
    }


HOLIDAY_ICONS = {
    "kerst": "pine-tree", "christmas": "pine-tree", "pasen": "egg-easter", "easter": "egg-easter",
    "koning": "crown", "king": "crown", "bevrijding": "flag", "liberation": "flag",
    "nieuwjaar": "party-popper", "new year": "party-popper", "oudejaar": "party-popper",
}


def holiday_events(cfg: Config, d: date) -> list[Event]:
    out: list[Event] = []
    country = (cfg.holidays_country or "").strip().upper()
    if not country:
        return out
    try:
        import holidays

        try:
            cal = holidays.country_holidays(country, years=d.year, language=cfg.language)
        except Exception:  # language not supported for this country
            cal = holidays.country_holidays(country, years=d.year)
        for name in cal.get_list(d):
            icon = next((i for k, i in HOLIDAY_ICONS.items() if k in name.lower()), "party-popper")
            out.append(Event(name, d, icon, "#e0453a", highlight=True, source="holiday"))
    except Exception as exc:  # pragma: no cover - library / country errors
        _LOGGER.warning("Holidays for %s failed: %s", country, exc)
    if cfg.holidays_extra and country in ("NL", "BE"):
        extra = _extra_days(d.year, cfg.language).get(d)
        if extra:
            out.append(Event(extra[0], d, extra[1], "#d6458f", highlight=False, source="holiday"))
    return out


# --------------------------------------------------------------------------
# HA calendar events (raw dicts from /api/calendars/<entity>)
# --------------------------------------------------------------------------
def _icon_rules(cfg: Config) -> list[tuple[str, str]]:
    rules = []
    for item in cfg.calendar_icon_rules or []:
        if "=" in str(item):
            k, v = str(item).split("=", 1)
            rules.append((k.strip().lower(), v.strip()))
    return rules


def apply_icon_rules(cfg: Config, ev: Event) -> Event:
    for word, icon in _icon_rules(cfg):
        if word and word in ev.title.lower():
            ev.icon = icon
            break
    return ev


def passes_filters(cfg: Config, title: str) -> bool:
    t = title.lower()
    include = [w.lower() for w in cfg.calendar_include_words or [] if w]
    exclude = [w.lower() for w in cfg.calendar_exclude_words or [] if w]
    if include and not any(w in t for w in include):
        return False
    return not any(w in t for w in exclude)


def calendar_events(cfg: Config, raw: list[dict], d: date, tz) -> list[Event]:
    out = []
    for item in raw:
        title = (item.get("summary") or "").strip()
        if not title or not passes_filters(cfg, title):
            continue
        s, e = item.get("start", {}), item.get("end", {})
        if "dateTime" in s:
            start = datetime.fromisoformat(s["dateTime"]).astimezone(tz)
            end = datetime.fromisoformat(e.get("dateTime", s["dateTime"])).astimezone(tz)
            if not (start.date() <= d <= (end - timedelta(seconds=1)).date()):
                continue
            ev = Event(title, d, "calendar-star", color_for(title), start, end, False, "calendar")
            if start.date() != d:  # multi-day timed event, show as all-day on later days
                ev.start = ev.end = None
        else:
            sd = date.fromisoformat(s.get("date"))
            ed = date.fromisoformat(e.get("date")) if e.get("date") else sd + timedelta(days=1)
            if not (sd <= d < ed):
                continue
            ev = Event(title, d, "calendar-star", color_for(title), None, None, False, "calendar")
        out.append(apply_icon_rules(cfg, ev))
    return out


def events_for_day(cfg: Config, d: date, tz, calendar_raw: list[dict]) -> list[Event]:
    evs = holiday_events(cfg, d) + configured_events(cfg, d, tz) + calendar_events(cfg, calendar_raw, d, tz)
    # de-duplicate by title
    seen, unique = set(), []
    for ev in evs:
        key = ev.title.lower()
        if key not in seen:
            seen.add(key)
            unique.append(ev)
    # highlights first, then all-day, then by start time
    unique.sort(key=lambda e: (not e.highlight, e.timed, e.start or datetime.combine(d, time.min, tz)))
    return unique
