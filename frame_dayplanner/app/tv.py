"""Thin, thread-safe wrapper around the Samsung Frame art mode API."""
from __future__ import annotations

import logging
import socket
import threading

from .config import DATA_DIR

_LOGGER = logging.getLogger(__name__)
TOKEN_FILE = DATA_DIR / "tv_token.txt"


class FrameTV:
    def __init__(self, host: str, port: int = 8002, matte: str = "none") -> None:
        self.host, self.port, self.matte = host, port, matte or "none"
        self._art = None
        self._lock = threading.Lock()

    # -------------------------------------------------------------- plumbing
    def _client(self):
        if self._art is None:
            from samsungtvws.art import SamsungTVArt

            self._art = SamsungTVArt(self.host, port=self.port, token_file=str(TOKEN_FILE),
                                     timeout=15, name="FrameDayplanner")
        return self._art

    def _reset(self):
        try:
            if self._art is not None:
                self._art.close()
        except Exception:
            pass
        self._art = None

    def _call(self, fn_name: str, *args, **kwargs):
        with self._lock:
            for attempt in (1, 2):
                try:
                    return getattr(self._client(), fn_name)(*args, **kwargs)
                except Exception as exc:
                    self._reset()
                    if attempt == 2:
                        raise
                    _LOGGER.debug("TV call %s failed (%s), retrying", fn_name, exc)

    # ------------------------------------------------------------------ API
    def reachable(self) -> bool:
        if not self.host:
            return False
        try:
            with socket.create_connection((self.host, self.port), timeout=3):
                return True
        except OSError:
            return False

    def art_mode_on(self) -> bool:
        return str(self._call("get_artmode")).lower() == "on"

    def current_content_id(self) -> str | None:
        data = self._call("get_current") or {}
        return data.get("content_id")

    def upload(self, jpeg: bytes) -> str:
        return self._call("upload", jpeg, matte=self.matte, portrait_matte=self.matte, file_type="JPEG")

    def show(self, content_id: str) -> None:
        self._call("select_image", content_id, show=True)

    def delete(self, content_ids: list[str]) -> None:
        if content_ids:
            self._call("delete_list", content_ids)

    def close(self):
        with self._lock:
            self._reset()
