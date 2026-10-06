from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.detector_validation import default_h6_paths, run_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Run H6 active-detector validation analysis.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root; defaults to this script's parent repository.",
    )
    parser.add_argument(
        "--h2-dir",
        type=Path,
        default=None,
        help="Directory containing H2 parity and evaluation outputs. Defaults to data/evaluation/h2.",
    )
    parser.add_argument(
        "--h5-dir",
        type=Path,
        default=None,
        help="Directory containing H5 evidence aggregation outputs. Defaults to data/evaluation/h5.",
    )
    parser.add_argument(
        "--h3-dir",
        type=Path,
        default=None,
        help="Directory containing H3 contextual outputs. Defaults to data/evaluation/h3.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for H6 outputs. Defaults to data/evaluation/h6.",
    )
    args = parser.parse_args()

    paths = default_h6_paths(repo_root=args.repo_root, output_dir=args.output_dir)
    outputs = run_all(paths, h2_dir=args.h2_dir, h5_dir=args.h5_dir, h3_dir=args.h3_dir)
    print(json.dumps({key: str(path) for key, path in outputs.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
