"""Add-on entry point."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from zoneinfo import ZoneInfo

from aiohttp import web

from .config import load_config
from .controller import Controller
from .ha import HAClient
from .web import make_app

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_LOGGER = logging.getLogger("frame_dayplanner")
logging.getLogger("samsungtvws").setLevel(logging.WARNING)
logging.getLogger("websocket").setLevel(logging.WARNING)


async def amain() -> None:
    cfg = load_config()
    ha = HAClient()
    tz_name = os.environ.get("TZ") or (await ha.time_zone() if ha.available else None) or "Europe/Amsterdam"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Amsterdam")
    _LOGGER.info("Frame Dayplanner starting: TV=%s orientation=%s tz=%s, %d schedule window(s)",
                 cfg.tv_host or "-", cfg.orientation, tz, len(cfg.schedule))

    ctrl = Controller(cfg, ha, tz)
    runner = web.AppRunner(make_app(ctrl))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8099))).start()

    tasks = [asyncio.create_task(ctrl.run())]
    if ha.available:
        tasks.append(asyncio.create_task(ha.listen_events("frame_dayplanner", ctrl.handle_event)))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    for t in tasks:
        t.cancel()
    await ctrl.shutdown()
    await runner.cleanup()
    await ha.close()


if __name__ == "__main__":
    asyncio.run(amain())
