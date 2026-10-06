from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.root_cause_analysis import default_h7_paths, run_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Run H7 deterministic evidence-based RCA analysis.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root; defaults to this script's parent repository.",
    )
    parser.add_argument(
        "--h6-dir",
        type=Path,
        default=None,
        help="Directory containing H6 validation outputs. Defaults to data/evaluation/h6.",
    )
    parser.add_argument(
        "--h5-dir",
        type=Path,
        default=None,
        help="Directory containing H5 evidence aggregation outputs. Defaults to data/evaluation/h5.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for H7 outputs. Defaults to data/evaluation/h7.",
    )
    args = parser.parse_args()

    paths = default_h7_paths(repo_root=args.repo_root, output_dir=args.output_dir)
    outputs = run_all(paths, h6_dir=args.h6_dir, h5_dir=args.h5_dir)
    print(json.dumps({key: str(path) for key, path in outputs.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
