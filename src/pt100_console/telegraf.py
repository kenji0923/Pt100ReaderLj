"""InfluxDB line-protocol formatting and Telegraf transports (udp/tcp/http)."""

import logging
import socket
import threading
import urllib.error
import urllib.request
from typing import Any, Optional

log = logging.getLogger(__name__)

SEND_TIMEOUT_S = 1.0


def _escape_key(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace("=", "\\=")
        .replace(" ", "\\ ")
    )


def _escape_measurement(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def _format_field(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def build_line(
    measurement: str, tags: dict, fields: dict, timestamp_ns: int
) -> str:
    tag_part = "".join(
        f",{_escape_key(key)}={_escape_key(value)}"
        for key, value in sorted(tags.items())
        if value is not None and str(value) != ""
    )
    field_part = ",".join(
        f"{_escape_key(key)}={_format_field(value)}"
        for key, value in fields.items()
        if value is not None
    )
    return f"{_escape_measurement(measurement)}{tag_part} {field_part} {timestamp_ns}"


class TelegrafSender:
    """Best-effort sender; errors are remembered for the UI, never raised."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._udp: Optional[socket.socket] = None
        self._tcp: dict[tuple, socket.socket] = {}
        self.last_error: Optional[str] = None

    def send(self, cfg: dict, line: str) -> bool:
        protocol = str(cfg.get("protocol", "udp")).lower()
        address = str(cfg.get("address", "127.0.0.1"))
        port = int(cfg.get("port", 8094))
        payload = (line + "\n").encode("utf-8")
        try:
            with self._lock:
                if protocol == "udp":
                    if self._udp is None:
                        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self._udp.sendto(payload, (address, port))
                elif protocol == "tcp":
                    conn = self._tcp.get((address, port))
                    if conn is None:
                        conn = socket.create_connection(
                            (address, port), timeout=SEND_TIMEOUT_S
                        )
                        self._tcp[(address, port)] = conn
                    conn.sendall(payload)
                elif protocol == "http":
                    request = urllib.request.Request(
                        f"http://{address}:{port}/telegraf",
                        data=payload,
                        method="POST",
                        headers={"Content-Type": "text/plain; charset=utf-8"},
                    )
                    urllib.request.urlopen(request, timeout=SEND_TIMEOUT_S).close()
                else:
                    raise ValueError(f"unknown telegraf protocol {protocol!r}")
            self.last_error = None
            return True
        except (OSError, urllib.error.URLError, ValueError) as exc:
            with self._lock:
                stale = self._tcp.pop((address, port), None)
            if stale is not None:
                try:
                    stale.close()
                except OSError:
                    pass
            self.last_error = f"{protocol}://{address}:{port}: {exc}"
            log.debug("telegraf send failed: %s", self.last_error)
            return False

    def close(self) -> None:
        with self._lock:
            if self._udp is not None:
                self._udp.close()
                self._udp = None
            for conn in self._tcp.values():
                try:
                    conn.close()
                except OSError:
                    pass
            self._tcp.clear()
