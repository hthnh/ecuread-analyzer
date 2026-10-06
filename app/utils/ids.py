from __future__ import annotations

import secrets
from datetime import UTC, datetime


def new_session_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(6)
    return f"sess_{stamp}_{suffix}"


def new_analysis_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(6)
    return f"analysis_{stamp}_{suffix}"


def new_artifact_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(6)
    return f"artifact_{stamp}_{suffix}"
