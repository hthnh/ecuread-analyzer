from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ecu.checksum import with_recomputed_checksum  # noqa: E402
from app.ecu.parser import parse_log_file  # noqa: E402


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def get_rpm(frame: list[int]) -> int:
    return (frame[9] << 8) | frame[10]


def set_rpm(frame: list[int], rpm: int) -> None:
    rpm = clamp_int(rpm, 0, 65535)
    frame[9] = (rpm >> 8) & 0xFF
    frame[10] = rpm & 0xFF


def set_tps_voltage(frame: list[int], voltage: float) -> None:
    frame[11] = clamp_int(round(voltage * 256.0 / 5.0), 0, 255)


def selected(frame_index: int, start: int, end: int) -> bool:
    return start <= frame_index <= end


def mutate_frames(
    frames: list[list[int]],
    anomaly_type: str,
    start_frame: int,
    end_frame: int,
    parameters: dict,
) -> list[list[int]]:
    mutated = [list(frame) for frame in frames]
    if anomaly_type == "random_missing_frames":
        ratio = float(parameters.get("missing_ratio", 0.2))
        rng = random.Random(int(parameters.get("seed", 42)))
        kept: list[list[int]] = []
        for index, frame in enumerate(mutated):
            if selected(index, start_frame, end_frame) and rng.random() < ratio:
                continue
            kept.append(frame)
        return kept

    reference_index = clamp_int(start_frame, 0, len(mutated) - 1)
    reference = mutated[reference_index]
    frozen_rpm = get_rpm(reference)
    frozen_tps = reference[11]
    frozen_map = reference[18]

    def apply_with_checksum(index: int, mutate: Callable[[list[int]], None]) -> None:
        mutate(mutated[index])
        if anomaly_type != "broken_checksum":
            mutated[index] = with_recomputed_checksum(mutated[index])

    for index, frame in enumerate(mutated):
        if not selected(index, start_frame, end_frame):
            continue
        progress = 0.0 if end_frame == start_frame else (index - start_frame) / (end_frame - start_frame)

        if anomaly_type == "rpm_spike":
            amplitude = int(parameters.get("amplitude", 2500))
            apply_with_checksum(index, lambda item: set_rpm(item, get_rpm(item) + amplitude))
        elif anomaly_type == "rpm_freeze":
            apply_with_checksum(index, lambda item: set_rpm(item, frozen_rpm))
        elif anomaly_type == "tps_voltage_freeze":
            apply_with_checksum(index, lambda item: item.__setitem__(11, frozen_tps))
        elif anomaly_type == "iat_sudden_jump":
            delta_c = int(parameters.get("delta_c", 35))
            apply_with_checksum(index, lambda item: item.__setitem__(16, clamp_int(item[16] + delta_c, 0, 255)))
        elif anomaly_type == "ect_sudden_jump":
            delta_c = int(parameters.get("delta_c", 35))
            apply_with_checksum(index, lambda item: item.__setitem__(17, clamp_int(item[17] + delta_c, 0, 255)))
        elif anomaly_type == "battery_gradual_drop":
            drop_v = float(parameters.get("drop_v", 3.0))
            apply_with_checksum(index, lambda item: item.__setitem__(15, clamp_int(round(item[15] - drop_v * 10.0 * progress), 0, 255)))
        elif anomaly_type == "map_freeze":
            apply_with_checksum(index, lambda item: item.__setitem__(18, frozen_map))
        elif anomaly_type == "broken_checksum":
            apply_with_checksum(index, lambda item: item.__setitem__(0, item[0] ^ 0x01))
        elif anomaly_type == "rpm_tps_relation_mismatch":
            amplitude = int(parameters.get("amplitude", 2500))
            apply_with_checksum(index, lambda item: set_rpm(item, get_rpm(item) + amplitude))
        else:
            raise ValueError(f"unsupported anomaly type: {anomaly_type}")

    return mutated


def frame_to_raw_line(frame: list[int]) -> str:
    return "RAW: " + ";".join(f"{byte:02X}" for byte in frame)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic ECU anomalies for tests and demos.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metadata-output", required=True)
    parser.add_argument(
        "--anomaly-type",
        required=True,
        choices=[
            "rpm_spike",
            "rpm_freeze",
            "tps_voltage_freeze",
            "iat_sudden_jump",
            "ect_sudden_jump",
            "battery_gradual_drop",
            "map_freeze",
            "random_missing_frames",
            "broken_checksum",
            "rpm_tps_relation_mismatch",
        ],
    )
    parser.add_argument("--start-frame", type=int, default=100)
    parser.add_argument("--end-frame", type=int, default=120)
    parser.add_argument("--amplitude", type=int, default=2500)
    parser.add_argument("--delta-c", type=int, default=35)
    parser.add_argument("--drop-v", type=float, default=3.0)
    parser.add_argument("--missing-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    parse_result = parse_log_file(args.input)
    frames = [frame.bytes for frame in parse_result.frames if frame.parse_ok]
    if not frames:
        raise SystemExit("No parseable frames found.")

    parameters = {
        "amplitude": args.amplitude,
        "delta_c": args.delta_c,
        "drop_v": args.drop_v,
        "missing_ratio": args.missing_ratio,
        "seed": args.seed,
    }
    mutated = mutate_frames(frames, args.anomaly_type, args.start_frame, args.end_frame, parameters)
    Path(args.output).write_text("\n".join(frame_to_raw_line(frame) for frame in mutated) + "\n", encoding="utf-8")

    metadata = {
        "anomaly_type": args.anomaly_type,
        "start_frame": args.start_frame,
        "end_frame": args.end_frame,
        "parameters": parameters,
        "source": str(args.input),
        "synthetic_data_note": "For pipeline tests, demos, and experimental evaluation only. Do not train default baseline with this data.",
    }
    Path(args.metadata_output).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Wrote {len(mutated)} frames to {args.output}")
    print(f"Wrote metadata to {args.metadata_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
