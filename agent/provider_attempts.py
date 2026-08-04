"""Optional request-local accounting at actual provider transport launches."""

from __future__ import annotations

from typing import Any


def reserve_provider_transport_attempt(agent: Any) -> None:
    """Reserve one actual provider call when a sealed caller installed a guard."""

    callback = getattr(agent, "_provider_attempt_reservation_callback", None)
    if callback is not None:
        callback()


__all__ = ["reserve_provider_transport_attempt"]
