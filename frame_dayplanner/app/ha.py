"""Minimal Home Assistant API client (REST + websocket events).

Inside the add-on the Supervisor proxy is used with SUPERVISOR_TOKEN.  For
local development set HA_URL (e.g. http://homeassistant.local:8123) and
HA_TOKEN (a long-lived access token).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Awaitable, Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)


class HAClient:
    def __init__(self) -> None:
        if os.environ.get("HA_URL"):
            base = os.environ["HA_URL"].rstrip("/")
            self.rest = f"{base}/api"
            self.ws_url = base.replace("http", "ws", 1) + "/api/websocket"
            self.token = os.environ.get("HA_TOKEN", "")
        else:
            self.rest = "http://supervisor/core/api"
            self.ws_url = "ws://supervisor/core/websocket"
            self.token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

    @property
    def available(self) -> bool:
        return bool(self.token)

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()

    async def _get(self, path: str, params: dict | None = None):
        async with self.session.get(f"{self.rest}{path}", params=params) as r:
            r.raise_for_status()
            return await r.json()

    async def _post(self, path: str, payload: dict):
        async with self.session.post(f"{self.rest}{path}", json=payload) as r:
            r.raise_for_status()
            return await r.json()

    async def time_zone(self) -> str | None:
        try:
            return (await self._get("/config")).get("time_zone")
        except Exception as exc:
            _LOGGER.warning("Could not read HA config: %s", exc)
            return None

    async def state(self, entity_id: str) -> dict | None:
        try:
            return await self._get(f"/states/{entity_id}")
        except Exception as exc:
            _LOGGER.warning("Could not read %s: %s", entity_id, exc)
            return None

    async def forecast(self, entity_id: str) -> tuple[str, list[dict], list[str]]:
        """Hourly forecast, falling back to twice_daily / daily.

        Returns (forecast type, entries, errors)."""
        errors = []
        for kind in ("hourly", "twice_daily", "daily"):
            try:
                data = await self._post("/services/weather/get_forecasts?return_response",
                                        {"entity_id": entity_id, "type": kind})
                fc = data.get("service_response", {}).get(entity_id, {}).get("forecast") or []
                if fc:
                    return kind, fc, errors
                errors.append(f"{kind}: empty")
            except Exception as exc:
                errors.append(f"{kind}: {exc}")
        return "", [], errors

    async def weather_entities(self) -> list[dict]:
        """All weather entities with the forecast types they support."""
        try:
            states = await self._get("/states")
        except Exception as exc:
            _LOGGER.warning("Could not list states: %s", exc)
            return []
        out = []
        for st in states:
            if not st.get("entity_id", "").startswith("weather."):
                continue
            feats = int(st.get("attributes", {}).get("supported_features") or 0)
            out.append({
                "entity_id": st["entity_id"],
                "name": st.get("attributes", {}).get("friendly_name", ""),
                "state": st.get("state"),
                "temperature": st.get("attributes", {}).get("temperature"),
                "forecasts": [n for bit, n in ((2, "hourly"), (4, "twice_daily"), (1, "daily")) if feats & bit],
            })
        return out

    async def calendar(self, entity_id: str, start: datetime, end: datetime) -> list[dict]:
        try:
            return await self._get(f"/calendars/{entity_id}",
                                   {"start": start.isoformat(), "end": end.isoformat()})
        except Exception as exc:
            _LOGGER.warning("Calendar %s failed: %s", entity_id, exc)
            return []

    async def set_state(self, entity_id: str, state: str, attributes: dict) -> None:
        try:
            await self._post(f"/states/{entity_id}", {"state": state, "attributes": attributes})
        except Exception as exc:
            _LOGGER.debug("Could not publish %s: %s", entity_id, exc)

    async def listen_events(self, event_type: str, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Subscribe to an HA event type forever (reconnects automatically)."""
        while True:
            try:
                async with self.session.ws_connect(self.ws_url, heartbeat=30) as ws:
                    msg = await ws.receive_json()  # auth_required
                    await ws.send_json({"type": "auth", "access_token": self.token})
                    msg = await ws.receive_json()
                    if msg.get("type") != "auth_ok":
                        raise RuntimeError(f"websocket auth failed: {msg}")
                    await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": event_type})
                    _LOGGER.info("Listening for '%s' events in Home Assistant", event_type)
                    async for raw in ws:
                        if raw.type != aiohttp.WSMsgType.TEXT:
                            continue
                        msg = json.loads(raw.data)
                        if msg.get("type") == "event":
                            try:
                                await callback(msg["event"].get("data") or {})
                            except Exception:
                                _LOGGER.exception("Event handler failed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.debug("Event websocket error: %s", exc)
            await asyncio.sleep(15)
