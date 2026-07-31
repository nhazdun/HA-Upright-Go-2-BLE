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


def decode_data_amount(payload: bytes) -> tuple[int | None, int | None]:
    """Decode DATA_AMOUNT into (bytes_pending, sessions).

    Bytes 0-2 are a **byte** count, not a record count: the app compares it
    against `currentAmount`, which it advances by 20 for every 20-byte packet
    (`endOfExpected` is `currentAmount >= expectedAmount`). Byte 3 is the
    number of stored sessions.
    """
    if len(payload) < 3:
        return None, None
    pending = payload[0] | (payload[1] << 8) | (payload[2] << 16)
    sessions = payload[3] if len(payload) > 3 else None
    return pending, sessions


def expected_record_count(pending_bytes: int, sessions: int | None) -> int:
    """Estimate how many interval records a dump of this size holds."""
    return max(pending_bytes - SESSION_HEADER_LENGTH * (sessions or 0), 0)


def detect_frequency(
    stream: bytes, expected_records: int | None
) -> tuple[int, list[Interval]]:
    """Work out which nibble marks a session header, by framing the stream.

    The app compares the header's high nibble against its own `intervalDuration`
    — a value that comes from its internal state rather than from anything on
    the wire, so it cannot simply be read off the device. Rather than guess
    whether that is the interval frequency or its length in seconds, every
    candidate is tried and the one that best explains the dump wins: it should
    frame close to the record count the device reported, without stopping early.
    """
    best_frequency = DEFAULT_INTERVAL_FREQUENCY
    best_intervals: list[Interval] = []
    best_score: float | None = None

    for candidate in range(16):
        framer = StreamFramer(candidate, 0.0)
        framer.feed(stream)
        count = len(framer.intervals)
        if not count:
            continue
        if expected_records:
            score = abs(count - expected_records) / expected_records
        else:
            # With nothing to compare against, prefer the framing that
            # accounts for the most of the stream.
            score = 1.0 - count / max(len(stream), 1)
        if best_score is None or score < best_score:
            best_score, best_frequency, best_intervals = score, candidate, framer.intervals

    return best_frequency, best_intervals


def summarise(intervals: list[Interval], tzinfo: object = None) -> HistorySummary:
    """Total the intervals per local calendar day."""
    summary = HistorySummary(intervals=intervals)
    for interval in intervals:
        moment = interval.start.astimezone(tzinfo) if tzinfo else interval.start
        day = moment.date().isoformat()
        bucket = summary.slouching if interval.slouching else summary.upright
        bucket[day] = bucket.get(day, 0) + interval.duration
    return summary


MAX_LIVE_GAP = 3600


class PostureClock:
    """Times how long each posture lasts while the device is connected.

    The stored history only covers stretches when nothing was connected — the
    device records to flash while offline — so a live connection has to be
    timed separately for the totals to mean anything.
    """

    def __init__(self) -> None:
        """Start the clock unarmed."""
        self._since: datetime | None = None
        self._slouching: bool | None = None

    def update(self, slouching: bool | None, now: datetime) -> tuple[float, float]:
        """Return (slouching, upright) seconds elapsed since the last call.

        Seconds are returned as floats. Credits arrive on every notification,
        several times a second, so rounding each one on the way in threw the
        whole lot away.
        """
        elapsed = self._elapsed(now)
        self._since = now
        self._slouching = slouching
        return elapsed

    def pause(self, now: datetime) -> tuple[float, float]:
        """Stop the clock, returning whatever was still owed."""
        elapsed = self._elapsed(now)
        self._since = None
        self._slouching = None
        return elapsed

    def _elapsed(self, now: datetime) -> tuple[float, float]:
        if self._since is None or self._slouching is None:
            return 0.0, 0.0
        seconds = (now - self._since).total_seconds()
        # Ignore implausible gaps: a suspended host or a long disconnect must
        # not land as hours of a posture nobody was actually in.
        if seconds <= 0 or seconds > MAX_LIVE_GAP:
            return 0.0, 0.0
        return (seconds, 0.0) if self._slouching else (0.0, seconds)


def history_since(
    intervals: list[Interval], watermark: datetime | None
) -> tuple[int, int, datetime | None]:
    """Total the stored intervals that have not been counted yet.

    Live timing advances the watermark as it goes, so anything the device
    recorded while connected is already accounted for. What is left is exactly
    the gaps — times Home Assistant was down or out of range — which is what
    should be topped up.
    """
    slouching = upright = 0
    newest = watermark
    for interval in intervals:
        end = interval.start + timedelta(seconds=interval.duration)
        if watermark is not None and end <= watermark:
            continue
        if interval.slouching:
            slouching += interval.duration
        else:
            upright += interval.duration
        if newest is None or end > newest:
            newest = end
    return slouching, upright, newest
