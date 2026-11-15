"""Ingress web UI: status, live preview, test / stop buttons."""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from .controller import Controller

_LOGGER = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"


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
