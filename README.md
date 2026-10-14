# Pt100ReaderLj

Python library for reading Pt100 RTDs with a LabJack U6 Pro and Mux80. Each
sensor is measured as two differential voltage drops: one across its series
reference resistor and one across the RTD.

The circuit's nominal excitation is 160 mV:

```text
ADR4520 (2.048 V) -- 118 kOhm --+-- 10 kOhm -- GND
                                |
                              0.160 V
```

The library calculates resistance from the voltage ratio, so the nominal
excitation voltage is not used in the resistance calculation:

```text
R_RTD = R_ref * V_RTD / V_ref
```

It converts resistance to temperature with the IEC 60751 Callendar-Van Dusen
curve over -200 to 850 degC.

The complete 20-sensor assignment is in
[`config/pt100.yaml`](config/pt100.yaml), with connector and CB37 terminal
locations in [`WIRING.md`](WIRING.md).

## Install

Create a virtual environment, activate it, then install this project and the
LabJackPython hardware dependency:

```shell
python -m pip install -e ".[labjack]"
```

On Windows, first install LabJack's
[UD software](https://support.labjack.com/docs/ud-software-installer-downloads-u3-u6-ue9).

On Linux, first install `libusb-1.0` and LabJack's
[Exodriver](https://support.labjack.com/docs/exodriver-downloads-for-ud-series-linux-and-macos-).
For Debian/Ubuntu, the native prerequisites and driver installation are:

```shell
sudo apt-get install build-essential libusb-1.0-0-dev git
git clone https://github.com/labjack/exodriver.git
cd exodriver
sudo ./install.sh
```

The Exodriver installer also installs the LabJack udev rule needed for
non-root USB access. Unplug and reconnect the U6 after installation.

## Use

```python
from pt100_reader import DifferentialInput, U6Pt100Reader

# Example assignment. Verify these numbers against the actual Mux80 wiring.
reference_drop = DifferentialInput(48, 56)
rtd_drop = DifferentialInput(84, 92)

with U6Pt100Reader(
    samples=10,
    reference_resistance_ohms=140.0,  # Prefer the resistor's measured value.
    minimum_drop_voltage_v=1e-3,      # Reject disconnected/near-zero signals.
) as reader:
    voltage = reader.read_voltage(48, 56)
    print(f"reference drop: {voltage * 1_000:.6f} mV")

    result = reader.read_pt100(reference_drop, rtd_drop)
    print(f"Vref: {result.reference_voltage_v * 1_000:.6f} mV")
    print(f"Vrtd: {result.rtd_voltage_v * 1_000:.6f} mV")
    print(f"RTD:  {result.resistance_ohms:.6f} ohm")
    print(f"Temp: {result.temperature_c:.6f} degC")
```

Pass `serial=...` or `local_id=...` to `U6Pt100Reader` when more than one U6 is
connected. The defaults select U6 Pro resolution index 12 and gain index 2,
which is the nominal +/-0.1 V differential range.

For Mux80 differential inputs, valid positive-channel blocks are AIN48-55,
AIN64-71, AIN80-87, AIN96-103, and AIN112-119. The corresponding negative
channel is always positive + 8. Built-in AIN0-AIN1 and AIN2-AIN3 are also
accepted. The reader validates this before accessing the hardware.

## Important range note

At 0 degC with a nominal 160 mV excitation, 140 ohm reference, and 100 ohm
Pt100, the expected drops are about 93.33 mV and 66.67 mV. They fit the U6
nominal +/-0.1 V range. Across the full IEC Pt100 temperature range, however,
one of the two drops can exceed that input range. Choose a wider U6 range or
change the excitation/reference design if the intended temperature range can
cause either measured drop to exceed the selected range. With nominal component
values and positive polarity, both drops remain below the U6's +101 mV limit
only from approximately -46 to +379 degC.

Both differential readings must use the same polarity. Reversing both pairs is
valid; reversing only one produces a negative calculated resistance and raises
`MeasurementError`. By default, `read_pt100` also rejects either voltage drop
below 1 mV so that dividing two input offsets cannot produce a believable but
invalid temperature. Set `minimum_drop_voltage_v=0` only if deliberately using
smaller signals.

## Run tests

Test one configured sensor with eight high-resolution readings:

```shell
python scripts/test_channel.py 1
```

The positional argument is the sensor ID from `config/pt100.yaml`. Use
`--reads N` to override the default of eight readings or `--config PATH` to use
another configuration file. The script prints every result, average, sample
standard deviation, the Vexc sum check, and the achieved acquisition rate. It
uses the U6 Pro 24-bit high-resolution ADC at configured resolution index 12 by
default. Add `--fast` to use resolution index 1 for a maximum-speed diagnostic:

```shell
python scripts/test_channel.py 1 --fast
```

To test every configured sensor from 1 through an inclusive target, use
`--up-to`. Invalid or disconnected sensors are reported without stopping the
remaining tests. A concise aggregate table is printed after the detailed
per-reading results. Sensors with `enabled: false` in the YAML are listed as
`SKIPPED` and are not sampled:

```shell
python scripts/test_channel.py 5 --up-to
```

Acquisition is interleaved by cycle. With sensors 1 through 3 enabled, the
hardware order is `1 -> 2 -> 3 -> 1 -> 2 -> 3 ...`, rather than taking every
reading from sensor 1 before moving to sensor 2.

When a sensor fails measurement validation or the Vexc sum check, an additional
failure-only table prints every raw Vref, Vrtd, and voltage sum in volts with
the associated validation error.

## Web console

A single-process web console (no dependencies beyond PyYAML) provides live
monitoring, per-channel configuration with default-following overrides,
per-drop auto-ranging, Telegraf output, and a channel-test feature:

```shell
python -m pt100_console --config config/console.yaml
```

The configuration file is created on first run; polling and telemetry start
Off so the console can be configured before any data is sent. Open
`http://localhost:8090/` (port per `config/console.yaml`). The design is
documented in [`docs/console-design.md`](docs/console-design.md); a systemd
unit example is in [`deploy/pt100-console.service`](deploy/pt100-console.service).

### Changing the listen address of a running deployment

The bind address is `server.host` in `config/console.yaml` (`0.0.0.0` = all
interfaces; `--host` on the command line overrides it). The console writes its
in-memory configuration back to this file on shutdown, so a plain edit followed
by a restart is overwritten. Edit the file, have the running process reload it
with SIGHUP, then restart so the new address is bound:

```shell
# e.g. restrict the deployment under /opt to the lab network interface
sed -i '/^server:/,/^[^ ]/ s/^  host: .*/  host: 192.168.1.5/' \
  /opt/Pt100ReaderLj/Pt100ReaderLj/config/console.yaml
sudo systemctl reload pt100-console    # SIGHUP: reload console.yaml into memory
sudo systemctl restart pt100-console   # rebind on the new address
ss -tlnp | grep 8091                   # expect 192.168.1.5:8091 only
```

## Run unit tests

No LabJack is required for the unit tests:

```shell
python -m unittest discover -s tests -v
```

To check that the web console shuts down cleanly on SIGTERM (exit status 0,
pending settings flushed) without touching the LabJack, run a throwaway
instance with a fresh configuration (polling starts Off) on a spare port:

```shell
mkdir -p /tmp/pt100-e2e/config
python -m pt100_console --config /tmp/pt100-e2e/config/console.yaml \
  --host 127.0.0.1 --port 18091 &
sleep 1                                            # wait for the listener
curl -s -X POST -d '{"console_name":"e2e-flush"}' \
  http://127.0.0.1:18091/api/settings
kill -TERM $!; wait $!; echo "exit=$?"            # expect exit=0
grep console_name /tmp/pt100-e2e/config/console.yaml  # expect e2e-flush
```
