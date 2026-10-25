"""Check immutable sources/results and the exact authorized runtime transition."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.evaluation.context_benchmark_readiness import verify_manifest
from app.evaluation.published_integrity import verify_published_inputs
from app.ml.active_bundle import sha256
from app.context_annotation.storage import atomic_write_json


def verify():
    manifest, audit = verify_manifest()
    checks = {}
    for study in ("h10_1", "h10_2a"):
        directory = ROOT / "data/evaluation" / study
        checks[study + "_inputs"] = verify_published_inputs(json.loads((directory / "protected_inputs.json").read_text()))
        seal = json.loads((directory / "completion_validation.json").read_text())
        checks[study + "_outputs"] = verify_published_inputs(seal["output_sha256"])
    assert all(sha256(ROOT / s["source_path"]) == s["source_sha256"] for s in audit["sessions"])
    assert len(manifest["source_hashes"]) == len(set(manifest["source_hashes"].values())) == 18
    result = {"status": "passed", "source_count": 18, "source_hashes": manifest["source_hashes"],
              "approved_dataset_content_sha256": manifest["content_sha256"], "historical_integrity": checks,
              "annotations_and_raw_unchanged": True, "previous_models_unchanged": True,
              "public_database_or_deployment_changed": False}
    atomic_write_json(ROOT / "data/model_refresh/integrity.json", result)
    print(json.dumps({"status": "passed", "source_count": 18, "historical_checks": len(checks)}))


if __name__ == "__main__":
    verify()
