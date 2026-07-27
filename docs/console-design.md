# Pt100 Web Console — Design Specification (draft v0.2)

Status: draft for mockup review. The backend implementation approach will be
specified separately and is intentionally left open here. All user-facing text
(this document and the web UI) is in English.

## 1. Overview

- A single Python process serving a web UI for configuration and monitoring.
- Must be able to run as a Linux systemd service later (unit example in §9);
  also runs on Windows for development.
- Two pages: **Basic settings** and **Advanced**, both table-centric.
- Measurement uses the existing `pt100_reader` library. Wiring definitions
  (AIN mapping, Rref, serial) stay in the existing `config/pt100.yaml`;
  console-specific settings live in a new `config/console.yaml` (§8).

## 2. Settings model (defaults + per-channel overrides)

- Every tunable setting has one global default. Each channel, per setting,
  either:
  1. **follows the default** (initial state), or
  2. **overrides it with its own value**.
- In the UI, the first entry of every per-channel dropdown is
  "Default (current: X)". Channels in this state automatically track later
  changes to the default.
- Free input: dropdowns offer a "Custom…" entry that switches the cell to a
  text input (mainly for the Telegraf destination and bucket). This is
  feasible and is demonstrated in the mockup.
- Persistence: `console.yaml` stores overrides only; an omitted key means
  "follow default".
- Polling follows the same model: a global default (Off on first run) plus a
  per-channel Default / On / Off choice. A channel that is effectively OFF is
  excluded from the acquisition cycle and from Telegraf output.
- Telemetry (Telegraf output) is a separate switch with the same model: a
  global default (Off on first run) plus a per-channel Default / On / Off
  choice. A channel sends records only when it is effectively polled **and**
  its telemetry is effectively On — so a channel can be acquired and shown
  in the UI without being sent.

## 3. Basic settings page

### 3.1 Default settings (form)

All settings apply instantly as they are changed (§3.4); there is no Save
button.

| Field | Maps to | Default (proposal) |
|---|---|---|
| Polling | master enable; channels follow it unless overridden | **Off** — the console starts silent so the console name, Telegraf, and channels can be configured before telemetry begins |
| Telemetry | master enable for Telegraf output; channels follow it unless overridden | **Off** — enable once the destination and tags are configured |
| Accuracy | U6 `resolution_index` (1–12) | 12 |
| Settling | U6 `settling_factor` (0 = auto, 1–9) | 0 (auto) |
| Polling interval [s] | acquisition loop period (§5) | 10 |
| Telegraf protocol | udp / tcp / http | udp |
| Telegraf address / port | destination | 127.0.0.1 : 8094 |
| Bucket name | sent as tag `tlg_bucket` on every record | (to be set) |

Accuracy (`resolution_index`) guide:

| resolution_index | ADC | Notes |
|---|---|---|
| 1–8 | 16 bit | fast (~ms per point), higher noise |
| 9–12 | 24 bit (U6-Pro only) | low noise; roughly 20/40/80/160 ms per point for 9→12 |

(Approximate; see the U6 datasheet resolution tables for exact values.)

### 3.2 Input ranges: fixed start, automatic per-drop adjustment

There is **no configured temperature range** — neither a default nor a
per-channel setting. Every differential drop starts at the design range
**±0.1 V** and auto-range adapts from there, so channels need no
range-related configuration at all.

For reference, with nominal Vexc = 0.160 V, per-channel Rref
(default 140 Ω), and IEC 60751 R(T):

```
V_ref(T) = Vexc · Rref / (Rref + R(T))    → grows as T falls
V_rtd(T) = Vexc · R(T) / (Rref + R(T))    → grows as T rises
```

Coverage with nominal values (usable limit ≈ 101 mV on the ±0.1 V range):

| Drop | Leaves ±0.1 V when | Value at the IEC extreme |
|---|---|---|
| Vref | T below ≈ −46 °C | ≈ 141 mV at −200 °C |
| Vrtd | T above ≈ +379 °C | ≈ 118 mV at +850 °C |

Since each drop is bounded by Vexc ≈ 160 mV, **±1 V covers the entire
−200 to +850 °C span for both drops**. ±10 V is unreachable in normal
operation; a signal that needs it implies an external fault (§6).

**Automatic range adjustment (auto-range).**

- **Each differential drop (Vref and Vrtd) keeps its own active range** as
  runtime state, initialized to ±0.1 V. The Rref and RTD measurements may
  use different ranges — resistance is the ratio of two independently
  calibrated voltage readings, so mixed ranges are valid (e.g. at −200 °C:
  Vref on ±1 V while Vrtd stays on ±0.1 V for best resolution).
- When a reading reaches the usable limit of its active range, the console
  steps that drop up one range (±0.1 → ±1 → ±10 V) and **re-reads
  immediately within the same cycle** — no data gap, and a recoverable
  over-range is not reported as a fault.
- Only if the reading is still saturated at ±10 V is the channel marked
  FAULT (§6); at that point it is a wiring/excitation problem, not a range
  problem.
- **Fault detection is preserved.** All §6 validation runs on the readings
  taken at the cycle's final range, so a shorted or open RTD is still
  detected. Escalation removes only *range* faults — in fact it improves
  diagnosis: a signal that merely rails at ±0.1 V becomes measurable at
  ±1 V (both drops sum to ~160 mV at most), which lets the console
  classify the wiring fault by its signature (§6).
- **Automatic step-down** (with hysteresis): when a drop stays below 70 %
  of the next narrower range's usable limit for 10 consecutive cycles,
  that drop narrows by one step, down to the ±0.1 V floor (with the
  nominal 160 mV excitation no valid drop can ever fit ±0.01 V, so gain 3
  is never used). No re-read is needed on a step-down — the current
  reading already proves the signal fits — so it simply takes effect from
  the next cycle. The 70 % / 10-cycle hysteresis leaves a dead band
  between the step-down threshold (≈ 71 mV for returning to ±0.1 V) and
  the step-up trigger (≈ 101 mV), so a signal hovering near the boundary
  settles at the wider range instead of oscillating. Step-downs are
  logged like step-ups. This recovers the low-noise ±0.1 V range after a
  transient excursion.
- Implementation: the U6 accepts a gain index on every `getAIN` call, so
  per-drop software control is straightforward. The U6 also offers
  hardware autorange (`gain_index` 15), but software control is preferred so
  the console always knows the active range and can apply hysteresis.
- Every range change is logged. The **active ranges (Rref / RTD) are
  displayed on the Advanced page** (§4).

### 3.3 Channel table (settings + status)

One row per channel (20 rows):

| Group | Columns |
|---|---|
| Identity | CH, Name (editable) |
| Acquisition | Polling (Default / On / Off), Accuracy, Settling, Interval [s] |
| Telegraf | Telemetry (Default / On / Off), Destination (protocol://address:port), tlg_bucket, Extra tags |
| Status | Latest temp [°C], Status, Updated |

- Setting columns use the §2 dropdowns (follow default / own value / custom
  free input).
- Extra tags: per-channel Telegraf tags, free-form `key=value`, comma
  separated.
- Name is editable and becomes the Telegraf `name` tag. The default comes
  from pt100.yaml; an edited name is stored as a console.yaml override.
  The `channel` number is fixed (not editable).
- Status per §6; FAULT rows show the reason string.

### 3.4 GUI synchronization (adapted from the fanlab gui-sync-pattern)

Follows `labscript-suite-fork-kenji/userlib/user_devices/fanlab_devices/doc/gui-sync-pattern.md`,
adapted to one web backend (**master**) and N browser tabs (**slaves**).

**Roles and value planes.** The backend owns the device and the settings
snapshot — the single source of truth. Browser tabs never touch the
hardware; they reconcile against the backend snapshot and submit writes to
it. The three value planes stay strictly separate: **setpoints**
(settings), **in-edit inputs** (browser-local, the only plane
reconciliation must not touch), and **measurements** (temperatures, raw
voltages, active ranges — displayed unconditionally, never gated by
editing state).

**Commit gestures.**

- Discrete controls (dropdowns, polling toggles) commit immediately on
  change — this satisfies the "deliver changes instantly" requirement.
- Text fields (name, tags, custom Telegraf values, numbers) follow the
  input state machine: SYNCED → EDITING (focus) → PENDING (value ≠
  last-fetched, amber) → CONFIRMING (committed, blue) → SYNCED on ack plus
  a matching fetch, or REJECTED (red + banner row) on error/timeout.
  **Enter** commits all pending fields as one bulk write; **Esc** reverts
  the focused field, and cascades panel-wide on a second press. Esc
  pressed while no field is focused reverts panel-wide directly.
  Un-committed edits auto-revert after `edit_expiry_ms`; the banner shows
  the 1 Hz countdown.
- Modified detection is **value-based, not event-based**, with amnesty: a
  field whose value equals the master's is SYNCED regardless of what
  events fired (prevents the sticky-dirty-latch desync).
- Commits are **never deduplicated** against previously sent values —
  retyping the same number after a failure must send again.

**Transport pacing (30 ms).** Commits land in a pending dict keyed by
field (**latest value wins**, so stacked edits collapse; merging per field
means edits to *different* fields are never lost), drained by a single
sender: the first commit goes out immediately, then at most one request
per 30 ms carrying the merged diff. At most one request is in flight at a
time, and a per-field token keeps a late result from settling a newer
commit with a stale outcome. The backend additionally rate-limits and
applies diffs idempotently as defense in depth.

**Reconciliation and monitors.** Each tab polls the backend snapshot
(settings + latest measurements) at `poll_interval_ms` and reconciles
every widget per the state machine; measurements update unconditionally on
every poll. When `now − last successful fetch` exceeds
`max(3 s, 10 × poll_interval_ms)` the status row becomes a staleness
warning naming the backend. Reconciliation through the master is also what
keeps multiple browser tabs converged with each other.

**Status banner.** Always visible, never hidden — a healthy panel shows
`Synced · hh:mm:ss` with the clock advancing at 1 Hz (the liveness
heartbeat). Segments are appended for pending / applying / rejected
counts; the worst state colors the row (red > blue > amber). Error rows
persist below the status row until acknowledged or superseded.

**Timing constants** (fixed in configuration, deliberately not
runtime-editable):

| Constant | Default | Meaning |
|---|---|---|
| `poll_interval_ms` | 100 | snapshot reconcile / monitor poll period |
| `commit_throttle_ms` | 30 | minimum spacing between commit requests |
| `apply_timeout_ms` | 3000 | silence after a commit before REJECTED |
| `edit_expiry_ms` | 60000 | idle time before un-committed edits revert |

- The backend applies accepted diffs to the in-memory snapshot immediately
  (§5.1) but debounces disk writes of console.yaml (~1 s) so a burst of
  edits does not hammer storage.

## 4. Advanced page

Settings on this page:

| Field | Maps to | Default (proposal) |
|---|---|---|
| Console name | Telegraf tag `console` on every record (§7) | pt100-lab |

Read-only detail table:

| Group | Columns |
|---|---|
| Identity | CH, Name |
| Wiring (from pt100.yaml) | Rref [Ω], Rref AIN (+/−), RTD AIN (+/−) |
| Range & raw voltages | **Active ranges** (Rref / RTD, per-drop auto-range state, e.g. ±0.1 / ±1 V), Vref [mV], Vrtd [mV], Sum [mV] |
| Derived | R_RTD [Ω], Temp [°C] |
| Status | Status, Updated |

- Rref and AIN numbers display `config/pt100.yaml` content (read-only for
  now; editing stays in the YAML — see §10).
- Raw voltages are the averages of the latest acquisition cycle. Sum
  supports the excitation sanity check (§6).

### 4.1 Channel test

A diagnostic run equivalent to `scripts/test_channel.py --up-to`, driven
from the Advanced page.

- Controls: **reads per channel** (default 8), a live **estimated
  duration** (enabled channels × reads × 2 points × per-point conversion
  time — the measured per-point time when known, otherwise the datasheet
  estimate for the current Accuracy), and **Start / Abort** buttons.
- While running: Telegraf output is suspended (regardless of telemetry
  settings) and the normal EDF polling loop pauses — the test owns the
  device. Channels with polling effectively On are read in even
  interleave (1 → 2 → … → n, repeated) at maximum speed with the
  **current Accuracy and Settling**; auto-range stays active. Progress
  (points done / total, elapsed) is shown live.
- **Abort** stops after the in-flight point; partial results are kept and
  marked `aborted`.
- Results: per-channel table — reads, mean and sample standard deviation
  (N−1) of Vref and Vrtd [mV], mean R [Ω], mean and σ of T [°C], status
  code (§6). Metadata: console name, timestamp, Accuracy/Settling,
  elapsed time, the **achieved rate (channel reads/s and AIN points/s)**,
  and the aborted flag — included in the on-screen summary, the HTML
  report, and the JSON.
- Output: a **self-contained standalone HTML report** and a **JSON file**
  (metadata + per-channel stats + raw readings), both written on the
  console machine under `reports_dir` (default `reports/`, filenames
  `channel-test_<console>_<YYYYMMDD-HHMMSS>.html` / `.json`). The UI
  shows the result table inline, opens the HTML report in a new tab, and
  offers both files for download; recent reports are listed.
- When the test ends (or aborts), telemetry and the EDF polling loop
  resume automatically.

## 5. Acquisition scheduling

- The U6 is a single device with serialized access, so there is one
  acquisition loop. Enabled channels are interleaved in ID order (same
  approach as the existing `test_channel.py --up-to`).
- Channels can have individual intervals: each cycle acquires only the
  channels whose deadline has arrived.
- The measured cycle time is kept and shown in the UI. If a configured
  interval is shorter than the measured cycle time, the UI notes that the
  effective rate is limited by the cycle time (not an error).
  Rough guide: 20 ch × 2 reads × ~160 ms (resolution 12) ≈ 6.5 s per cycle.

### 5.1 Configuration reload (≤ 1 s, no busy polling)

- Settings changed in the web UI arrive through the console's own HTTP
  handler, so no polling is involved at all: the handler atomically swaps an
  immutable in-memory config snapshot, persists `console.yaml`, and sets a
  `threading.Event`.
- The acquisition loop never sleeps blindly: it waits on that Event with a
  timeout equal to the time until the next channel is due. A config change
  wakes it immediately (effectively 0 s, well under the 1 s requirement),
  and an idle loop consumes nothing while waiting.
- For `console.yaml` edited by hand while the service runs, a file watcher
  (inotify via `watchdog` on Linux, `ReadDirectoryChangesW` on Windows)
  feeds the same Event. A 1 Hz mtime `stat()` is an acceptable fallback —
  one syscall per second is negligible, not busy polling.
- Optionally `ExecReload=` in the systemd unit sends SIGHUP as an explicit
  reload path.

### 5.3 Read ordering with unequal intervals

- Scheduling is **deadline-based** (earliest deadline first, EDF). Each
  enabled channel keeps a `next_due` time; the loop always acquires the
  due channel with the earliest `next_due`, breaking ties by channel ID —
  so with equal intervals the order is the familiar 1 → 2 → … → 20
  interleave.
- After acquisition, `next_due += interval` (fixed cadence, no drift). If
  the loop has fallen behind, it clamps:
  `next_due = max(next_due + interval, now)` — one late read does not
  trigger a burst of catch-up reads.
- If the requested rates exceed what the ADC can deliver (§5), deadlines
  slip proportionally for every channel — EDF degrades fairly and no
  channel starves. The UI shows measured vs. configured rates.
- When nothing is due, the loop waits on the config-change Event (§5.1)
  with a timeout equal to the time until the earliest deadline.

### 5.2 Hardware concurrency (U6 + Mux80)

- The U6 cannot sample two inputs at the same instant. Every reading goes
  through one converter (16-bit SAR, or the U6-Pro's 24-bit sigma-delta for
  resolution 9–12), so acquisition is inherently sequential.
- The Mux80 is not one 80-way switch: it is **ten 8:1 multiplexers** sitting
  on U6 AIN4–AIN13, all sharing one 3-bit select bus (MIO0–2). One select
  setting therefore routes ten external lines — five differential pairs —
  onto the U6 pins *simultaneously*; this is also why a valid pair is always
  "negative = positive + 8" (both ends of a pair sit at the same mux
  position on adjacent U6 pins). But the connected lines are still
  *converted* one at a time by the single ADC.
- Ordering reads by mux position would minimize mux switching, but switching
  costs microseconds while a resolution-12 conversion costs ~160 ms, so the
  cycle time is set by the ADC, not the mux. Meaningful speedups come only
  from a lower resolution index (or stream mode, which is limited to the
  16-bit converter, resolution ≤ 8) — a noise-versus-speed trade, not a
  parallelism opportunity.

## 6. Status determination

Every acquisition yields a short **status code**. The UI displays the code
(plus a human-readable detail line on faults); telemetry carries both the
code and the numeric `ok` 1/0 (§7). Criteria follow the existing
`scripts/test_channel.py` validation, except that recoverable over-ranges
trigger auto-range escalation (§3.2) instead of a fault.

| Code | Condition (nominal Vexc ≈ 160 mV) | Typical cause |
|---|---|---|
| `OK` | all checks pass | — |
| `COMM` | acquisition exception | USB/driver error |
| `OVERRANGE` | still saturated at ±10 V after escalation | external voltage or gross miswiring |
| `SHORT` | Vrtd < 1 mV and Vref ≈ Vexc | RTD or its wiring shorted |
| `OPEN` | Vref < 1 mV and Vrtd ≈ Vexc | RTD open / broken lead (or Rref short) |
| `EXCLOSS` | both drops < 1 mV | excitation lost / wiring fault |
| `SUMERR` | \|Vref + Vrtd\| > Vexc | excitation sanity check failed |
| `POLARITY` | computed resistance not positive | one differential pair reversed |
| `RANGE` | temperature outside IEC 60751 (−200 to 850 °C) | resistance outside the Pt100 curve |

`OK` means ok = 1; every other code means ok = 0. Codes are evaluated in
the table order (most fundamental first: no data → unmeasurable → wiring
signatures → consistency checks); the first match wins and the detail
line lists everything that failed. The SHORT/OPEN/EXCLOSS signatures are
measurable thanks to auto-range escalation (§3.2).

UI-only display states (never sent to Telegraf): **OFF** (polling
disabled — the channel is neither acquired nor sent), **NO DATA** (just
started), and a **STALE** overlay when the last update is older than
3 × interval (distinct from a fault).

## 7. Telegraf output

- InfluxDB line protocol, one record per channel per acquisition.
- measurement: `temperature` (configurable)
- tags on every record:
  - `console` — the console name, editable on the Advanced page (§4)
  - `channel` — channel number (1–20)
  - `name` — sensor name from pt100.yaml
  - `tlg_bucket` — bucket name (§3.1)
  - plus per-channel extra tags
- fields: `temperature_c`, `resistance_ohms`, `vref_v`, `vrtd_v`,
  `ok` (1/0 — numeric, for easy alerting/aggregation), `status` (the §6
  short code as a string field, e.g. `"OK"`, `"SHORT"`), and `range_ref_v` /
  `range_rtd_v` — the per-drop active ranges in volts, e.g. `0.1`, for
  data-quality tracking
- timestamp: ns epoch
- The judgment is part of every record via `ok` + `status`. The
  human-readable detail line stays in the UI and logs only.
- A record is sent only when the channel is effectively polled **and** its
  telemetry is effectively On (§2). Polling-OFF channels are not sent at
  all; telemetry-Off channels are acquired and displayed but not sent.
  All output is suspended while a channel test runs (§4.1).
- On a fault: send `ok=0`, the `status` code, and whatever raw voltages
  were read; omit temperature and resistance so downstream aggregation is
  not polluted.

Example:

```
temperature,console=pt100-lab,channel=3,name=pt100_03,tlg_bucket=chamber_a,location=chamber_A temperature_c=23.412,resistance_ohms=109.117,vref_v=0.089951,vrtd_v=0.070112,ok=1,status="OK",range_v=0.1 1753274400000000000
```

Protocol support (Telegraf input plugin side):

| Setting | Transport | Telegraf side |
|---|---|---|
| udp | UDP datagram | `inputs.socket_listener` (udp://) |
| tcp | TCP stream | `inputs.socket_listener` (tcp://) |
| http | HTTP POST (body = line protocol) | `inputs.http_listener_v2` |

## 8. Configuration file draft (config/console.yaml)

```yaml
schema_version: 1

console_name: pt100-lab         # Telegraf tag `console`; editable on Advanced page
reports_dir: reports            # channel-test HTML/JSON output (§4.1)

server:
  host: 0.0.0.0
  port: 8080

hardware:
  config: config/pt100.yaml     # wiring, Rref, serial (existing file)

defaults:
  polling_enabled: false        # starts Off: configure console name/Telegraf first
  telemetry_enabled: false      # starts Off: Telegraf output gated separately
  polling_interval_s: 10
  resolution_index: 12
  settling_factor: 0            # 0 = auto
  telegraf:
    protocol: udp               # udp | tcp | http
    address: 127.0.0.1
    port: 8094
    tlg_bucket: fanlab
    measurement: temperature

channels:                       # overrides only; omitted key = follow default
  - id: 3
    name: chamber_a_top         # Telegraf `name` tag; default from pt100.yaml
    polling_interval_s: 5
    telegraf:
      tlg_bucket: chamber_a
    tags:
      location: chamber_A
  - id: 6
    polling_enabled: false      # explicit Off, regardless of the default
  - id: 12
    telemetry_enabled: false    # acquired and displayed, but not sent
```

- The UI writes the same shape (overrides only), atomically, and the change
  takes effect after saving.
- The active range is runtime state, not configuration — it is neither saved
  here nor editable.

## 9. systemd unit example

```ini
[Unit]
Description=Pt100 web console (LabJack U6 Pro + Mux80)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pt100
WorkingDirectory=/opt/pt100readerlj
ExecStart=/opt/pt100readerlj/.venv/bin/python -m pt100_console --config /etc/pt100/console.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- The entry-point name `pt100_console` is provisional.
- On SIGTERM the acquisition loop stops and the U6 is closed before exit
  (systemd stop support).
- On Linux the Exodriver and its udev rule are prerequisites (see README).

## 10. Open items

1. Backend implementation approach — **decided and implemented**: standard
   library only (`http.server` ThreadingHTTPServer + one acquisition
   thread), package `src/pt100_console/`, no new dependencies.
2. Confirm interpretation: Accuracy = `resolution_index`,
   Settling = `settling_factor`.
3. Auto-range step-down is decided (§3.2); are 10 cycles / 70 % the right
   hysteresis parameters?
4. Send `range_v` as a Telegraf field?
5. Authentication / extra headers for Telegraf http output?
6. Make Rref editable in the UI? (currently display-only)
7. UI auto-refresh period (the mock is static).
8. Access control for the console itself.
9. Tolerance for the "≈ Vexc" comparison in the short/open classification
   (§6), e.g. within ±10 % of nominal 160 mV.
