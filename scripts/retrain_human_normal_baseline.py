from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audit.human_normal_retraining import run_human_normal_retraining  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and evaluate the human-labelled SH Mode normal baseline.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--calibration-dir", type=Path, default=Path("data/calibration"))
    parser.add_argument("--docs-dir", type=Path, default=Path("docs/ml"))
    parser.add_argument("--old-model-dir", type=Path, default=Path("data/models/honda_keihin_71_17_v2"))
    parser.add_argument(
        "--candidate-model-dir",
        type=Path,
        default=Path("data/models/honda_keihin_71_17_v2_candidate_human_normal"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_human_normal_retraining(
        data_dir=args.data_dir,
        calibration_dir=args.calibration_dir,
        docs_dir=args.docs_dir,
        old_model_dir=args.old_model_dir,
        candidate_model_dir=args.candidate_model_dir,
    )
    print(
        json.dumps(
            {
                "label_counts": dict(result.label_counts),
                "train_sessions": result.train_sessions,
                "holdout_sessions": result.holdout_sessions,
                "candidate_model_version": result.candidate_model_version,
                "candidate_training_window_count": result.candidate_training_window_count,
                "production_recommendation": result.production_recommendation,
                "report_paths": result.report_paths,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
