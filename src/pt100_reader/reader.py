"""Cross-platform command/response acquisition for a LabJack U6 Pro."""

import math
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Optional, Protocol

from .channels import DifferentialInput
from .conversion import (
    pt100_resistance_to_temperature,
    rtd_resistance_from_voltages,
)
from .errors import HardwareUnavailableError, MeasurementError


class _U6Device(Protocol):
    def getAIN(
        self,
        positiveChannel: int,
        resolutionIndex: int = 0,
        gainIndex: int = 0,
        settlingFactor: int = 0,
        differential: bool = False,
    ) -> float:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class Pt100Measurement:
    """One processed Pt100 measurement."""

    reference_voltage_v: float
    rtd_voltage_v: float
    resistance_ohms: float
    temperature_c: float


class U6Pt100Reader:
    """Read differential voltages and Pt100s through a U6 Pro + Mux80.

    ``gain_index=2`` selects the U6's nominal +/-0.1 V range. Resolution index
    12 selects the U6 Pro high-resolution converter. A caller-provided ``device``
    is useful for testing and remains owned by the caller.
    """

    def __init__(
        self,
        device: Optional[_U6Device] = None,
        *,
        serial: Optional[int] = None,
        local_id: Optional[int] = None,
        resolution_index: int = 12,
        gain_index: int = 2,
        settling_factor: int = 0,
        samples: int = 1,
        reference_resistance_ohms: float = 140.0,
        minimum_drop_voltage_v: float = 1e-3,
    ) -> None:
        if device is not None and (serial is not None or local_id is not None):
            raise ValueError("serial/local_id cannot be used with a supplied device")
        if serial is not None and local_id is not None:
            raise ValueError("specify serial or local_id, not both")
        if not isinstance(samples, int) or isinstance(samples, bool) or samples < 1:
            raise ValueError("samples must be a positive integer")
        if not 0 <= resolution_index <= 12:
            raise ValueError("resolution_index must be between 0 and 12")
        if gain_index not in (0, 1, 2, 3, 15):
            raise ValueError("gain_index must be 0, 1, 2, 3, or 15")
        if not 0 <= settling_factor <= 9:
            raise ValueError("settling_factor must be between 0 and 9")
        if not math.isfinite(reference_resistance_ohms) or reference_resistance_ohms <= 0:
            raise ValueError("reference resistance must be finite and greater than zero")
        if not math.isfinite(minimum_drop_voltage_v) or minimum_drop_voltage_v < 0:
            raise ValueError("minimum drop voltage must be finite and non-negative")

        self.resolution_index = resolution_index
        self.gain_index = gain_index
        self.settling_factor = settling_factor
        self.samples = samples
        self.reference_resistance_ohms = reference_resistance_ohms
        self.minimum_drop_voltage_v = minimum_drop_voltage_v
        self._owns_device = device is None
        self._closed = False
        self._device = device if device is not None else self._open_device(serial, local_id)

    @staticmethod
    def _open_device(serial: Optional[int], local_id: Optional[int]) -> _U6Device:
        try:
            import u6  # type: ignore[import-not-found]
        except (ImportError, OSError) as exc:
            raise HardwareUnavailableError(
                "LabJackPython or its native driver is unavailable; install the "
                "'labjack' extra and the LabJack UD driver (Windows) or "
                "Exodriver (Linux)"
            ) from exc

        kwargs: dict[str, Any] = {}
        if serial is not None:
            kwargs.update(firstFound=False, serial=serial)
        elif local_id is not None:
            kwargs.update(firstFound=False, localId=local_id)
        try:
            return u6.U6(**kwargs)
        except Exception as exc:
            raise HardwareUnavailableError(f"could not open LabJack U6: {exc}") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise HardwareUnavailableError("the LabJack reader is closed")

    def _read_once(self, channel: DifferentialInput) -> float:
        self._ensure_open()
        channel.validate_for_mux80()
        try:
            # The U6 low-level command carries the positive channel plus a
            # differential bit. The physical negative channel is implied by
            # the U6/Mux80 mapping, so it is validated above rather than sent.
            value = float(
                self._device.getAIN(
                    channel.positive,
                    resolutionIndex=self.resolution_index,
                    gainIndex=self.gain_index,
                    settlingFactor=self.settling_factor,
                    differential=True,
                )
            )
        except Exception as exc:
            raise MeasurementError(
                f"failed to read AIN{channel.positive}-AIN{channel.negative}: {exc}"
            ) from exc
        if not math.isfinite(value):
            raise MeasurementError(
                f"AIN{channel.positive}-AIN{channel.negative} returned a non-finite voltage"
            )
        return value

    def read_voltage(
        self,
        positive_channel: int,
        negative_channel: int,
        *,
        samples: Optional[int] = None,
    ) -> float:
        """Read and average one U6/Mux80 differential input in volts."""

        sample_count = self._sample_count(samples)
        channel = DifferentialInput(positive_channel, negative_channel)
        channel.validate_for_mux80()
        return fmean(self._read_once(channel) for _ in range(sample_count))

    def read_pt100(
        self,
        reference_input: DifferentialInput,
        rtd_input: DifferentialInput,
        *,
        samples: Optional[int] = None,
        reference_resistance_ohms: Optional[float] = None,
    ) -> Pt100Measurement:
        """Read both series voltage drops and return resistance and temperature."""

        sample_count = self._sample_count(samples)
        reference_input.validate_for_mux80()
        rtd_input.validate_for_mux80()
        if reference_input == rtd_input:
            raise ValueError("reference_input and rtd_input must be different pairs")

        # Alternate readings so slow excitation changes affect both averages alike.
        reference_readings = []
        rtd_readings = []
        for _ in range(sample_count):
            reference_readings.append(self._read_once(reference_input))
            rtd_readings.append(self._read_once(rtd_input))

        reference_voltage = fmean(reference_readings)
        rtd_voltage = fmean(rtd_readings)
        for label, voltage in (
            ("reference", reference_voltage),
            ("RTD", rtd_voltage),
        ):
            if abs(voltage) < self.minimum_drop_voltage_v:
                raise MeasurementError(
                    f"{label} differential voltage {voltage:.9g} V is below the "
                    f"configured minimum {self.minimum_drop_voltage_v:.9g} V; "
                    "check excitation and wiring"
                )
        r_ref = (
            self.reference_resistance_ohms
            if reference_resistance_ohms is None
            else reference_resistance_ohms
        )
        resistance = rtd_resistance_from_voltages(
            rtd_voltage_v=rtd_voltage,
            reference_voltage_v=reference_voltage,
            reference_resistance_ohms=r_ref,
        )
        return Pt100Measurement(
            reference_voltage_v=reference_voltage,
            rtd_voltage_v=rtd_voltage,
            resistance_ohms=resistance,
            temperature_c=pt100_resistance_to_temperature(resistance),
        )

    def _sample_count(self, samples: Optional[int]) -> int:
        count = self.samples if samples is None else samples
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("samples must be a positive integer")
        return count

    def close(self) -> None:
        """Close a device opened by this reader."""

        if not self._closed and self._owns_device:
            self._device.close()
        self._closed = True

    def __enter__(self) -> "U6Pt100Reader":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
