from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.diagnostic_integration import default_h9_paths, run_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Run H9 analyzer to DriveSafe diagnostic evidence integration evaluation.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root; defaults to this script's parent repository.",
    )
    parser.add_argument(
        "--h8-dir",
        type=Path,
        default=None,
        help="Directory containing H8 outputs. Defaults to data/evaluation/h8.",
    )
    parser.add_argument(
        "--drivesafe-repo",
        type=Path,
        default=None,
        help="Optional DriveSafe Web App repository path for availability reporting.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for H9 outputs. Defaults to data/evaluation/h9.",
    )
    args = parser.parse_args()

    paths = default_h9_paths(repo_root=args.repo_root, output_dir=args.output_dir)
    outputs = run_all(paths, h8_dir=args.h8_dir, drivesafe_repo=args.drivesafe_repo)
    print(json.dumps({key: str(path) for key, path in outputs.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
