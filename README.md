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
| Slouching today | sensor | minutes spent slouching, from stored history |
| Upright today | sensor | minutes spent upright |
| History last synced | sensor | diagnostic |
| Calibrate | button | sets the current pose as upright |
| Sync history | button | pull stored history now |
| Clear history | button | erases on-device history, disabled by default |
| Clear calibration | button | disabled by default |
| Deep sleep | button | disabled by default |

## Daily history

The GO 2 records posture continuously and keeps it on-board, so the day's
totals are **not** limited to the time Home Assistant was connected. On each
sync the integration downloads that stored history and writes it to the
recorder as long-term statistics with real timestamps, which means a day is
complete even if Bluetooth was out of range for most of it.

Two statistics are produced:

```
upright_go2:<address>_slouching_seconds
upright_go2:<address>_upright_seconds
```

Add them to a **Statistics** card and pick a daily period to get the per-day
breakdown. The *Slouching today* and *Upright today* sensors carry the same
numbers for the current day.

History syncs hourly by default (configurable, 10 min – 24 h) and can be
triggered with the *Sync history* button. Nothing is deleted from the device
unless you press *Clear history*, which is disabled by default.

Resolution is the device's own recording interval — 10 s out of the box, which
is the `dataInterval` field in its general settings.

Serial number, firmware and hardware revision are attached to the device entry.

## Requirements

- Home Assistant 2024.12 or newer
- A **connectable** Bluetooth adapter or an ESPHome BLE proxy with a free
  connection slot within range of the device

The GO 2 does not put battery or posture data in its advertisement — it only
advertises its name and the Device Information service. Everything else needs a
real GATT connection, so a passive-only proxy is not enough.

## Installation

### HACS

1. HACS → three-dot menu → **Custom repositories**
2. Add `https://github.com/nhazdun/HA-Upright-Go-2-BLE`, category **Integration**
3. Install **Upright GO 2**, then restart Home Assistant

### Manual

Copy `custom_components/upright_go2` into your Home Assistant `config/custom_components/`
directory and restart.

## Setup

The device is discovered automatically once it advertises within range —
look for a new discovered device on the **Devices & Services** page. Otherwise
add it via **Add integration → Upright GO 2**.

If nothing shows up, move the device to wake it: the GO 2 stops advertising
when it has been still for a while.

## How it stays up to date

Posture and posture angle arrive as **BLE notifications**, so they update in
real time as you move — the same mechanism the official app uses. The device
only reports posture while a subscriber is attached, which is also what makes
the slouch vibration fire, so the integration keeps the connection open.

Battery, charging state, errors and the settings have no notification, so they
are re-read on a slower tick over that same connection. Default is 5 minutes;
change it under the integration's **Configure** menu (60–3600 s).

**Trade-off:** the GO 2 accepts only **one Bluetooth connection at a time**.
While Home Assistant is connected, the phone app cannot connect — and if the
app grabs the device first, Home Assistant will keep retrying with backoff
until it is released. Disable the integration entry if you need the app.

### Why isn't it vibrating?

- The device must be **calibrated** — press the *Calibrate* button while
  sitting upright.
- The buzz only fires after the **vibration delay** has elapsed while you are
  still slouching. Check the *Vibration delay* number; 15 s is a common
  default, so a quick lean will not trigger it.
- *Posture sensitivity* sets how far you may lean before it counts as a
  slouch (1–6). Lower it if it feels too forgiving.
- The *Vibration* switch must be on.

## Brand images

The integration ships its own icon in `custom_components/upright_go2/brand/`,
which Home Assistant 2026.3+ serves directly — no submission to
[home-assistant/brands](https://github.com/home-assistant/brands) required.
See the [brands proxy API announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/).

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
