# Upright GO 2 for Home Assistant

A custom integration that talks to the [Upright GO 2](https://www.uprightpose.com/)
posture trainer over Bluetooth LE — including through an **ESPHome Bluetooth
proxy**, so the device does not need to be near the Home Assistant host.

No cloud, no account, no phone app in the loop. Home Assistant connects
directly to the device's GATT services.

## What you get

| Entity | Type | Notes |
|---|---|---|
| Battery | sensor | percent |
| Charging state | sensor | not charging / charging / fully charged |
| Charging | binary sensor | `battery_charging` device class |
| Low battery | binary sensor | at or below 10 % |
| Posture | sensor | straight / slouching |
| Slouching | binary sensor | |
| Posture angle | sensor | degrees, one decimal |
| Device errors | sensor | diagnostic, disabled by default |
| Problem | binary sensor | on when the device reports any error |
| Vibration | switch | slouch buzz on/off |
| Vibration pattern | select | 9 patterns |
| Vibration strength | select | gentle / medium / strong |
| Posture sensitivity | number | 1–6 |
| Vibration delay | number | seconds before the buzz |
| Slouching time | sensor | cumulative, drives the daily statistics |
| Upright time | sensor | cumulative |
| History last synced | sensor | diagnostic |
| Calibrate | button | sets the current pose as upright |
| Sync history | button | pull stored history now |
| Clear history | button | erases on-device history, disabled by default |
| Clear calibration | button | disabled by default |
| Deep sleep | button | disabled by default |

## Posture totals and history

Two cumulative counters, **Slouching time** and **Upright time**, in seconds.
They only ever go up, so Home Assistant's recorder derives per-day, per-week
and per-month figures from them automatically — add either to a **Statistics**
card, pick *Change* and a daily period, and you get exactly how long you spent
slouched on each day.

They are fed from two places:

- **Live**, while the connection is up: the time each posture lasts is added as
  it elapses.
- **Topped up from the device**, for the stretches nothing was connected. The
  GO 2 records to flash while offline, so a period when Home Assistant was down
  or out of range is recovered on the next sync.

A watermark of what has already been counted keeps the two from
double-counting, and an outage is never banked as posture time. The totals are
persisted, so a restart continues rather than resetting to zero.

History syncs hourly by default (configurable, 10 min – 24 h) and can be
triggered with the *Sync history* button. Nothing is deleted from the device
unless you press *Clear history*, which is disabled by default.

Resolution of the topped-up part is the device's own recording interval — 10 s
out of the box.

## Protocol

The BLE protocol was reverse-engineered from the official Android app and is
documented in full in [`docs/PROTOCOL.md`](docs/PROTOCOL.md): service and
characteristic UUIDs, byte offsets, enums and command values.

## Disclaimer

Not affiliated with or endorsed by Upright Technologies or DarioHealth.
"Upright GO" is their trademark. Use at your own risk — writing settings and
`Deep sleep` change device state.

## Licence

MIT — see [LICENSE](LICENSE).
