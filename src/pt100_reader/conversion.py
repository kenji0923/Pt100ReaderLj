"""Electrical and IEC 60751 Pt100 conversion functions."""

import math
from typing import Final

from .errors import MeasurementError


ADR4520_NOMINAL_VOLTAGE_V: Final[float] = 2.048
EXCITATION_DIVIDER_HIGH_OHMS: Final[float] = 118_000.0
EXCITATION_DIVIDER_LOW_OHMS: Final[float] = 10_000.0
NOMINAL_EXCITATION_VOLTAGE_V: Final[float] = (
    ADR4520_NOMINAL_VOLTAGE_V
    * EXCITATION_DIVIDER_LOW_OHMS
    / (EXCITATION_DIVIDER_HIGH_OHMS + EXCITATION_DIVIDER_LOW_OHMS)
)

PT100_R0_OHMS: Final[float] = 100.0
IEC_60751_A: Final[float] = 3.9083e-3
IEC_60751_B: Final[float] = -5.775e-7
IEC_60751_C: Final[float] = -4.183e-12
IEC_60751_MIN_TEMPERATURE_C: Final[float] = -200.0
IEC_60751_MAX_TEMPERATURE_C: Final[float] = 850.0


def rtd_resistance_from_voltages(
    *,
    rtd_voltage_v: float,
    reference_voltage_v: float,
    reference_resistance_ohms: float = 140.0,
) -> float:
    """Calculate RTD resistance from its drop and the series-reference drop.

    Since the same current flows through both devices::

        R_RTD = R_ref * V_RTD / V_ref

    Use the measured value of the reference resistor for best accuracy. Both
    voltage readings must use the same polarity.
    """

    values = (rtd_voltage_v, reference_voltage_v, reference_resistance_ohms)
    if not all(math.isfinite(value) for value in values):
        raise MeasurementError("voltages and reference resistance must be finite")
    if reference_resistance_ohms <= 0.0:
        raise MeasurementError("reference resistance must be greater than zero")
    if math.isclose(reference_voltage_v, 0.0, abs_tol=1e-12):
        raise MeasurementError("reference voltage is too close to zero")

    resistance = reference_resistance_ohms * rtd_voltage_v / reference_voltage_v
    if resistance <= 0.0:
        raise MeasurementError(
            "calculated RTD resistance is not positive; check differential polarity"
        )
    return resistance


def pt100_temperature_to_resistance(
    temperature_c: float,
    *,
    r0_ohms: float = PT100_R0_OHMS,
) -> float:
    """Return Pt100 resistance using the IEC 60751 Callendar-Van Dusen curve."""

    if not math.isfinite(temperature_c):
        raise ValueError("temperature must be finite")
    if not math.isfinite(r0_ohms) or r0_ohms <= 0.0:
        raise ValueError("R0 must be finite and greater than zero")
    if not IEC_60751_MIN_TEMPERATURE_C <= temperature_c <= IEC_60751_MAX_TEMPERATURE_C:
        raise ValueError("temperature is outside the IEC 60751 range -200 to 850 degC")

    t = temperature_c
    if t >= 0.0:
        return r0_ohms * (1.0 + IEC_60751_A * t + IEC_60751_B * t * t)
    return r0_ohms * (
        1.0
        + IEC_60751_A * t
        + IEC_60751_B * t * t
        + IEC_60751_C * (t - 100.0) * t * t * t
    )


def pt100_resistance_to_temperature(
    resistance_ohms: float,
    *,
    r0_ohms: float = PT100_R0_OHMS,
) -> float:
    """Convert Pt100 resistance to degrees Celsius per IEC 60751.

    The positive-temperature quadratic is inverted analytically. The negative
    Callendar-Van Dusen polynomial is inverted with a bounded bisection, which
    avoids an external numerical-library dependency.
    """

    if not math.isfinite(resistance_ohms):
        raise MeasurementError("RTD resistance must be finite")
    if not math.isfinite(r0_ohms) or r0_ohms <= 0.0:
        raise ValueError("R0 must be finite and greater than zero")

    minimum_resistance = pt100_temperature_to_resistance(
        IEC_60751_MIN_TEMPERATURE_C, r0_ohms=r0_ohms
    )
    maximum_resistance = pt100_temperature_to_resistance(
        IEC_60751_MAX_TEMPERATURE_C, r0_ohms=r0_ohms
    )
    below_minimum = resistance_ohms < minimum_resistance and not math.isclose(
        resistance_ohms, minimum_resistance, rel_tol=1e-12, abs_tol=1e-9
    )
    above_maximum = resistance_ohms > maximum_resistance and not math.isclose(
        resistance_ohms, maximum_resistance, rel_tol=1e-12, abs_tol=1e-9
    )
    if below_minimum or above_maximum:
        raise MeasurementError(
            "RTD resistance is outside the IEC 60751 Pt100 range "
            f"({minimum_resistance:.6g} to {maximum_resistance:.6g} ohms)"
        )
    resistance_ohms = min(max(resistance_ohms, minimum_resistance), maximum_resistance)

    if resistance_ohms >= r0_ohms:
        normalized = resistance_ohms / r0_ohms
        discriminant = IEC_60751_A**2 - 4.0 * IEC_60751_B * (1.0 - normalized)
        return (-IEC_60751_A + math.sqrt(discriminant)) / (2.0 * IEC_60751_B)

    lower = IEC_60751_MIN_TEMPERATURE_C
    upper = 0.0
    # 60 iterations are ample for sub-nanokelvin numerical resolution here.
    for _ in range(60):
        midpoint = (lower + upper) / 2.0
        midpoint_resistance = pt100_temperature_to_resistance(
            midpoint, r0_ohms=r0_ohms
        )
        if midpoint_resistance < resistance_ohms:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0
