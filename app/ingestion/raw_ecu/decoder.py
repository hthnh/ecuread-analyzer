from __future__ import annotations

from dataclasses import dataclass

from app.domain.telemetry import CORE_SIGNAL_COLUMNS


TRUSTED_SIGNAL_COLUMNS = CORE_SIGNAL_COLUMNS

UNKNOWN_SIGNAL_COLUMNS = [
    "signal_b19",
    "signal_word_20_21",
    "signal_b22",
    "signal_b23",
    "signal_b24",
]


@dataclass(slots=True)
class DecodedFrame:
    frame_index: int
    rpm: int
    tps_voltage: float
    tps_raw_candidate: int
    battery_voltage: float
    iat_c: int
    ect_c_candidate: int
    map_raw: int
    signal_b19: int
    signal_word_20_21: int
    signal_b22: int
    signal_b23: int
    signal_b24: int
    relative_time_ms: float | None = None

    def to_dict(self) -> dict:
        return {
            "frame_index": self.frame_index,
            "relative_time_ms": self.relative_time_ms,
            "rpm": self.rpm,
            "tps_voltage": self.tps_voltage,
            "tps_raw_candidate": self.tps_raw_candidate,
            "battery_voltage": self.battery_voltage,
            "iat_c": self.iat_c,
            "ect_c_candidate": self.ect_c_candidate,
            "map_raw": self.map_raw,
            "signal_b19": self.signal_b19,
            "signal_word_20_21": self.signal_word_20_21,
            "signal_b22": self.signal_b22,
            "signal_b23": self.signal_b23,
            "signal_b24": self.signal_b24,
        }


def decode_frame(frame: list[int], frame_index: int, relative_time_ms: float | None = None) -> DecodedFrame:
    """Decode the legacy 29-byte research RAW ECU frame into canonical signal names."""
    if len(frame) != 29:
        raise ValueError("decode_frame expects exactly 29 bytes")

    return DecodedFrame(
        frame_index=frame_index,
        relative_time_ms=relative_time_ms,
        rpm=(frame[9] << 8) | frame[10],
        tps_voltage=frame[11] * 5.0 / 256.0,
        tps_raw_candidate=frame[12],
        battery_voltage=frame[15] / 10.0,
        iat_c=frame[16] - 40,
        ect_c_candidate=frame[17] - 40,
        map_raw=frame[18],
        signal_b19=frame[19],
        signal_word_20_21=(frame[20] << 8) | frame[21],
        signal_b22=frame[22],
        signal_b23=frame[23],
        signal_b24=frame[24],
    )

