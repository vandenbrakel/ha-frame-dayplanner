"""Ingress web UI: status, live preview, test / stop buttons."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from aiohttp import web

from .config import DEFAULTS, build_config
from .controller import Controller
from .render.canvas import FONT_DIR, _codepoints

_LOGGER = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
TRANSLATIONS = Path(__file__).resolve().parent.parent / "translations" / "en.yaml"

# shown first in the icon picker when nothing is typed yet
POPULAR_ICONS = [
    "swim", "soccer", "basketball", "bicycle", "school", "bag-personal", "walk", "run", "music",
    "piano", "guitar-acoustic", "palette", "brush", "book-open-variant", "cake-variant", "gift",
    "party-popper", "account-group", "human-male-female-child", "home", "car", "bus", "airplane",
    "tooth", "doctor", "bed", "food-apple", "silverware-fork-knife", "horse", "dog", "cat", "tree",
    "pine-tree", "star", "heart", "flower", "calendar-star", "clock-outline", "alarm", "shower",
    "tshirt-crew", "teddy-bear", "owl", "ladybug", "penguin", "duck", "bee", "snail", "unicorn",
]


# Dutch search words -> MDI names (icon names are English)
SYNONYMS = {
    "fiets": ["bicycle"], "zwem": ["swim", "pool"], "voetbal": ["soccer"], "school": ["school", "bag-personal"],
    "muziek": ["music", "piano", "guitar-acoustic"], "verjaardag": ["cake-variant", "party-popper", "gift"],
    "jarig": ["cake-variant"], "tand": ["tooth"], "dokter": ["doctor", "hospital-box"], "huisarts": ["doctor"],
    "auto": ["car"], "eten": ["food-apple", "silverware-fork-knife"], "paard": ["horse"], "hond": ["dog"],
    "kat": ["cat"], "poes": ["cat"], "boom": ["tree"], "ster": ["star"], "hart": ["heart"], "bloem": ["flower"],
    "slapen": ["bed"], "bed": ["bed"], "douche": ["shower"], "gym": ["dumbbell", "gymnastics", "run"],
    "turnen": ["gymnastics"], "dans": ["dance-ballroom", "human-female-dance"], "ballet": ["human-female-dance"],
    "teken": ["palette", "draw"], "knutsel": ["palette", "brush"], "lezen": ["book-open-variant"],
    "boek": ["book-open-variant"], "cadeau": ["gift"], "feest": ["party-popper"], "oma": ["human-cane"],
    "opa": ["human-cane"], "vriend": ["account-group"], "spelen": ["toy-brick", "account-group"],
    "trein": ["train"], "vliegtuig": ["airplane"], "vakantie": ["airplane", "beach"], "wandel": ["walk"],
    "rennen": ["run"], "zon": ["weather-sunny"], "kerst": ["pine-tree"], "pasen": ["egg-easter"],
    "gitaar": ["guitar-acoustic"], "judo": ["karate"], "boodschap": ["cart", "shopping"],
    "winkel": ["shopping"], "step": ["scooter"], "schaats": ["skate"], "tv": ["television"],
    "spel": ["gamepad-variant"], "kerk": ["church"], "bios": ["movie"], "theater": ["theater"],
    "baby": ["baby-carriage"], "zwembad": ["pool", "swim"], "hockey": ["hockey-sticks"],
}


def _labels() -> dict:
    try:
        import yaml

        return yaml.safe_load(TRANSLATIONS.read_text()).get("configuration", {})
    except Exception as exc:
        _LOGGER.debug("No translations: %s", exc)
        return {}


@lru_cache(maxsize=1)
def _emoji_rows() -> list[list[str]]:
    try:
        return json.loads((FONT_DIR / "emoji_index.json").read_text(encoding="utf-8"))
    except OSError:
        return []


POPULAR_EMOJI = list("🐒🦁🐘🦒🐶🐱🐰🦄🐟🦋⚽🏊🚲🎨🎵🎂🎁🍎🍕🦷🏫📚🚗🚌✈️🏖️🎄🐣")


def _mdi(name: str, cp: int) -> dict:
    return {"v": f"mdi:{name}", "l": name, "t": "mdi", "cp": cp}


def _emo(row: list[str]) -> dict:
    return {"v": row[0], "l": row[2] or row[1], "t": "emoji", "en": row[1]}


def search_icons(query: str, limit: int = 60) -> list[dict]:
    """Search Material Design Icons and colour emoji (English and Dutch names)."""
    cps = _codepoints()
    rows = _emoji_rows()
    q = query.strip().lower().removeprefix("mdi:").strip()
    if not q:
        by_char = {r[0]: r for r in rows}
        emo = [_emo(by_char[e]) for e in POPULAR_EMOJI if e in by_char]
        return [_mdi(n, cps[n]) for n in POPULAR_ICONS[:30] if n in cps] + emo
    qd = q.replace(" ", "-")

    # mdi: Dutch synonyms, then names starting with q, then words / substrings
    synonyms = [n for word, names in SYNONYMS.items() if word.startswith(qd) or qd.startswith(word)
                for n in names if n in cps]
    starts = sorted((n for n in cps if n.startswith(qd) and n not in synonyms), key=len)
    seen = set(synonyms) | set(starts)
    words = sorted(n for n in cps if f"-{qd}" in n and n not in seen)
    rest = sorted(n for n in cps if qd in n and n not in seen and n not in words)
    mdi_hits = list(dict.fromkeys(synonyms + starts)) + words + rest

    # emoji: name (en / nl) starting with q, a word of the name, then keywords
    def score(r):
        names = f"{r[1]} {r[2]}".lower()
        if any(n.startswith(q) for n in (r[1].lower(), r[2].lower())):
            return 0
        if re.search(rf"\b{re.escape(q)}", names):
            return 1
        if re.search(rf"\b{re.escape(q)}", r[3]):
            return 2
        return 3 if q in names else None
    scored = sorted(((sc, i) for i, r in enumerate(rows) if (sc := score(r)) is not None))
    emoji_hits = [rows[i] for _sc, i in scored]
    strong = [r for r in emoji_hits if score(r) <= 1]
    weak = [r for r in emoji_hits if score(r) > 1]

    # interleave so both kinds show up near the top
    out = [_emo(r) for r in strong[:12]] + [_mdi(n, cps[n]) for n in mdi_hits[:24]]
    out += [_emo(r) for r in strong[12:] + weak] + [_mdi(n, cps[n]) for n in mdi_hits[24:]]
    return out[:limit]


def make_app(ctrl: Controller) -> web.Application:
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(_req):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @routes.get("/api/status")
    async def status(_req):
        return web.json_response(ctrl.status_dict())

    @routes.get("/api/preview.jpg")
    async def preview(_req):
        jpeg = await ctrl.render_jpeg(force_refresh=True)
        return web.Response(body=jpeg, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @routes.get("/api/weather")
    async def weather(_req):
        await ctrl.planner.refresh(ctrl.cfg, ctrl.now(), force=True)
        entities = await ctrl.ha.weather_entities() if ctrl.ha.available else []
        return web.json_response({"configured": ctrl.planner.weather_info, "entities": entities,
                                  "ha_connected": ctrl.ha.available})

    @routes.get("/fonts/mdi.ttf")
    async def mdi_font(_req):
        return web.FileResponse(FONT_DIR / "materialdesignicons-webfont.ttf",
                                headers={"Cache-Control": "max-age=86400"})

    @routes.get("/api/options")
    async def get_options(_req):
        return web.json_response({"options": {k: ctrl.cfg.raw.get(k) for k in DEFAULTS},
                                  "labels": _labels()})

    @routes.post("/api/options")
    async def save_options(req):
        options = (await req.json()).get("options") or {}
        # merge into the current options, so a partial update never resets other settings
        options = {**{k: ctrl.cfg.raw.get(k) for k in DEFAULTS},
                   **{k: v for k, v in options.items() if k in DEFAULTS}}
        try:
            cfg = build_config(options, strict=True)
            await ctrl.ha.save_addon_options(options)
        except ValueError as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=400)
        ctrl.reconfigure(cfg)
        return web.json_response({"ok": True, "message": "Saved and applied"})

    @routes.post("/api/preview")
    async def preview_options(req):
        """Render unsaved options (live preview while editing)."""
        options = (await req.json()).get("options") or {}
        cfg = build_config({**ctrl.cfg.raw, **{k: v for k, v in options.items() if k in DEFAULTS}})
        jpeg = await ctrl.render_jpeg(cfg=cfg)
        return web.Response(body=jpeg, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @routes.get("/api/icons")
    async def icons(req):
        return web.json_response(search_icons(req.query.get("q", "")))

    @routes.get("/api/entities")
    async def entities(req):
        domain = req.query.get("domain", "")
        if not domain.isidentifier() or not ctrl.ha.available:
            return web.json_response([])
        return web.json_response(await ctrl.ha.entities(domain))

    @routes.post("/api/test")
    async def test(req):
        data = await req.json() if req.can_read_body else {}
        ctrl.start_test(float(data.get("minutes", 10)))
        return web.json_response(ctrl.status_dict())

    @routes.post("/api/stop")
    async def stop(_req):
        ctrl.stop()
        return web.json_response(ctrl.status_dict())

    @routes.post("/api/pair")
    async def pair(_req):
        """Connect to the TV so it shows the 'allow' prompt and we get a token."""
        import asyncio

        try:
            if not await asyncio.to_thread(ctrl.tv.reachable):
                return web.json_response({"ok": False, "message": "TV not reachable"})
            art = await asyncio.to_thread(ctrl.tv.art_mode_on)
            cur = await asyncio.to_thread(ctrl.tv.current_content_id)
            return web.json_response({"ok": True, "message": f"Connected. Art mode: {'on' if art else 'off'}, current art: {cur}"})
        except Exception as exc:
            return web.json_response({"ok": False, "message": f"{exc} - accept the prompt on the TV and try again"})

    app = web.Application()
    app.add_routes(routes)
    return app
