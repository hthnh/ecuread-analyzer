from __future__ import annotations

from app.config import SignalLimits


def validate_decoded_signals(
    decoded: dict,
    limits: SignalLimits | dict[str, tuple[float, float]] | None = None,
) -> tuple[bool, list[str]]:
    rules = limits.as_dict() if isinstance(limits, SignalLimits) else limits
    if rules is None:
        rules = SignalLimits().as_dict()

    errors: list[str] = []
    for field_name, (minimum, maximum) in rules.items():
        value = decoded.get(field_name)
        if value is None:
            errors.append(f"{field_name} is missing")
            continue
        if value < minimum or value > maximum:
            errors.append(f"{field_name}={value} outside configured range {minimum}..{maximum}")

    return len(errors) == 0, errors

