"""Acquisition loop: EDF scheduling, per-drop auto-range, status codes.

Single thread owns the U6. It waits on the config change event with a
timeout equal to the time until the next channel is due, so settings apply
immediately and an idle loop consumes nothing (spec §5).
"""

import logging
import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pt100_reader import (
    DifferentialInput,
    HardwareUnavailableError,
    MeasurementError,
    Pt100ReaderError,
    U6Pt100Reader,
    pt100_resistance_to_temperature,
    rtd_resistance_from_voltages,
)

from .report import write_reports
from .telegraf import TelegrafSender, build_line

log = logging.getLogger(__name__)

# Usable |V| per U6 gain index (±0.1 V floor; ±0.01 V can never hold a
# valid drop with the nominal 160 mV excitation).
GAIN_LIMITS = {2: 0.101, 1: 1.01, 0: 10.1}
GAIN_RANGE_V = {2: 0.1, 1: 1.0, 0: 10.0}
WIDER = {2: 1, 1: 0}
NARROWER = {0: 1, 1: 2}
STEPDOWN_FRACTION = 0.7
STEPDOWN_CYCLES = 10
NEAR_VEXC = (0.75, 1.25)
HW_RETRY_S = 5.0
MIN_INTERVAL_S = 0.5
DEFAULT_POINT_S = {1: 0.004, 8: 0.006, 9: 0.024, 10: 0.044, 11: 0.084, 12: 0.164}


@dataclass
class DropState:
    gain: int = 2
    down_count: int = 0


@dataclass
class ChannelState:
    next_due: float = 0.0
    ref: DropState = field(default_factory=DropState)
    rtd: DropState = field(default_factory=DropState)
    sample: Optional[dict] = None


def read_drop(
    reader: Any, pair: DifferentialInput, drop: DropState, margin: float
) -> tuple:
    """One auto-ranged differential read; returns (volts, saturated)."""

    gain = drop.gain
    while True:
        reader.gain_index = gain
        value = reader.read_voltage(pair.positive, pair.negative, samples=1)
        if abs(value) >= GAIN_LIMITS[gain] - margin and gain in WIDER:
            gain = WIDER[gain]  # widen and re-read immediately (spec §3.2)
            drop.down_count = 0
            continue
        break
    saturated = gain not in WIDER and abs(value) >= GAIN_LIMITS[gain] - margin
    if gain in NARROWER and not saturated:
        if abs(value) < STEPDOWN_FRACTION * GAIN_LIMITS[NARROWER[gain]]:
            drop.down_count += 1
            if drop.down_count >= STEPDOWN_CYCLES:
                gain = NARROWER[gain]
                drop.down_count = 0
        else:
            drop.down_count = 0
    if drop.gain != gain:
        log.info(
            "AIN%d-AIN%d range -> ±%g V", pair.positive, pair.negative,
            GAIN_RANGE_V[gain],
        )
    drop.gain = gain
    return value, saturated


def classify(
    vref: float,
    vrtd: float,
    rref_ohms: float,
    saturated: bool,
    vexc: float,
    min_drop: float,
) -> tuple:
    """Return (status_code, detail, resistance, temperature) per spec §6."""

    def mv(value: float) -> str:
        return f"{value * 1000:.3f} mV"

    if saturated:
        return (
            "OVERRANGE",
            f"saturated at ±10 V (Vref {mv(vref)}, Vrtd {mv(vrtd)})",
            None,
            None,
        )
    low_ref = abs(vref) < min_drop
    low_rtd = abs(vrtd) < min_drop
    near_ref = NEAR_VEXC[0] * vexc <= abs(vref) <= NEAR_VEXC[1] * vexc
    near_rtd = NEAR_VEXC[0] * vexc <= abs(vrtd) <= NEAR_VEXC[1] * vexc
    if low_ref and low_rtd:
        return (
            "EXCLOSS",
            f"both drops below {mv(min_drop)} (Vref {mv(vref)}, Vrtd {mv(vrtd)})",
            None,
            None,
        )
    if low_rtd:
        hint = " — Vref ≈ Vexc" if near_ref else ""
        return ("SHORT", f"Vrtd {mv(vrtd)} < min {mv(min_drop)}{hint}", None, None)
    if low_ref:
        hint = " — Vrtd ≈ Vexc" if near_rtd else ""
        return ("OPEN", f"Vref {mv(vref)} < min {mv(min_drop)}{hint}", None, None)
    if abs(vref + vrtd) > vexc:
        return (
            "SUMERR",
            f"|Vref + Vrtd| = {mv(abs(vref + vrtd))} exceeds Vexc {mv(vexc)}",
            None,
            None,
        )
    try:
        resistance = rtd_resistance_from_voltages(
            rtd_voltage_v=vrtd,
            reference_voltage_v=vref,
            reference_resistance_ohms=rref_ohms,
        )
    except MeasurementError as exc:
        return ("POLARITY", str(exc), None, None)
    try:
        temperature = pt100_resistance_to_temperature(resistance)
    except MeasurementError as exc:
        return ("RANGE", str(exc), resistance, None)
    return ("OK", None, resistance, temperature)


class AcquisitionLoop(threading.Thread):
    def __init__(self, config: Any, hardware: dict, sender: TelegrafSender) -> None:
        super().__init__(name="acquisition", daemon=True)
        self.config = config
        self.hw = hardware
        self.sender = sender
        self.wake = config.change_event
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._states = {cid: ChannelState() for cid in hardware["sensors"]}
        self._reader: Optional[U6Pt100Reader] = None
        self._hw_error: Optional[str] = None
        self._last_open_attempt = 0.0
        self._point_s: Optional[float] = None  # EMA of measured s/point
        self._test: dict = {
            "running": False,
            "done": 0,
            "total": 0,
            "aborted": False,
            "result": None,
            "meta": None,
            "report_html": None,
            "report_json": None,
        }
        self._test_req: Optional[dict] = None
        self._test_abort = threading.Event()

    # ---- public API ------------------------------------------------------

    def stop(self) -> None:
        self._stop_event.set()
        self._test_abort.set()
        self.wake.set()
        self.join(timeout=15)
        self._close_reader()

    def request_test(self, reads: int) -> bool:
        with self._lock:
            if self._test["running"] or self._test_req is not None:
                return False
            self._test_req = {"reads": max(1, int(reads))}
        self._test_abort.clear()
        self.wake.set()
        return True

    def abort_test(self) -> None:
        self._test_abort.set()

    def states_snapshot(self) -> dict:
        with self._lock:
            return {
                cid: {
                    "sample": dict(state.sample) if state.sample else None,
                    "range_ref_v": GAIN_RANGE_V[state.ref.gain],
                    "range_rtd_v": GAIN_RANGE_V[state.rtd.gain],
                }
                for cid, state in self._states.items()
            }

    def meta_snapshot(self) -> dict:
        with self._lock:
            point = self._point_s
            test = dict(self._test)
        return {
            "point_s": point,
            "hw_connected": self._reader is not None,
            "hw_error": self._hw_error,
            "telegraf_error": self.sender.last_error,
            "test": test,
        }

    # ---- internals -------------------------------------------------------

    def run(self) -> None:  # noqa: C901
        log.info("acquisition loop started (%d sensors)", len(self._states))
        while not self._stop_event.is_set():
            with self._lock:
                request = self._test_req
                self._test_req = None
            if request is not None:
                try:
                    self._run_test(request)
                except Exception:
                    log.exception("channel test failed")
                    with self._lock:
                        self._test["running"] = False
                continue

            enabled = [
                cid
                for cid in self._states
                if self.config.effective(cid).get("polling_enabled")
            ]
            now = time.monotonic()
            due = [cid for cid in enabled if self._states[cid].next_due <= now]
            if not due:
                if enabled:
                    timeout = min(
                        self._states[cid].next_due for cid in enabled
                    ) - now
                    timeout = min(max(timeout, 0.05), 2.0)
                else:
                    timeout = 2.0
                self.wake.wait(timeout)
                self.wake.clear()
                continue

            cid = min(due, key=lambda c: (self._states[c].next_due, c))
            effective = self.config.effective(cid)
            sample = self._acquire(cid, effective)
            if (
                sample is not None
                and effective.get("telemetry_enabled")
                and sample["status"] != "OFF"
            ):
                self._send_telemetry(cid, effective, sample)
            state = self._states[cid]
            interval = max(
                MIN_INTERVAL_S, float(effective.get("polling_interval_s", 10.0))
            )
            # Fixed cadence; clamp so falling behind never causes a burst.
            state.next_due = max(state.next_due + interval, time.monotonic())
        log.info("acquisition loop stopped")

    def _ensure_reader(self, effective: dict) -> Optional[U6Pt100Reader]:
        if self._reader is not None:
            return self._reader
        now = time.monotonic()
        if now - self._last_open_attempt < HW_RETRY_S:
            return None
        self._last_open_attempt = now
        try:
            self._reader = U6Pt100Reader(
                serial=self.hw.get("serial"),
                resolution_index=self._clamped_resolution(effective),
                gain_index=2,
                settling_factor=self._clamped_settling(effective),
                samples=1,
                minimum_drop_voltage_v=0.0,
            )
            self._hw_error = None
            log.info("LabJack U6 opened (serial %s)", self.hw.get("serial"))
        except (HardwareUnavailableError, Pt100ReaderError) as exc:
            self._hw_error = str(exc)
            self._reader = None
            log.warning("cannot open LabJack: %s", exc)
        return self._reader

    def _close_reader(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
            self._reader = None

    @staticmethod
    def _clamped_resolution(effective: dict) -> int:
        return min(max(int(effective.get("resolution_index", 12)), 1), 12)

    @staticmethod
    def _clamped_settling(effective: dict) -> int:
        return min(max(int(effective.get("settling_factor", 0)), 0), 9)

    def _acquire(self, cid: int, effective: dict) -> Optional[dict]:
        sensor = self.hw["sensors"][cid]
        state = self._states[cid]
        reader = self._ensure_reader(effective)
        stamp = time.time()
        if reader is None:
            sample = {
                "status": "COMM",
                "detail": self._hw_error or "LabJack unavailable",
                "vref_v": None,
                "vrtd_v": None,
                "r_ohm": None,
                "t_c": None,
                "updated": stamp,
            }
        else:
            reader.resolution_index = self._clamped_resolution(effective)
            reader.settling_factor = self._clamped_settling(effective)
            try:
                started = time.perf_counter()
                vref, saturated_ref = read_drop(
                    reader, sensor["rref"], state.ref, self.hw["overrange_margin_v"]
                )
                vrtd, saturated_rtd = read_drop(
                    reader, sensor["rtd"], state.rtd, self.hw["overrange_margin_v"]
                )
                self._note_point_time((time.perf_counter() - started) / 2.0)
                code, detail, resistance, temperature = classify(
                    vref,
                    vrtd,
                    sensor["rref_ohms"],
                    saturated_ref or saturated_rtd,
                    self.hw["vexc_v"],
                    self.hw["minimum_drop_v"],
                )
                sample = {
                    "status": code,
                    "detail": detail,
                    "vref_v": vref,
                    "vrtd_v": vrtd,
                    "r_ohm": resistance,
                    "t_c": temperature,
                    "updated": stamp,
                }
            except (MeasurementError, HardwareUnavailableError) as exc:
                sample = {
                    "status": "COMM",
                    "detail": str(exc),
                    "vref_v": None,
                    "vrtd_v": None,
                    "r_ohm": None,
                    "t_c": None,
                    "updated": stamp,
                }
                self._close_reader()  # force reopen; USB may have dropped
        with self._lock:
            state.sample = sample
        return sample

    def _note_point_time(self, seconds: float) -> None:
        with self._lock:
            if self._point_s is None:
                self._point_s = seconds
            else:
                self._point_s = 0.8 * self._point_s + 0.2 * seconds

    def _send_telemetry(self, cid: int, effective: dict, sample: dict) -> None:
        with self._lock:
            if self._test["running"]:
                return  # suspended during a channel test (spec §4.1)
        telegraf = effective.get("telegraf") or {}
        sensor = self.hw["sensors"][cid]
        state = self._states[cid]
        tags = {
            "console": self.config.console_name,
            "channel": cid,
            "name": effective.get("name") or sensor["name"],
            "tlg_bucket": telegraf.get("tlg_bucket", ""),
        }
        tags.update(effective.get("tags") or {})
        ok = sample["status"] == "OK"
        fields: dict = {"ok": 1 if ok else 0, "status": sample["status"]}
        if sample["vref_v"] is not None:
            fields["vref_v"] = float(sample["vref_v"])
            fields["vrtd_v"] = float(sample["vrtd_v"])
            fields["range_ref_v"] = GAIN_RANGE_V[state.ref.gain]
            fields["range_rtd_v"] = GAIN_RANGE_V[state.rtd.gain]
        if ok:
            fields["temperature_c"] = float(sample["t_c"])
            fields["resistance_ohms"] = float(sample["r_ohm"])
        line = build_line(
            telegraf.get("measurement", "temperature"),
            tags,
            fields,
            time.time_ns(),
        )
        self.sender.send(telegraf, line)

    # ---- channel test (spec §4.1) ---------------------------------------

    def _run_test(self, request: dict) -> None:
        reads = request["reads"]
        enabled = [
            cid
            for cid in sorted(self._states)
            if self.config.effective(cid).get("polling_enabled")
        ]
        with self._lock:
            self._test.update(
                running=True,
                done=0,
                total=2 * reads * len(enabled),
                aborted=False,
                result=None,
                meta=None,
                report_html=None,
                report_json=None,
            )
        log.info("channel test: %d reads x %d channels", reads, len(enabled))
        readings: dict[int, list] = {cid: [] for cid in enabled}
        started_wall = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        started = time.perf_counter()
        aborted = False
        for _ in range(reads):
            for cid in enabled:
                if self._test_abort.is_set() or self._stop_event.is_set():
                    aborted = True
                    break
                sample = self._acquire(cid, self.config.effective(cid))
                readings[cid].append(sample)
                with self._lock:
                    self._test["done"] += 2
            if aborted:
                break
        elapsed = max(time.perf_counter() - started, 1e-9)
        total_reads = sum(len(items) for items in readings.values())

        rows = []
        for cid in enabled:
            items = [s for s in readings[cid] if s and s["vref_v"] is not None]
            if not items and not readings[cid]:
                continue
            worst = next(
                (s["status"] for s in readings[cid] if s and s["status"] != "OK"),
                "OK",
            )
            vref = [s["vref_v"] * 1000 for s in items]
            vrtd = [s["vrtd_v"] * 1000 for s in items]
            temps = [s["t_c"] for s in items if s["t_c"] is not None]
            ohms = [s["r_ohm"] for s in items if s["r_ohm"] is not None]

            def mean_sd(values: list) -> tuple:
                if not values:
                    return None, None
                sd = statistics.stdev(values) if len(values) > 1 else 0.0
                return statistics.fmean(values), sd

            vref_m, vref_s = mean_sd(vref)
            vrtd_m, vrtd_s = mean_sd(vrtd)
            t_m, t_s = mean_sd(temps)
            r_m, _ = mean_sd(ohms)
            effective = self.config.effective(cid)
            rows.append(
                {
                    "ch": cid,
                    "name": effective.get("name")
                    or self.hw["sensors"][cid]["name"],
                    "reads": len(readings[cid]),
                    "vref_mean_mv": vref_m,
                    "vref_sd_mv": vref_s,
                    "vrtd_mean_mv": vrtd_m,
                    "vrtd_sd_mv": vrtd_s,
                    "r_mean_ohm": r_m,
                    "t_mean_c": t_m,
                    "t_sd_c": t_s,
                    "status": worst,
                }
            )

        defaults = self.config.defaults()
        meta = {
            "console": self.config.console_name,
            "started": started_wall,
            "reads_per_channel": reads,
            "resolution_index": defaults.get("resolution_index"),
            "settling_factor": defaults.get("settling_factor"),
            "aborted": aborted,
            "elapsed_s": round(elapsed, 3),
            "channel_reads_per_s": round(total_reads / elapsed, 3),
            "ain_points_per_s": round(2 * total_reads / elapsed, 3),
        }
        raw = {
            str(cid): [
                {
                    "vref_v": s["vref_v"],
                    "vrtd_v": s["vrtd_v"],
                    "r_ohm": s["r_ohm"],
                    "t_c": s["t_c"],
                    "status": s["status"],
                }
                for s in readings[cid]
                if s
            ]
            for cid in enabled
        }
        html_name = json_name = None
        try:
            html_name, json_name = write_reports(
                self.config.reports_dir, meta, rows, raw
            )
        except OSError:
            log.exception("failed to write channel-test reports")
        with self._lock:
            self._test.update(
                running=False,
                aborted=aborted,
                result=rows,
                meta=meta,
                report_html=html_name,
                report_json=json_name,
            )
        log.info(
            "channel test %s: %d reads in %.1f s (%.2f channel reads/s)",
            "aborted" if aborted else "done",
            total_reads,
            elapsed,
            meta["channel_reads_per_s"],
        )
