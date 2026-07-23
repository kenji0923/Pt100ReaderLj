import unittest
from pathlib import Path

import yaml

from pt100_reader import DifferentialInput


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "pt100.yaml"


class ConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_global_values_and_sensor_count(self):
        self.assertEqual(self.config["global"]["vexc_v"], 0.160)
        sensors = self.config["sensors"]
        self.assertEqual([sensor["id"] for sensor in sensors], list(range(1, 21)))
        self.assertTrue(all(sensor["rref_ohms"] == 140.0 for sensor in sensors))

    def test_all_mux80_pairs_are_valid_and_unique(self):
        inputs = []
        for sensor in self.config["sensors"]:
            for key in ("rref_input", "rtd_input"):
                configured = sensor[key]
                channel = DifferentialInput(
                    configured["positive"], configured["negative"]
                )
                channel.validate_for_mux80()
                inputs.append(channel)

        self.assertEqual(len(inputs), 40)
        self.assertEqual(len(set(inputs)), 40)
        expected_positive = (
            list(range(48, 56))
            + list(range(64, 72))
            + list(range(80, 88))
            + list(range(96, 104))
            + list(range(112, 120))
        )
        self.assertEqual(sorted(channel.positive for channel in inputs), expected_positive)

    def test_first_sensor_preserves_tested_mapping(self):
        sensor = self.config["sensors"][0]
        self.assertEqual(sensor["rref_input"], {"positive": 48, "negative": 56})
        self.assertEqual(sensor["rtd_input"], {"positive": 84, "negative": 92})


if __name__ == "__main__":
    unittest.main()
