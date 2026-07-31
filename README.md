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
| Charging | binary sensor | `battery_charging` device class |
| Slouching | binary sensor | |
| Posture angle | sensor | degrees, one decimal |
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

## Dashboard card

The integration ships a custom card and registers it itself — no dashboard
resource to add by hand. It shows a silhouette that leans with the measured
angle in real time, a ring that turns red when you slouch, the running totals,
and a calibrate / vibration / delay row.

```yaml
type: custom:upright-go2-card
angle: sensor.upright_go_2_posture_angle
slouching: binary_sensor.upright_go_2_slouching
battery: sensor.upright_go_2_battery
upright_time: sensor.upright_go_2_upright_time
slouching_time: sensor.upright_go_2_slouching_time
vibration: switch.upright_go_2_vibration
delay: number.upright_go_2_vibration_delay
calibrate: button.upright_go_2_calibrate
# Angles that count as fully upright and fully slouched, which depend on how
# the unit sits on your back. Tune these if the figure leans too much or
# too little.
upright_angle: 35
slouch_angle: 75
```

## Recorder load

The angle characteristic notifies about 15 times a second. Recording all of it
writes tens of thousands of rows an hour, so updates are published twice a
second — fast enough that the card still reads as live.

If database size matters more than the angle's history, exclude just that
sensor and the write rate drops to almost nothing while everything else keeps
working:

```yaml
recorder:
  exclude:
    entities:
      - sensor.upright_go_2_posture_angle
```

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
