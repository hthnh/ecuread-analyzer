from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.research_architecture_audit import default_ra1_paths, run_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the RA1 research architecture and evidence audit bundle.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root; defaults to this script's parent repository.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for RA1 outputs. Defaults to data/evaluation/ra1.",
    )
    parser.add_argument(
        "--drivesafe-repo",
        type=Path,
        default=None,
        help="Optional DriveSafe Web App repository path for availability reporting.",
    )
    args = parser.parse_args()

    paths = default_ra1_paths(repo_root=args.repo_root, output_dir=args.output_dir)
    outputs = run_all(paths, drivesafe_repo=args.drivesafe_repo)
    print(json.dumps({key: str(path) for key, path in outputs.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
