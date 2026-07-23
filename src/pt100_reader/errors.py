"""Exceptions raised by :mod:`pt100_reader`."""


class Pt100ReaderError(Exception):
    """Base exception for this package."""


class ChannelConfigurationError(Pt100ReaderError, ValueError):
    """A channel pair cannot be used as a U6/Mux80 differential input."""


class MeasurementError(Pt100ReaderError, ValueError):
    """Measured values cannot describe a valid RTD measurement."""


class HardwareUnavailableError(Pt100ReaderError):
    """The LabJack Python module, driver, or U6 is unavailable."""

