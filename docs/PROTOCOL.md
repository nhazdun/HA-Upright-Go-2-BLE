# Upright GO 2 — BLE protocol

Reverse-engineered from the official Android app `com.uprightpose.upright_go2` v3.0.19.

The app is React Native; all BLE logic lives in the Hermes bytecode bundle
(`assets/index.android.bundle`, HBC v96), not in Java. The constants below were
recovered by parsing the Hermes string table and disassembling the modules that
reference it (function #17085 holds the GATT map, #38238/#38253/#38412 hold the
command table, #35481–35483 hold the settings serializers).

Nothing here is guessed — every value is read off a `LoadConst*` / `PutById`
pair in the bytecode.

## UUIDs

All custom UUIDs are 16-bit shorts expanded with the Bluetooth base UUID:

```
0000XXXX-0000-1000-8000-00805f9b34fb
```

### Services

| Short  | Name         | Purpose                          |
|--------|--------------|----------------------------------|
| `aac0` | `TRAIN`      | Training sessions                |
| `baa0` | `DATA`       | Offline/online posture data      |
| `bab0` | `SETTING`    | User settings                    |
| `bac0` | `CALIBRATION`| Calibration + live posture       |
| `bad0` | `POWER`      | Battery, charging, errors, HAL   |
| `bae0` | `TEST`       | Factory test                     |
| `baf0` | `BOOTLOADER` | DFU                              |
| `180a` | `INFO`       | Standard Device Information      |

### Characteristics

| Short  | Name                               | Service     |
|--------|------------------------------------|-------------|
| `baa1` | `DATA_AMOUNT`                      | DATA        |
| `baa2` | `DATA_COMMAND`                     | DATA        |
| `baa3` | `PACKET_NUMBER`                    | DATA        |
| `baa4` | `OFFLINE_DATA`                     | DATA        |
| `baa5` | `ONLINE_DATA`                      | DATA        |
| `baa6` | `CURRENT_TIMESTAMP`                | DATA        |
| `bab2` | `FREESTYLE_SETTING`                | SETTING     |
| `bab3` | `GENERAL_SETTING`                  | SETTING     |
| `bab5` | `VIBRATION_STATUS`                 | SETTING     |
| `bac1` | `START_CALIBRATION`                | CALIBRATION |
| `bac2` | `CALIBRATION_ACK`                  | CALIBRATION |
| `bac3` | `POSTURE_STATUS`                   | CALIBRATION |
| `bac4` | `SMOOTH_ANGLE`                     | CALIBRATION |
| `bac6` | `FW_MSS_STATE`                     | CALIBRATION |
| `bad1` | `POWER_DATA_FIRST_CHARACTERISTIC`  | POWER       |
| `bad2` | `POWER_DATA_SECOND_CHARACTERISTIC` | POWER       |
| `bad3` | `ERRORS`                           | POWER       |
| `bad4` | `HAL_CONTROL`                      | POWER       |
| `bae1` | `TEST_SENSOR`                      | TEST        |
| `bae3` | `VIBRATION_PRODUCTION_COMMAND`     | TEST        |
| `bae4` | `TEST_DATA_FOURTH_CHARACTERISTIC`  | TEST        |
| `baf1` | `BATTERY_LEVEL_DFU`                | BOOTLOADER  |
| `baf2` | `CHARGING_STATUS_DFU`              | BOOTLOADER  |
| `2a25` | `SERIAL_NUMBER`                    | INFO        |
| `2a26` | `FW_VERSION`                       | INFO        |
| `2a27` | `HW_VERSION`                       | INFO        |

## Advertised names

```
GO2 = UprightGO2
GOS = UprightGOS
DFU = UprightGo2DFU
```

Brand name strings: `Upright`, `Dario Move`.

## Payload encoding

`react-native-ble-plx` moves characteristic values as base64. Underneath:

- `encodeNumber(n)` → single unsigned byte
- `decodeByteArray(v)` → `Int8Array` (**signed** bytes)
- `decodeByte16Array(v)` → `Int16Array` (signed 16-bit, little-endian)
- `valueFromBytesDivided(a, b)` → `Int16Array(Int8Array.of(a, b).buffer)[0] / 10`

The last one is the angle decoder: little-endian signed 16-bit, in tenths of a degree.

Helpers used when serializing settings:

```
firstByte(v)             = v % 256
secondByte(v)            = floor(v / 256)
stopSecondsFirstByte(v)  = (v * 10) % 256
stopSecondsSecondByte(v) = floor((v * 10) / 256)
DELAY_MULTIPLIER         = 10
```

## POWER service

### `bad1` POWER_DATA_FIRST — read

| Offset | Field           |
|--------|-----------------|
| 0      | `BATTERY_LEVEL` (percent) |
| 4      | `TILE_FLAGS`    |

`LOW_BATTERY_VALUE = 10`.

### `bad2` POWER_DATA_SECOND — read

| Offset | Field            |
|--------|------------------|
| 0      | `CHARGING_STATE` |

`ChargingState`: `0` disconnected, `1` connectedNotFinished (charging),
`2` connectedFinished (charged).

### `bad3` ERRORS — read

`byteArrayToErrorData` splits this into four independent fields:

| Offset | Field            | Encoding                          |
|--------|------------------|-----------------------------------|
| 0–1    | `errorCode`      | bitmask over `GoDeviceErrorStatus`|
| 4      | `malfunction`    | boolean (`!!byte`)                |
| 5      | `shutdownReason` | index into `ShutdownReason`       |
| 7, 9   | `resetReason`    | bitmask over `ResetReason`        |

The bitmask helper is `parseErrors(bytes, enum)`, which maps each byte through
`Number.prototype.toBites()`:

```js
toBites() { return this.toString(2).padStart(8, '0').split('').reverse().join('') }
```

so bits are **LSB first** and bytes are concatenated in order — equivalent to
`int.from_bytes(payload, "little")` with bit *n* selecting `enum[n]`.

`GoDeviceErrorStatus` bit positions:

```
0  no_magnometer                   7  shutdown_due_to_button_press
1  memory_failure                  8  memory_mutex_lock_failure
2  memory_full                     9  sensor_mutex_lock_failure
3  read_sensors_failure           10  flash_reload_values_failure
4  empty_battery                  11  soft_reset_due_to_button_press
5  wrong_battery                  12  no_sensor_is_detected
6  shutdown_in_one_minute
```

`resetReason` reads byte 7 as bits 0–7 and byte 9 as bits 8–15. That mirrors the
nRF `RESETREAS` register, whose upper causes sit in hardware bits 16–19 — hence
the two non-adjacent bytes rather than a 16-bit little-endian pair.

### `bad4` HAL_CONTROL — write single byte

`HalControlCommand`:

```
0  DO_NOTHING
1  FW_UPDATE
2  FW_UPDATE_WITH_BULK_ERASE
8  DEEP_SLEEP
9  AIRPLANE_MODE
```

## CALIBRATION service

### `bac1` START_CALIBRATION — write single byte

`CalibrationCommand`: `0` START_CALIB, `1` CLEAR_CALIB, `2` SILENT_CALIB.

### `bac3` POSTURE_STATUS — read / notify

`PostureState`: `0` Straight, `1` Slouch.

### `bac4` SMOOTH_ANGLE — read / notify

Decoded with `valueFromBytesDivided` → degrees.

## SETTING service

### `bab3` GENERAL_SETTING — read / write (signed byte array)

| Offset | Field                    |
|--------|--------------------------|
| 0      | `dataInterval`           |
| 1      | `saveWhileConnected`     |
| 2      | `ledFunctionality`       |
| 3      | `compatibility`          |
| 4      | `restingBreaks`          |
| 5      | `ledDimming`             |
| 6      | `bending`                |
| 7      | `features.firstByte`     |
| 8      | `features.secondByte`    |
| 9      | `255` (padding)          |
| 10     | `255` (padding)          |
| 11     | `sittingReminderMinutes` (default 0)  |
| 12     | `activeResetSeconds` (default 30)     |
| 13     | `enableMoveFeature` (0/1)             |

Bytes 9–13 are only appended in extended mode; the short form is 9 bytes.

`compatibility`: `COMPATIBILITY_MODE_POSTURE = 1`, `COMPATIBILITY_MODE_MSK = 3`.
Reading byte 3 back yields `ConnectionMode.MSK` when it equals 3, else `POSTURE`.

`IntervalFrequency` (byte 0): `EVERY_1_SEC=1`, `EVERY_5_SEC=5`, `EVERY_10_SEC=7`,
`EVERY_30_SEC=10`, `EVERY_60_SEC=11`. Default is `EVERY_10_SEC`.

### `bab2` FREESTYLE_SETTING — read / write (11 unsigned bytes)

| Offset | Field                                  |
|--------|----------------------------------------|
| 0      | `range` (posture sensitivity)          |
| 1      | `firstByte(delay * 10)`                |
| 2      | `secondByte(delay * 10)`               |
| 3      | `vibPattern`                           |
| 4      | `vibStrength`                          |
| 5      | `stopPeriods`                          |
| 6      | `stopSecondsFirstByte(stopSeconds)`    |
| 7      | `stopSecondsSecondByte(stopSeconds)`   |
| 8      | `stopSecondsFirstByte(ledStopSeconds)` |
| 9      | `stopSecondsSecondByte(ledStopSeconds)`|
| 10     | `backwardsSlouchRange`                 |

`GoRange`: `MIN=1`, `MAX=6`, `DEFAULT=5`.

`GoDelay` (seconds): `5`, `15`, `30`, `60`.

`VibrationPattern`: `LONG=0`, `MEDIUM=1`, `SHORT=2`, `RUMPUP=3`, `KNOCK=4`,
`HEARTBEAT=5`, `TUK_TUK=6`, `ECSTATIC=7`, `MUZZLE=8`.

`VibrationPatternDemo` uses the same names offset by 128 (`LONG=128` … `MUZZLE=136`).

`VibrationStrength`: `STRONG=1`, `MEDIUM=35`, `GENTLE=70`, `DEFAULT=35`.
Lower value means a stronger buzz.

`DefVibPattern`: `GOS_DEFAULT = MEDIUM (1)`, `GO2_DEFAULT = KNOCK (4)`.

### `bab5` VIBRATION_STATUS — read / write

`VibrationMode`: `ON=0`, `OFF=1`.

## TEST service

`bae3` VIBRATION_PRODUCTION_COMMAND — write single byte from
`ProductionVibrationCommandByte`: `NORMAL=0`, `VIBRATION_ON=1`,
`VIBRATION_HALF=2`, `VIBRATION_OFF=3`.

## Other constants

```
BYTES         = 256
IGNORE_VALUE  = 255
SensorState   = CHARGING | DISCONNECTED | SEARCHING | CONNECTED | DFU_UPDATE
ResetReason   = pin_reset_detected, watchdog_detected, soft_reset_detected,
                cpu_lock_up_detected, detect_signal_from_gpio,
                anadetect_signal_from_lpcomp,
                entering_into_debug_interface_mode, nfc_field_detect
ShutdownReason= regular_off, is_active, softreset, empty_battery, bootloader,
                deepsleep, fw_update_with_bulk_erase,
                fw_update_without_bulk_erase, fw_unattached, fw_uncalibrated
```

DFU is Nordic (the APK ships `IMG_B_DFU.zip`, `FULL_DFU.zip` and their
`STANDUP_` variants, and bundles `no.nordicsemi.android.dfu`).
