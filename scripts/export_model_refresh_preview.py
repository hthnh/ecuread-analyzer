"""Export actual refreshed results for an isolated, loopback DriveSafe preview."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.ml.active_bundle import activate_bundle, sha256
from app.services.analysis_service import AnalysisService


def export(path):
    record = json.loads((ROOT / "data/model_refresh/build.json").read_text())
    with tempfile.TemporaryDirectory(prefix="ecu-preview-config-") as temporary:
        settings = Settings(data_dir=ROOT / "data", model_dir=Path(temporary))
        activate_bundle(settings, ROOT / record["bundle_manifest"])
        result = []
        for sid in ("session_000001", "session_000003"):
            raw = ROOT / "real_data/fulldata_afterclean" / (sid + ".jsonl")
            canonical = import_native_jsonl_file(raw, session_id=sid).canonical_session
            output = AnalysisService(settings).analyze(canonical, persist=False)
            result.append({"canonical_session": canonical.model_dump(mode="json"), "analysis": output.summary, "source_sha256": sha256(raw)})
        path.write_text(json.dumps(result, allow_nan=False))
        print(path)


if __name__ == "__main__":
    export(Path(sys.argv[1]))
