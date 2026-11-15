"""Session control: schedule / test runs, pushing frames, restoring art."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import DATA_DIR, Config
from .ha import HAClient
from .planner import Planner
from .render.engine import render, to_jpeg
from .tv import FrameTV

_LOGGER = logging.getLogger(__name__)
STATE_FILE = DATA_DIR / "state.json"
RETRY_SECONDS = 30


class Controller:
    def __init__(self, cfg: Config, ha: HAClient, tz: ZoneInfo) -> None:
        self.cfg, self.ha, self.tz = cfg, ha, tz
        self.tv = FrameTV(cfg.tv_host, int(cfg.tv_port), cfg.matte)
        self.planner = Planner(ha)
        self.state = self._load_state()
        self.test_until: datetime | None = None
        self.suppressed = False  # 'stop' pressed during a schedule window
        self.status = "idle"
        self.message = ""
        self.last_push: datetime | None = None
        self.last_push_seconds: float | None = None
        self.next_push: datetime | None = None
        self._retry_at: datetime | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- state
    def _load_state(self) -> dict:
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {"active": False, "restore_id": None, "uploaded": [], "session": ""}

    def _save_state(self) -> None:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        os.replace(tmp, STATE_FILE)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    # ------------------------------------------------------------- decisions
    def in_schedule(self, now: datetime) -> bool:
        t = now.time()
        return any(w.contains(now.weekday(), t) for w in self.cfg.schedule)

    def event_window(self, now: datetime) -> str | None:
        """Title of the event whose 'show around events' window is active now."""
        for begin, end, title in self.planner.event_windows(self.cfg, now):
            if begin <= now < end:
                return title
        return None

    def window(self, now: datetime) -> tuple[str, str]:
        """('schedule' | 'event' | '', detail) for the planned windows (not test runs)."""
        if self.in_schedule(now):
            return "schedule", ""
        title = self.event_window(now)
        return ("event", title) if title else ("", "")

    def desired(self, now: datetime) -> tuple[bool, str]:
        if self.test_until and now < self.test_until:
            return True, "test"
        self.test_until = None
        reason, _detail = self.window(now)
        if not reason:
            self.suppressed = False
        if reason and not self.suppressed:
            return True, reason
        return False, ""

    # -------------------------------------------------------------- commands
    def start_test(self, minutes: float) -> None:
        minutes = max(1.0, min(float(minutes), 24 * 60))
        self.test_until = self.now() + timedelta(minutes=minutes)
        self.suppressed = False
        self._retry_at = None
        _LOGGER.info("Test run started for %s minutes", minutes)
        self._wake.set()

    def stop(self) -> None:
        self.test_until = None
        if self.window(self.now())[0]:
            self.suppressed = True
        _LOGGER.info("Stop requested")
        self._wake.set()

    def push_now(self) -> None:
        self.next_push = self.now()
        self._wake.set()

    async def handle_event(self, data: dict) -> None:
        action = str(data.get("action", "test")).lower()
        if action in ("test", "start"):
            self.start_test(float(data.get("minutes", 10)))
        elif action == "stop":
            self.stop()
        elif action == "push":
            self.push_now()

    # --------------------------------------------------------------- render
    def reconfigure(self, cfg: Config) -> None:
        """Apply new options without restarting."""
        old, self.cfg = self.cfg, cfg
        if (old.tv_host, old.tv_port, old.matte) != (cfg.tv_host, cfg.tv_port, cfg.matte):
            self.tv.close()
            self.tv = FrameTV(cfg.tv_host, int(cfg.tv_port), cfg.matte)
        self._retry_at = None
        if self.state["active"]:
            self.next_push = self.now()
        _LOGGER.info("Settings applied")
        self._wake.set()

    async def render_jpeg(self, when: datetime | None = None, force_refresh=False, cfg: Config | None = None) -> bytes:
        cfg = cfg or self.cfg
        when = (when or self.now()).replace(second=0, microsecond=0)
        await self.planner.refresh(cfg, when, force=force_refresh)
        ctx = self.planner.build(cfg, when, self.state.get("session", ""))
        img = await asyncio.to_thread(render, ctx)
        quality = 85 if ctx.theme.get("texture") else 90
        return await asyncio.to_thread(to_jpeg, img, quality)

    # ------------------------------------------------------------ TV session
    async def _start_session(self, reason: str) -> bool:
        if not self.cfg.tv_host:
            self.message = "No TV IP address configured"
            return False
        if not await asyncio.to_thread(self.tv.reachable):
            self.message = "TV not reachable (off or wrong IP?)"
            return False
        if self.cfg.only_in_art_mode and not await asyncio.to_thread(self.tv.art_mode_on):
            self.message = "TV is not in art mode, waiting"
            return False
        current = await asyncio.to_thread(self.tv.current_content_id)
        if current and current not in self.state["uploaded"]:
            self.state["restore_id"] = current
        self.state["active"] = True
        self.state["session"] = self.now().strftime("%Y%m%d%H%M")
        self._save_state()
        self.status = reason
        _LOGGER.info("Session started (%s), will restore %s afterwards", reason, self.state.get("restore_id"))
        return True

    async def _push(self, display_time: datetime) -> None:
        if self.cfg.only_in_art_mode and not await asyncio.to_thread(self.tv.art_mode_on):
            self.message = "TV left art mode, paused"
            return
        t0 = asyncio.get_running_loop().time()
        jpeg = await self.render_jpeg(display_time)
        new_id = await asyncio.to_thread(self.tv.upload, jpeg)
        self.state["uploaded"].append(new_id)
        self._save_state()
        await asyncio.to_thread(self.tv.show, new_id)
        old = [c for c in self.state["uploaded"] if c != new_id]
        if old:
            try:
                await asyncio.to_thread(self.tv.delete, old)
                self.state["uploaded"] = [new_id]
            except Exception as exc:
                _LOGGER.warning("Could not delete old frames %s: %s", old, exc)
        self._save_state()
        self.last_push = self.now()
        self.last_push_seconds = round(asyncio.get_running_loop().time() - t0, 1)
        self.message = f"Showing {display_time:%H:%M} ({len(jpeg) // 1024} kB, {self.last_push_seconds}s)"
        _LOGGER.info(self.message)

    async def _end_session(self) -> bool:
        if not await asyncio.to_thread(self.tv.reachable):
            self.message = "Session ended but TV unreachable; will restore later"
            return False
        restore = self.state.get("restore_id")
        if self.cfg.restore_previous_art and restore:
            try:
                await asyncio.to_thread(self.tv.show, restore)
                _LOGGER.info("Restored previous art %s", restore)
            except Exception as exc:
                _LOGGER.warning("Could not restore %s: %s", restore, exc)
        if self.state["uploaded"]:
            try:
                await asyncio.to_thread(self.tv.delete, self.state["uploaded"])
            except Exception as exc:
                _LOGGER.warning("Could not delete frames: %s", exc)
        self.state.update({"active": False, "uploaded": [], "restore_id": None})
        self._save_state()
        self.status, self.message, self.next_push = "idle", "Previous art restored", None
        return True

    # ------------------------------------------------------------------ loop
    def _next_boundary(self, now: datetime) -> datetime:
        step = max(1, int(self.cfg.update_every_minutes))
        base = now.replace(second=0, microsecond=0)
        minutes = (base.hour * 60 + base.minute) // step * step + step
        return base.replace(hour=0, minute=0) + timedelta(minutes=minutes)

    async def run(self) -> None:
        last_refresh = 0.0
        while True:
            if self.cfg.schedule_events and self.cfg.calendar_entities and not self.state["active"]:
                mono = asyncio.get_running_loop().time()
                if mono - last_refresh > 120:
                    last_refresh = mono
                    try:
                        await self.planner.refresh(self.cfg, self.now())
                    except Exception as exc:
                        _LOGGER.debug("Background refresh failed: %s", exc)
            # start early enough for the upload to finish before the minute changes
            lead_s = max(int(self.cfg.lead_seconds), math.ceil((self.last_push_seconds or 0) + 1))
            lead = timedelta(seconds=min(lead_s, 45))
            now = self.now()
            want, reason = self.desired(now)
            try:
                async with self._lock:
                    if want and not self.state["active"]:
                        if not self._retry_at or now >= self._retry_at:
                            if await self._start_session(reason):
                                self.next_push = now  # push immediately
                            else:
                                self._retry_at = now + timedelta(seconds=RETRY_SECONDS)
                    elif want:
                        self.status = reason
                        if self.next_push is None:
                            self.next_push = now
                        if now >= self.next_push:
                            # an immediate push shows the current minute,
                            # a scheduled push the upcoming boundary
                            boundary = self._next_boundary(now)
                            display = boundary if boundary - now <= lead + timedelta(seconds=2) else now
                            try:
                                await self._push(display)
                            except Exception as exc:
                                self.message = f"Push failed: {exc}"
                                _LOGGER.warning(self.message)
                            self.next_push = self._next_boundary(self.now() + lead) - lead
                    elif self.state["active"] or self.state["uploaded"]:
                        if not self._retry_at or now >= self._retry_at:
                            if not await self._end_session():
                                self._retry_at = now + timedelta(seconds=RETRY_SECONDS)
                    else:
                        self.status = "idle"
            except Exception as exc:
                self.message = f"Error: {exc}"
                _LOGGER.exception("Controller error")
                self._retry_at = now + timedelta(seconds=RETRY_SECONDS)
            await self._publish()
            # sleep until next push or at most 5 s
            delay = 5.0
            if self.next_push and self.state["active"]:
                delay = max(0.2, min(delay, (self.next_push - self.now()).total_seconds()))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def shutdown(self) -> None:
        if self.state["active"]:
            _LOGGER.info("Shutting down, restoring previous art")
            try:
                await self._end_session()
            except Exception as exc:
                _LOGGER.warning("Restore on shutdown failed: %s", exc)
        self.tv.close()

    # ---------------------------------------------------------------- status
    def status_dict(self) -> dict:
        return {
            "status": self.status,
            "active": self.state["active"],
            "message": self.message,
            "test_until": self.test_until.isoformat() if self.test_until else None,
            "in_schedule": self.in_schedule(self.now()),
            "event_window": self.event_window(self.now()),
            "suppressed": self.suppressed,
            "last_push": self.last_push.isoformat() if self.last_push else None,
            "last_push_seconds": self.last_push_seconds,
            "next_push": self.next_push.isoformat() if self.next_push and self.state["active"] else None,
            "restore_id": self.state.get("restore_id"),
            "tv_host": self.cfg.tv_host,
            "orientation": self.cfg.orientation,
            "now": self.now().isoformat(),
        }

    _published: tuple | None = None

    async def _publish(self) -> None:
        if not self.ha.available:
            return
        key = (self.status, self.message, self.test_until, self.last_push)
        if key == self._published:
            return
        self._published = key
        d = self.status_dict()
        await self.ha.set_state("sensor.frame_dayplanner", self.status, {
            "friendly_name": "Frame Dayplanner", "icon": "mdi:calendar-clock", **{k: v for k, v in d.items() if k != "status"}})
