from __future__ import annotations

from app.ecu.profiles import honda_keihin_71_17


SUPPORTED_PROFILES = {
    honda_keihin_71_17.PROFILE_ID_V01: honda_keihin_71_17,
    honda_keihin_71_17.PROFILE_ID_V02: honda_keihin_71_17,
}


def get_profile(profile_id: str):
    try:
        return SUPPORTED_PROFILES[profile_id]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_PROFILES))
        raise ValueError(f"unsupported decoder profile {profile_id!r}; supported profiles: {supported}") from exc
