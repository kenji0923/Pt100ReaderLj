import unittest

from pt100_reader import (
    NOMINAL_EXCITATION_VOLTAGE_V,
    MeasurementError,
    pt100_resistance_to_temperature,
    pt100_temperature_to_resistance,
    rtd_resistance_from_voltages,
)


class ConversionTests(unittest.TestCase):
    def test_nominal_excitation_is_160_mv(self):
        self.assertAlmostEqual(NOMINAL_EXCITATION_VOLTAGE_V, 0.160, places=12)

    def test_voltage_ratio_to_resistance(self):
        resistance = rtd_resistance_from_voltages(
            rtd_voltage_v=0.0666666666667,
            reference_voltage_v=0.0933333333333,
            reference_resistance_ohms=140.0,
        )
        self.assertAlmostEqual(resistance, 100.0, places=9)

    def test_reversed_polarity_on_both_readings_is_valid(self):
        resistance = rtd_resistance_from_voltages(
            rtd_voltage_v=-0.08,
            reference_voltage_v=-0.08,
        )
        self.assertEqual(resistance, 140.0)

    def test_zero_reference_voltage_is_rejected(self):
        with self.assertRaises(MeasurementError):
            rtd_resistance_from_voltages(
                rtd_voltage_v=0.05,
                reference_voltage_v=0.0,
            )

    def test_opposite_polarities_are_rejected(self):
        with self.assertRaises(MeasurementError):
            rtd_resistance_from_voltages(
                rtd_voltage_v=-0.05,
                reference_voltage_v=0.08,
            )

    def test_iec_60751_reference_points(self):
        points = {
            -200.0: 18.52008,
            -100.0: 60.25584,
            0.0: 100.0,
            100.0: 138.5055,
            850.0: 390.481125,
        }
        for temperature, resistance in points.items():
            with self.subTest(temperature=temperature):
                self.assertAlmostEqual(
                    pt100_temperature_to_resistance(temperature), resistance, places=6
                )
                self.assertAlmostEqual(
                    pt100_resistance_to_temperature(resistance), temperature, places=8
                )

    def test_temperature_round_trip(self):
        for temperature in (-199.9, -50.25, -0.01, 0.01, 250.5, 849.9):
            with self.subTest(temperature=temperature):
                resistance = pt100_temperature_to_resistance(temperature)
                self.assertAlmostEqual(
                    pt100_resistance_to_temperature(resistance), temperature, places=8
                )

    def test_out_of_range_resistance_is_rejected(self):
        for resistance in (18.0, 391.0):
            with self.subTest(resistance=resistance):
                with self.assertRaises(MeasurementError):
                    pt100_resistance_to_temperature(resistance)


if __name__ == "__main__":
    unittest.main()

