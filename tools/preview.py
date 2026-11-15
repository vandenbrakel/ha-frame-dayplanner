"""Render sample previews without a TV or Home Assistant.

    python tools/preview.py [YYYY-MM-DDTHH:MM]

Writes docs/preview_portrait.png and docs/preview_landscape.png.
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "frame_dayplanner"))

from app.config import load_config  # noqa: E402
from app.ha import HAClient  # noqa: E402
from app.planner import Planner  # noqa: E402
from app.render.engine import render  # noqa: E402

tz = ZoneInfo("Europe/Amsterdam")
now = datetime.fromisoformat(sys.argv[1]).replace(tzinfo=tz) if len(sys.argv) > 1 else datetime(2026, 11, 16, 8, 40, tzinfo=tz)
out = ROOT / "docs"
out.mkdir(exist_ok=True)

for orientation in ("portrait", "landscape"):
    opts = {
        "orientation": orientation,
        "weather_entity": "weather.home",
        "events": [
            {"title": "Zwemdag", "when": "mon", "icon": "mdi:swim", "start": "14:00", "end": "17:00",
             "highlight": True, "color": "#1f9aa8"},
            {"title": "Spelen met Fedde", "when": now.date().isoformat(), "icon": "mdi:account-group"},
            {"title": "Gym", "when": "thu", "icon": "mdi:basketball"},
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(opts, f)
    cfg = load_config(Path(f.name))
    planner = Planner(HAClient())
    planner._current = {"state": "rainy", "attributes": {"temperature": 7.4}}
    planner._forecast = [
        {"datetime": now.replace(hour=14, minute=0).isoformat(), "condition": "sunny", "temperature": 11},
        {"datetime": now.replace(hour=19, minute=0).isoformat(), "condition": "cloudy", "temperature": 8},
    ]
    img = render(planner.build(cfg, now))
    img.resize((img.width // 4, img.height // 4)).save(out / f"preview_{orientation}.png")
    print("wrote", out / f"preview_{orientation}.png")
