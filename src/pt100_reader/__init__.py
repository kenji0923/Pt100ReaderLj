"""Pt100 acquisition with a LabJack U6 Pro and Mux80."""

from .channels import DifferentialInput
from .conversion import (
    ADR4520_NOMINAL_VOLTAGE_V,
    EXCITATION_DIVIDER_HIGH_OHMS,
    EXCITATION_DIVIDER_LOW_OHMS,
    NOMINAL_EXCITATION_VOLTAGE_V,
    pt100_resistance_to_temperature,
    pt100_temperature_to_resistance,
    rtd_resistance_from_voltages,
)
from .errors import (
    ChannelConfigurationError,
    HardwareUnavailableError,
    MeasurementError,
    Pt100ReaderError,
)
from .reader import Pt100Measurement, U6Pt100Reader

__all__ = [
    "ADR4520_NOMINAL_VOLTAGE_V",
    "ChannelConfigurationError",
    "DifferentialInput",
    "EXCITATION_DIVIDER_HIGH_OHMS",
    "EXCITATION_DIVIDER_LOW_OHMS",
    "HardwareUnavailableError",
    "MeasurementError",
    "NOMINAL_EXCITATION_VOLTAGE_V",
    "Pt100Measurement",
    "Pt100ReaderError",
    "U6Pt100Reader",
    "pt100_resistance_to_temperature",
    "pt100_temperature_to_resistance",
    "rtd_resistance_from_voltages",
]

