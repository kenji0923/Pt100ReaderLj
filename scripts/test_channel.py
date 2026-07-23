#!/usr/bin/env python3
"""Acquire and summarize a single configured Pt100 channel."""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from pt100_reader import (
    DifferentialInput,
    MeasurementError,
    Pt100ReaderError,
    U6Pt100Reader,
    pt100_resistance_to_temperature,
    rtd_resistance_from_voltages,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "pt100.yaml"
FASTEST_RESOLUTION_INDEX = 1
AUTO_SETTLING_FACTOR = 0


@dataclass(frozen=True)
class Sample:
    reference_voltage_v: float
    rtd_voltage_v: float
    resistance_ohms: Optional[float]
    temperature_c: Optional[float]
    error: Optional[str] = None


@dataclass(frozen=True)
class ChannelSummary:
    channel_id: int
    name: str
    status: str
    average_reference_mv: Optional[float]
    average_rtd_mv: Optional[float]
    average_sum_mv: Optional[float]
    average_resistance_ohms: Optional[float]
    average_temperature_c: Optional[float]
    temperature_stddev_c: Optional[float]
    valid_reads: int
    total_reads: int


@dataclass(frozen=True)
class ChannelTarget:
    channel_id: int
    name: str
    enabled: bool
    vexc_v: float
    reference_input: DifferentialInput
    rtd_input: DifferentialInput
    rref_ohms: float
    serial: Optional[int]
    gain_index: int
    resolution_index: int
    settling_factor: int
    minimum_drop_v: float
    input_min_v: float
    input_max_v: float
    overrange_margin_v: float


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def finite_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def load_sensor(config_path: Path, channel_id: int) -> tuple[dict[str, Any], dict[str, Any], float]:
    with config_path.open("r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream)

    if not isinstance(config, dict):
        raise ValueError("configuration root must be a mapping")
    global_config = config.get("global")
    device_config = config.get("device")
    sensors = config.get("sensors")
    if not isinstance(global_config, dict):
        raise ValueError("configuration must contain a 'global' mapping")
    if not isinstance(device_config, dict):
        raise ValueError("configuration must contain a 'device' mapping")
    if not isinstance(sensors, list):
        raise ValueError("configuration must contain a 'sensors' list")

    sensor = next(
        (
            item
            for item in sensors
            if isinstance(item, dict) and item.get("id") == channel_id
        ),
        None,
    )
    if sensor is None:
        available = ", ".join(
            str(item.get("id")) for item in sensors if isinstance(item, dict)
        )
        raise ValueError(f"channel {channel_id} is not configured; available: {available}")
    vexc_v = finite_float(global_config.get("vexc_v"), "global.vexc_v")
    if vexc_v <= 0.0:
        raise ValueError("global.vexc_v must be greater than zero")
    return device_config, sensor, vexc_v


def configured_input(sensor: dict[str, Any], name: str) -> DifferentialInput:
    value = sensor.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"sensor {sensor.get('id')} must contain a '{name}' mapping")
    try:
        channel = DifferentialInput(
            positive=int(value["positive"]),
            negative=int(value["negative"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"sensor {sensor.get('id')} has an invalid '{name}'") from exc
    channel.validate_for_mux80()
    return channel


def average(values: list[float]) -> float:
    return statistics.fmean(values)


def sample_stddev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def display_value(value: Optional[float], width: int = 13) -> str:
    return f"{value:{width}.6f}" if value is not None else f"{'INVALID':>{width}}"


def prepare_target(args: argparse.Namespace, channel_id: int) -> ChannelTarget:
    device_config, sensor, vexc_v = load_sensor(args.config, channel_id)
    reference_input = configured_input(sensor, "rref_input")
    rtd_input = configured_input(sensor, "rtd_input")
    rref_ohms = finite_float(sensor.get("rref_ohms"), "sensor.rref_ohms")
    if rref_ohms <= 0.0:
        raise ValueError("sensor.rref_ohms must be greater than zero")

    serial_value = device_config.get("serial")
    serial = int(serial_value) if serial_value is not None else None
    gain_index = int(device_config.get("gain_index", 2))
    resolution_index = (
        FASTEST_RESOLUTION_INDEX
        if args.fast
        else int(device_config.get("resolution_index", 12))
    )
    settling_factor = (
        AUTO_SETTLING_FACTOR
        if args.fast
        else int(device_config.get("settling_factor", 0))
    )
    minimum_drop_v = finite_float(
        device_config.get("minimum_drop_voltage_v", 0.001),
        "device.minimum_drop_voltage_v",
    )
    input_min_v = finite_float(
        device_config.get("differential_min_v", -0.106),
        "device.differential_min_v",
    )
    input_max_v = finite_float(
        device_config.get("differential_max_v", 0.101),
        "device.differential_max_v",
    )
    overrange_margin_v = finite_float(
        device_config.get("overrange_margin_v", 0.0002),
        "device.overrange_margin_v",
    )
    if input_min_v >= input_max_v:
        raise ValueError("device differential input limits are reversed")
    if overrange_margin_v < 0.0:
        raise ValueError("device.overrange_margin_v must be non-negative")

    return ChannelTarget(
        channel_id=channel_id,
        name=str(sensor.get("name", "unnamed")),
        enabled=bool(sensor.get("enabled", False)),
        vexc_v=vexc_v,
        reference_input=reference_input,
        rtd_input=rtd_input,
        rref_ohms=rref_ohms,
        serial=serial,
        gain_index=gain_index,
        resolution_index=resolution_index,
        settling_factor=settling_factor,
        minimum_drop_v=minimum_drop_v,
        input_min_v=input_min_v,
        input_max_v=input_max_v,
        overrange_margin_v=overrange_margin_v,
    )


def acquire_sample(reader: U6Pt100Reader, target: ChannelTarget) -> Sample:
    reference_voltage = reader.read_voltage(
        target.reference_input.positive,
        target.reference_input.negative,
        samples=1,
    )
    rtd_voltage = reader.read_voltage(
        target.rtd_input.positive,
        target.rtd_input.negative,
        samples=1,
    )
    try:
        problems = []
        for label, voltage in (
            ("reference", reference_voltage),
            ("RTD", rtd_voltage),
        ):
            if abs(voltage) < target.minimum_drop_v:
                problems.append(
                    f"{label} drop {voltage:.9g} V is below "
                    f"{target.minimum_drop_v:.9g} V"
                )
            if (
                voltage <= target.input_min_v + target.overrange_margin_v
                or voltage >= target.input_max_v - target.overrange_margin_v
            ):
                problems.append(
                    f"{label} drop {voltage:.9g} V is at/near an ADC range limit"
                )
        if problems:
            raise MeasurementError("; ".join(problems))
        resistance = rtd_resistance_from_voltages(
            rtd_voltage_v=rtd_voltage,
            reference_voltage_v=reference_voltage,
            reference_resistance_ohms=target.rref_ohms,
        )
        temperature = pt100_resistance_to_temperature(resistance)
        return Sample(reference_voltage, rtd_voltage, resistance, temperature)
    except MeasurementError as exc:
        return Sample(reference_voltage, rtd_voltage, None, None, str(exc))


def summary_value(value: Optional[float], width: int, decimals: int = 4) -> str:
    return f"{value:{width}.{decimals}f}" if value is not None else f"{'--':>{width}}"


def print_batch_summary(summaries: list[ChannelSummary], elapsed: float) -> None:
    print("\n" + "=" * 112)
    print("All-channel summary")
    print(
        f"{'Ch':>3}  {'Name':<10}  {'Status':<8}  {'Vref mV':>9}  "
        f"{'Vrtd mV':>9}  {'Sum mV':>9}  {'R ohm':>10}  "
        f"{'Temp C':>9}  {'T std C':>9}  {'Valid':>7}"
    )
    print("-" * 112)
    for summary in summaries:
        print(
            f"{summary.channel_id:3d}  {summary.name:<10.10}  "
            f"{summary.status:<8.8}  "
            f"{summary_value(summary.average_reference_mv, 9)}  "
            f"{summary_value(summary.average_rtd_mv, 9)}  "
            f"{summary_value(summary.average_sum_mv, 9)}  "
            f"{summary_value(summary.average_resistance_ohms, 10)}  "
            f"{summary_value(summary.average_temperature_c, 9)}  "
            f"{summary_value(summary.temperature_stddev_c, 9)}  "
            f"{summary.valid_reads:>3}/{summary.total_reads:<3}"
        )
    print("-" * 112)
    tested = [summary for summary in summaries if summary.status != "SKIPPED"]
    passed = sum(summary.status == "PASS" for summary in tested)
    skipped = len(summaries) - len(tested)
    print(
        f"{passed}/{len(tested)} tested sensors passed; {skipped} skipped; "
        f"elapsed {elapsed:.3f} s"
    )


def print_failed_raw_data(
    channel_id: int,
    measurements: list[Sample],
    vexc_v: float,
) -> None:
    print()
    print(f"Raw voltage data for failed sensor {channel_id}")
    print(
        f"{'Read':>4}  {'Vref (V)':>14}  {'Vrtd (V)':>14}  "
        f"{'Sum (V)':>14}  {'Failure':<40}"
    )
    print("-" * 94)
    for index, measurement in enumerate(measurements, start=1):
        voltage_sum = measurement.reference_voltage_v + measurement.rtd_voltage_v
        failures = []
        if measurement.error is not None:
            failures.append(measurement.error)
        if abs(voltage_sum) > vexc_v:
            failures.append(f"|sum| exceeds Vexc {vexc_v:.9g} V")
        print(
            f"{index:4d}  {measurement.reference_voltage_v:14.9f}  "
            f"{measurement.rtd_voltage_v:14.9f}  {voltage_sum:14.9f}  "
            f"{'; '.join(failures) or 'channel-level failure':<40}"
        )


def run_channel(
    args: argparse.Namespace,
    channel_id: int,
    measurements: Optional[list[Sample]] = None,
    elapsed: Optional[float] = None,
) -> tuple[int, ChannelSummary]:
    try:
        target = prepare_target(args, channel_id)
        if not target.enabled:
            if not args.up_to:
                raise ValueError(f"channel {channel_id} is disabled")
            print(
                f"Sensor {channel_id} ({target.name}): "
                "SKIPPED (disabled in configuration)"
            )
            return 0, ChannelSummary(
                channel_id=channel_id,
                name=target.name,
                status="SKIPPED",
                average_reference_mv=None,
                average_rtd_mv=None,
                average_sum_mv=None,
                average_resistance_ohms=None,
                average_temperature_c=None,
                temperature_stddev_c=None,
                valid_reads=0,
                total_reads=0,
            )

        print(
            f"Sensor {target.channel_id} ({target.name}): "
            f"Rref AIN{target.reference_input.positive}-"
            f"AIN{target.reference_input.negative}, "
            f"RTD AIN{target.rtd_input.positive}-AIN{target.rtd_input.negative}"
        )
        print(
            f"Rref={target.rref_ohms:.6f} ohm, Vexc={target.vexc_v:.9f} V, "
            f"mode={'fast' if args.fast else 'accuracy'}, "
            f"resolution={target.resolution_index}, gain={target.gain_index}, "
            f"settling={'auto' if target.settling_factor == 0 else target.settling_factor}"
        )

        if measurements is None:
            measurements = []
            with U6Pt100Reader(
                serial=target.serial,
                resolution_index=target.resolution_index,
                gain_index=target.gain_index,
                settling_factor=target.settling_factor,
                samples=1,
                reference_resistance_ohms=target.rref_ohms,
                minimum_drop_voltage_v=target.minimum_drop_v,
            ) as reader:
                started = time.perf_counter()
                for _ in range(args.reads):
                    measurements.append(acquire_sample(reader, target))
                elapsed = time.perf_counter() - started
        if elapsed is None:
            raise ValueError("acquisition elapsed time is unavailable")

        vexc_v = target.vexc_v

        print()
        print(
            f"{'Read':>4}  {'Vref (mV)':>12}  {'Vrtd (mV)':>12}  "
            f"{'Sum (mV)':>12}  {'|Sum|<=Vexc':>12}  "
            f"{'R_RTD (ohm)':>13}  {'Temp (degC)':>12}"
        )
        print("-" * 94)
        for index, measurement in enumerate(measurements, start=1):
            voltage_sum = measurement.reference_voltage_v + measurement.rtd_voltage_v
            under_vexc = abs(voltage_sum) <= vexc_v
            print(
                f"{index:4d}  {measurement.reference_voltage_v * 1_000:12.6f}  "
                f"{measurement.rtd_voltage_v * 1_000:12.6f}  "
                f"{voltage_sum * 1_000:12.6f}  "
                f"{('YES' if under_vexc else 'NO'):>12}  "
                f"{display_value(measurement.resistance_ohms)}  "
                f"{display_value(measurement.temperature_c, 12)}"
            )

        reference_voltages = [item.reference_voltage_v for item in measurements]
        rtd_voltages = [item.rtd_voltage_v for item in measurements]
        voltage_sums = [ref + rtd for ref, rtd in zip(reference_voltages, rtd_voltages)]
        resistances = [
            item.resistance_ohms
            for item in measurements
            if item.resistance_ohms is not None
        ]
        temperatures = [
            item.temperature_c for item in measurements if item.temperature_c is not None
        ]
        average_resistance = average(resistances) if resistances else None
        average_temperature = average(temperatures) if temperatures else None
        stddev_resistance = sample_stddev(resistances) if resistances else None
        stddev_temperature = sample_stddev(temperatures) if temperatures else None

        print("-" * 94)
        print(
            f"{'AVG':>4}  {average(reference_voltages) * 1_000:12.6f}  "
            f"{average(rtd_voltages) * 1_000:12.6f}  "
            f"{average(voltage_sums) * 1_000:12.6f}  "
            f"{('YES' if abs(average(voltage_sums)) <= vexc_v else 'NO'):>12}  "
            f"{display_value(average_resistance)}  "
            f"{display_value(average_temperature, 12)}"
        )
        print(
            f"{'STD':>4}  {sample_stddev(reference_voltages) * 1_000:12.6f}  "
            f"{sample_stddev(rtd_voltages) * 1_000:12.6f}  "
            f"{sample_stddev(voltage_sums) * 1_000:12.6f}  "
            f"{'':>12}  {display_value(stddev_resistance)}  "
            f"{display_value(stddev_temperature, 12)}"
        )

        passing = sum(abs(value) <= vexc_v for value in voltage_sums)
        status = "PASS" if passing == len(voltage_sums) else "FAIL"
        valid = sum(item.error is None for item in measurements)
        validity_status = "PASS" if valid == len(measurements) else "FAIL"
        print()
        print(
            f"Vexc check: {status} ({passing}/{len(voltage_sums)} readings have "
            f"|Vref + Vrtd| <= {vexc_v * 1_000:.6f} mV)"
        )
        print(
            f"Measurement validity: {validity_status} "
            f"({valid}/{len(measurements)} resistance/temperature results valid)"
        )
        for index, measurement in enumerate(measurements, start=1):
            if measurement.error is not None:
                print(f"  Read {index}: {measurement.error}")
        if validity_status == "FAIL" or status == "FAIL":
            print_failed_raw_data(channel_id, measurements, vexc_v)
        print(
            f"Acquisition: {elapsed:.6f} s, {len(measurements) / elapsed:.2f} "
            f"sensor readings/s, {2 * len(measurements) / elapsed:.2f} AIN reads/s"
        )
        print("STD is sample standard deviation (N-1).")
        summary_status = (
            "INVALID"
            if validity_status == "FAIL"
            else ("PASS" if status == "PASS" else "VEXC")
        )
        summary = ChannelSummary(
            channel_id=channel_id,
            name=target.name,
            status=summary_status,
            average_reference_mv=average(reference_voltages) * 1_000,
            average_rtd_mv=average(rtd_voltages) * 1_000,
            average_sum_mv=average(voltage_sums) * 1_000,
            average_resistance_ohms=average_resistance,
            average_temperature_c=average_temperature,
            temperature_stddev_c=stddev_temperature,
            valid_reads=valid,
            total_reads=len(measurements),
        )
        if validity_status == "FAIL":
            return 2, summary
        return (0 if status == "PASS" else 1), summary
    except (OSError, ValueError, yaml.YAMLError, Pt100ReaderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2, ChannelSummary(
            channel_id=channel_id,
            name="--",
            status="ERROR",
            average_reference_mv=None,
            average_rtd_mv=None,
            average_sum_mv=None,
            average_resistance_ohms=None,
            average_temperature_c=None,
            temperature_stddev_c=None,
            valid_reads=0,
            total_reads=args.reads,
        )


def run_cycling_batch(
    args: argparse.Namespace, channel_ids: list[int]
) -> tuple[list[int], list[ChannelSummary], float]:
    batch_started = time.perf_counter()
    try:
        targets = [prepare_target(args, channel_id) for channel_id in channel_ids]
    except (OSError, ValueError, yaml.YAMLError, Pt100ReaderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        summary = ChannelSummary(
            channel_id=channel_ids[0],
            name="--",
            status="ERROR",
            average_reference_mv=None,
            average_rtd_mv=None,
            average_sum_mv=None,
            average_resistance_ohms=None,
            average_temperature_c=None,
            temperature_stddev_c=None,
            valid_reads=0,
            total_reads=args.reads,
        )
        return [2], [summary], time.perf_counter() - batch_started

    active_targets = [target for target in targets if target.enabled]
    measurements = {target.channel_id: [] for target in active_targets}
    channel_elapsed = {target.channel_id: 0.0 for target in active_targets}

    if active_targets:
        first = active_targets[0]
        order = " -> ".join(str(target.channel_id) for target in active_targets)
        print(f"Cycling acquisition order: {order} (repeated {args.reads} times)")
        with U6Pt100Reader(
            serial=first.serial,
            resolution_index=first.resolution_index,
            gain_index=first.gain_index,
            settling_factor=first.settling_factor,
            samples=1,
            reference_resistance_ohms=first.rref_ohms,
            minimum_drop_voltage_v=first.minimum_drop_v,
        ) as reader:
            for _ in range(args.reads):
                for target in active_targets:
                    started = time.perf_counter()
                    measurements[target.channel_id].append(
                        acquire_sample(reader, target)
                    )
                    channel_elapsed[target.channel_id] += time.perf_counter() - started

    results = []
    summaries = []
    for index, target in enumerate(targets):
        if index or active_targets:
            print("\n" + "=" * 94 + "\n")
        if target.enabled:
            result, summary = run_channel(
                args,
                target.channel_id,
                measurements=measurements[target.channel_id],
                elapsed=channel_elapsed[target.channel_id],
            )
        else:
            result, summary = run_channel(args, target.channel_id)
        results.append(result)
        summaries.append(summary)

    return results, summaries, time.perf_counter() - batch_started


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read a configured Pt100 channel eight times. Accuracy mode uses "
            "the U6 Pro high-resolution ADC by default."
        )
    )
    parser.add_argument("channel", type=positive_integer, help="configured sensor ID")
    parser.add_argument(
        "--reads",
        type=positive_integer,
        default=8,
        help="number of complete Rref/RTD readings per sensor (default: 8)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"configuration YAML (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="use resolution index 1 for maximum speed instead of accuracy mode",
    )
    parser.add_argument(
        "--up-to",
        action="store_true",
        help="test every configured sensor from 1 through CHANNEL, inclusive",
    )
    args = parser.parse_args()

    if args.up_to:
        results, summaries, elapsed = run_cycling_batch(
            args, list(range(1, args.channel + 1))
        )
        print_batch_summary(summaries, elapsed)
        return max(results, default=0)

    result, _ = run_channel(args, args.channel)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
