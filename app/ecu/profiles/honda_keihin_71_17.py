from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


PROFILE_ID_V01 = "honda_keihin_71_17_v0.1"
PROFILE_ID_V02 = "honda_keihin_71_17_v0.2"
PROFILE_ID = PROFILE_ID_V01
PRODUCTION_ECU_PROFILE_ID = "honda_keihin_71_17"
PRODUCTION_DECODER_ID = "honda_keihin_71_17"
PRODUCTION_DECODER_VERSION = "1.0.0"
TPS_CLOSED_RAW = 0.0
TPS_OPEN_RAW = 156.0
PRODUCTION_SIGNAL_COLUMNS = [
    "rpm",
    "tps_voltage",
    "tps_raw",
    "battery_voltage",
    "iat_c",
    "ect_c",
]
FRAME_LENGTH = 24
LEGACY_FRAME_LENGTH = 29
LEGACY_PREFIX = [0xFF] * 5
HEADER = [0x02, 0x18, 0x71, 0x17]


class FrameFormatError(ValueError):
    """Raised when a frame cannot be normalized to the 24-byte table."""


@dataclass(slots=True)
class FrameValidation:
    parse_valid: bool
    length_valid: bool
    header_valid: bool
    checksum_valid: bool
    profile_supported: bool
    source_format: str
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_byte_values(frame: Iterable[int]) -> list[int]:
    normalized = list(frame)
    for byte in normalized:
        if not isinstance(byte, int) or byte < 0 or byte > 255:
            raise FrameFormatError("frame bytes must be integers in 0..255")
    return normalized


def normalize_frame(frame: Iterable[int], *, allow_legacy_29_byte: bool = True) -> tuple[list[int], str]:
    """Return a normalized 24-byte 0x71/0x17 frame and its source format.

    Decoder offsets in this profile always refer to the returned 24-byte frame.
    Legacy frames are accepted only when they have the known five-byte FF prefix.
    """
    raw = _ensure_byte_values(frame)
    if len(raw) == FRAME_LENGTH:
        return raw, "normalized_24"
    if len(raw) == LEGACY_FRAME_LENGTH and raw[:5] == LEGACY_PREFIX:
        if not allow_legacy_29_byte:
            raise FrameFormatError("legacy 29-byte frame found but --allow-legacy-29-byte was not enabled")
        return raw[5:], "legacy_29_ff_prefix"
    raise FrameFormatError(
        "unsupported frame format: expected 24 bytes or 29 bytes with five leading FF bytes"
    )


def checksum_valid(frame: Iterable[int]) -> bool:
    try:
        normalized = _ensure_byte_values(frame)
    except FrameFormatError:
        return False
    return len(normalized) == FRAME_LENGTH and sum(normalized) % 256 == 0


def expected_checksum_byte(frame_without_checksum: Iterable[int]) -> int:
    prefix = _ensure_byte_values(frame_without_checksum)
    if len(prefix) != FRAME_LENGTH - 1:
        raise ValueError("expected 23 bytes before checksum")
    return (-sum(prefix)) & 0xFF


def validate_normalized_frame(frame: Iterable[int], *, source_format: str = "normalized_24") -> FrameValidation:
    errors: list[str] = []
    try:
        normalized = _ensure_byte_values(frame)
    except FrameFormatError as exc:
        return FrameValidation(
            parse_valid=False,
            length_valid=False,
            header_valid=False,
            checksum_valid=False,
            profile_supported=False,
            source_format=source_format,
            errors=[str(exc)],
        )

    length_valid = len(normalized) == FRAME_LENGTH
    if not length_valid:
        errors.append(f"expected {FRAME_LENGTH} bytes, got {len(normalized)}")

    header_valid = length_valid and normalized[:4] == HEADER
    if length_valid and not header_valid:
        errors.append(
            "unsupported header: "
            + " ".join(f"{byte:02X}" for byte in normalized[:4])
            + f" != {' '.join(f'{byte:02X}' for byte in HEADER)}"
        )

    checksum_ok = checksum_valid(normalized)
    if length_valid and not checksum_ok:
        errors.append("checksum failed: sum(frame) % 256 != 0")

    profile_supported = bool(length_valid and header_valid)
    parse_valid = bool(length_valid and header_valid and checksum_ok and profile_supported)
    return FrameValidation(
        parse_valid=parse_valid,
        length_valid=length_valid,
        header_valid=header_valid,
        checksum_valid=checksum_ok,
        profile_supported=profile_supported,
        source_format=source_format,
        errors=errors,
    )


def bytes_to_hex(frame: Iterable[int]) -> str:
    return "".join(f"{byte:02X}" for byte in frame)


def _voltage(raw: int) -> float:
    return raw * 5.0 / 256.0


def _tps_percent_calibrated(raw: int) -> float:
    percent = (raw - TPS_CLOSED_RAW) / (TPS_OPEN_RAW - TPS_CLOSED_RAW) * 100.0
    return max(0.0, min(100.0, percent))


def decode_production_values(
    frame: Iterable[int],
    *,
    strict_checksum: bool = True,
    allow_legacy_29_byte: bool = False,
) -> dict[str, Any]:
    """Decode the production Pi/native 0x71/0x17 signal contract.

    This adapter-facing decode is intentionally native by default: callers must
    opt in to five-FF legacy normalization, and production Pi code should not.
    """
    normalized, source_format = normalize_frame(frame, allow_legacy_29_byte=allow_legacy_29_byte)
    validation = validate_normalized_frame(normalized, source_format=source_format)
    if not validation.length_valid or not validation.header_valid:
        raise FrameFormatError("; ".join(validation.errors) or "unsupported Honda 0x17 frame")
    if strict_checksum and not validation.parse_valid:
        raise FrameFormatError("; ".join(validation.errors) or "invalid Honda 0x17 frame")

    b = normalized
    tps_raw = b[7]
    injector_raw = (b[15] << 8) | b[16]
    return {
        "rpm": float((b[4] << 8) | b[5]),
        "tps_voltage": _voltage(b[6]),
        "tps_raw": float(tps_raw),
        "battery_voltage": b[14] / 10.0,
        "iat_c": float(b[11] - 40),
        "ect_c": float(b[13] - 40),
        "tps_percent_calibrated": _tps_percent_calibrated(tps_raw),
        "iat_voltage_candidate": _voltage(b[10]),
        "ect_voltage_candidate": _voltage(b[12]),
        "injector_raw": float(injector_raw),
        "injector_ms_candidates": {
            "raw / 100": injector_raw / 100.0,
            "raw / 250": injector_raw / 250.0,
            "raw / 256": injector_raw / 256.0,
        },
        "byte17": float(b[17]),
        "byte18": float(b[18]),
        "unknown_reserved_raw": b[19:23],
        "checksum_byte": b[23],
        "normalized_frame": normalized,
        "normalized_hex": bytes_to_hex(normalized),
        "source_format": source_format,
        "validation": validation.to_dict(),
    }


def _field(
    *,
    value: Any,
    status: str,
    source_bytes: list[int],
    formula: str | None = None,
    raw: Any = None,
    note: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "value": value,
        "status": status,
        "source_bytes": source_bytes,
    }
    if formula is not None:
        data["formula"] = formula
    if raw is not None:
        data["raw"] = raw
    if note is not None:
        data["note"] = note
    return data


def decode_frame(
    frame: Iterable[int],
    *,
    strict_checksum: bool = True,
    allow_legacy_29_byte: bool = True,
) -> dict[str, Any]:
    """Decode the candidate Honda/Keihin 0x71/0x17 layout.

    The result is intentionally conservative. It separates structural validity
    from semantic confidence and keeps unverified fields as raw candidates.
    """
    normalized, source_format = normalize_frame(frame, allow_legacy_29_byte=allow_legacy_29_byte)
    validation = validate_normalized_frame(normalized, source_format=source_format)
    result: dict[str, Any] = {
        "profile_id": PROFILE_ID,
        "normalized_frame": normalized,
        "normalized_hex": bytes_to_hex(normalized),
        "source_format": source_format,
        **validation.to_dict(),
    }
    if not validation.length_valid or not validation.header_valid:
        result["fields"] = {}
        return result
    if strict_checksum and not validation.parse_valid:
        result["fields"] = {}
        return result

    b = normalized
    rpm = (b[4] << 8) | b[5]
    tps_voltage = _voltage(b[6])
    tps_position_raw = b[7]
    ect_pair = [b[8], b[9]]
    iat_voltage = _voltage(b[10])
    iat_c = b[11] - 40
    map_voltage = _voltage(b[12])
    map_engineering_raw = b[13]
    battery_v = b[14] / 10.0
    injector_raw = (b[15] << 8) | b[16]
    ignition_raw = b[17]
    speed_or_signal_raw = b[18]
    unknown_reserved = b[19:23]

    fields: dict[str, Any] = {
        "rpm": _field(
            value=rpm,
            status="high_confidence_candidate",
            source_bytes=[4, 5],
            formula="big_endian_u16",
            raw=[b[4], b[5]],
        ),
        "tps_voltage": _field(
            value=tps_voltage,
            status="high_confidence_candidate",
            source_bytes=[6],
            formula="raw * 5 / 256",
            raw=b[6],
        ),
        "tps_position_raw": _field(
            value=tps_position_raw,
            status="candidate",
            source_bytes=[7],
            formula="raw",
            raw=b[7],
            note="preserved raw; no percent formula selected",
        ),
        "iat": _field(
            value=iat_c,
            status="high_confidence_candidate",
            source_bytes=[10, 11],
            formula="engineering_byte - 40",
            raw=[b[10], b[11]],
            note="candidate pending ground-truth temperature validation",
        ),
        "map": {
            "voltage": map_voltage,
            "engineering_raw": map_engineering_raw,
            "status": "scale_unverified",
            "source_bytes": [12, 13],
            "formula": {
                "voltage": "raw * 5 / 256",
                "engineering_raw": "raw",
            },
            "raw": [b[12], b[13]],
        },
        "battery": _field(
            value=battery_v,
            status="high_confidence_candidate",
            source_bytes=[14],
            formula="raw / 10",
            raw=b[14],
        ),
        "injector": {
            "raw": injector_raw,
            "status": "scale_unverified",
            "source_bytes": [15, 16],
            "formula": "big_endian_u16",
            "candidates_ms": {
                "raw / 100": injector_raw / 100.0,
                "raw / 250": injector_raw / 250.0,
                "raw / 256": injector_raw / 256.0,
            },
        },
        "ignition_raw": _field(
            value=ignition_raw,
            status="unknown",
            source_bytes=[17],
            formula="raw",
            raw=ignition_raw,
        ),
        "speed_or_signal_raw": _field(
            value=speed_or_signal_raw,
            status="candidate",
            source_bytes=[18],
            formula="raw",
            raw=speed_or_signal_raw,
            note="not named speed without external reference",
        ),
        "unknown_reserved": _field(
            value=unknown_reserved,
            status="unknown",
            source_bytes=[19, 20, 21, 22],
            formula="raw bytes",
            raw=unknown_reserved,
        ),
        "checksum": _field(
            value=b[23],
            status="verified" if validation.checksum_valid else "out_of_physical_range",
            source_bytes=[23],
            formula="twos_complement_sum",
            raw=b[23],
        ),
    }

    if ect_pair == [0xFF, 0xFF]:
        fields["ect"] = _field(
            value=None,
            status="unavailable",
            source_bytes=[8, 9],
            raw=ect_pair,
            note="FF/FF pair treated as unavailable_or_unsupported sentinel",
        )
        ect_status = "unavailable_or_unsupported"
        ect_voltage_candidate = None
        ect_c_candidate = None
    else:
        fields["ect"] = {
            "value": b[9] - 40,
            "voltage": _voltage(b[8]),
            "status": "candidate",
            "source_bytes": [8, 9],
            "formula": {
                "voltage": "raw * 5 / 256",
                "temperature": "engineering_byte - 40",
            },
            "raw": ect_pair,
        }
        ect_status = "candidate"
        ect_voltage_candidate = _voltage(b[8])
        ect_c_candidate = b[9] - 40

    result.update(
        {
            "rpm": rpm,
            "tps_voltage_candidate": tps_voltage,
            "tps_position_raw_candidate": tps_position_raw,
            "ect_voltage_raw": b[8],
            "ect_temperature_raw": b[9],
            "ect_status": ect_status,
            "ect_voltage_candidate": ect_voltage_candidate,
            "ect_c_candidate": ect_c_candidate,
            "iat_voltage_candidate": iat_voltage,
            "iat_c_candidate": iat_c,
            "map_voltage_candidate": map_voltage,
            "map_engineering_raw_candidate": map_engineering_raw,
            "battery_v_candidate": battery_v,
            "injector_raw_candidate": injector_raw,
            "injector_scale_status": "scale_unverified",
            "injector_ms_candidate": injector_raw / 100.0,
            "injector_ms_candidates": {
                "raw / 100": injector_raw / 100.0,
                "raw / 250": injector_raw / 250.0,
                "raw / 256": injector_raw / 256.0,
            },
            "ignition_raw_candidate": ignition_raw,
            "speed_or_signal_raw_candidate": speed_or_signal_raw,
            "unknown_reserved_raw": unknown_reserved,
            "fields": fields,
        }
    )
    return result


def default_profile_document(profile_id: str = PROFILE_ID) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "frame_length": FRAME_LENGTH,
        "header": HEADER,
        "checksum": {
            "type": "twos_complement_sum",
            "valid_condition": "sum(frame) % 256 == 0",
            "expected_checksum_formula": "(-sum(frame[:23])) & 0xFF",
        },
        "fields": {
            "rpm": {
                "source_bytes": [4, 5],
                "formula": "big_endian_u16",
                "status": "high_confidence_candidate",
            },
            "tps_voltage": {
                "source_bytes": [6],
                "formula": "raw * 5 / 256",
                "status": "high_confidence_candidate",
            },
            "tps_position_raw": {
                "source_bytes": [7],
                "formula": "raw",
                "status": "candidate",
            },
            "ect": {
                "source_bytes": [8, 9],
                "formula": "voltage raw * 5 / 256; engineering byte - 40",
                "status": "unavailable_when_ff_ff_else_candidate",
            },
            "iat": {
                "source_bytes": [10, 11],
                "formula": "voltage raw * 5 / 256; engineering byte - 40",
                "status": "high_confidence_candidate",
            },
            "map": {
                "source_bytes": [12, 13],
                "formula": "voltage raw * 5 / 256; engineering raw preserved",
                "status": "scale_unverified",
            },
            "battery": {
                "source_bytes": [14],
                "formula": "raw / 10",
                "status": "high_confidence_candidate",
            },
            "injector": {
                "source_bytes": [15, 16],
                "formula": "big_endian_u16; ms scale unverified",
                "status": "scale_unverified",
                "candidate_scales": ["raw / 100", "raw / 250", "raw / 256"],
            },
            "ignition_raw": {"source_bytes": [17], "formula": "raw", "status": "unknown"},
            "speed_or_signal_raw": {
                "source_bytes": [18],
                "formula": "raw",
                "status": "candidate",
            },
            "unknown_reserved": {
                "source_bytes": [19, 20, 21, 22],
                "formula": "raw bytes",
                "status": "unknown",
            },
        },
        "verified_fields": ["checksum"],
        "candidate_fields": [
            "rpm",
            "tps_voltage",
            "tps_position_raw",
            "iat",
            "battery",
            "injector_raw",
            "map",
            "speed_or_signal_raw",
        ],
        "unknown_fields": ["ignition_raw", "unknown_reserved"],
        "known_sentinels": {
            "ff_ff_pair": "unavailable_or_unsupported",
            "ect_ff_ff": {"source_bytes": [8, 9], "meaning": "unavailable_or_unsupported"},
        },
        "vehicle_scope": {
            "vehicle_id": None,
            "ecu_part_number": None,
            "model": None,
            "year": None,
            "firmware_version": None,
            "protocol_table": "0x71/0x17",
        },
        "evidence": [],
        "limitations": [
            "Not a universal Honda/Keihin profile.",
            "Vehicle model, model year, ECU part number, ECM identification response, and firmware version are not present in the supplied logs.",
            "Temperature, MAP, injector scale, and speed semantics require controlled ground-truth validation.",
        ],
    }
