"""Session control: schedule / test runs, pushing frames, restoring art."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
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
WAKE_TRIES = 3  # per planner window: if someone switches the TV off on purpose, leave it off


class Controller:
    def __init__(self, cfg: Config, ha: HAClient, tz: ZoneInfo) -> None:
        self.cfg, self.ha, self.tz = cfg, ha, tz
        self.tv = FrameTV(cfg.tv_host, int(cfg.tv_port), cfg.matte)
        self.planner = Planner(ha)
        self.state = self._load_state()
        self.test_until: datetime | None = None
        self.sim_offset: timedelta | None = None  # test run 'as if it is' another time
        self.suppressed = False  # 'stop' pressed during a schedule window
        self.status = "idle"
        self.message = ""
        self.last_push: datetime | None = None
        self.last_push_seconds: float | None = None
        self.next_push: datetime | None = None
        self._retry_at: datetime | None = None
        self._wake_tries = 0  # per window
        self._rotate_lock = asyncio.Lock()
        self._last_art: bool | None = None  # for automatic rotation: art mode as last seen
        self._auto_task: asyncio.Task | None = None
        self._auto_checked = 0.0
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
        self.sim_offset = None
        reason, _detail = self.window(now)
        if not reason:
            self.suppressed = False
        if reason and not self.suppressed:
            return True, reason
        return False, ""

    # -------------------------------------------------------------- commands
    def parse_at(self, value) -> datetime | None:
        """'2026-11-16T07:00' or '07:00' (next occurrence) -> aware datetime; None if empty."""
        value = str(value or "").strip()
        if not value:
            return None
        now = self.now()
        if re.fullmatch(r"\d{1,2}:\d{2}", value):
            h, m = map(int, value.split(":"))
            at = now.replace(hour=h, minute=m, second=0, microsecond=0)
            return at if at > now else at + timedelta(days=1)
        at = datetime.fromisoformat(value)
        return at.replace(tzinfo=self.tz) if at.tzinfo is None else at.astimezone(self.tz)

    def start_test(self, minutes: float, at: datetime | None = None) -> None:
        """Test run for `minutes`; with `at` the planner shows that time (and runs on from it)."""
        minutes = max(1.0, min(float(minutes), 24 * 60))
        now = self.now()
        self.test_until = now + timedelta(minutes=minutes)
        # whole minutes, so the shown minute still flips exactly at the real minute boundary
        self.sim_offset = (at.replace(second=0, microsecond=0) - now.replace(second=0, microsecond=0)) if at else None
        self.suppressed = False
        self._retry_at = None
        if self.state["active"]:
            self.next_push = now  # show the new (simulated) time right away
        _LOGGER.info("Test run started for %s minutes%s", minutes, f" as if it is {at:%Y-%m-%d %H:%M}" if at else "")
        self._wake.set()

    def display_now(self) -> datetime:
        """The time shown on the planner: real time, or the simulated test time."""
        return self.now() + self.sim_offset if self.sim_offset else self.now()

    def stop(self) -> None:
        self.test_until = None
        self.sim_offset = None
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
            self.start_test(float(data.get("minutes", 10)), self.parse_at(data.get("at")))
        elif action == "stop":
            self.stop()
        elif action == "push":
            self.push_now()
        elif action == "rotate":
            await self.rotate(data.get("to"))

    # --------------------------------------------------------------- rotate
    @property
    def orientation(self) -> str | None:
        return self.state.get("tv_orientation")

    async def read_orientation(self) -> str | None:
        """Ask the TV (only when it is on); remember the answer."""
        try:
            if (await asyncio.to_thread(self.tv.power_state))[0] != "on":
                return None
            value = await asyncio.to_thread(self.tv.rotation)
        except Exception as exc:
            _LOGGER.debug("Could not read rotation: %s", exc)
            return None
        if value and value != self.orientation:
            self.state["tv_orientation"] = value
            self._save_state()
        return value

    async def rotate(self, to: str | None = None) -> str:
        """Rotate the TV (auto rotating mount) by holding the rotate key.  to = portrait / landscape
        rotates only when needed; empty = always rotate (toggle)."""
        to = (str(to).strip().lower() or None) if to else None
        if to not in (None, "portrait", "landscape"):
            return self._rotate_message(f"Unknown orientation '{to}' (use portrait or landscape)")
        async with self._rotate_lock:
            if not self.cfg.tv_host:
                return self._rotate_message("No TV IP address configured")
            if (await asyncio.to_thread(self.tv.power_state))[0] != "on":
                return self._rotate_message("TV is off, cannot rotate")
            before = await self.read_orientation() or self.orientation
            if to and before == to:
                return self._rotate_message(f"Already {to}")
            key, hold = self.cfg.rotate_key or "KEY_MULTI_VIEW", float(self.cfg.rotate_hold_seconds)
            _LOGGER.info("Rotating the TV (%s -> %s): holding %s for %ss", before, to or "other", key, hold)
            try:
                await asyncio.to_thread(self.tv.hold_key, key, hold)
            except Exception as exc:
                return self._rotate_message(f"Rotate failed: {exc}")
            expected = to or {"portrait": "landscape", "landscape": "portrait"}.get(before or "")
            # the mount takes a while; the TV reports the new rotation when it is done
            now = None
            for _ in range(10):
                await asyncio.sleep(self.ROTATE_POLL)
                now = await self.read_orientation()
                if now and now != before:
                    break
            if now and now != before:
                return self._rotate_message(f"Rotated to {now}")
            if expected and not now:  # the TV did not tell: keep our own bookkeeping
                self.state["tv_orientation"] = expected
                self._save_state()
                return self._rotate_message(f"Rotated (probably {expected})")
            return self._rotate_message(f"Held {key} for {hold:g}s but the TV still reports {now or 'no rotation'}")

    ROTATE_POLL = 2.0
    rotate_message = ""

    def _rotate_message(self, text: str) -> str:
        self.rotate_message = text
        _LOGGER.info(text)
        self._wake.set()
        return text

    async def _auto_rotate(self) -> None:
        """Art mode -> rotate_art orientation, watching TV -> rotate_tv orientation (on changes)."""
        try:
            if (await asyncio.to_thread(self.tv.power_state))[0] != "on":
                self._last_art = None  # off: decide again when it comes back on
                return
            art = await asyncio.to_thread(self.tv.art_mode_on)
            if art == self._last_art:
                return
            self._last_art = art
            target = self.cfg.rotate_art if art else self.cfg.rotate_tv
            if target in ("portrait", "landscape"):
                await self.rotate(target)
        except Exception as exc:
            _LOGGER.debug("Auto rotate check failed: %s", exc)

    def _maybe_auto_rotate(self) -> None:
        if not self.cfg.rotate_auto or not self.cfg.tv_host:
            return
        mono = asyncio.get_running_loop().time()
        if mono - self._auto_checked < 10 or (self._auto_task and not self._auto_task.done()):
            return
        self._auto_checked = mono
        self._auto_task = asyncio.create_task(self._auto_rotate())

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
        when = (when or self.display_now()).replace(second=0, microsecond=0)
        simulated = abs((when - self.now()).total_seconds()) > 120
        await self.planner.refresh(cfg, when, force=force_refresh)
        ctx = self.planner.build(cfg, when, self.state.get("session", ""), simulated=simulated)
        img = await asyncio.to_thread(render, ctx)
        quality = 85 if ctx.theme.get("texture") else 90
        return await asyncio.to_thread(to_jpeg, img, quality)

    # ------------------------------------------------------------ TV session
    async def _start_session(self, reason: str) -> bool:
        if not self.cfg.tv_host:
            self.message = "No TV IP address configured"
            return False
        await self._wake_tv()
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

    async def _wake_tv(self) -> None:
        """Off or in standby (e.g. in the morning): switch the TV on in art mode."""
        state, mac = await asyncio.to_thread(self.tv.power_state)
        if mac and mac != self.state.get("tv_mac"):  # remember the MAC for when the TV is fully off
            self.state["tv_mac"] = mac
            self._save_state()
        if state == "on" or not self.cfg.wake_tv or self._wake_tries >= WAKE_TRIES:
            return
        self._wake_tries += 1
        self.message = "Waking the TV…"
        await self._publish()
        mac = (self.cfg.tv_mac or "").strip() or self.state.get("tv_mac") or ""
        try:
            result = await asyncio.to_thread(self.tv.wake, mac)
        except Exception as exc:
            result = f"waking failed: {exc}"
        self.message = f"TV {result}"
        _LOGGER.info("TV was %s: %s", state, result)

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
                            if self.sim_offset:
                                display += self.sim_offset
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
                    if not want:
                        self._wake_tries = 0
            except Exception as exc:
                self.message = f"Error: {exc}"
                _LOGGER.exception("Controller error")
                self._retry_at = now + timedelta(seconds=RETRY_SECONDS)
            self._maybe_auto_rotate()
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
            "simulated": self.display_now().isoformat() if self.sim_offset else None,
            "in_schedule": self.in_schedule(self.now()),
            "event_window": self.event_window(self.now()),
            "suppressed": self.suppressed,
            "last_push": self.last_push.isoformat() if self.last_push else None,
            "last_push_seconds": self.last_push_seconds,
            "next_push": self.next_push.isoformat() if self.next_push and self.state["active"] else None,
            "restore_id": self.state.get("restore_id"),
            "tv_host": self.cfg.tv_host,
            "tv_mac": (self.cfg.tv_mac or "").strip() or self.state.get("tv_mac"),
            "tv_orientation": self.orientation,
            "rotate_message": self.rotate_message,
            "orientation": self.cfg.orientation,
            "now": self.now().isoformat(),
        }

    _published: tuple | None = None

    async def _publish(self) -> None:
        if not self.ha.available:
            return
        key = (self.status, self.message, self.test_until, self.last_push, self.orientation, self.rotate_message)
        if key == self._published:
            return
        self._published = key
        d = self.status_dict()
        await self.ha.set_state("sensor.frame_dayplanner", self.status, {
            "friendly_name": "Frame Dayplanner", "icon": "mdi:calendar-clock", **{k: v for k, v in d.items() if k != "status"}})
        await self.ha.set_state("sensor.frame_tv_orientation", self.orientation or "unknown", {
            "friendly_name": "Frame TV orientation", "message": self.rotate_message,
            "icon": "mdi:phone-rotate-portrait" if self.orientation == "portrait" else "mdi:phone-rotate-landscape"})
