# U6 Pro + Mux80 wiring map

This allocation preserves the tested first sensor and divides all 40 Mux80
differential pairs into a 20-pair reference-resistor bank followed by a
20-pair RTD bank. `+` and `-` indicate the polarity passed to the differential
measurement. `X# / label` is the connector and printed terminal label on a
CB37 attached to that Mux80 connector.

| Sensor | Rref AIN pair | Rref CB37 terminals (+ / -) | RTD AIN pair | RTD CB37 terminals (+ / -) |
|---:|---:|---|---:|---|
| 1 | 48 / 56 | X3/AIN0 / X3/AIN8 | 84 / 92 | X4/AIN12 / X4/FIO4 |
| 2 | 49 / 57 | X3/AIN1 / X3/AIN9 | 85 / 93 | X4/AIN13 / X4/FIO5 |
| 3 | 50 / 58 | X3/AIN2 / X3/AIN10 | 86 / 94 | X4/DAC0 / X4/FIO6 |
| 4 | 51 / 59 | X3/AIN3 / X3/AIN11 | 87 / 95 | X4/DAC1 / X4/FIO7 |
| 5 | 52 / 60 | X3/AIN4 / X3/AIN12 | 96 / 104 | X5/AIN0 / X5/AIN8 |
| 6 | 53 / 61 | X3/AIN5 / X3/AIN13 | 97 / 105 | X5/AIN1 / X5/AIN9 |
| 7 | 54 / 62 | X3/AIN6 / X3/DAC0 | 98 / 106 | X5/AIN2 / X5/AIN10 |
| 8 | 55 / 63 | X3/AIN7 / X3/DAC1 | 99 / 107 | X5/AIN3 / X5/AIN11 |
| 9 | 64 / 72 | X3/FIO0 / X4/AIN0 | 100 / 108 | X5/AIN4 / X5/AIN12 |
| 10 | 65 / 73 | X3/FIO1 / X4/AIN1 | 101 / 109 | X5/AIN5 / X5/AIN13 |
| 11 | 66 / 74 | X3/FIO2 / X4/AIN2 | 102 / 110 | X5/AIN6 / X5/DAC0 |
| 12 | 67 / 75 | X3/FIO3 / X4/AIN3 | 103 / 111 | X5/AIN7 / X5/DAC1 |
| 13 | 68 / 76 | X3/FIO4 / X4/AIN4 | 112 / 120 | X5/FIO0 / X2/AIN4 |
| 14 | 69 / 77 | X3/FIO5 / X4/AIN5 | 113 / 121 | X5/FIO1 / X2/AIN5 |
| 15 | 70 / 78 | X3/FIO6 / X4/AIN6 | 114 / 122 | X5/FIO2 / X2/AIN6 |
| 16 | 71 / 79 | X3/FIO7 / X4/AIN7 | 115 / 123 | X5/FIO3 / X2/AIN7 |
| 17 | 80 / 88 | X4/AIN8 / X4/FIO0 | 116 / 124 | X5/FIO4 / X2/AIN8 |
| 18 | 81 / 89 | X4/AIN9 / X4/FIO1 | 117 / 125 | X5/FIO5 / X2/AIN9 |
| 19 | 82 / 90 | X4/AIN10 / X4/FIO2 | 118 / 126 | X5/FIO6 / X2/AIN10 |
| 20 | 83 / 91 | X4/AIN11 / X4/FIO3 | 119 / 127 | X5/FIO7 / X2/AIN11 |

## Wiring notes

- A Mux80 differential pair uses a positive channel in one of AIN48-55,
  AIN64-71, AIN80-87, AIN96-103, or AIN112-119. Its negative channel is the
  positive channel plus 8.
- CB37 terminals printed `FIO#` and `DAC#` in the table are analog Mux80
  connections in this application; use the connector column as well as the
  printed terminal label to identify them.
- Pairs 64-79 cross from X3 to X4. Pairs 112-127 cross from X5 to X2. These
  split pairs are required to use all 40 differential inputs.
- Keep the sense leads high impedance and connect both midpoint sense leads at
  the physical Rref/RTD junction. Do not route sensor or excitation current
  through a voltage-sense lead.
- AIN0-AIN1 and AIN2-AIN3 remain available on the U6/Mux80. They can be kept
  as diagnostic differential inputs; for example, AIN0-AIN1 could monitor the
  global excitation using the +/-1 V range. Vexc monitoring is not needed for
  the resistance-ratio calculation.
- On U6/U6-Pro, MIO0-MIO2 used by the Mux80 are shared with CIO0-CIO2, so do
  not allocate CIO0-CIO2 to other functions.

Sources: [Mux80 AIN Expansion Board datasheet](https://support.labjack.com/docs/mux80-ain-expansion-board-datasheet),
[U6 channel-number documentation](https://support.labjack.com/docs/2-6-1-channel-numbers),
and [U6 analog-input ranges and resolution](https://support.labjack.com/docs/4-1-3-analog-inputs-u6-datasheet).

