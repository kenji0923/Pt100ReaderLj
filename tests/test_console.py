"""Unit tests for the web console backend (no hardware required)."""

import math
import tempfile
import unittest
from pathlib import Path

from pt100_console.acquisition import (
    DropState,
    GAIN_LIMITS,
    STEPDOWN_CYCLES,
    classify,
    read_drop,
)
from pt100_console.config import ConsoleConfig, ensure_console_yaml
from pt100_console.telegraf import build_line

from pt100_reader import DifferentialInput


class FakeReader:
    """Returns a gain-dependent reading like a saturating ADC."""

    def __init__(self, true_volts):
        self.true_volts = true_volts
        self.gain_index = 2
        self.reads = 0

    def read_voltage(self, positive, negative, *, samples=1):
        self.reads += 1
        limit = GAIN_LIMITS[self.gain_index]
        value = self.true_volts
        return math.copysign(min(abs(value), limit), value)


class ReadDropTests(unittest.TestCase):
    PAIR = DifferentialInput(48, 56)
    MARGIN = 0.0002

    def test_in_range_stays_at_gain_2(self):
        reader = FakeReader(0.093)
        drop = DropState()
        value, saturated = read_drop(reader, self.PAIR, drop, self.MARGIN)
        self.assertAlmostEqual(value, 0.093)
        self.assertFalse(saturated)
        self.assertEqual(drop.gain, 2)
        self.assertEqual(reader.reads, 1)

    def test_over_range_escalates_and_rereads(self):
        reader = FakeReader(0.150)  # rails at ±0.1 V, fits ±1 V
        drop = DropState()
        value, saturated = read_drop(reader, self.PAIR, drop, self.MARGIN)
        self.assertAlmostEqual(value, 0.150)
        self.assertFalse(saturated)
        self.assertEqual(drop.gain, 1)
        self.assertEqual(reader.reads, 2)  # one re-read within the same cycle

    def test_saturated_at_widest_range(self):
        reader = FakeReader(11.0)
        drop = DropState()
        value, saturated = read_drop(reader, self.PAIR, drop, self.MARGIN)
        self.assertTrue(saturated)
        self.assertEqual(drop.gain, 0)

    def test_step_down_after_hysteresis(self):
        reader = FakeReader(0.150)
        drop = DropState()
        read_drop(reader, self.PAIR, drop, self.MARGIN)
        self.assertEqual(drop.gain, 1)
        reader.true_volts = 0.060  # below 70 % of the ±0.1 V usable limit
        for _ in range(STEPDOWN_CYCLES - 1):
            read_drop(reader, self.PAIR, drop, self.MARGIN)
            self.assertEqual(drop.gain, 1)
        read_drop(reader, self.PAIR, drop, self.MARGIN)
        self.assertEqual(drop.gain, 2)

    def test_no_step_down_inside_dead_band(self):
        reader = FakeReader(0.150)
        drop = DropState()
        read_drop(reader, self.PAIR, drop, self.MARGIN)
        reader.true_volts = 0.090  # fits ±0.1 V but above the 70 % threshold
        for _ in range(STEPDOWN_CYCLES + 5):
            read_drop(reader, self.PAIR, drop, self.MARGIN)
        self.assertEqual(drop.gain, 1)


class ClassifyTests(unittest.TestCase):
    VEXC = 0.160
    MIN = 0.001

    def check(self, vref, vrtd, saturated=False):
        return classify(vref, vrtd, 140.0, saturated, self.VEXC, self.MIN)

    def test_ok(self):
        code, detail, resistance, temperature = self.check(0.0933, 0.0667)
        self.assertEqual(code, "OK")
        self.assertIsNone(detail)
        self.assertAlmostEqual(resistance, 140.0 * 0.0667 / 0.0933, places=9)
        self.assertAlmostEqual(temperature, 0.0, delta=0.6)

    def test_short(self):
        code, detail, *_ = self.check(0.1597, 0.0002)
        self.assertEqual(code, "SHORT")
        self.assertIn("Vexc", detail)

    def test_open(self):
        code, *_ = self.check(0.0002, 0.1597)
        self.assertEqual(code, "OPEN")

    def test_excloss(self):
        code, *_ = self.check(0.0002, 0.0003)
        self.assertEqual(code, "EXCLOSS")

    def test_sumerr(self):
        code, *_ = self.check(0.100, 0.070)
        self.assertEqual(code, "SUMERR")

    def test_polarity(self):
        code, *_ = self.check(0.0933, -0.0667)
        self.assertEqual(code, "POLARITY")

    def test_range(self):
        # Vrtd/Vref ratio puts R far above the IEC 60751 span.
        code, *_ = self.check(0.030, 0.120)
        self.assertEqual(code, "RANGE")

    def test_overrange(self):
        code, *_ = self.check(0.05, 0.05, saturated=True)
        self.assertEqual(code, "OVERRANGE")


class ConfigTests(unittest.TestCase):
    def make_config(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "config" / "console.yaml"
        ensure_console_yaml(path)
        self.addCleanup(self.tmp.cleanup)
        return ConsoleConfig(path)

    def test_defaults_and_override_and_revert(self):
        config = self.make_config()
        self.assertFalse(config.defaults()["polling_enabled"])
        config.apply_settings_diff(
            {
                "defaults": {"polling_enabled": True, "telegraf": {"port": 9000}},
                "channels": {"3": {"polling_interval_s": 5, "name": "probe_a"}},
            }
        )
        self.assertTrue(config.defaults()["polling_enabled"])
        self.assertEqual(config.defaults()["telegraf"]["port"], 9000)
        self.assertEqual(config.effective(3)["polling_interval_s"], 5)
        self.assertEqual(config.effective(3)["name"], "probe_a")
        # revert the channel override; entry disappears entirely
        config.apply_settings_diff(
            {"channels": {"3": {"polling_interval_s": None, "name": None}}}
        )
        self.assertEqual(config.channel_overrides(3), {})
        self.assertEqual(config.effective(3)["polling_interval_s"], 10.0)

    def test_tags_are_replaced_not_merged(self):
        config = self.make_config()
        config.apply_settings_diff({"channels": {"1": {"tags": {"a": "1", "b": "2"}}}})
        config.apply_settings_diff({"channels": {"1": {"tags": {"c": "3"}}}})
        self.assertEqual(config.channel_overrides(1)["tags"], {"c": "3"})
        config.apply_settings_diff({"channels": {"1": {"tags": None}}})
        self.assertEqual(config.channel_overrides(1), {})

    def test_flush_round_trip(self):
        config = self.make_config()
        config.apply_settings_diff({"console_name": "bench-2"})
        config.flush()
        reloaded = ConsoleConfig(config.path)
        self.assertEqual(reloaded.console_name, "bench-2")


class TelegrafLineTests(unittest.TestCase):
    def test_line_format_and_escaping(self):
        line = build_line(
            "temperature",
            {"console": "pt100 lab", "channel": 3, "name": "pt100_03", "tlg_bucket": "b,1"},
            {"ok": 1, "status": "OK", "temperature_c": 23.5},
            1753274400000000000,
        )
        self.assertTrue(line.startswith("temperature,"))
        self.assertIn("console=pt100\\ lab", line)
        self.assertIn("tlg_bucket=b\\,1", line)
        self.assertIn("channel=3", line)
        self.assertIn("ok=1i", line)
        self.assertIn('status="OK"', line)
        self.assertIn("temperature_c=23.5", line)
        self.assertTrue(line.endswith(" 1753274400000000000"))

    def test_empty_tags_are_dropped(self):
        line = build_line("temperature", {"tlg_bucket": ""}, {"ok": 0}, 1)
        self.assertNotIn("tlg_bucket", line)


if __name__ == "__main__":
    unittest.main()
