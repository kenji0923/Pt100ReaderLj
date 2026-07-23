import unittest

from pt100_reader import (
    ChannelConfigurationError,
    DifferentialInput,
    HardwareUnavailableError,
    MeasurementError,
    U6Pt100Reader,
)


class FakeU6:
    def __init__(self, readings):
        self.readings = {channel: iter(values) for channel, values in readings.items()}
        self.calls = []
        self.closed = False

    def getAIN(self, positiveChannel, **kwargs):
        self.calls.append((positiveChannel, kwargs))
        return next(self.readings[positiveChannel])

    def close(self):
        self.closed = True


class ChannelTests(unittest.TestCase):
    def test_valid_mux80_pairs(self):
        for pair in (
            DifferentialInput(0, 1),
            DifferentialInput(2, 3),
            DifferentialInput(48, 56),
            DifferentialInput(55, 63),
            DifferentialInput(64, 72),
            DifferentialInput(119, 127),
        ):
            with self.subTest(pair=pair):
                pair.validate_for_mux80()

    def test_invalid_mux80_pairs(self):
        for pair in (
            DifferentialInput(4, 5),
            DifferentialInput(47, 55),
            DifferentialInput(48, 49),
            DifferentialInput(56, 64),
            DifferentialInput(120, 128),
        ):
            with self.subTest(pair=pair):
                with self.assertRaises(ChannelConfigurationError):
                    pair.validate_for_mux80()


class ReaderTests(unittest.TestCase):
    def test_read_voltage_uses_requested_u6_settings_and_averages(self):
        fake = FakeU6({48: [0.060, 0.062]})
        reader = U6Pt100Reader(device=fake, samples=2)

        voltage = reader.read_voltage(48, 56)

        self.assertAlmostEqual(voltage, 0.061)
        self.assertEqual(len(fake.calls), 2)
        for channel, kwargs in fake.calls:
            self.assertEqual(channel, 48)
            self.assertEqual(kwargs["resolutionIndex"], 12)
            self.assertEqual(kwargs["gainIndex"], 2)
            self.assertEqual(kwargs["settlingFactor"], 0)
            self.assertIs(kwargs["differential"], True)

    def test_read_pt100(self):
        fake = FakeU6(
            {
                48: [0.093, 0.0936666666666],
                84: [0.066, 0.0673333333334],
            }
        )
        reader = U6Pt100Reader(device=fake, samples=2)

        measurement = reader.read_pt100(
            DifferentialInput(48, 56),
            DifferentialInput(84, 92),
        )

        self.assertAlmostEqual(measurement.reference_voltage_v, 0.0933333333333)
        self.assertAlmostEqual(measurement.rtd_voltage_v, 0.0666666666667)
        self.assertAlmostEqual(measurement.resistance_ohms, 100.0, places=9)
        self.assertAlmostEqual(measurement.temperature_c, 0.0, places=8)
        self.assertEqual([call[0] for call in fake.calls], [48, 84, 48, 84])

    def test_injected_device_is_not_closed(self):
        fake = FakeU6({48: [0.05]})
        reader = U6Pt100Reader(device=fake)
        reader.close()
        self.assertFalse(fake.closed)
        with self.assertRaises(HardwareUnavailableError):
            reader.read_voltage(48, 56)

    def test_per_call_invalid_sample_count_is_rejected(self):
        fake = FakeU6({48: [0.05]})
        reader = U6Pt100Reader(device=fake)
        with self.assertRaises(ValueError):
            reader.read_voltage(48, 56, samples=0)

    def test_reference_and_rtd_inputs_must_differ(self):
        fake = FakeU6({48: [0.05]})
        reader = U6Pt100Reader(device=fake)
        pair = DifferentialInput(48, 56)
        with self.assertRaises(ValueError):
            reader.read_pt100(pair, pair)
        self.assertEqual(fake.calls, [])

    def test_near_zero_drop_is_rejected(self):
        fake = FakeU6({48: [-60e-6], 84: [-55e-6]})
        reader = U6Pt100Reader(device=fake)
        with self.assertRaisesRegex(MeasurementError, "below the configured minimum"):
            reader.read_pt100(
                DifferentialInput(48, 56),
                DifferentialInput(84, 92),
            )


if __name__ == "__main__":
    unittest.main()
