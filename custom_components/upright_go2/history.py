"""Parsing of the Upright GO 2's stored posture history.

The device records posture continuously and keeps it on-board, so this is what
lets daily totals cover periods when nothing was connected over Bluetooth.

Stream layout, as implemented by the app's fillIntervalsToSessionInProgress:

    [session header, 10 bytes] [interval byte] [interval byte] ... [0xFF]

A byte starts a new session header when its high nibble equals the configured
interval frequency and its low nibble is 7 (clean timestamp) or 15 (dirty).
0xFF ends the stream. Everything else is a one-byte interval record.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .const import (
    DEFAULT_INTERVAL_FREQUENCY,
    END_OF_DATA,
    HEADER_CLEAN_NIBBLE,
    HEADER_DIRTY_NIBBLE,
    INTERVAL_SECONDS,
    SESSION_HEADER_LENGTH,
)


@dataclass(slots=True)
class Interval:
    """One recorded interval."""

    start: datetime
    duration: int
    slouching: bool
    vibrating: bool
    movement: int
    vibration_count: int


@dataclass(slots=True)
class HistorySummary:
    """Per-day totals derived from the stored intervals."""

    # Local-date -> seconds
    slouching: dict[str, int] = field(default_factory=dict)
    upright: dict[str, int] = field(default_factory=dict)
    intervals: list[Interval] = field(default_factory=list)

    @property
    def days(self) -> list[str]:
        """Return every day that has data, oldest first."""
        return sorted(set(self.slouching) | set(self.upright))


def interval_seconds(frequency: int) -> int:
    """Return how many seconds one record covers."""
    return INTERVAL_SECONDS.get(frequency, INTERVAL_SECONDS[DEFAULT_INTERVAL_FREQUENCY])


def is_end_of_data(byte: int) -> bool:
    """Return True at the end-of-stream marker."""
    return byte == END_OF_DATA


def is_session_header(frequency: int, byte: int) -> bool:
    """Return True when this byte starts a session header."""
    return (byte >> 4) == frequency and (byte & 0x0F) in (
        HEADER_CLEAN_NIBBLE,
        HEADER_DIRTY_NIBBLE,
    )


def decode_session_timestamp(header: bytes) -> int | None:
    """Return the session's raw timestamp counter.

    The app computes `b[1] + b[2]<<8 + b[3]<<16 + b[4]*2**32`, which skips
    2**24 — that looks like a bug on its side. A plain little-endian 32-bit
    read is used here, and the caller anchors it against the device clock, so
    the absolute epoch never has to be guessed.
    """
    if len(header) < 5:
        return None
    return int.from_bytes(header[1:5], "little")


def decode_angle_pair(low: int, high: int) -> float:
    """Decode the app's valueFromBytesDivided pair."""
    return struct.unpack("<h", bytes([low, high]))[0] / 10


class StreamFramer:
    """Frames the offline stream incrementally as packets arrive.

    Framing has to be stateful: `0xFF` only ends the stream when it lands on an
    interval slot. Inside a session header it is ordinary data — the timestamp
    alone can contain it — so a download must not stop at the first `0xFF` it
    sees in a packet.
    """

    def __init__(self, frequency: int, clock_offset: float) -> None:
        """Initialise the framer for one download."""
        self._frequency = frequency
        self._seconds = interval_seconds(frequency)
        self._clock_offset = clock_offset
        self._buffer = bytearray()
        self._index = 0
        self._session_start: datetime | None = None
        self._position = 0
        self.intervals: list[Interval] = []
        self.complete = False

    def feed(self, chunk: bytes) -> None:
        """Add a packet and frame whatever is now complete."""
        self._buffer += chunk
        self._drain()

    def _drain(self) -> None:
        while not self.complete and self._index < len(self._buffer):
            byte = self._buffer[self._index]

            if is_end_of_data(byte):
                self.complete = True
                return

            if is_session_header(self._frequency, byte):
                if len(self._buffer) - self._index < SESSION_HEADER_LENGTH:
                    return  # wait for the rest of the header
                header = bytes(
                    self._buffer[self._index : self._index + SESSION_HEADER_LENGTH]
                )
                raw = decode_session_timestamp(header)
                self._session_start = (
                    datetime.fromtimestamp(raw + self._clock_offset, UTC)
                    if raw is not None
                    else None
                )
                self._position = 0
                self._index += SESSION_HEADER_LENGTH
                continue

            if self._session_start is not None:
                self.intervals.append(
                    Interval(
                        start=self._session_start
                        + timedelta(seconds=self._position * self._seconds),
                        duration=self._seconds,
                        slouching=bool((byte >> 7) & 1),
                        vibrating=bool((byte >> 6) & 1),
                        movement=(byte >> 4) & 0x03,
                        vibration_count=byte & 0x07,
                    )
                )
                self._position += 1

            self._index += 1


def parse_stream(
    stream: bytes,
    frequency: int,
    clock_offset: float,
) -> list[Interval]:
    """Turn a raw offline dump into timestamped intervals.

    `clock_offset` is `wall_clock_now - device_timestamp_now`, so device
    timestamps land on real time whatever epoch the firmware counts from.
    """
    framer = StreamFramer(frequency, clock_offset)
    framer.feed(stream)
    return framer.intervals


def decode_data_amount(payload: bytes) -> int | None:
    """Decode DATA_AMOUNT: how many records the device is holding."""
    if len(payload) < 3:
        return None
    return payload[0] | (payload[1] << 8) | (payload[2] << 16)


def summarise(intervals: list[Interval], tzinfo: object = None) -> HistorySummary:
    """Total the intervals per local calendar day."""
    summary = HistorySummary(intervals=intervals)
    for interval in intervals:
        moment = interval.start.astimezone(tzinfo) if tzinfo else interval.start
        day = moment.date().isoformat()
        bucket = summary.slouching if interval.slouching else summary.upright
        bucket[day] = bucket.get(day, 0) + interval.duration
    return summary


def hourly_totals(
    intervals: list[Interval], tzinfo: object = None
) -> dict[datetime, tuple[int, int]]:
    """Total the intervals per hour as (slouching, upright) seconds.

    Hourly buckets are what the recorder wants; it rolls them up into days and
    months for the statistics UI on its own.
    """
    buckets: dict[datetime, tuple[int, int]] = {}
    for interval in intervals:
        moment = interval.start.astimezone(tzinfo) if tzinfo else interval.start
        hour = moment.replace(minute=0, second=0, microsecond=0)
        slouch, upright = buckets.get(hour, (0, 0))
        if interval.slouching:
            slouch += interval.duration
        else:
            upright += interval.duration
        buckets[hour] = (slouch, upright)
    return buckets
