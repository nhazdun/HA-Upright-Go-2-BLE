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
| Calibrate | button | sets the current pose as upright |
| Clear calibration | button | disabled by default |
| Deep sleep | button | disabled by default |

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

## Polling

The GO 2 accepts **one Bluetooth connection at a time**. The integration
therefore connects, reads everything, and disconnects again, rather than
holding the link open. Default interval is 5 minutes; change it under the
integration's **Configure** menu (60–3600 s).

While a poll is in flight the phone app cannot connect, and vice versa — if the
official app is connected, polls will fail until it disconnects.

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
