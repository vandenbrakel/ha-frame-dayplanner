"""Thin, thread-safe wrapper around the Samsung Frame art mode API."""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time

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

    def device_info(self) -> dict | None:
        """REST info (port 8001): PowerState 'on' / 'standby', wifiMac.  None = not answering (off)."""
        if not self.host:
            return None
        import requests

        try:
            return requests.get(f"http://{self.host}:8001/api/v2/", timeout=3).json().get("device") or {}
        except Exception:
            return None

    def power_state(self) -> tuple[str, str]:
        """('on' | 'standby' | 'off', mac)."""
        info = self.device_info()
        if info is None:
            return "off", ""
        mac = info.get("wifiMac") or ""
        return str(info.get("PowerState") or "on").lower(), mac

    def send_wol(self, mac: str) -> None:
        """Wake-on-LAN magic packet (broadcast, subnet broadcast and straight to the TV)."""
        digits = re.sub(r"[^0-9a-fA-F]", "", mac or "")
        if len(digits) != 12:
            raise ValueError(f"invalid MAC address '{mac}'")
        packet = b"\xff" * 6 + bytes.fromhex(digits) * 16
        targets = {"255.255.255.255", self.host}
        try:
            targets.add(str(ipaddress.ip_network(f"{self.host}/24", strict=False).broadcast_address))
        except ValueError:
            pass
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for target in targets:
                for port in (9, 7):
                    try:
                        s.sendto(packet, (target, port))
                    except OSError as exc:
                        _LOGGER.debug("WoL to %s:%s failed: %s", target, port, exc)

    def power_key(self) -> None:
        """Short press on the power button (only used when the TV says it is in standby)."""
        from samsungtvws import SamsungTVWS

        tv = SamsungTVWS(self.host, port=self.port, token_file=str(TOKEN_FILE), timeout=8, name="FrameDayplanner")
        try:
            tv.send_key("KEY_POWER")
        finally:
            try:
                tv.close()
            except Exception:
                pass

    def wake(self, mac: str, wait: float = 25) -> str:
        """Switch a TV that is off / in standby on, into art mode.  Returns what happened."""
        state, found_mac = self.power_state()
        mac = mac or found_mac
        if state == "on":
            return "already on"
        steps = []
        if mac:
            self.send_wol(mac)
            steps.append("wake-on-LAN")
        if not self._wait_on(wait / 2 if state == "standby" else wait, resend=mac):
            if state == "standby" or self.power_state()[0] == "standby":
                self.power_key()  # still in standby: press power
                steps.append("power key")
                self._wait_on(wait / 2)
        if self.power_state()[0] != "on":
            return f"TV did not wake up ({', '.join(steps) or 'no MAC address known'})"
        # the TV may come up with the normal TV picture: switch to art mode
        for _ in range(5):
            try:
                if self.art_mode_on():
                    break
                self._call("set_artmode", "on")
                steps.append("art mode")
            except Exception as exc:
                _LOGGER.debug("Art mode not ready yet: %s", exc)
            time.sleep(2)
        return "woke up (" + ", ".join(steps) + ")"

    def _wait_on(self, seconds: float, resend: str = "") -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            time.sleep(2)
            if self.power_state()[0] == "on" and self.reachable():
                return True
            if resend:
                self.send_wol(resend)
        return False

    def rotation(self) -> str | None:
        """'portrait' / 'landscape' as reported by the art mode API (None = unknown)."""
        value = str(self._call("get_rotation")).strip()
        return {"1": "landscape", "2": "portrait"}.get(value)

    def hold_key(self, key: str, seconds: float) -> None:
        """Press and hold a remote key, e.g. Multi View for 3 s rotates the auto rotating mount."""
        from samsungtvws import SamsungTVWS
        from samsungtvws.remote import SendRemoteKey

        tv = SamsungTVWS(self.host, port=self.port, token_file=str(TOKEN_FILE), timeout=10, name="FrameDayplanner")
        try:
            tv.send_command(SendRemoteKey.hold(key, seconds))
        finally:
            try:
                tv.close()
            except Exception:
                pass

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
