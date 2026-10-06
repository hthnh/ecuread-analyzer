from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.model_harness_evaluation import default_paths, run_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline Model Harness evaluation.")
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
        help="Directory for evaluation outputs. Defaults to data/evaluation/h2.",
    )
    args = parser.parse_args()

    paths = default_paths(repo_root=args.repo_root, output_dir=args.output_dir)
    outputs = run_all(paths)
    print(json.dumps({key: str(path) for key, path in outputs.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
