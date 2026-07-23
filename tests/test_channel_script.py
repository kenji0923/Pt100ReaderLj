import io
import unittest
from contextlib import redirect_stdout

from scripts.test_channel import Sample, print_failed_raw_data


class ChannelScriptTests(unittest.TestCase):
    def test_failed_sensor_prints_raw_voltages_and_error(self):
        sample = Sample(
            reference_voltage_v=0.000125,
            rtd_voltage_v=-0.10585,
            resistance_ohms=None,
            temperature_c=None,
            error="reference drop is too small",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            print_failed_raw_data(3, [sample], 0.160)

        text = output.getvalue()
        self.assertIn("Raw voltage data for failed sensor 3", text)
        self.assertIn("0.000125000", text)
        self.assertIn("-0.105850000", text)
        self.assertIn("-0.105725000", text)
        self.assertIn("reference drop is too small", text)


if __name__ == "__main__":
    unittest.main()

