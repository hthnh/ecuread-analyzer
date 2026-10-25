"""Verify and seal the completed local refresh without changing running services."""
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.context_annotation.storage import atomic_write_json
from app.ml.active_bundle import load_bundle, resolve_active_bundle, sha256
from verify_model_refresh_integrity import verify


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text())


def http_json(url):
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def seal():
    verify()
    directory = ROOT / "data/model_refresh"
    build = read_json(directory / "build.json")
    smoke = read_json(directory / "corpus_smoke.json")
    require(smoke["status"] == "passed", "full-corpus smoke did not pass")
    require((smoke["source_count"], smoke["canonical_count"], smoke["window_count"])
            == (18, 100688, 9989), "unexpected full-corpus counts")
    require(all(smoke[key] for key in ("all_canonical_values_upload_parity",
        "timestamp_window_identity_parity", "long_retry_idempotent", "missing_speed_safe_skip")),
        "required inference/parity checks did not pass")
    require(all(not item["errors"] for item in smoke["sessions"]), "session errors present")
    current_code = {str(path.relative_to(ROOT)): sha256(path) for path in (ROOT / "app").rglob("*.py")}
    require(current_code == smoke["runtime_code_sha256"], "runtime changed after full-corpus smoke")

    suites = ET.parse(directory / "analyzer_tests.xml").getroot().findall("testsuite")
    totals = {key: sum(int(suite.attrib[key]) for suite in suites)
              for key in ("tests", "errors", "failures", "skipped")}
    require(totals == {"tests": 410, "errors": 0, "failures": 0, "skipped": 0},
            "analyzer regression results incomplete")
    drive_log = (directory / "drivesafe_tests.log").read_text()
    require(re.search(r"^Ran 83 tests in [\d.]+s\s+OK\s*$", drive_log, re.MULTILINE),
            "DriveSafe regression results incomplete")
    browser = read_json(directory / "drivesafe_browser.json")
    require(browser["status"] == "passed" and not browser["page_errors"]
            and len(browser["checks"]) == 16
            and all(item["status"] == "passed" for item in browser["checks"]),
            "dashboard browser verification incomplete")

    settings = Settings(data_dir=ROOT / "data", model_dir=ROOT / "data/models")
    active = resolve_active_bundle(settings)
    require(active is not None and active.manifest_sha256 == sha256(ROOT / build["bundle_manifest"]),
            "active bundle differs from verified build")
    rollback = load_bundle(settings, ROOT / "data/models/bundles/native-pre-full18/manifest.json")
    model_info = http_json("http://127.0.0.1:8770/api/v1/model")
    require(model_info["model_loaded"] and model_info["model_bundle"] == active.provenance(),
            "local HTTP runtime does not match active bundle")
    require(http_json("http://127.0.0.1:8770/health")["status"] == "ok", "local health failed")
    require("csrf_token" in http_json("http://127.0.0.1:5071/auth/csrf"), "preview unavailable")

    public_state = json.loads(subprocess.check_output(
        ["docker", "inspect", "ecu-analyzer", "drisafe-web"], text=True))
    public_probe = json.loads(subprocess.check_output(["docker", "exec", "ecu-analyzer", "python", "-c",
        'from pathlib import Path; import app,hashlib,json; root=Path(app.__file__).parent; '
        'print(json.dumps({"active_bundle_references":[str(p) for p in root.rglob("*.py") '
        'if b"active_bundle" in p.read_bytes()],"native_if_sha256":hashlib.sha256('
        'Path("/app/data/models/honda_keihin_71_17_v2/isolation_forest.joblib").read_bytes()).hexdigest()}))'],
        text=True))
    require(not public_probe["active_bundle_references"], "public image unexpectedly supports local activation")
    require(public_probe["native_if_sha256"] == rollback.manifest["artifacts"]["if_model"]["sha256"],
            "public native IF is not the preserved previous artifact")

    drive_root = ROOT.parent / "drisafe"
    for repo in (ROOT, drive_root):
        subprocess.run(["git", "diff", "--check"], cwd=repo, check=True)
    files = set(directory.glob("*.json")) | set(directory.glob("*.png"))
    files.discard(directory / "completion.json")
    files.update(directory / name for name in ("analyzer_tests.xml", "drivesafe_tests.log", "pre_refresh_code.tar"))
    files.update((ROOT / "scripts").glob("*model*refresh*.py"))
    files.update(ROOT / path for path in current_code)
    files.update((ROOT / "tests").glob("*.py"))
    files.add(ROOT / "docs/FINAL_CONTEXTUAL_SPEED_MODEL_REFRESH.md")
    files.add(ROOT / "data/models/active_bundle.json")
    files.update((ROOT / build["bundle_manifest"]).parent.rglob("*"))
    for folder in ("services", "templates", "static", "tests", "scripts"):
        files.update(path for path in (drive_root / folder).rglob("*")
                     if path.suffix in (".py", ".html", ".js", ".cjs", ".css"))
    result = {
        "schema_version": "local-model-refresh-completion-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "final_dashboard_integrated",
        "activation_scope": "local",
        "public_deployment_performed": False,
        "public_probe": public_probe,
        "public_containers": [{"name": item["Name"], "image": item["Image"],
                               "started_at": item["State"]["StartedAt"]} for item in public_state],
        "active_bundle": active.provenance(),
        "rollback_bundle": rollback.provenance(),
        "source_count": 18, "canonical_count": 100688, "window_count": 9989,
        "approved_dataset_content_sha256": smoke["approved_dataset_content_sha256"],
        "analyzer_tests": totals, "drivesafe_tests_passed": 83,
        "browser_combinations_passed": 16,
        "local_runtime_verified": True,
        "historical_integrity": read_json(directory / "integrity.json"),
        "file_sha256": {str(path): sha256(path) for path in sorted(files) if path.is_file()},
    }
    atomic_write_json(directory / "completion.json", result)
    print(json.dumps({"verdict": result["verdict"], "bundle": active.manifest["bundle_id"],
                      "sealed_files": len(result["file_sha256"]), "public_deployment_performed": False}))


if __name__ == "__main__":
    seal()
