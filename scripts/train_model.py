from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.ml.profile_v02_training import (  # noqa: E402
    PROFILE_ID_V02,
    discover_jsonl_files,
    train_profile_v02_from_jsonl_files,
)
from app.repositories.file_repository import FileBackedRepository  # noqa: E402
from app.services.training_service import train_from_raw_files  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the V1 ECU Isolation Forest baseline.")
    parser.add_argument("--input", action="append", default=[], help="Input ECU log. Can be repeated.")
    parser.add_argument("--input-dir", type=Path, default=None, help="Directory of JSONL inputs for profile training.")
    parser.add_argument(
        "--decoder-profile",
        default="legacy_29",
        choices=["legacy_29", PROFILE_ID_V02],
        help="Decoder/profile path to use for training.",
    )
    parser.add_argument(
        "--output-model-dir",
        type=Path,
        default=None,
        help="Model artifact directory. Defaults to settings.model_dir for legacy, profile-specific directory for v0.2.",
    )
    parser.add_argument(
        "--output-data-dir",
        type=Path,
        default=Path("data/decoder-audit/ml/real_run_v0.2"),
        help="Profile v0.2 frame/feature/report output directory.",
    )
    parser.add_argument("--strict-checksum", action="store_true", help="Reject checksum-failing frames for profile training.")
    parser.add_argument("--allow-legacy-29-byte", action="store_true", help="Allow five-FF-prefixed legacy frames.")
    parser.add_argument("--max-records", type=int, default=None, help="Maximum records per input file.")
    parser.add_argument("--sample-interval-ms", type=float, default=None)
    parser.add_argument("--sampling-rate-hz", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    repository = FileBackedRepository(settings)
    started = perf_counter()

    try:
        if args.decoder_profile == PROFILE_ID_V02:
            input_paths = discover_jsonl_files(args.input_dir, [Path(path) for path in args.input])
            model_dir = args.output_model_dir or Path("data/models/honda_keihin_71_17_v2")
            metadata, report = train_profile_v02_from_jsonl_files(
                input_paths=input_paths,
                settings=settings,
                model_dir=model_dir,
                output_dir=args.output_data_dir,
                strict_checksum=args.strict_checksum,
                allow_legacy_29_byte=args.allow_legacy_29_byte,
                max_records=args.max_records,
            )
        else:
            if not args.input:
                raise ValueError("legacy_29 training requires at least one --input file")
            metadata, report = train_from_raw_files(
                [Path(path) for path in args.input],
                settings,
                sample_interval_ms=args.sample_interval_ms,
                sampling_rate_hz=args.sampling_rate_hz,
                repository=repository,
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    elapsed = perf_counter() - started

    print("Training report")
    print("---------------")
    if args.decoder_profile == PROFILE_ID_V02:
        print(f"decoder profile: {report['decoder_profile_id']}")
        print(f"records seen: {report['total_records']}")
        print(f"valid frames: {report['valid_frames']}")
        print(f"ML-eligible frames: {report['ml_eligible_frames']}")
        print(f"checksum failures: {report['checksum_failures']}")
        print(f"profile data output: {args.output_data_dir}")
    else:
        print(f"frames found: {report['frames_found']}")
        print(f"valid checksum frames: {report['valid_checksum_frames']}")
        print(f"decoded valid frames: {report['decoded_valid_frames']}")
    print(f"windows generated: {report['windows_generated']}")
    print(f"feature count: {len(metadata['feature_names'])}")
    print(f"training time: {elapsed:.3f}s")
    print(f"model output directory: {report.get('model_dir') or args.output_model_dir or settings.model_dir}")
    print("training scope: experimental baseline training data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
