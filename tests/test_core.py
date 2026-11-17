import json
import sys
import tempfile
import unittest
from datetime import date, datetime, time

from PIL import Image
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "frame_dayplanner"))

from app import puzzles  # noqa: E402
from app.config import load_config, parse_days, parse_layout  # noqa: E402
from app.events import events_for_day  # noqa: E402

TZ = ZoneInfo("Europe/Amsterdam")


def cfg_with(**opts):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(opts, f)
    return load_config(Path(f.name))


class ConfigTests(unittest.TestCase):
    def test_days(self):
        self.assertEqual(parse_days("mon-fri"), {0, 1, 2, 3, 4})
        self.assertEqual(parse_days("za,zo"), {5, 6})
        self.assertEqual(parse_days("fri-mon"), {4, 5, 6, 0})
        self.assertEqual(parse_days("daily"), set(range(7)))

    def test_schedule_window(self):
        cfg = cfg_with(schedule=[{"days": "mon-fri", "start": "07:00", "end": "08:30"},
                                 {"days": "sat", "start": "22:00", "end": "01:00"}])
        w, night = cfg.schedule
        self.assertTrue(w.contains(0, time(7, 0)))
        self.assertFalse(w.contains(0, time(8, 30)))
        self.assertFalse(w.contains(5, time(7, 30)))
        self.assertTrue(night.contains(5, time(23, 0)))
        self.assertTrue(night.contains(6, time(0, 30)))  # sunday after midnight

    def test_layout(self):
        cols = parse_layout([{"width": 80, "modules": "day, clock:3, sum | word"}])
        self.assertEqual(cols[0].rows, [[("day", None)], [("clock", 3.0)], [("sum", None), ("word", None)]])


class PuzzleTests(unittest.TestCase):
    def test_sounds_and_pattern(self):
        self.assertEqual(puzzles.sounds("boom"), ["b", "oo", "m"])
        self.assertEqual(puzzles.pattern("vis"), "mkm")
        self.assertEqual(puzzles.pattern("slak"), "mmkm")
        self.assertEqual(puzzles.pattern("kast"), "mkmm")
        self.assertEqual(puzzles.pattern("lach"), "mkm")

    def test_word_filters(self):
        cfg = cfg_with(word_patterns="mkm", word_max_letters=3, word_digraphs=False)
        for i in range(50):
            w = puzzles.pick_word(cfg, str(i))
            self.assertEqual(puzzles.pattern(w), "mkm")
            self.assertLessEqual(len(w), 3)
            self.assertTrue(all(len(s) == 1 for s in puzzles.sounds(w)), w)

    def test_word_list_is_klankzuiver_shaped(self):
        for w in puzzles.WORDS_NL:
            self.assertFalse(w[-1] in "dbgvz", f"{w} ends with a devoiced letter")

    def test_sums_within_bounds(self):
        cfg = cfg_with(sum_operations="+,-", sum_max=10, sum_terms=3)
        for i in range(300):
            s = puzzles.make_sum(cfg, str(i))
            expr = s.split("=")[0]
            nums = [int(x) for x in expr.replace("+", " ").replace("-", " ").split()]
            self.assertTrue(all(0 <= n <= 10 for n in nums), s)
            self.assertTrue(0 <= eval(expr) <= 10, s)

    def test_sums_never_use_zero(self):
        for opts in ({}, {"sum_operations": "+,-", "sum_terms": 3, "sum_max": 20}, {"sum_operations": "x,:"},
                     {"sum_operations": "-", "sum_max": 5}):
            cfg = cfg_with(**opts)
            for i in range(500):
                expr = puzzles.make_sum(cfg, str(i)).split("=")[0]
                nums = [int(x) for x in expr.replace("+", " ").replace("-", " ").replace("x", " ").replace(":", " ").split()]
                self.assertNotIn(0, nums, expr)

    def test_sum_stable_per_day(self):
        cfg = cfg_with()
        now = datetime(2026, 11, 16, 8, 0, tzinfo=TZ)
        later = now.replace(hour=9, minute=59)
        self.assertEqual(puzzles.make_sum(cfg, puzzles.seed_for(now, "daily")),
                         puzzles.make_sum(cfg, puzzles.seed_for(later, "daily")))


class EventTests(unittest.TestCase):
    def test_sources(self):
        cfg = cfg_with(
            events=[{"title": "Zwemdag", "when": "mon", "highlight": True},
                    {"title": "Oma", "when": "12-24"},
                    {"title": "Vakantie", "when": "2026-12-19..2027-01-03"}],
            calendar_exclude_words=["werk"],
            calendar_icon_rules=["voetbal=mdi:soccer"],
        )
        cal = [{"summary": "Voetbal training", "start": {"dateTime": "2026-12-24T15:00:00+01:00"},
                "end": {"dateTime": "2026-12-24T16:00:00+01:00"}},
               {"summary": "Werk overleg", "start": {"date": "2026-12-24"}, "end": {"date": "2026-12-25"}}]
        titles = [e.title for e in events_for_day(cfg, date(2026, 12, 24), TZ, cal)]
        self.assertIn("Oma", titles)
        self.assertIn("Vakantie", titles)
        self.assertIn("Voetbal training", titles)
        self.assertNotIn("Werk overleg", titles)
        evs = events_for_day(cfg, date(2026, 12, 24), TZ, cal)
        self.assertEqual(next(e for e in evs if e.title == "Voetbal training").icon, "mdi:soccer")
        # christmas + sinterklaas
        self.assertTrue(any(e.source == "holiday" for e in events_for_day(cfg, date(2026, 12, 25), TZ, [])))
        self.assertIn("Sinterklaas", [e.title for e in events_for_day(cfg, date(2026, 12, 5), TZ, [])])
        self.assertIn("Zwemdag", [e.title for e in events_for_day(cfg, date(2026, 11, 16), TZ, [])])


class MonthlyAndValidationTests(unittest.TestCase):
    def _days(self, when, y=2026, m=11):
        import calendar

        from app.config import EventRule
        from app.events import rule_matches

        return [d for d in range(1, calendar.monthrange(y, m)[1] + 1) if rule_matches(EventRule("x", when), date(y, m, d))]

    def test_repeat_types(self):
        self.assertEqual(self._days("mon"), [2, 9, 16, 23, 30])         # weekly
        self.assertEqual(self._days("monthly 15"), [15])                 # monthly, day
        self.assertEqual(self._days("monthly 31"), [30])                 # last day of a 30-day month
        self.assertEqual(self._days("monthly 31", 2026, 2), [28])
        self.assertEqual(self._days("monthly 1st wed"), [4])             # monthly, nth weekday
        self.assertEqual(self._days("monthly last fri"), [27])
        self.assertEqual(self._days("maandelijks laatste zo"), [29])
        self.assertEqual(self._days("11-11"), [11])                      # yearly
        self.assertEqual(self._days("2026-11-20"), [20])                 # once
        self.assertEqual(self._days("2026-11-28..2026-12-02"), [28, 29, 30])

    def test_strict_validation(self):
        from app.config import build_config

        for bad in ("monthly 32", "monthly 5th wed", "funday", "2026-02-30", "13-01"):
            with self.assertRaises(ValueError, msg=bad):
                build_config({"events": [{"title": "x", "when": bad}]}, strict=True)
        with self.assertRaises(ValueError):
            build_config({"schedule": [{"days": "mon-xyz", "start": "07:00", "end": "08:00"}]}, strict=True)
        build_config({"events": [{"title": "x", "when": "monthly 2 di; 12-24"}]}, strict=True)


class EventWindowTests(unittest.TestCase):
    def _ctrl(self, **opts):
        import tempfile as _t
        import os

        os.environ.setdefault("DATA_DIR", _t.mkdtemp())
        from app.config import build_config
        from app.controller import Controller
        from app.ha import HAClient

        ha = HAClient()
        ha.token = ""
        return Controller(build_config(opts), ha, TZ)

    def test_events_overrule_schedule(self):
        ev = [{"title": "Zwemles", "when": "mon", "start": "14:00", "end": "15:00", "leave_before": 45}]
        sched = [{"days": "mon-fri", "start": "07:00", "end": "08:30"}]
        c = self._ctrl(schedule=sched, events=ev)
        at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=TZ)  # noqa: E731  monday
        self.assertEqual(c.desired(at(7, 30)), (True, "schedule"))
        self.assertEqual(c.desired(at(12, 44)), (False, ""))
        self.assertEqual(c.desired(at(12, 45)), (True, "event"))   # 14:00 - 45 leave - 30 prep
        self.assertEqual(c.desired(at(14, 59)), (True, "event"))
        self.assertEqual(c.desired(at(15, 0)), (False, ""))
        self.assertEqual(c.event_window(at(13, 0)), "Zwemles")
        # tuesday: no event
        self.assertEqual(c.desired(datetime(2026, 11, 17, 13, 0, tzinfo=TZ)), (False, ""))

    def test_simulated_test_time(self):
        from unittest import mock

        c = self._ctrl(schedule=[])
        real = datetime(2026, 11, 15, 23, 28, 41, tzinfo=TZ)
        with mock.patch.object(type(c), "now", lambda self: real):
            at = c.parse_at("07:00")
            self.assertEqual(at, datetime(2026, 11, 16, 7, 0, tzinfo=TZ))        # next occurrence
            self.assertEqual(c.parse_at("23:50"), datetime(2026, 11, 15, 23, 50, tzinfo=TZ))
            self.assertEqual(c.parse_at("2026-11-20T16:30"), datetime(2026, 11, 20, 16, 30, tzinfo=TZ))
            self.assertIsNone(c.parse_at(""))
            c.start_test(10, at)
            shown = c.display_now()
            self.assertEqual((shown.day, shown.hour, shown.minute), (16, 7, 0))
            self.assertEqual(shown.second, 41)                                    # minutes flip with real time
            self.assertTrue(c.status_dict()["simulated"].startswith("2026-11-16T07:00"))
            c.stop()
            self.assertEqual(c.display_now(), real)

    def test_simulation_keeps_weather_memory(self):
        from app.config import build_config
        from app.ha import HAClient
        from app.planner import Planner

        cfg = build_config({"weather_entity": "weather.x"})
        p = Planner(HAClient())
        p._current = {"state": "sunny", "attributes": {"temperature": 9}}
        today = datetime(2026, 11, 15, 10, 0, tzinfo=TZ)
        p._weather_periods(cfg, today, {"periods": ["a", "b", "c"]})
        memo = dict(p._weather_memo)
        p._weather_periods(cfg, datetime(2026, 11, 16, 7, 0, tzinfo=TZ), {"periods": ["a", "b", "c"]}, simulated=True)
        self.assertEqual(p._weather_memo, memo)

    def test_setting_off_and_prep_time(self):
        ev = [{"title": "Voetbal", "when": "mon", "start": "17:00", "end": "18:00"}]
        at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=TZ)  # noqa: E731
        self.assertEqual(self._ctrl(events=ev, schedule=[], schedule_events=False).desired(at(17, 30)), (False, ""))
        c = self._ctrl(events=ev, schedule=[], schedule_event_minutes=10)
        self.assertEqual(c.desired(at(16, 49)), (False, ""))
        self.assertEqual(c.desired(at(16, 50)), (True, "event"))
        # events without time never switch it on
        c = self._ctrl(events=[{"title": "Oma jarig", "when": "mon"}], schedule=[])
        self.assertEqual(c.desired(at(12, 0)), (False, ""))


class SlotAndHiddenTests(unittest.TestCase):
    def _render(self, now, **opts):
        from app.ha import HAClient
        from app.planner import Planner
        from app.render.engine import render

        cfg = cfg_with(resolution="1080p", **opts)
        planner = Planner(HAClient())
        ctx = planner.build(cfg, now)
        with self.assertNoLogs(level="ERROR"):
            img = render(ctx)
        return ctx, img

    def test_slots_today_only_on_configured_days(self):
        slot = [{"label": "Naar school", "days": "mon-fri", "start": "08:15", "end": "08:25"}]
        ctx, _ = self._render(datetime(2026, 11, 16, 8, 2, tzinfo=TZ), clock_slots=slot)  # monday
        self.assertEqual(ctx.slots[0].title, "Naar school")
        self.assertEqual(ctx.slots[0].start.hour, 8)
        ctx, _ = self._render(datetime(2026, 11, 14, 8, 2, tzinfo=TZ), clock_slots=slot)  # saturday
        self.assertEqual([s.start.date().weekday() for s in ctx.slots], [])  # sat: none, sun: none

    def test_clock_slots_become_countdown_events(self):
        from app.config import migrate_options

        old = {"clock_slots": [{"label": "Naar school", "days": "mon-fri", "start": "08:00", "end": "08:30",
                                "icon": "mdi:school"}], "events": [{"title": "Zwemles", "when": "mon"}]}
        new = migrate_options(old)
        self.assertEqual(new["clock_slots"], [])
        self.assertEqual(new["events"][1], {"title": "Naar school", "when": "mon-fri", "start": "08:00",
                                            "end": "08:30", "icon": "mdi:school", "color": "#e0453a",
                                            "show": "countdown"})
        self.assertEqual(migrate_options(new), new)  # nothing left to move
        cfg = cfg_with(**old)
        self.assertEqual([r.show for r in cfg.events], [frozenset({"list", "week"}), frozenset({"countdown"})])

    def test_show_options(self):
        from app.config import build_config

        events = [
            {"title": "School", "when": "mon", "start": "08:00", "end": "08:30", "show": "countdown"},
            {"title": "Gym", "when": "mon", "icon": "mdi:basketball", "show": "list,countdown", "start": "08:10"},
            {"title": "Oma", "when": "mon", "icon": "mdi:account", "show": "week"},
            {"title": "Fruit", "when": "mon", "icon": "mdi:food-apple"},
        ]
        ctx, _ = self._render(datetime(2026, 11, 16, 7, 45, tzinfo=TZ), events=events, holidays_country="")
        from app.render.modules import EventsModule
        self.assertEqual([e.title for e in EventsModule()._visible(ctx)], ["Fruit", "Gym"])
        self.assertEqual({s.title for s in ctx.slots if s.day == date(2026, 11, 16)}, {"School", "Gym"})
        self.assertEqual(sorted(i for i, _c in ctx.week_icons[0]), ["mdi:account", "mdi:food-apple"])
        gym = next(s for s in ctx.slots if s.title == "Gym")
        self.assertEqual(gym.end, gym.start)  # countdown without end: counts down to the start
        with self.assertRaises(ValueError):
            build_config({"events": [{"title": "X", "when": "mon", "show": "clock"}]}, strict=True)

    def test_countdown_band_only_paints_the_real_range(self):
        from app.events import Event
        from app.render.modules import ClockModule

        at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=TZ)  # noqa: E731
        slot = Event("School", date(2026, 11, 16), icon="mdi:school", start=at(8, 0), end=at(8, 30), source="slot")
        ctx, _ = self._render(at(7, 45))
        arcs = []

        class Rec:
            def arc(self, bb, a0, a1, **kw):
                arcs.append((a0, a1))

            def ellipse(self, *a, **kw):
                pass
        ClockModule()._minute_band(Rec(), 0, 0, 100, slot, ctx)
        self.assertAlmostEqual(min(a for a, _ in arcs), -90)          # minute 0 (8:00), not 45 (now)
        self.assertLess(max(b for _, b in arcs), 90.5)                 # up to minute 30

    def test_slot_states_render(self):
        slot = [{"label": "Naar school", "days": "daily", "start": "08:15", "end": "08:25"}]
        for hm in ((7, 0), (8, 2), (8, 15), (8, 20), (8, 30), (23, 50)):
            self._render(datetime(2026, 11, 16, *hm, tzinfo=TZ), clock_slots=slot)

    def test_minute_mode_uses_24h_hour(self):
        from app.render.modules import ClockModule
        from app.events import Event

        clock = ClockModule()
        slot = Event("Naar school", date(2026, 11, 16), start=datetime(2026, 11, 16, 8, 15, tzinfo=TZ),
                     end=datetime(2026, 11, 16, 8, 25, tzinfo=TZ))
        at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=TZ)  # noqa: E731
        self.assertFalse(clock._minute_mode(slot, at(7, 59)))
        self.assertTrue(clock._minute_mode(slot, at(8, 0)))
        self.assertTrue(clock._minute_mode(slot, at(8, 24)))
        self.assertFalse(clock._minute_mode(slot, at(8, 25)))
        self.assertFalse(clock._minute_mode(slot, at(20, 15)))  # PM, not the 08:xx hour
        self.assertTrue(clock._in_half(slot, at(6, 0)))
        self.assertFalse(clock._in_half(slot, at(20, 0)))

    def test_week_strip_shows_upcoming_days(self):
        cfg_events = [{"title": "Gym", "when": "thu", "icon": "mdi:basketball"}]
        sunday = datetime(2026, 11, 15, 21, 0, tzinfo=TZ)
        ctx, _ = self._render(sunday, events=cfg_events)  # default: monday first
        self.assertEqual(ctx.week_days[0], date(2026, 11, 16))  # 'ma' = tomorrow
        self.assertEqual(ctx.week_days[2], date(2026, 11, 18))  # 'wo' = upcoming wednesday
        self.assertEqual(ctx.week_days[6], date(2026, 11, 15))  # 'zo' = today
        self.assertEqual(ctx.week_icons[3][0][0], "mdi:basketball")
        self.assertTrue(all(d >= sunday.date() for d in ctx.week_days))
        self.assertNotIn("candle", [i for icons in ctx.week_icons for i, _c in icons])  # no past Sint-Maarten
        ctx, _ = self._render(sunday, week_start="sunday")
        self.assertEqual(ctx.week_days[0], date(2026, 11, 15))
        self.assertEqual(ctx.week_days[1], date(2026, 11, 16))
        ctx, _ = self._render(datetime(2026, 11, 19, 8, 0, tzinfo=TZ), week_start="today")
        self.assertEqual(ctx.week_days[0], date(2026, 11, 19))
        self.assertEqual(ctx.week_days[6], date(2026, 11, 25))

    def test_letter_or_word_as_icon(self):
        from PIL import ImageDraw

        from app.render.canvas import draw_icon, icon_char, icon_text

        self.assertIsNotNone(icon_char("mdi:swim"))
        self.assertIsNotNone(icon_char("swim"))       # bare lower-case icon name still works
        self.assertIsNone(icon_char("GYM"))           # capitals = text
        self.assertIsNone(icon_char("A"))
        self.assertEqual(icon_text("text:swim"), "swim")
        for name in ("A", "GYM", "3", "text:swim"):
            img = Image.new("RGB", (200, 200), "white")
            self.assertTrue(draw_icon(ImageDraw.Draw(img), name, (100, 100), 120, "black"))
            self.assertIsNotNone(img.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox(), name)
        events = [{"title": "Gym", "when": "mon", "icon": "GYM"}, {"title": "Juf Anna", "when": "mon", "icon": "A"}]
        self._render(datetime(2026, 11, 16, 8, 5, tzinfo=TZ), events=events,
                     clock_slots=[{"label": "School", "days": "mon", "start": "08:15", "end": "08:25", "icon": "S"}])

    def test_emoji_icons_and_search(self):
        from PIL import ImageDraw

        from app.render.canvas import draw_icon, emoji_value
        from app.web import search_icons

        self.assertEqual(emoji_value("🐒"), "🐒")
        self.assertIsNone(emoji_value("mdi:swim"))
        self.assertIsNone(emoji_value("A"))
        img = Image.new("RGB", (200, 200), "white")
        self.assertTrue(draw_icon(ImageDraw.Draw(img), "🐒", (100, 100), 120, "black"))
        colours = img.getcolors(maxcolors=1 << 16)
        self.assertGreater(len(colours), 20)  # drawn in colour, not as a single-colour glyph
        self.assertEqual(search_icons("aap")[0]["v"], "🐒")          # Dutch name
        self.assertIn("🐵", [r["v"] for r in search_icons("monkey")])
        self.assertIn("mdi:swim", [r["v"] for r in search_icons("swim")])
        events = [{"title": "Dierentuin", "when": "mon", "icon": "🐒", "start": "10:00", "end": "12:00"}]
        self._render(datetime(2026, 11, 16, 8, 5, tzinfo=TZ), events=events, hidden_icons="🐒, mdi:owl")

    def test_leave_before(self):
        from app.config import build_config
        from app.events import events_for_day

        cfg = build_config({"events": [{"title": "Zwemles", "when": "mon", "start": "14:00", "end": "15:00",
                                        "leave_before": 45}]}, strict=True)
        ev = events_for_day(cfg, date(2026, 11, 16), TZ, [])[0]
        self.assertEqual(ev.leave, datetime(2026, 11, 16, 13, 15, tzinfo=TZ))
        with self.assertRaises(ValueError):
            build_config({"events": [{"title": "x", "when": "mon", "start": "14:00", "leave_before": 999}]}, strict=True)
        # renders before, during and after the lead-in, and when only the lead-in is in this half of the day
        for hm in ((11, 0), (13, 20), (13, 59), (14, 30)):
            self._render(datetime(2026, 11, 16, *hm, tzinfo=TZ), events=[
                {"title": "Zwemles", "when": "mon", "start": "14:00", "end": "15:00", "leave_before": 45},
                {"title": "Lunch", "when": "mon", "start": "12:10", "end": "12:40", "leave_before": 30}])

    def test_leave_before_minute_band_and_label(self):
        events = [{"title": "Zwemles", "when": "mon", "start": "14:00", "end": "15:00", "leave_before": 45}]
        ctx, _ = self._render(datetime(2026, 11, 16, 13, 30, tzinfo=TZ), events=events, clock_slots=[])
        leave = [s for s in ctx.slots if s.source == "leave"]
        self.assertEqual(len(leave), 1)
        self.assertEqual((leave[0].start.hour, leave[0].start.minute, leave[0].end.hour), (13, 15, 14))
        self.assertEqual(leave[0].title, "Zwemles: vertrekken")
        from app.render.modules import ClockModule

        clock = ClockModule()
        self.assertTrue(clock._minute_mode(leave[0], datetime(2026, 11, 16, 13, 30, tzinfo=TZ)))
        self.assertFalse(clock._minute_mode(leave[0], datetime(2026, 11, 16, 12, 30, tzinfo=TZ)))
        self.assertFalse(clock._minute_mode(leave[0], datetime(2026, 11, 16, 14, 0, tzinfo=TZ)))

    def test_textured_themes(self):
        from app.render.canvas import THEMES, contrast
        from app.render.modules import day_color

        for theme in ("paper-texture", "kraft", "chalkboard"):
            ctx, img = self._render(datetime(2026, 11, 15, 8, 5, tzinfo=TZ), theme=theme)
            self.assertEqual(img.size, (1080, 1920))
            # textured: the background is not one flat colour any more
            corner = img.crop((0, 0, 200, 200)).convert("L")
            self.assertGreater(len(corner.getcolors(maxcolors=4096) or []), 10, theme)
            # stable: same frame twice (texture is seeded, not random per render)
            _, again = self._render(datetime(2026, 11, 15, 8, 5, tzinfo=TZ), theme=theme)
            self.assertEqual(img.tobytes(), again.tobytes())
        # weekend tiles stay visible on dark backgrounds
        for theme in ("dark", "chalkboard"):
            ctx, _ = self._render(datetime(2026, 11, 15, 8, 5, tzinfo=TZ), theme=theme)
            self.assertGreaterEqual(contrast(day_color(ctx, 6), THEMES[theme]["bg"]), 1.6)

    def test_leave_event_drawn_once(self):
        """During the hour of leaving the event is on the minute band only, not also on the hour ring."""
        from unittest import mock

        from app.render.modules import ClockModule

        events = [{"title": "TEST", "when": "sun", "start": "23:35", "leave_before": 20}]
        drawn = []
        orig = ClockModule._hour_arcs
        with mock.patch.object(ClockModule, "_hour_arcs", lambda self, d, cx, cy, r, o, items, ctx:
                               (drawn.append([i.title for i in items]), orig(self, d, cx, cy, r, o, items, ctx))):
            ctx, _ = self._render(datetime(2026, 11, 15, 23, 6, tzinfo=TZ), events=events, clock_slots=[])
        self.assertEqual(drawn, [])  # nothing left for the hour ring
        leave = [s for s in ctx.slots if s.source == "leave"][0]
        self.assertIs(leave.ref, ctx.events[0])
        drawn.clear()
        with mock.patch.object(ClockModule, "_hour_arcs", lambda self, d, cx, cy, r, o, items, ctx:
                               (drawn.append([i.title for i in items]), orig(self, d, cx, cy, r, o, items, ctx))):
            self._render(datetime(2026, 11, 15, 22, 40, tzinfo=TZ), events=events, clock_slots=[])
        self.assertEqual(drawn, [["TEST"]])  # before that hour: on the hour ring

    def test_time_left_colours(self):
        from app.render.canvas import hex_to_rgb
        from app.render.modules import TIME_LEFT, time_left_color

        green, orange, red, purple = (hex_to_rgb(c) for _m, c in TIME_LEFT)
        self.assertEqual(time_left_color(25), green)     # 8:05 for 8:30
        self.assertEqual(time_left_color(12.5), orange)  # 8:17:30
        self.assertEqual(time_left_color(7.5), red)      # 8:22:30
        self.assertEqual(time_left_color(1), purple)     # 8:29
        # in between: blended, not muddy (stays saturated)
        import colorsys
        r, g, b = (v / 255 for v in time_left_color(15))
        self.assertGreater(colorsys.rgb_to_hls(r, g, b)[2], 0.5)

    def test_outer_ring_only_within_the_hour(self):
        from app.events import Event
        from app.render.modules import ClockModule

        clock = ClockModule()
        at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=TZ)  # noqa: E731
        voetbal = Event("Voetbal", date(2026, 11, 16), start=at(17, 0), end=at(18, 0))
        zwem = Event("Zwemles", date(2026, 11, 16), start=at(14, 0), end=at(15, 0), leave=at(13, 15))
        slot = Event("School", date(2026, 11, 16), start=at(8, 0), end=at(8, 30), source="slot")
        ctx, _ = self._render(at(15, 59))
        self.assertFalse(clock._soon(voetbal, ctx))           # 61 min before
        ctx.now = at(16, 0)
        self.assertTrue(clock._soon(voetbal, ctx))            # 60 min before
        ctx.now = at(17, 30)
        self.assertTrue(clock._soon(voetbal, ctx))            # running
        ctx.now = at(12, 45)                                   # long prep: screen is on, ring not yet
        self.assertFalse(clock._soon(zwem, ctx))
        ctx.now = at(13, 0)                                    # always 60 minutes before the start
        self.assertTrue(clock._soon(zwem, ctx))
        ctx.now = at(7, 29)                                    # slot: 60 minutes before its end
        self.assertFalse(clock._soon(slot, ctx))
        ctx.now = at(7, 30)
        self.assertTrue(clock._soon(slot, ctx))
        self.assertEqual(clock._ring_from(slot), at(7, 30))     # ring starts at 7:30, not 8:00
        self.assertEqual(clock._ring_from(zwem), at(13, 15))
        # the slot countdown is drawn on the minute band (7:30-8:30), not as a stub on the hour ring
        self.assertFalse(clock._minute_mode(slot, at(7, 29)))
        self.assertTrue(clock._minute_mode(slot, at(7, 45)))
        self.assertTrue(clock._minute_mode(slot, at(8, 10)))

    def test_readable_text(self):
        from app.render.canvas import readable_on

        self.assertEqual(readable_on("#f2c230"), "#2b2622")  # yellow -> dark text
        self.assertEqual(readable_on("#2f6fd6"), "#ffffff")  # blue -> white text
        self.assertEqual(readable_on("#3a3a3a"), "#ffffff")

    def test_daily_forecast_is_used(self):
        from app.ha import HAClient
        from app.planner import Planner

        cfg = cfg_with(weather_entity="weather.buienradar")
        p = Planner(HAClient())
        p._forecast = [{"datetime": "2026-11-16T00:00:00+01:00", "condition": "rainy", "temperature": 9},
                       {"datetime": "2026-11-17T00:00:00+01:00", "condition": "sunny", "temperature": 12}]
        periods = p._weather_periods(cfg, datetime(2026, 11, 16, 7, 0, tzinfo=TZ), {"periods": ["a", "b", "c"]})
        self.assertEqual([x["condition"] for x in periods], ["rainy", "rainy", "rainy"])

    def test_history_uses_last_updated(self):
        from app.ha import HAClient
        from app.planner import Planner

        p = Planner(HAClient())
        # cloudy all day: last_changed stays at midnight, only last_updated moves
        p._history = [
            {"state": "cloudy", "attributes": {"temperature": 4}, "last_changed": "2026-11-15T00:00:00+01:00",
             "last_updated": "2026-11-15T00:00:00+01:00"},
            {"state": "cloudy", "attributes": {"temperature": 7}, "last_changed": "2026-11-15T00:00:00+01:00",
             "last_updated": "2026-11-15T08:50:00+01:00"},
            {"state": "cloudy", "attributes": {"temperature": 10}, "last_changed": "2026-11-15T00:00:00+01:00",
             "last_updated": "2026-11-15T13:55:00+01:00"},
            {"state": "cloudy", "attributes": {"temperature": 8}, "last_changed": "2026-11-15T00:00:00+01:00",
             "last_updated": "2026-11-15T20:00:00+01:00"},
        ]
        now = datetime(2026, 11, 15, 21, 0, tzinfo=TZ)
        self.assertEqual(p._from_history(now, 9)["temperature"], 7)
        self.assertEqual(p._from_history(now, 14)["temperature"], 10)

    def test_hidden_figure_stays_out_of_elements(self):
        from PIL import ImageChops, ImageDraw

        from app.render.canvas import THEMES

        for theme in ("paper", "dark"):
            for day in range(15, 22):
                now = datetime(2026, 11, day, 8, 5, tzinfo=TZ)
                opts = dict(theme=theme, hidden_size=5,
                            clock_slots=[{"label": "School", "days": "daily", "start": "08:15", "end": "08:25"}])
                ctx, on = self._render(now, **opts)
                _, off = self._render(now, **dict(opts, hidden_enabled=False))
                box = ImageChops.difference(on, off).getbbox()
                self.assertIsNotNone(box, f"no figure drawn on day {day}")
                # everything under the figure must be plain background ...
                region = off.crop(box)
                bg = Image.new("RGB", region.size, THEMES[theme]["bg"])
                self.assertIsNone(ImageChops.difference(region, bg).point(lambda v: 255 if v > 4 else 0).getbbox(),
                                  f"figure covers an element on day {day} ({theme})")
                # ... and no figure pixel inside the clock circle
                changed = ImageChops.difference(on, off).convert("L").point(lambda v: 255 if v else 0)
                for shape, circle in ctx.blocked:
                    mask = Image.new("L", on.size, 0)
                    ImageDraw.Draw(mask).ellipse(circle, fill=255)
                    self.assertIsNone(ImageChops.multiply(changed, mask).getbbox(),
                                      f"figure inside the clock on day {day} ({theme})")

    def test_hidden_colour_follows_theme(self):
        from app.render.canvas import THEMES, hex_to_rgb
        from app.render.engine import hidden_color

        for theme in ("paper", "dark"):
            for value in ("", "#e0453a"):  # empty and the old default both follow the theme
                ctx, _ = self._render(datetime(2026, 11, 16, 8, 0, tzinfo=TZ), theme=theme, hidden_color=value)
                ink, bg, got = (hex_to_rgb(THEMES[theme]["ink"]), hex_to_rgb(THEMES[theme]["bg"]),
                                hidden_color(ctx))
                self.assertLess(sum(abs(a - b) for a, b in zip(got, ink)), sum(abs(a - b) for a, b in zip(got, bg)))
        ctx, _ = self._render(datetime(2026, 11, 16, 8, 0, tzinfo=TZ), hidden_color="#2f6fd6")
        self.assertEqual(hidden_color(ctx), "#2f6fd6")

    def test_hidden_same_spot_all_day_and_own_picture(self):
        from PIL import Image

        d = Path(tempfile.mkdtemp())
        Image.new("RGBA", (64, 64), (0, 200, 0, 255)).save(d / "fig.png")
        base = dict(hidden_image=str(d), hidden_size=8, clock_slots=[])
        _, a = self._render(datetime(2026, 11, 16, 8, 0, tzinfo=TZ), **base)
        _, b = self._render(datetime(2026, 11, 16, 8, 0, tzinfo=TZ), **base)
        _, off = self._render(datetime(2026, 11, 16, 8, 0, tzinfo=TZ), **dict(base, hidden_enabled=False))
        self.assertEqual(a.tobytes(), b.tobytes())
        self.assertNotEqual(a.tobytes(), off.tobytes())
        green = dict((c, n) for n, c in a.getcolors(maxcolors=1 << 24)).get((0, 200, 0), 0)
        self.assertGreater(green, 1000)  # the picture is drawn


if __name__ == "__main__":
    unittest.main()


class WakeTests(unittest.TestCase):
    def _tv(self, states, art=(False, True)):
        """FrameTV with a scripted power state; records what it did."""
        from unittest import mock

        from app.tv import FrameTV

        tv = FrameTV("192.168.1.50")
        calls, seq, art_seq = [], list(states), list(art)
        tv.power_state = lambda: (seq.pop(0) if len(seq) > 1 else seq[0], "A0:D7:F3:27:B0:B0")
        tv.send_wol = lambda mac: calls.append("wol")
        tv.power_key = lambda: calls.append("power")
        tv.reachable = lambda: True
        tv.art_mode_on = lambda: art_seq.pop(0) if len(art_seq) > 1 else art_seq[0]
        tv._call = lambda name, *a: calls.append(name)
        return tv, calls, mock.patch("app.tv.time.sleep", lambda s: None)

    def test_already_on_does_nothing(self):
        tv, calls, nosleep = self._tv(["on"])
        with nosleep:
            self.assertEqual(tv.wake(""), "already on")
        self.assertEqual(calls, [])

    def test_off_wakes_with_wol_then_art_mode(self):
        tv, calls, nosleep = self._tv(["off", "off", "on"])
        with nosleep:
            self.assertIn("woke up", tv.wake("A0:D7:F3:27:B0:B0", wait=0.5))
        self.assertEqual(calls[0], "wol")
        self.assertIn("set_artmode", calls)
        self.assertNotIn("power", calls)

    def test_standby_falls_back_to_power_key(self):
        tv, calls, nosleep = self._tv(["standby"], art=(True,))  # wake-on-LAN is ignored in standby

        def press():
            calls.append("power")
            tv.power_state = lambda: ("on", "A0:D7:F3:27:B0:B0")
        tv.power_key = press
        with nosleep:
            result = tv.wake("A0:D7:F3:27:B0:B0", wait=0.2)
        self.assertIn("power", calls)
        self.assertNotIn("set_artmode", calls)  # came up in art mode already
        self.assertIn("woke up", result)

    def test_magic_packet(self):
        from unittest import mock

        from app.tv import FrameTV

        sent = []
        with mock.patch("app.tv.socket.socket") as sock:
            sock.return_value.__enter__.return_value.sendto = lambda p, t: sent.append((p, t))
            FrameTV("192.168.200.49").send_wol("a0-d7-f3-27-b0-b0")
        packet = sent[0][0]
        self.assertEqual(packet[:6], b"\xff" * 6)
        self.assertEqual(packet[6:12], bytes.fromhex("a0d7f327b0b0"))
        self.assertEqual(len(packet), 102)
        self.assertIn(("192.168.200.255", 9), [t for _p, t in sent])
        with self.assertRaises(ValueError):
            FrameTV("x").send_wol("nope")

    def test_controller_wakes_at_most_three_times_per_window(self):
        import asyncio
        from unittest import mock

        from app import controller as ctl
        from app.ha import HAClient

        with tempfile.TemporaryDirectory() as d, mock.patch.object(ctl, "STATE_FILE", Path(d) / "state.json"):
            c = ctl.Controller(cfg_with(tv_host="192.168.1.50"), HAClient(), TZ)
            c.tv.power_state = lambda: ("standby", "A0:D7:F3:27:B0:B0")
            woke = []
            c.tv.wake = lambda mac: woke.append(mac) or "woke up"
            for _ in range(5):
                asyncio.run(c._wake_tv())
            self.assertEqual(woke, ["A0:D7:F3:27:B0:B0"] * 3)
            self.assertEqual(c.state["tv_mac"], "A0:D7:F3:27:B0:B0")
            c.cfg = cfg_with(tv_host="192.168.1.50", wake_tv=False)
            c._wake_tries = 0
            asyncio.run(c._wake_tv())
            self.assertEqual(len(woke), 3)  # setting off: never
