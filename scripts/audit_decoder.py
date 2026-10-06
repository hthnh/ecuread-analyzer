from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.audit.report import run_audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit and reconstruct the Honda/Keihin 0x71/0x17 ECU decoder profile."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Directory containing .jsonl, .log, or .txt road-session files.",
    )
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        default=[],
        help="Specific input file. May be provided multiple times.",
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="Versioned decoder profile id, e.g. honda_keihin_71_17_v0.1.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Report output directory. Generated files are written outside the immutable source directory.",
    )
    parser.add_argument(
        "--strict-checksum",
        action="store_true",
        help="Do not candidate-decode frames that fail the normalized 24-byte checksum.",
    )
    parser.add_argument(
        "--allow-legacy-29-byte",
        action="store_true",
        help="Accept legacy frames with five leading FF bytes and normalize them to 24 bytes.",
    )
    parser.add_argument(
        "--generate-plots",
        action="store_true",
        help="Generate SVG time-series plots and a byte-correlation heatmap.",
    )
    parser.add_argument(
        "--compare-existing-fields",
        action="store_true",
        help="Infer raw byte/word sources for decoded JSON fields.",
    )
    parser.add_argument(
        "--include-calibration",
        action="store_true",
        help="Recursively discover controlled calibration sessions and generate Phase 2 calibration reports.",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Maximum records to process per input file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.input_dir is None and not args.input:
        parser.error("provide --input-dir or at least one --input file")
    try:
        summary = run_audit(
            input_dir=args.input_dir,
            input_paths=args.input,
            profile_id=args.profile,
            output_dir=args.output,
            strict_checksum=args.strict_checksum,
            allow_legacy_29_byte=args.allow_legacy_29_byte,
            generate_plots=args.generate_plots,
            compare_existing_fields=args.compare_existing_fields,
            include_calibration=args.include_calibration,
            max_records=args.max_records,
        )
    except ValueError as exc:
        print(f"audit failed: {exc}", file=sys.stderr)
        return 2

    totals = summary["totals"]
    output = {
        "profile_id": summary["profile_id"],
        "records_parsed": totals.get("records_parsed", 0),
        "valid_frames": totals.get("valid_frames", 0),
        "checksum_failure_ratio": totals.get("checksum_failure_ratio", 0.0),
        "ready_for_ml": summary["readiness"]["ready_for_ml"],
        "output": str(args.output),
    }
    if "calibration" in summary:
        output["calibration_ready_for_ml"] = summary["calibration"]["ml_readiness"]["ready_for_ml"]
        output["phase2_profile_id"] = summary["calibration"]["phase2_profile_id"]
        output["phase2_profile_path"] = summary["calibration"]["v02_profile_path"]
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
