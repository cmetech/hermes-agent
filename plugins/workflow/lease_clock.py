"""Monotonic-corroborated clock samples for profile-local coordinator leases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import platform
import time
from typing import Protocol

import psutil


@dataclass(frozen=True, slots=True)
class LeaseClockSample:
    utc_now: datetime
    monotonic_now: float
    boot_id: str

    def __post_init__(self) -> None:
        if self.utc_now.tzinfo is None or self.utc_now.utcoffset() is None:
            raise ValueError("lease UTC clock must be timezone-aware")
        if not math.isfinite(float(self.monotonic_now)) or self.monotonic_now < 0:
            raise ValueError("lease monotonic clock must be finite and non-negative")
        if not isinstance(self.boot_id, str) or not self.boot_id:
            raise ValueError("lease boot identity must be non-empty")


class CorroboratedLease(Protocol):
    lease_expires_at: datetime
    heartbeat_monotonic: float | None
    lease_seconds: float | None
    boot_id: str | None


def current_boot_id() -> str:
    """Return a stable opaque identity for this host boot."""
    material = f"{platform.node()}\0{int(psutil.boot_time())}".encode()
    return hashlib.sha256(material).hexdigest()


def sample_lease_clock() -> LeaseClockSample:
    return LeaseClockSample(
        datetime.now(timezone.utc),
        time.monotonic(),
        current_boot_id(),
    )


def lease_is_fresh(lease: CorroboratedLease, sample: LeaseClockSample) -> bool:
    """Use monotonic elapsed time when the persisted clock domain is known."""
    if (
        lease.boot_id is None
        or lease.heartbeat_monotonic is None
        or lease.lease_seconds is None
    ):
        return sample.utc_now.astimezone(timezone.utc) < lease.lease_expires_at
    if lease.boot_id != sample.boot_id:
        return False
    elapsed = sample.monotonic_now - lease.heartbeat_monotonic
    return 0 <= elapsed < lease.lease_seconds


__all__ = [
    "LeaseClockSample",
    "current_boot_id",
    "lease_is_fresh",
    "sample_lease_clock",
]
