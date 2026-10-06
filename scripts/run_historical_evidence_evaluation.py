from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.historical_evidence import default_h8_paths, run_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Run H8 historical-evidence and long-term-baseline evaluation.")
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
        help="Directory containing H2 outputs. Defaults to data/evaluation/h2.",
    )
    parser.add_argument(
        "--h5-dir",
        type=Path,
        default=None,
        help="Directory containing H5 outputs. Defaults to data/evaluation/h5.",
    )
    parser.add_argument(
        "--h73-dir",
        type=Path,
        default=None,
        help="Directory containing H7.3 outputs. Defaults to data/evaluation/h7_3.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for H8 outputs. Defaults to data/evaluation/h8.",
    )
    args = parser.parse_args()

    paths = default_h8_paths(repo_root=args.repo_root, output_dir=args.output_dir)
    outputs = run_all(paths, h2_dir=args.h2_dir, h5_dir=args.h5_dir, h73_dir=args.h73_dir)
    print(json.dumps({key: str(path) for key, path in outputs.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
