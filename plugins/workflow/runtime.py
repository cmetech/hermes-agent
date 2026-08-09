"""Bounded runtime resources for the workflow HTTP adapter."""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping

from plugins.workflow.store import RunStore


class StoreRegistryCapacityError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowRetentionPolicy:
    terminal_board_days: int = 7

    @classmethod
    def from_profile(cls, hermes_home: str | Path) -> "WorkflowRetentionPolicy":
        from hermes_cli.config import load_config_readonly

        path = Path(hermes_home) / "config.yaml"
        document = load_config_readonly(path)
        try:
            values = document["plugins"]["entries"]["workflow"]["retention"]
        except (KeyError, TypeError):
            values = {}
        if not isinstance(values, Mapping):
            raise ValueError("plugins.entries.workflow.retention must be a mapping")
        days = values.get("terminal_board_days", 7)
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
            raise ValueError(
                "plugins.entries.workflow.retention.terminal_board_days must be 1..3650"
            )
        return cls(terminal_board_days=days)


@dataclass(frozen=True)
class WorkflowApiLimits:
    max_cached_profiles: int = 8
    max_event_waiters: int = 16
    store_io_workers: int = 4

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "WorkflowApiLimits":
        defaults = cls()
        parsed = {}
        for name in ("max_cached_profiles", "max_event_waiters", "store_io_workers"):
            value = values.get(name, getattr(defaults, name))
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 256
            ):
                raise ValueError(f"plugins.entries.workflow.api.{name} must be 1..256")
            parsed[name] = value
        return cls(**parsed)

    @classmethod
    def from_profile(cls, hermes_home: str | Path) -> "WorkflowApiLimits":
        from hermes_cli.config import load_config_readonly

        path = Path(hermes_home) / "config.yaml"
        document = load_config_readonly(path)
        try:
            values = document["plugins"]["entries"]["workflow"]["api"]
        except (KeyError, TypeError):
            values = {}
        if not isinstance(values, Mapping):
            raise ValueError("plugins.entries.workflow.api must be a mapping")
        return cls.from_mapping(values)


@dataclass
class _Entry:
    store: RunStore
    references: int = 0


class WorkflowStoreRegistry:
    def __init__(
        self,
        *,
        max_profiles: int = 8,
        store_factory: Callable[[Path], RunStore] = RunStore,
    ) -> None:
        if (
            isinstance(max_profiles, bool)
            or not isinstance(max_profiles, int)
            or max_profiles < 1
        ):
            raise ValueError("max_profiles must be positive")
        self._max_profiles = max_profiles
        self._store_factory = store_factory
        self._entries: OrderedDict[Path, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    @contextmanager
    def lease(self, hermes_home: str | Path) -> Iterator[RunStore]:
        home = Path(hermes_home).resolve()
        with self._lock:
            entry = self._entries.get(home)
            if entry is None:
                while len(self._entries) >= self._max_profiles:
                    idle = next(
                        (
                            path
                            for path, candidate in self._entries.items()
                            if candidate.references == 0
                        ),
                        None,
                    )
                    if idle is None:
                        raise StoreRegistryCapacityError(
                            "all cached workflow profiles are in use"
                        )
                    self._entries.pop(idle)
                entry = _Entry(self._store_factory(home))
                self._entries[home] = entry
            entry.references += 1
            self._entries.move_to_end(home)
        try:
            yield entry.store
        finally:
            with self._lock:
                entry.references -= 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "profiles": len(self._entries),
                "active_leases": sum(
                    entry.references for entry in self._entries.values()
                ),
                "max_profiles": self._max_profiles,
            }


class WorkflowApiRuntime:
    def __init__(self, limits: WorkflowApiLimits) -> None:
        self.limits = limits
        self.stores = WorkflowStoreRegistry(max_profiles=limits.max_cached_profiles)
        self.event_waiters = asyncio.Semaphore(limits.max_event_waiters)
        self.store_io = ThreadPoolExecutor(
            max_workers=limits.store_io_workers,
            thread_name_prefix="workflow-store-io",
        )

    async def run_store_io(self, function, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.store_io, lambda: function(*args, **kwargs)
        )

    def close(self) -> None:
        self.store_io.shutdown(wait=True, cancel_futures=True)


__all__ = [
    "StoreRegistryCapacityError",
    "WorkflowApiLimits",
    "WorkflowApiRuntime",
    "WorkflowRetentionPolicy",
    "WorkflowStoreRegistry",
]
