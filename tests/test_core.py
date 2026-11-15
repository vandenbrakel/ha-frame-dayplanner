import json
import sys
import tempfile
import unittest
from datetime import date, datetime, time
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

    def test_slot_states_render(self):
        slot = [{"label": "Naar school", "days": "daily", "start": "08:15", "end": "08:25"}]
        for hm in ((7, 0), (8, 2), (8, 15), (8, 20), (8, 30), (23, 50)):
            self._render(datetime(2026, 11, 16, *hm, tzinfo=TZ), clock_slots=slot)

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
        green = sum(1 for p in a.getdata() if p == (0, 200, 0))
        self.assertGreater(green, 1000)  # the picture is drawn


if __name__ == "__main__":
    unittest.main()
