"""Console configuration: defaults + per-channel overrides (console.yaml).

The file stores overrides only; an omitted key means "follow the default".
`apply_diff` merges a nested diff where ``None`` removes the key (revert to
default). Disk writes are debounced; the in-memory state applies immediately.
"""

import copy
import logging
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

from pt100_reader import DifferentialInput

log = logging.getLogger(__name__)

BUILTIN_DEFAULTS: dict = {
    "polling_enabled": False,
    "telemetry_enabled": False,
    "polling_interval_s": 10.0,
    "resolution_index": 12,
    "settling_factor": 0,
    "telegraf": {
        "protocol": "udp",
        "address": "127.0.0.1",
        "port": 8094,
        "tlg_bucket": "",
        "measurement": "temperature",
    },
}

SAVE_DEBOUNCE_S = 1.0


def deep_merge(base: dict, override: Optional[dict]) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def apply_diff(target: dict, diff: dict) -> None:
    """Merge ``diff`` into ``target`` in place; ``None`` removes the key."""

    for key, value in diff.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict):
            sub = target.get(key)
            if not isinstance(sub, dict):
                sub = {}
                target[key] = sub
            apply_diff(sub, value)
            if not sub:
                target.pop(key, None)
        else:
            target[key] = copy.deepcopy(value)


class ConsoleConfig:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._save_timer: Optional[threading.Timer] = None
        self._raw: dict = {}
        # Set on every accepted change; the acquisition loop waits on this.
        self.change_event = threading.Event()
        self.load()

    def load(self) -> None:
        raw: dict = {}
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as stream:
                loaded = yaml.safe_load(stream)
                if isinstance(loaded, dict):
                    raw = loaded
        with self._lock:
            self._raw = raw

    # ---- accessors -------------------------------------------------------

    def raw_snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._raw)

    @property
    def console_name(self) -> str:
        with self._lock:
            return str(self._raw.get("console_name", "pt100-console"))

    @property
    def reports_dir(self) -> Path:
        with self._lock:
            configured = self._raw.get("reports_dir", "reports")
        path = Path(configured)
        if not path.is_absolute():
            path = self.path.resolve().parent.parent / path
        return path

    @property
    def server_host(self) -> str:
        with self._lock:
            return str((self._raw.get("server") or {}).get("host", "0.0.0.0"))

    @property
    def server_port(self) -> int:
        with self._lock:
            return int((self._raw.get("server") or {}).get("port", 8080))

    @property
    def hardware_config_path(self) -> Path:
        with self._lock:
            configured = (self._raw.get("hardware") or {}).get(
                "config", "config/pt100.yaml"
            )
        path = Path(configured)
        if path.is_absolute():
            return path
        for base in (Path.cwd(), self.path.resolve().parent.parent):
            candidate = base / path
            if candidate.exists():
                return candidate
        return path

    def defaults(self) -> dict:
        with self._lock:
            return deep_merge(BUILTIN_DEFAULTS, self._raw.get("defaults"))

    def channel_overrides(self, channel_id: int) -> dict:
        with self._lock:
            for entry in self._raw.get("channels") or []:
                if isinstance(entry, dict) and entry.get("id") == channel_id:
                    overrides = copy.deepcopy(entry)
                    overrides.pop("id", None)
                    return overrides
        return {}

    def effective(self, channel_id: int) -> dict:
        overrides = self.channel_overrides(channel_id)
        merged = deep_merge(self.defaults(), overrides)
        merged.setdefault("tags", overrides.get("tags", {}))
        if "name" in overrides:
            merged["name"] = overrides["name"]
        return merged

    # ---- mutation --------------------------------------------------------

    def apply_settings_diff(self, diff: dict) -> None:
        """Apply a settings diff from the UI (or any API client)."""

        if not isinstance(diff, dict):
            raise ValueError("settings diff must be an object")
        with self._lock:
            if "console_name" in diff:
                value = diff["console_name"]
                if value is None:
                    self._raw.pop("console_name", None)
                else:
                    self._raw["console_name"] = str(value)
            if isinstance(diff.get("defaults"), dict):
                defaults = self._raw.setdefault("defaults", {})
                apply_diff(defaults, diff["defaults"])
                if not defaults:
                    self._raw.pop("defaults", None)
            if isinstance(diff.get("channels"), dict):
                channels = self._raw.setdefault("channels", [])
                for cid_text, channel_diff in diff["channels"].items():
                    cid = int(cid_text)
                    if not isinstance(channel_diff, dict):
                        continue
                    channel_diff = dict(channel_diff)
                    entry = next(
                        (
                            item
                            for item in channels
                            if isinstance(item, dict) and item.get("id") == cid
                        ),
                        None,
                    )
                    if entry is None:
                        entry = {"id": cid}
                        channels.append(entry)
                    if "tags" in channel_diff:
                        # extra tags are replaced wholesale, never merged
                        tags = channel_diff.pop("tags")
                        if tags is None:
                            entry.pop("tags", None)
                        elif isinstance(tags, dict):
                            entry["tags"] = {
                                str(key): str(value) for key, value in tags.items()
                            }
                    apply_diff(entry, channel_diff)
                    if set(entry.keys()) <= {"id"}:
                        channels.remove(entry)
                if not channels:
                    self._raw.pop("channels", None)
            self._schedule_save()
        self.change_event.set()

    def _schedule_save(self) -> None:
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(SAVE_DEBOUNCE_S, self.flush)
        self._save_timer.daemon = True
        self._save_timer.start()

    def flush(self) -> None:
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            data = copy.deepcopy(self._raw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, sort_keys=False, allow_unicode=True)
        tmp.replace(self.path)
        log.debug("saved %s", self.path)


DEFAULT_CONSOLE_YAML = {
    "schema_version": 1,
    "console_name": "pt100-console",
    "reports_dir": "reports",
    "server": {"host": "0.0.0.0", "port": 8080},
    "hardware": {"config": "config/pt100.yaml"},
}


def ensure_console_yaml(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(DEFAULT_CONSOLE_YAML, stream, sort_keys=False)
    log.info("created default console configuration at %s", path)


def load_hardware(path: Path) -> dict:
    """Load wiring/device facts from the existing pt100.yaml."""

    with Path(path).open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: hardware configuration must be a mapping")
    device = raw.get("device") or {}
    global_cfg = raw.get("global") or {}
    sensors: dict[int, dict] = {}
    for item in raw.get("sensors") or []:
        if not isinstance(item, dict) or "id" not in item:
            continue
        cid = int(item["id"])
        rref = item.get("rref_input") or {}
        rtd = item.get("rtd_input") or {}
        sensors[cid] = {
            "name": str(item.get("name", f"pt100_{cid:02d}")),
            "rref_ohms": float(item.get("rref_ohms", 140.0)),
            "rref": DifferentialInput(int(rref["positive"]), int(rref["negative"])),
            "rtd": DifferentialInput(int(rtd["positive"]), int(rtd["negative"])),
        }
    serial = device.get("serial")
    return {
        "path": str(path),
        "serial": int(serial) if serial is not None else None,
        "vexc_v": float(global_cfg.get("vexc_v", 0.160)),
        "minimum_drop_v": float(device.get("minimum_drop_voltage_v", 0.001)),
        "overrange_margin_v": float(device.get("overrange_margin_v", 0.0002)),
        "sensors": sensors,
    }
