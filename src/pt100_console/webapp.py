"""HTTP server: static UI, settings API, snapshot API, channel test, reports."""

import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STALE_FACTOR = 3.0


class ConsoleApp:
    """Bundles the pieces the HTTP handler needs."""

    def __init__(self, config: Any, hardware: dict, loop: Any) -> None:
        self.config = config
        self.hw = hardware
        self.loop = loop

    def snapshot(self) -> dict:
        defaults = self.config.defaults()
        states = self.loop.states_snapshot()
        meta = self.loop.meta_snapshot()
        now = time.time()
        channels = []
        acquiring = 0
        for cid in sorted(self.hw["sensors"]):
            sensor = self.hw["sensors"][cid]
            overrides = self.config.channel_overrides(cid)
            effective = self.config.effective(cid)
            state = states.get(cid) or {}
            sample = state.get("sample")
            polled = bool(effective.get("polling_enabled"))
            if polled:
                acquiring += 1
            if not polled:
                status, detail = "OFF", None
            elif sample is None:
                status, detail = "NODATA", None
            else:
                status, detail = sample["status"], sample.get("detail")
            interval = max(0.5, float(effective.get("polling_interval_s", 10.0)))
            stale = bool(
                polled
                and sample
                and now - sample["updated"] > STALE_FACTOR * interval
            )
            channels.append(
                {
                    "id": cid,
                    "hw_name": sensor["name"],
                    "name": effective.get("name") or sensor["name"],
                    "overrides": overrides,
                    "effective": effective,
                    "wiring": {
                        "rref_ohms": sensor["rref_ohms"],
                        "rref": [sensor["rref"].positive, sensor["rref"].negative],
                        "rtd": [sensor["rtd"].positive, sensor["rtd"].negative],
                    },
                    "m": {
                        "status": status,
                        "detail": detail,
                        "stale": stale,
                        "t_c": sample["t_c"] if sample else None,
                        "r_ohm": sample["r_ohm"] if sample else None,
                        "vref_v": sample["vref_v"] if sample else None,
                        "vrtd_v": sample["vrtd_v"] if sample else None,
                        "updated": sample["updated"] if sample else None,
                        "range_ref_v": state.get("range_ref_v"),
                        "range_rtd_v": state.get("range_rtd_v"),
                    },
                }
            )
        point_s = meta["point_s"]
        cycle_s = (
            2 * acquiring * point_s if (point_s and acquiring) else None
        )
        return {
            "now": now,
            "console_name": self.config.console_name,
            "defaults": defaults,
            "channels": channels,
            "hw": {
                "serial": self.hw.get("serial"),
                "connected": meta["hw_connected"],
                "error": meta["hw_error"],
            },
            "meta": {
                "point_s": point_s,
                "cycle_s": cycle_s,
                "acquiring": acquiring,
                "total": len(channels),
                "telegraf_error": meta["telegraf_error"],
            },
            "test": meta["test"],
        }

    def list_reports(self) -> list:
        directory = self.config.reports_dir
        if not directory.is_dir():
            return []
        files = sorted(
            (p for p in directory.glob("channel-test_*.*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [p.name for p in files[:40]]


def make_handler(app: ConsoleApp) -> type:
    class ConsoleHandler(BaseHTTPRequestHandler):
        server_version = "Pt100Console/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s %s", self.address_string(), fmt % args)

        # -- helpers -------------------------------------------------------

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, code: int = 200) -> None:
            self._send(
                code,
                json.dumps(payload).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _read_json(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 1_000_000:
                raise ValueError("bad request body length")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        # -- routes --------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                if path in ("/", "/index.html"):
                    body = (STATIC_DIR / "index.html").read_bytes()
                    self._send(200, body, "text/html; charset=utf-8")
                elif path == "/api/snapshot":
                    self._json(app.snapshot())
                elif path == "/api/reports":
                    self._json(app.list_reports())
                elif path.startswith("/reports/"):
                    name = Path(path).name
                    if not name.startswith("channel-test_") or "/" in name[1:]:
                        self._json({"error": "not found"}, 404)
                        return
                    target = app.config.reports_dir / name
                    if not target.is_file():
                        self._json({"error": "not found"}, 404)
                        return
                    kind = (
                        "text/html; charset=utf-8"
                        if name.endswith(".html")
                        else "application/json; charset=utf-8"
                    )
                    self._send(200, target.read_bytes(), kind)
                else:
                    self._json({"error": "not found"}, 404)
            except (ConnectionError, BrokenPipeError):
                pass
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("GET %s failed", path)
                try:
                    self._json({"error": str(exc)}, 500)
                except Exception:
                    pass

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                if path == "/api/settings":
                    diff = self._read_json()
                    app.config.apply_settings_diff(diff)
                    self._json({"ok": True})
                elif path == "/api/test/start":
                    body = self._read_json()
                    reads = int(body.get("reads", 8))
                    if app.loop.request_test(reads):
                        self._json({"ok": True})
                    else:
                        self._json({"ok": False, "error": "test already running"}, 409)
                elif path == "/api/test/abort":
                    app.loop.abort_test()
                    self._json({"ok": True})
                else:
                    self._json({"error": "not found"}, 404)
            except (ValueError, KeyError, TypeError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except (ConnectionError, BrokenPipeError):
                pass
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("POST %s failed", path)
                try:
                    self._json({"error": str(exc)}, 500)
                except Exception:
                    pass

    return ConsoleHandler


def serve(app: ConsoleApp, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server
