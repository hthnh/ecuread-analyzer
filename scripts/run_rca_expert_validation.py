from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.rca_expert_validation import default_h71_paths, run_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Run H7.1 expert-validation dataset and RCA feedback summaries.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root; defaults to this script's parent repository.",
    )
    parser.add_argument(
        "--h7-dir",
        type=Path,
        default=None,
        help="Directory containing H7 RCA outputs. Defaults to data/evaluation/h7.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for H7.1 outputs. Defaults to data/evaluation/h7_1.",
    )
    args = parser.parse_args()

    paths = default_h71_paths(repo_root=args.repo_root, output_dir=args.output_dir)
    outputs = run_all(paths, h7_dir=args.h7_dir)
    print(json.dumps({key: str(path) for key, path in outputs.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
