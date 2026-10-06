from __future__ import annotations

import html
import math
from dataclasses import asdict, dataclass
from statistics import fmean, median, pstdev
from typing import Any, Iterable


BYTE_ATLAS_RANGE = range(4, 23)


@dataclass(slots=True)
class ByteStatistic:
    session: str
    byte_index: int
    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    median: float | None
    stddev: float | None
    unique_values: int
    pct_00: float
    pct_ff: float
    mean_abs_change: float | None
    max_abs_change: float | None
    longest_frozen_run: int
    corr_rpm: float | None
    corr_tps_voltage: float | None
    corr_injector_raw: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[int(position)])
    fraction = position - lower
    return float(ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction)


def pearson(left: Iterable[float], right: Iterable[float]) -> float | None:
    xs = [float(value) for value in left]
    ys = [float(value) for value in right]
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = fmean(xs)
    mean_y = fmean(ys)
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denom_x = math.sqrt(sum(value * value for value in dx))
    denom_y = math.sqrt(sum(value * value for value in dy))
    denom = denom_x * denom_y
    if denom == 0:
        return None
    value = sum(x * y for x, y in zip(dx, dy, strict=False)) / denom
    return value if math.isfinite(value) else None


def longest_frozen_run(values: list[int]) -> int:
    if not values:
        return 0
    best = 1
    current = 1
    previous = values[0]
    for value in values[1:]:
        if value == previous:
            current += 1
        else:
            best = max(best, current)
            current = 1
            previous = value
    return max(best, current)


def _candidate_series(records: list[dict[str, Any]], field_name: str) -> list[float]:
    values: list[float] = []
    for record in records:
        decoded = record.get("candidate", {})
        value = decoded.get(field_name)
        values.append(float(value) if value is not None else math.nan)
    return values


def _byte_values(records: list[dict[str, Any]], byte_index: int) -> list[int]:
    return [record["frame"][byte_index] for record in records]


def compute_byte_statistics(
    records: Iterable[dict[str, Any]],
    *,
    session: str = "all",
    byte_indexes: range = BYTE_ATLAS_RANGE,
) -> list[ByteStatistic]:
    usable = [record for record in records if record.get("frame") is not None]
    rpm = _candidate_series(usable, "rpm")
    tps = _candidate_series(usable, "tps_voltage_candidate")
    injector = _candidate_series(usable, "injector_raw_candidate")
    rows: list[ByteStatistic] = []

    for byte_index in byte_indexes:
        values = _byte_values(usable, byte_index)
        diffs = [abs(values[index] - values[index - 1]) for index in range(1, len(values))]
        rows.append(
            ByteStatistic(
                session=session,
                byte_index=byte_index,
                count=len(values),
                minimum=float(min(values)) if values else None,
                maximum=float(max(values)) if values else None,
                mean=fmean(values) if values else None,
                median=float(median(values)) if values else None,
                stddev=pstdev(values) if len(values) > 1 else 0.0 if values else None,
                unique_values=len(set(values)),
                pct_00=(values.count(0) / len(values) * 100.0) if values else 0.0,
                pct_ff=(values.count(255) / len(values) * 100.0) if values else 0.0,
                mean_abs_change=fmean(diffs) if diffs else 0.0 if values else None,
                max_abs_change=float(max(diffs)) if diffs else 0.0 if values else None,
                longest_frozen_run=longest_frozen_run(values),
                corr_rpm=pearson(values, rpm) if values else None,
                corr_tps_voltage=pearson(values, tps) if values else None,
                corr_injector_raw=pearson(values, injector) if values else None,
            )
        )
    return rows


def correlation_matrix(
    records: Iterable[dict[str, Any]],
    *,
    byte_indexes: range = BYTE_ATLAS_RANGE,
) -> list[dict[str, Any]]:
    usable = [record for record in records if record.get("frame") is not None]
    matrix: list[dict[str, Any]] = []
    series = {byte_index: _byte_values(usable, byte_index) for byte_index in byte_indexes}
    for left in byte_indexes:
        row: dict[str, Any] = {"byte_index": left}
        for right in byte_indexes:
            row[f"b{right}"] = pearson(series[left], series[right])
        matrix.append(row)
    return matrix


def change_point_summary(
    records: Iterable[dict[str, Any]],
    *,
    session: str = "all",
    byte_indexes: range = BYTE_ATLAS_RANGE,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    usable = [record for record in records if record.get("frame") is not None]
    changes: list[dict[str, Any]] = []
    for byte_index in byte_indexes:
        previous: dict[str, Any] | None = None
        for record in usable:
            if previous is None:
                previous = record
                continue
            delta = abs(record["frame"][byte_index] - previous["frame"][byte_index])
            if delta:
                changes.append(
                    {
                        "session": session,
                        "byte_index": byte_index,
                        "source_file": record.get("source_file"),
                        "line_number": record.get("line_number"),
                        "seq": record.get("seq"),
                        "device_time_ms": record.get("device_time_ms"),
                        "previous_seq": previous.get("seq"),
                        "previous_device_time_ms": previous.get("device_time_ms"),
                        "delta": delta,
                        "previous_value": previous["frame"][byte_index],
                        "value": record["frame"][byte_index],
                    }
                )
            previous = record
    changes.sort(key=lambda row: row["delta"], reverse=True)
    return changes[:top_n]


def frozen_signal_summary(
    records: Iterable[dict[str, Any]],
    *,
    session: str = "all",
    byte_indexes: range = BYTE_ATLAS_RANGE,
) -> list[dict[str, Any]]:
    usable = [record for record in records if record.get("frame") is not None]
    rows: list[dict[str, Any]] = []
    for byte_index in byte_indexes:
        values = _byte_values(usable, byte_index)
        rows.append(
            {
                "session": session,
                "byte_index": byte_index,
                "longest_frozen_run": longest_frozen_run(values),
                "count": len(values),
                "unique_values": len(set(values)),
                "pct_session_frozen": (longest_frozen_run(values) / len(values) * 100.0) if values else 0.0,
            }
        )
    rows.sort(key=lambda row: (row["longest_frozen_run"], -row["unique_values"]), reverse=True)
    return rows


def injector_scale_summary(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    usable = [record for record in records if record.get("frame") is not None]
    raw_values = [float(record["candidate"]["injector_raw_candidate"]) for record in usable]
    rpm_values = [float(record["candidate"]["rpm"]) for record in usable]
    tps_values = [float(record["candidate"]["tps_voltage_candidate"]) for record in usable]
    candidates = {
        "raw / 100": [value / 100.0 for value in raw_values],
        "raw / 250": [value / 250.0 for value in raw_values],
        "raw / 256": [value / 256.0 for value in raw_values],
    }
    rows: list[dict[str, Any]] = []
    for scale_name, values in candidates.items():
        zero_rpm_values = [value for value, rpm in zip(values, rpm_values, strict=False) if rpm == 0]
        cranking_values = [value for value, rpm in zip(values, rpm_values, strict=False) if 0 < rpm < 500]
        rows.append(
            {
                "scale": scale_name,
                "minimum": min(values) if values else None,
                "median": quantile(values, 0.5),
                "p95": quantile(values, 0.95),
                "maximum": max(values) if values else None,
                "rpm_zero_median": quantile(zero_rpm_values, 0.5),
                "cranking_median": quantile(cranking_values, 0.5),
                "correlation_rpm": pearson(values, rpm_values),
                "correlation_tps_voltage": pearson(values, tps_values),
                "physical_plausibility": _injector_plausibility(scale_name, values, zero_rpm_values),
            }
        )
    return rows


def _injector_plausibility(scale_name: str, values: list[float], zero_rpm_values: list[float]) -> str:
    if not values:
        return "no data"
    maximum = max(values)
    p95 = quantile(values, 0.95) or 0.0
    zero_nonzero = any(value > 0 for value in zero_rpm_values)
    notes: list[str] = []
    if maximum > 25:
        notes.append("upper range high for typical motorcycle injector pulse width")
    elif maximum < 3:
        notes.append("upper range low for hard acceleration")
    else:
        notes.append("range plausible but unverified")
    if p95 > 15:
        notes.append("95th percentile high")
    if zero_nonzero:
        notes.append("non-zero at RPM zero/cranking transitions needs review")
    return "; ".join(notes) + f"; {scale_name} not selected as verified"


def _scale_points(values: list[float], width: int, height: int, padding: int) -> list[tuple[float, float]]:
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    span = maximum - minimum if maximum != minimum else 1.0
    x_span = max(1, len(values) - 1)
    points: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        x = padding + (index / x_span) * (width - padding * 2)
        y = height - padding - ((value - minimum) / span) * (height - padding * 2)
        points.append((x, y))
    return points


def write_timeseries_svg(
    records: Iterable[dict[str, Any]],
    *,
    byte_index: int,
    title: str,
    output_path,
    max_points: int = 1200,
) -> None:
    usable = [record for record in records if record.get("frame") is not None]
    values = _byte_values(usable, byte_index)
    if len(values) > max_points:
        step = math.ceil(len(values) / max_points)
        values = values[::step]
    width = 960
    height = 260
    padding = 34
    points = _scale_points([float(value) for value in values], width, height, padding)
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    minimum = min(values) if values else None
    maximum = max(values) if values else None
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{padding}" y="22" font-family="sans-serif" font-size="14" fill="#111827">{html.escape(title)}</text>
  <line x1="{padding}" y1="{height - padding}" x2="{width - padding}" y2="{height - padding}" stroke="#9ca3af" stroke-width="1"/>
  <line x1="{padding}" y1="{padding}" x2="{padding}" y2="{height - padding}" stroke="#9ca3af" stroke-width="1"/>
  <text x="{padding}" y="{height - 8}" font-family="sans-serif" font-size="11" fill="#4b5563">record order</text>
  <text x="{padding + 4}" y="{padding - 8}" font-family="sans-serif" font-size="11" fill="#4b5563">min={minimum} max={maximum}</text>
  <polyline points="{polyline}" fill="none" stroke="#2563eb" stroke-width="1.5"/>
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def write_correlation_heatmap_svg(matrix: list[dict[str, Any]], *, output_path, title: str) -> None:
    byte_indexes = [int(row["byte_index"]) for row in matrix]
    cell = 32
    padding = 58
    width = padding + cell * len(byte_indexes) + 14
    height = padding + cell * len(byte_indexes) + 32

    def color(value: float | None) -> str:
        if value is None:
            return "#f3f4f6"
        clamped = max(-1.0, min(1.0, value))
        if clamped >= 0:
            red = int(255 - clamped * 150)
            green = int(255 - clamped * 90)
            blue = int(255)
        else:
            red = int(255)
            green = int(255 + clamped * 120)
            blue = int(255 + clamped * 170)
        return f"#{red:02x}{green:02x}{blue:02x}"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="12" y="22" font-family="sans-serif" font-size="14" fill="#111827">{html.escape(title)}</text>',
    ]
    for idx, byte_index in enumerate(byte_indexes):
        x = padding + idx * cell + cell / 2
        y = padding - 10
        parts.append(
            f'<text x="{x:.1f}" y="{y}" text-anchor="middle" font-family="sans-serif" font-size="10" fill="#374151">b{byte_index}</text>'
        )
        parts.append(
            f'<text x="{padding - 8}" y="{padding + idx * cell + cell / 2 + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="10" fill="#374151">b{byte_index}</text>'
        )
    for row_index, row in enumerate(matrix):
        for col_index, byte_index in enumerate(byte_indexes):
            value = row.get(f"b{byte_index}")
            x = padding + col_index * cell
            y = padding + row_index * cell
            label = "" if value is None else f"{value:.1f}"
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="{color(value)}" stroke="#ffffff" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{x + cell / 2:.1f}" y="{y + cell / 2 + 3:.1f}" text-anchor="middle" font-family="sans-serif" font-size="9" fill="#111827">{label}</text>'
            )
    parts.append("</svg>\n")
    output_path.write_text("\n".join(parts), encoding="utf-8")

