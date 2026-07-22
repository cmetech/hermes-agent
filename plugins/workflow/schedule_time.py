"""Exact RFC 3339 schedule-instant normalization shared by API and storage."""

from __future__ import annotations

from datetime import datetime, timezone
import re


_RFC3339_INSTANT = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[Tt]"
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<zone>[Zz]|[+-]\d{2}:\d{2})$"
)


class ScheduleInstantError(ValueError):
    """An instant is not representable canonical RFC 3339 schedule identity."""


def normalize_rfc3339_instant(value: object) -> str:
    """Return exact UTC-Z identity without losing submicrosecond precision."""
    if not isinstance(value, str):
        raise ScheduleInstantError("schedule instant must be text")
    matched = _RFC3339_INSTANT.fullmatch(value)
    if matched is None or matched.group("zone") == "-00:00":
        raise ScheduleInstantError("schedule instant is not RFC 3339")
    zone = matched.group("zone")
    normalized_zone = "+00:00" if zone in {"Z", "z"} else zone
    try:
        local_second = datetime.fromisoformat(
            f"{matched.group('date')}T{matched.group('time')}{normalized_zone}"
        )
        utc_second = local_second.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ScheduleInstantError("schedule instant is out of range") from exc
    canonical = utc_second.isoformat(timespec="seconds").removesuffix("+00:00")
    fraction = (matched.group("fraction") or "").rstrip("0")
    if fraction and len(fraction) <= 6:
        fraction = fraction.ljust(6, "0")
    return f"{canonical}{f'.{fraction}' if fraction else ''}Z"


def rfc3339_instant_is_after(value: str, observed: datetime) -> bool:
    """Compare an exact canonical schedule instant with an aware clock sample."""
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("observed must be timezone-aware")
    canonical = normalize_rfc3339_instant(value)
    if canonical != value:
        raise ScheduleInstantError("schedule instant is not canonical")
    try:
        observed_utc = observed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ScheduleInstantError("observed instant is out of range") from exc
    scheduled_second = datetime.fromisoformat(canonical[:19] + "+00:00")
    observed_second = observed_utc.replace(microsecond=0)
    if scheduled_second != observed_second:
        return scheduled_second > observed_second
    fraction = canonical[20:-1] if canonical[19:20] == "." else ""
    observed_fraction = f"{observed_utc.microsecond:06d}".rstrip("0")
    width = max(len(fraction), len(observed_fraction))
    return fraction.ljust(width, "0") > observed_fraction.ljust(width, "0")


__all__ = [
    "ScheduleInstantError",
    "normalize_rfc3339_instant",
    "rfc3339_instant_is_after",
]
