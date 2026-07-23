"""U6 Pro and Mux80 differential-channel definitions."""

from dataclasses import dataclass

from .errors import ChannelConfigurationError


# With a Mux80 attached, only AIN0-AIN3 remain available as built-in AINs.
_BUILT_IN_PAIRS = {(0, 1), (2, 3)}
_EXTENDED_POSITIVE_BLOCK_STARTS = (48, 64, 80, 96, 112)


@dataclass(frozen=True)
class DifferentialInput:
    """The positive and negative AIN numbers of one differential reading."""

    positive: int
    negative: int

    def validate_for_mux80(self) -> None:
        """Raise if this is not a differential pair supported by U6 + Mux80."""

        if isinstance(self.positive, bool) or not isinstance(self.positive, int):
            raise ChannelConfigurationError("positive channel must be an integer")
        if isinstance(self.negative, bool) or not isinstance(self.negative, int):
            raise ChannelConfigurationError("negative channel must be an integer")

        pair = (self.positive, self.negative)
        if pair in _BUILT_IN_PAIRS:
            return

        extended_positive = any(
            start <= self.positive <= start + 7
            for start in _EXTENDED_POSITIVE_BLOCK_STARTS
        )
        if extended_positive and self.negative == self.positive + 8:
            return

        raise ChannelConfigurationError(
            "invalid U6/Mux80 differential pair "
            f"AIN{self.positive}-AIN{self.negative}; use AIN0-AIN1, "
            "AIN2-AIN3, or an extended positive channel in AIN48-55, "
            "AIN64-71, AIN80-87, AIN96-103, or AIN112-119 with "
            "negative channel = positive channel + 8"
        )

