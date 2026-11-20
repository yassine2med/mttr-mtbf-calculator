"""MTTR, MTBF and availability from a failure log.

Definitions used here:
    downtime      = merged time the asset was down inside the observation window
    uptime        = window hours - downtime
    MTTR (hours)  = downtime / number of failures
    MTBF (hours)  = uptime / number of failures
    availability  = uptime / window hours
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta

REPEAT_DAYS = 7


class FailureLogError(ValueError):
    pass


@dataclass(frozen=True)
class Failure:
    asset_id: str
    failed_at: datetime
    restored_at: datetime | None
    cause: str = ""


@dataclass
class AssetMetrics:
    asset_id: str
    failures: int = 0
    downtime_hours: float = 0.0
    uptime_hours: float = 0.0
    window_hours: float = 0.0
    longest_repair_hours: float = 0.0
    repeat_failures: int = 0
    open_failures: int = 0
    mttr_hours: float | None = None
    mtbf_hours: float | None = None
    availability: float = 1.0
    causes: dict[str, int] = field(default_factory=dict)


def parse_time(text: str) -> datetime:
    text = text.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise FailureLogError(f"{text!r} is not a date and time like 2026-03-02 22:10")


def load_failures(path: str) -> list[Failure]:
    out = []
    with open(path, newline="", encoding="utf-8") as handle:
        for n, row in enumerate(csv.DictReader(handle), start=2):
            where = f"{path} line {n}"
            asset = (row.get("asset_id") or "").strip()
            if not asset:
                raise FailureLogError(f"{where}: asset_id is empty")
            try:
                failed = parse_time(row.get("failed_at") or "")
                restored_text = (row.get("restored_at") or "").strip()
                restored = parse_time(restored_text) if restored_text else None
            except FailureLogError as exc:
                raise FailureLogError(f"{where}: {exc}") from exc
            if restored and restored < failed:
                raise FailureLogError(f"{where}: restored_at is before failed_at")
            out.append(Failure(asset, failed, restored, (row.get("cause") or "").strip()))
    return out


def merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Merge overlapping or touching intervals so downtime is never counted twice."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600.0


def compute(failures: list[Failure], start: datetime, end: datetime) -> dict[str, AssetMetrics]:
    """Metrics per asset for the window [start, end].

    Only failures that start inside the window count. A failure with no restore time runs to the
    window end and is marked open. Records that overlap or touch are one incident, so a duplicate
    entry never counts twice.
    """
    if end <= start:
        raise FailureLogError("the window end must be after its start")
    window = hours(end - start)
    by_asset: dict[str, list[Failure]] = {}
    for f in failures:
        if start <= f.failed_at <= end:
            by_asset.setdefault(f.asset_id, []).append(f)
    result: dict[str, AssetMetrics] = {}
    for asset, items in sorted(by_asset.items()):
        m = AssetMetrics(asset, window_hours=window)
        items.sort(key=lambda f: f.failed_at)
        # Group the records into incidents: [start, end, open, cause].
        incidents: list[list] = []
        for f in items:
            stop = min(f.restored_at or end, end)
            if incidents and f.failed_at <= incidents[-1][1]:
                incidents[-1][1] = max(incidents[-1][1], stop)
                incidents[-1][2] = incidents[-1][2] or f.restored_at is None
            else:
                incidents.append([f.failed_at, stop, f.restored_at is None, f.cause])
        previous_end = None
        for begin, finish, is_open, cause in incidents:
            m.failures += 1
            m.downtime_hours += hours(finish - begin)
            m.longest_repair_hours = max(m.longest_repair_hours, hours(finish - begin))
            m.open_failures += 1 if is_open else 0
            if previous_end is not None and begin - previous_end <= timedelta(days=REPEAT_DAYS):
                m.repeat_failures += 1
            previous_end = finish
            if cause:
                key = cause.split(" (")[0].replace(" repeat", "")
                m.causes[key] = m.causes.get(key, 0) + 1
        m.uptime_hours = max(window - m.downtime_hours, 0.0)
        m.availability = m.uptime_hours / window
        m.mttr_hours = m.downtime_hours / m.failures
        m.mtbf_hours = m.uptime_hours / m.failures
        result[asset] = m
    return result


def fleet(metrics: dict[str, AssetMetrics]) -> AssetMetrics:
    """Combined figures: total failures and downtime over all assets, availability averaged by asset."""
    total = AssetMetrics("ALL", window_hours=next(iter(metrics.values())).window_hours if metrics else 0.0)
    for m in metrics.values():
        total.failures += m.failures
        total.downtime_hours += m.downtime_hours
        total.uptime_hours += m.uptime_hours
        total.repeat_failures += m.repeat_failures
        total.open_failures += m.open_failures
        total.longest_repair_hours = max(total.longest_repair_hours, m.longest_repair_hours)
    if total.failures:
        total.mttr_hours = total.downtime_hours / total.failures
        total.mtbf_hours = total.uptime_hours / total.failures
    if metrics:
        total.availability = sum(m.availability for m in metrics.values()) / len(metrics)
    return total


def availability_from(mtbf: float, mttr: float) -> float:
    """Inherent availability from MTBF and MTTR."""
    if mtbf < 0 or mttr < 0 or mtbf + mttr == 0:
        raise FailureLogError("MTBF and MTTR must not be negative and not both zero")
    return mtbf / (mtbf + mttr)
