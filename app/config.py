from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


MIN_SESSION_WINDOWS_FOR_STATUS = 10


@dataclass(frozen=True)
class SignalLimits:
    rpm: tuple[float, float] = (0, 16000)
    tps_voltage: tuple[float, float] = (0.0, 5.0)
    tps_raw_candidate: tuple[float, float] = (0, 255)
    battery_voltage: tuple[float, float] = (6.0, 18.0)
    iat_c: tuple[float, float] = (-40, 150)
    ect_c_candidate: tuple[float, float] = (-40, 180)
    map_raw: tuple[float, float] = (0, 255)

    def as_dict(self) -> dict[str, tuple[float, float]]:
        return {
            "rpm": self.rpm,
            "tps_voltage": self.tps_voltage,
            "tps_raw_candidate": self.tps_raw_candidate,
            "battery_voltage": self.battery_voltage,
            "iat_c": self.iat_c,
            "ect_c_candidate": self.ect_c_candidate,
            "map_raw": self.map_raw,
        }


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    service_name: str = "ecuread-analyzer"
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))
    model_dir: Path = Path(os.getenv("MODEL_DIR", os.getenv("DATA_DIR", "data") + "/models"))
    max_upload_mb: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_MB", 25))
    window_size_samples: int = field(default_factory=lambda: _env_int("WINDOW_SIZE_SAMPLES", 50))
    window_step_samples: int = field(default_factory=lambda: _env_int("WINDOW_STEP_SAMPLES", 10))
    max_window_checksum_error_ratio: float = field(
        default_factory=lambda: _env_float("MAX_WINDOW_CHECKSUM_ERROR_RATIO", 0.0)
    )
    max_window_invalid_decoded_ratio: float = field(
        default_factory=lambda: _env_float("MAX_WINDOW_INVALID_DECODED_RATIO", 0.0)
    )
    isolation_n_estimators: int = field(default_factory=lambda: _env_int("ISOLATION_N_ESTIMATORS", 200))
    isolation_contamination: float = field(default_factory=lambda: _env_float("ISOLATION_CONTAMINATION", 0.03))
    isolation_random_state: int = field(default_factory=lambda: _env_int("ISOLATION_RANDOM_STATE", 42))
    min_session_windows_for_status: int = field(
        default_factory=lambda: _env_int("MIN_SESSION_WINDOWS_FOR_STATUS", MIN_SESSION_WINDOWS_FOR_STATUS)
    )
    allow_model_training_api: bool = field(default_factory=lambda: _env_bool("ALLOW_MODEL_TRAINING_API", False))
    cors_origins: list[str] = field(
        default_factory=lambda: _env_list("CORS_ORIGINS", ["http://localhost:3000", "http://localhost:5173"])
    )
    validation_limits: SignalLimits = field(default_factory=SignalLimits)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    def ensure_directories(self) -> None:
        for directory in (self.raw_dir, self.processed_dir, self.results_dir, self.model_dir):
            directory.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
