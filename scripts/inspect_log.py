from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.ecu.parser import parse_log_file  # noqa: E402
from app.services.session_processor import build_frames_dataframe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect ECU RAW frame extraction and V1 decoding.")
    parser.add_argument("input")
    parser.add_argument("--sample-interval-ms", type=float, default=None)
    args = parser.parse_args()

    settings = get_settings()
    parse_result = parse_log_file(args.input)
    frames = build_frames_dataframe(parse_result, settings, args.sample_interval_ms)
    print("Parse statistics")
    for key, value in parse_result.statistics.to_dict().items():
        print(f"{key}: {value}")
    if not frames.empty:
        display_columns = [
            "frame_index",
            "checksum_valid",
            "is_valid",
            "rpm",
            "tps_voltage",
            "battery_voltage",
            "iat_c",
            "ect_c_candidate",
            "map_raw",
        ]
        print("\nFirst decoded frames")
        print(frames[display_columns].head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

