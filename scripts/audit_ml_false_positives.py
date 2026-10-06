from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audit.ml_false_positive_audit import run_false_positive_audit  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit current V2 anomaly false positives and calibration readiness.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--model-dir", type=Path, default=Path("data/models/honda_keihin_71_17_v2"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/calibration"))
    parser.add_argument("--docs-dir", type=Path, default=Path("docs/ml"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_false_positive_audit(
        data_dir=args.data_dir,
        model_dir=args.model_dir,
        output_dir=args.output_dir,
        docs_dir=args.docs_dir,
    )
    printable = {
        "sessions_evaluated": result["sessions_evaluated"],
        "records_evaluated": result["records_evaluated"],
        "labels": dict(result["labels"]),
        "status_distribution": dict(result["status_distribution"]),
        "policy_status_distribution": dict(result["policy_status_distribution"]),
        "candidate_trained": result["candidate_trained"],
        "candidate_training_reason": result["candidate_training_reason"],
        "report_paths": result["report_paths"],
    }
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
