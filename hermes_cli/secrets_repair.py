"""Read-only diagnosis and explicit transactional repair for secret storage."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
import threading
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

from hermes_cli import container_storage
from hermes_cli import secret_keystore as sk
from hermes_cli.plugin_configuration import PluginConfigurationService
from hermes_cli.secret_authority import (
    AUTHORITY_FILE,
    AUTHORITY_VERSION,
    AuthorityRegistry,
    AuthorityRegistryError,
    SecretAuthority,
    _reject_duplicate_keys,
    _validated_entries,
)


class RepairRefusedError(sk.KeystoreError):
    """A plan is ambiguous, stale, or lacks destructive confirmation."""


@dataclass(frozen=True)
class SecretFinding:
    code: str
    severity: Literal["info", "warning", "error"]
    key: str | None
    message: str


@dataclass(frozen=True)
class DoctorReport:
    configured_mode: str
    authorities: Mapping[str, str]
    findings: tuple[SecretFinding, ...]


@dataclass(frozen=True)
class RepairAction:
    code: str
    key: str | None
    source: str | None
    destination: str | None


@dataclass(frozen=True)
class RepairPlan:
    actions: tuple[RepairAction, ...]
    blocked_findings: tuple[SecretFinding, ...]


@dataclass(frozen=True)
class RepairReport:
    applied: tuple[RepairAction, ...]
    quarantine_paths: tuple[Path, ...]
    failed: tuple[SecretFinding, ...]


@dataclass(frozen=True)
class _SecretSnapshot:
    root: Path
    configured_mode: str
    registry: AuthorityRegistry | None
    registry_corrupt: bool
    authorities: Mapping[str, SecretAuthority]
    keys: tuple[str, ...]
    file_values: Mapping[str, str] | None = field(repr=False)
    file_corrupt: bool
    os_available: bool
    os_values: Mapping[str, str | None] = field(repr=False)
    path_states: Mapping[str, tuple[int, int, int, int]] = field(repr=False)
    path_modes: Mapping[str, int] = field(repr=False)
    findings: tuple[SecretFinding, ...]


@dataclass(frozen=True)
class _PlanBinding:
    fingerprint: bytes = field(repr=False)
    move_to: Literal["os", "file"] | None
    reset_unrecoverable: bool
    actions: tuple[RepairAction, ...]
    blocked_findings: tuple[SecretFinding, ...]


_LIVE_FILE_MODES = {
    "keystore.key": 0o600,
    "keystore.enc": 0o600,
    "keystore.lock": 0o600,
    AUTHORITY_FILE: 0o600,
}
_PLAN_FINGERPRINT_KEY = secrets.token_bytes(32)
_PLAN_BINDINGS: dict[
    int,
    tuple[weakref.ReferenceType[RepairPlan], _PlanBinding],
] = {}
_PLAN_BINDINGS_LOCK = threading.Lock()


def _path_state(path: Path) -> tuple[int, int, int, int] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _load_authority_registry_readonly(root: Path) -> AuthorityRegistry | None:
    """Read authority metadata without following a filesystem link."""
    path = root / AUTHORITY_FILE
    info = sk._lstat_regular_artifact(path)
    if info is None:
        return None
    try:
        raw = json.loads(
            sk._read_regular_file_nofollow(path, expected=info).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except AuthorityRegistryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, sk.KeystoreError) as exc:
        raise AuthorityRegistryError("cannot read authority registry") from exc
    if not isinstance(raw, dict) or set(raw) != {"version", "authorities"}:
        raise AuthorityRegistryError("invalid authority registry fields")
    entries = _validated_entries(raw["version"], raw["authorities"])
    return AuthorityRegistry(
        version=AUTHORITY_VERSION,
        entries=MappingProxyType(entries),
    )


def _finding(
    code: str,
    severity: Literal["info", "warning", "error"],
    key: str | None,
    message: str,
) -> SecretFinding:
    return SecretFinding(code=code, severity=severity, key=key, message=message)


def _inventory_keys() -> list[str]:
    try:
        return PluginConfigurationService().secret_storage_keys()
    except Exception:
        # Static plugin inventory is an aid, never authority. Registry and file
        # keys below still remain inspectable if a plugin manifest is damaged.
        return []


def _inspect_permissions(root: Path) -> list[SecretFinding]:
    root_info = _lstat(root)
    if (
        root_info is None
        or stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
    ):
        return []
    findings: list[SecretFinding] = []
    candidates = [(root, 0o700)]
    for filename, mode in _LIVE_FILE_MODES.items():
        path = root / filename
        info = _lstat(path)
        if info is not None and stat.S_ISREG(info.st_mode):
            candidates.append((path, mode))
    try:
        children = tuple(root.iterdir())
    except OSError:
        children = ()
    for path in children:
        info = _lstat(path)
        if (
            info is not None
            and stat.S_ISREG(info.st_mode)
            and path.name.startswith(".")
            and path.name.endswith(".tmp")
        ):
            candidates.append((path, 0o600))
    if sk._is_windows():
        from hermes_cli.windows_permissions import (
            WindowsAclError,
            inspect_directory_acl,
            inspect_file_acl,
        )

        for path, _expected in candidates:
            try:
                inspection = (
                    inspect_directory_acl(path)
                    if path == root
                    else inspect_file_acl(path)
                )
            except WindowsAclError as exc:
                inspection_detail = f"inspection failed ({type(exc).__name__})"
            else:
                if inspection.secure:
                    continue
                inspection_detail = inspection.detail or "ACL does not match policy"
            findings.append(
                _finding(
                    "PERMISSION_DRIFT",
                    "warning",
                    None,
                    f"{path} has mode Windows ACL drift ({inspection_detail}); "
                    "expected current-user-only",
                )
            )
        return findings

    for path, expected in candidates:
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (
                stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
            ):
                continue
            actual = stat.S_IMODE(info.st_mode)
        except OSError:
            continue
        if actual != expected:
            findings.append(
                _finding(
                    "PERMISSION_DRIFT",
                    "warning",
                    None,
                    f"{path} has mode {actual:04o}; expected {expected:04o}",
                )
            )
    return findings


def _abandoned_temp_findings(root: Path) -> list[SecretFinding]:
    root_info = _lstat(root)
    if (
        root_info is None
        or stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
    ):
        return []
    try:
        paths = sorted(
            path
            for path in root.iterdir()
            if path.name.startswith(".")
            and path.name.endswith(".tmp")
            and (info := _lstat(path)) is not None
            and (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode))
        )
    except OSError:
        return []
    return [
        _finding(
            "ABANDONED_TEMP",
            "warning",
            None,
            f"abandoned atomic-replacement temporary file: {path}",
        )
        for path in paths
    ]


def _container_storage_finding(root: Path) -> SecretFinding | None:
    if not container_storage.is_container():
        return None
    evidence = container_storage.inspect_mount_persistence(root)
    if evidence.state is container_storage.PersistenceState.PERSISTENT:
        return None
    return _finding(
        "CONTAINER_STORAGE_UNPROVEN",
        "error",
        None,
        f"encrypted file storage at {root} is {evidence.state.value}: "
        f"{evidence.reason}. Mount HERMES_HOME on durable storage; in "
        f"Kubernetes or another ambiguous runtime, verify the storage class "
        f"and retention policy, then set "
        f"security.container_persistence_acknowledged: true in config.yaml",
    )


def _inspect_secrets() -> _SecretSnapshot:
    profile_identity = sk._active_profile_identity()
    root = sk._secrets_root(profile_identity)
    mode = sk._resolve_mode()
    findings: list[SecretFinding] = []
    storage_finding = _container_storage_finding(root)
    if storage_finding is not None:
        findings.append(storage_finding)

    registry: AuthorityRegistry | None = None
    registry_corrupt = False
    root_info = _lstat(root)
    root_unsafe = root_info is not None and (
        stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode)
    )
    try:
        if root_unsafe:
            raise AuthorityRegistryError("unsafe secret-store root")
        registry = _load_authority_registry_readonly(root)
    except (AuthorityRegistryError, OSError, sk.KeystoreError):
        registry_corrupt = True
        findings.append(
            _finding(
                "AUTHORITY_CORRUPT",
                "error",
                None,
                "the authority registry is unreadable or invalid",
            )
        )
    authorities = dict(registry.entries) if registry is not None else {}

    file_corrupt = False
    file_values: dict[str, str] | None
    try:
        file_values = sk._read_file_store_readonly(root)
    except (OSError, sk.KeystoreError):
        file_values = None
        file_corrupt = True
    has_file_authority = any(
        authority is SecretAuthority.FILE for authority in authorities.values()
    )
    if (
        not file_corrupt
        and has_file_authority
        and not (root / sk._KEY_FILE).exists()
        and not (root / sk._DATA_FILE).exists()
    ):
        # This is also the restart shape after a reset/rebuild was interrupted
        # immediately after quarantine. Keeping it diagnosable makes repair
        # retryable without consuming the quarantine.
        file_values = None
        file_corrupt = True
    if file_corrupt:
        findings.append(
            _finding(
                "FILE_STORE_CORRUPT",
                "error",
                None,
                "the encrypted file store cannot be validated",
            )
        )

    known_keys = set(_inventory_keys()) | set(authorities)
    if file_values is not None:
        known_keys.update(file_values)
    keys = tuple(sorted(known_keys))

    os_store = sk.OSKeystore(profile_identity)
    os_available = True
    os_values: dict[str, str | None] = {}
    try:
        # A get is the only portable non-mutating keyring availability check.
        # Never call probe_os_keystore(): its set/get/delete round trip would
        # violate doctor's contract even though the probe key is namespaced.
        os_store.get(sk._PROBE_KEY)
    except Exception:
        os_available = False
        findings.append(
            _finding(
                "OS_UNAVAILABLE",
                "warning",
                None,
                "the OS keystore cannot be read within the bounded deadline",
            )
        )
    if os_available:
        for key in keys:
            try:
                os_values[key] = os_store.get(key)
            except Exception:
                os_available = False
                os_values.clear()
                findings.append(
                    _finding(
                        "OS_UNAVAILABLE",
                        "warning",
                        None,
                        "the OS keystore became unavailable during bounded reads",
                    )
                )
                break

    findings.extend(_inspect_permissions(root))
    findings.extend(_abandoned_temp_findings(root))

    for key in keys:
        authority = authorities.get(key)
        file_value = None if file_values is None else file_values.get(key)
        os_known = os_available
        os_value = os_values.get(key) if os_known else None

        if authority in {SecretAuthority.OS, SecretAuthority.FILE}:
            if mode in {"os", "file"} and authority.value != mode:
                findings.append(
                    _finding(
                        "AUTHORITY_MODE_MISMATCH",
                        "warning",
                        key,
                        f"registered {authority.value} authority conflicts with {mode} mode",
                    )
                )
            if authority is SecretAuthority.FILE and file_corrupt:
                continue
            if authority is SecretAuthority.OS and not os_known:
                continue
            authoritative = file_value if authority is SecretAuthority.FILE else os_value
            alternate = os_value if authority is SecretAuthority.FILE else file_value
            alternate_known = os_known if authority is SecretAuthority.FILE else not file_corrupt
            if authoritative is not None and alternate_known and alternate is not None:
                findings.append(
                    _finding(
                        "STALE_DUPLICATE",
                        "warning",
                        key,
                        f"a non-authoritative {('os' if authority is SecretAuthority.FILE else 'file')} copy exists",
                    )
                )
            elif authoritative is None and alternate_known and alternate is not None:
                findings.append(
                    _finding(
                        "AUTHORITY_MODE_MISMATCH",
                        "error",
                        key,
                        "registered authority is absent while the alternate tier has a recoverable copy",
                    )
                )
            elif authoritative is None and alternate_known:
                findings.append(
                    _finding(
                        "AUTHORITY_MODE_MISMATCH",
                        "error",
                        key,
                        "registered authority has no corresponding stored value",
                    )
                )
        elif authority is SecretAuthority.CLEARED:
            stale_tiers = []
            if file_value is not None:
                stale_tiers.append("file")
            if os_known and os_value is not None:
                stale_tiers.append("os")
            if stale_tiers:
                findings.append(
                    _finding(
                        "STALE_DUPLICATE",
                        "warning",
                        key,
                        "a cleared key retains a stale copy in " + ", ".join(stale_tiers),
                    )
                )
        elif file_value is not None and os_known and os_value is not None:
            if file_value == os_value:
                findings.append(
                    _finding(
                        "STALE_DUPLICATE",
                        "warning",
                        key,
                        "equal pre-registry copies require one authoritative tier",
                    )
                )
            else:
                findings.append(
                    _finding(
                        "COMPETING_VALUES",
                        "error",
                        key,
                        "pre-registry tiers contain different values; no value was selected",
                    )
                )

    ordered_findings = tuple(
        sorted(findings, key=lambda item: (item.code, item.key or "", item.message))
    )
    tracked_paths = {root / filename for filename in _LIVE_FILE_MODES}
    tracked_paths.add(root)
    tracked_paths.update(
        Path(finding.message.split(": ", 1)[1])
        for finding in ordered_findings
        if finding.code == "ABANDONED_TEMP"
    )
    path_states: dict[str, tuple[int, int, int, int]] = {}
    path_modes: dict[str, int] = {}
    for path in tracked_paths:
        try:
            info = path.lstat()
        except OSError:
            continue
        path_states[str(path)] = (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        )
        path_modes[str(path)] = stat.S_IMODE(info.st_mode)
    return _SecretSnapshot(
        root=root,
        configured_mode=mode,
        registry=registry,
        registry_corrupt=registry_corrupt,
        authorities=MappingProxyType(authorities),
        keys=keys,
        file_values=(
            None if file_values is None else MappingProxyType(dict(file_values))
        ),
        file_corrupt=file_corrupt,
        os_available=os_available,
        os_values=MappingProxyType(os_values),
        path_states=MappingProxyType(path_states),
        path_modes=MappingProxyType(path_modes),
        findings=ordered_findings,
    )


def diagnose_secrets() -> DoctorReport:
    """Inspect one profile using only bounded gets and filesystem reads."""
    snapshot = _inspect_secrets()
    return DoctorReport(
        configured_mode=snapshot.configured_mode,
        authorities=MappingProxyType(
            {
                key: authority.value
                for key, authority in sorted(snapshot.authorities.items())
            }
        ),
        findings=snapshot.findings,
    )


def _preferred_tier(snapshot: _SecretSnapshot, move_to: str | None) -> str:
    if move_to is not None:
        return move_to
    if snapshot.configured_mode == "file":
        return "file"
    return "os"


def _blocked(snapshot: _SecretSnapshot, code: str, key: str | None = None):
    return next(
        (
            finding
            for finding in snapshot.findings
            if finding.code == code and (key is None or finding.key == key)
        ),
        _finding(code, "error", key, "repair cannot determine a unique safe action"),
    )


def _build_plan(
    snapshot: _SecretSnapshot,
    *,
    move_to: Literal["os", "file"] | None,
    reset_unrecoverable: bool,
) -> RepairPlan:
    actions: list[RepairAction] = []
    blocked: list[SecretFinding] = []
    preferred = _preferred_tier(snapshot, move_to)

    for finding in snapshot.findings:
        if finding.code == "PERMISSION_DRIFT":
            path_text, _rest = finding.message.split(" has mode ", 1)
            expected = "0700" if Path(path_text) == snapshot.root else "0600"
            actions.append(
                RepairAction("REPAIR_PERMISSIONS", None, path_text, expected)
            )
        elif finding.code == "ABANDONED_TEMP":
            actions.append(
                RepairAction(
                    "CLEAN_ABANDONED_TEMP",
                    None,
                    finding.message.split(": ", 1)[1],
                    None,
                )
            )

    file_authority_keys = tuple(
        key
        for key, authority in snapshot.authorities.items()
        if authority is SecretAuthority.FILE
    )
    storage_unproven = next(
        (
            finding
            for finding in snapshot.findings
            if finding.code == "CONTAINER_STORAGE_UNPROVEN"
        ),
        None,
    )
    if snapshot.file_corrupt:
        recoverable = bool(file_authority_keys) and snapshot.os_available and all(
            snapshot.os_values.get(key) is not None for key in file_authority_keys
        )
        if recoverable and not snapshot.registry_corrupt:
            if storage_unproven is not None:
                blocked.append(storage_unproven)
            else:
                actions.append(
                    RepairAction("REBUILD_FILE_STORE", None, "os", "file")
                )
        elif (
            reset_unrecoverable
            and not snapshot.registry_corrupt
            and snapshot.os_available
        ):
            if storage_unproven is not None:
                blocked.append(storage_unproven)
            else:
                actions.append(
                    RepairAction("RESET_UNRECOVERABLE", None, "file", "cleared")
                )
        else:
            blocked.append(_blocked(snapshot, "FILE_STORE_CORRUPT"))
            if not snapshot.os_available:
                blocked.append(_blocked(snapshot, "OS_UNAVAILABLE"))

    if snapshot.registry_corrupt:
        competing = [
            finding
            for finding in snapshot.findings
            if finding.code == "COMPETING_VALUES"
        ]
        if snapshot.file_corrupt or (not snapshot.os_available and snapshot.keys):
            blocked.append(_blocked(snapshot, "AUTHORITY_CORRUPT"))
            if not snapshot.os_available and snapshot.keys:
                blocked.append(_blocked(snapshot, "OS_UNAVAILABLE"))
        elif competing:
            blocked.extend(competing)
        else:
            actions.append(
                RepairAction("REBUILD_AUTHORITY", None, "corrupt", preferred)
            )
            nonselected = "file" if preferred == "os" else "os"
            for key in snapshot.keys:
                file_value = (
                    None
                    if snapshot.file_values is None
                    else snapshot.file_values.get(key)
                )
                os_value = snapshot.os_values.get(key)
                if (
                    file_value is not None
                    and os_value is not None
                    and file_value == os_value
                ):
                    actions.append(
                        RepairAction(
                            "DELETE_STALE_COPY", key, nonselected, None
                        )
                    )

    if not snapshot.registry_corrupt and not snapshot.file_corrupt:
        for key in snapshot.keys:
            authority = snapshot.authorities.get(key)
            file_value = (
                None if snapshot.file_values is None else snapshot.file_values.get(key)
            )
            os_known = snapshot.os_available
            os_value = snapshot.os_values.get(key) if os_known else None
            if authority in {SecretAuthority.OS, SecretAuthority.FILE}:
                source = authority.value
                authoritative = file_value if source == "file" else os_value
                other = "os" if source == "file" else "file"
                alternate_known = os_known if other == "os" else True
                alternate = os_value if other == "os" else file_value
                if move_to is not None and source != move_to:
                    if authoritative is None:
                        blocked.append(_blocked(snapshot, "AUTHORITY_MODE_MISMATCH", key))
                    else:
                        actions.append(
                            RepairAction("MOVE_SECRET", key, source, move_to)
                        )
                    continue
                if (
                    move_to is None
                    and snapshot.configured_mode in {"os", "file"}
                    and source != snapshot.configured_mode
                ):
                    blocked.append(_blocked(snapshot, "AUTHORITY_MODE_MISMATCH", key))
                    continue
                if source == "os" and not os_known:
                    blocked.append(_blocked(snapshot, "OS_UNAVAILABLE"))
                elif authoritative is not None and alternate_known and alternate is not None:
                    actions.append(
                        RepairAction("DELETE_STALE_COPY", key, other, None)
                    )
                elif authoritative is None and alternate_known and alternate is not None:
                    actions.append(RepairAction("RESUME_MOVE", key, source, other))
                elif authoritative is None and alternate_known:
                    if reset_unrecoverable and source == "file":
                        if storage_unproven is not None:
                            blocked.append(storage_unproven)
                        else:
                            actions.append(
                                RepairAction(
                                    "RESET_UNRECOVERABLE", None, "file", "cleared"
                                )
                            )
                    else:
                        blocked.append(
                            _blocked(snapshot, "AUTHORITY_MODE_MISMATCH", key)
                        )
            elif authority is SecretAuthority.CLEARED:
                if file_value is not None:
                    actions.append(
                        RepairAction("DELETE_STALE_COPY", key, "file", None)
                    )
                if os_known and os_value is not None:
                    actions.append(
                        RepairAction("DELETE_STALE_COPY", key, "os", None)
                    )
            else:
                if file_value is not None and not os_known:
                    blocked.append(_blocked(snapshot, "OS_UNAVAILABLE"))
                elif file_value is not None and os_value is not None:
                    if file_value != os_value:
                        blocked.append(_blocked(snapshot, "COMPETING_VALUES", key))
                    else:
                        actions.append(
                            RepairAction("REBUILD_AUTHORITY", key, preferred, preferred)
                        )
                        other = "file" if preferred == "os" else "os"
                        actions.append(
                            RepairAction("DELETE_STALE_COPY", key, other, None)
                        )
                elif file_value is not None:
                    destination = move_to or "file"
                    if destination == "file":
                        actions.append(
                            RepairAction("REBUILD_AUTHORITY", key, "file", "file")
                        )
                    else:
                        actions.append(
                            RepairAction("MOVE_SECRET", key, "file", destination)
                        )
                elif os_known and os_value is not None:
                    destination = move_to or "os"
                    if destination == "os":
                        actions.append(
                            RepairAction("REBUILD_AUTHORITY", key, "os", "os")
                        )
                    else:
                        actions.append(
                            RepairAction("MOVE_SECRET", key, "os", destination)
                        )

    unique_blocked = {
        (item.code, item.key, item.message): item for item in blocked
    }.values()
    unique_actions = {
        (item.code, item.key, item.source, item.destination): item for item in actions
    }.values()
    ordered_actions = tuple(
        sorted(unique_actions, key=lambda item: (item.code, item.key or ""))
    )
    ordered_blocked = tuple(
        sorted(unique_blocked, key=lambda item: (item.code, item.key or "", item.message))
    )
    return RepairPlan(actions=ordered_actions, blocked_findings=ordered_blocked)


def _keyed_value_digest(tier: str, key: str, value: str | None) -> str:
    marker = b"absent" if value is None else b"present\0" + value.encode("utf-8")
    message = b"value\0" + tier.encode() + b"\0" + key.encode() + b"\0" + marker
    return hmac.new(_PLAN_FINGERPRINT_KEY, message, hashlib.sha256).hexdigest()


def _snapshot_fingerprint(snapshot: _SecretSnapshot) -> bytes:
    value_digests: list[tuple[str, str, str]] = []
    if snapshot.file_values is not None:
        value_digests.extend(
            ("file", key, _keyed_value_digest("file", key, snapshot.file_values.get(key)))
            for key in snapshot.keys
        )
    if snapshot.os_available:
        value_digests.extend(
            ("os", key, _keyed_value_digest("os", key, snapshot.os_values.get(key)))
            for key in snapshot.keys
        )
    artifact_states = sorted(
        (path, state, snapshot.path_modes[path])
        for path, state in snapshot.path_states.items()
        if Path(path) != snapshot.root and Path(path).name != sk._LOCK_FILE
    )
    payload = {
        "mode": snapshot.configured_mode,
        "registry_corrupt": snapshot.registry_corrupt,
        "authorities": sorted(
            (key, authority.value) for key, authority in snapshot.authorities.items()
        ),
        "keys": snapshot.keys,
        "file_corrupt": snapshot.file_corrupt,
        "os_available": snapshot.os_available,
        "value_digests": value_digests,
        "artifact_states": artifact_states,
        "findings": [
            (finding.code, finding.severity, finding.key, finding.message)
            for finding in snapshot.findings
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        _PLAN_FINGERPRINT_KEY,
        b"snapshot\0" + encoded,
        hashlib.sha256,
    ).digest()


def _bind_plan(
    plan: RepairPlan,
    snapshot: _SecretSnapshot,
    *,
    move_to: Literal["os", "file"] | None,
    reset_unrecoverable: bool,
) -> RepairPlan:
    binding = _PlanBinding(
        fingerprint=_snapshot_fingerprint(snapshot),
        move_to=move_to,
        reset_unrecoverable=reset_unrecoverable,
        actions=plan.actions,
        blocked_findings=plan.blocked_findings,
    )
    plan_id = id(plan)

    def _discard(reference: weakref.ReferenceType[RepairPlan]) -> None:
        with _PLAN_BINDINGS_LOCK:
            current = _PLAN_BINDINGS.get(plan_id)
            if current is not None and current[0] is reference:
                _PLAN_BINDINGS.pop(plan_id, None)

    reference = weakref.ref(plan, _discard)
    with _PLAN_BINDINGS_LOCK:
        _PLAN_BINDINGS[plan_id] = (reference, binding)
    return plan


def _plan_binding(plan: RepairPlan) -> _PlanBinding:
    with _PLAN_BINDINGS_LOCK:
        current = _PLAN_BINDINGS.get(id(plan))
    if current is None or current[0]() is not plan:
        raise RepairRefusedError("secret storage state changed after planning")
    binding = current[1]
    if (
        plan.actions != binding.actions
        or plan.blocked_findings != binding.blocked_findings
    ):
        raise RepairRefusedError("secret storage state changed after planning")
    return binding


def _assert_snapshot_bound(snapshot: _SecretSnapshot, binding: _PlanBinding) -> None:
    if not hmac.compare_digest(_snapshot_fingerprint(snapshot), binding.fingerprint):
        raise RepairRefusedError("secret storage state changed after planning")


def plan_secret_repair(
    *,
    move_to: Literal["os", "file"] | None = None,
    reset_unrecoverable: bool = False,
) -> RepairPlan:
    """Build a deterministic, side-effect-free repair plan."""
    if move_to not in {None, "os", "file"}:
        raise ValueError("move_to must be 'os', 'file', or None")
    snapshot = _inspect_secrets()
    plan = _build_plan(
        snapshot,
        move_to=move_to,
        reset_unrecoverable=reset_unrecoverable,
    )
    return _bind_plan(
        plan,
        snapshot,
        move_to=move_to,
        reset_unrecoverable=reset_unrecoverable,
    )


def _quarantine_artifacts(
    root: Path,
    paths: list[Path],
    *,
    finding_codes: tuple[str, ...],
) -> Path | None:
    root = Path(os.path.abspath(root))
    root_info = _lstat(root)
    if root_info is None or stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(
        root_info.st_mode
    ):
        raise RepairRefusedError("quarantine root is not a direct directory")
    quarantinable: list[Path] = []
    names: set[str] = set()
    for candidate in paths:
        path = Path(os.path.abspath(candidate))
        if path.parent != root:
            raise RepairRefusedError("quarantine artifact must be a direct child")
        if path.name in names or path.name == "manifest.json":
            raise RepairRefusedError("quarantine artifact name is not unique")
        info = _lstat(path)
        if info is None:
            continue
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise RepairRefusedError("quarantine artifact has unsupported file type")
        names.add(path.name)
        quarantinable.append(path)
    if not quarantinable:
        return None
    quarantine_root = root / "quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    sk._ensure_private_permissions(quarantine_root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = quarantine_root / f"{stamp}-{secrets.token_hex(4)}"
    destination.mkdir(mode=0o700)
    sk._ensure_private_permissions(destination, 0o700)
    manifest = {
        "version": 1,
        "finding_codes": sorted(set(finding_codes)),
        "paths": [path.name for path in quarantinable],
    }
    sk._write_private(
        destination / "manifest.json",
        (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        ),
    )
    for path in quarantinable:
        quarantined_path = destination / path.name
        os.replace(path, quarantined_path)
        moved_info = quarantined_path.lstat()
        if stat.S_ISREG(moved_info.st_mode):
            sk._ensure_private_permissions(quarantined_path, 0o600)
        elif not stat.S_ISLNK(moved_info.st_mode):
            raise RepairRefusedError("quarantined artifact changed file type")
    return destination


def _initialize_clean_file_store(root: Path) -> None:
    """Initialize a verified empty encrypted store while its lock is held."""
    store = sk.FileKeystore(root)
    store._initialize_root()
    store._write_all({})
    if store._read_all() != {}:
        raise sk.KeystoreError("clean file-store verification failed")


def _require_persistent_container_storage(root: Path) -> None:
    """Recheck durability immediately before destructive file-store repair."""
    finding = _container_storage_finding(root)
    if finding is not None:
        raise RepairRefusedError(finding.message)


def _registry_with(
    registry: AuthorityRegistry | None,
    updates: Mapping[str, SecretAuthority],
) -> AuthorityRegistry:
    entries = dict(registry.entries) if registry is not None else {}
    entries.update(updates)
    return AuthorityRegistry(version=AUTHORITY_VERSION, entries=entries)


def _reconstructed_registry(snapshot: _SecretSnapshot, preferred: str) -> AuthorityRegistry:
    entries: dict[str, SecretAuthority] = {}
    for key in snapshot.keys:
        file_value = (
            None if snapshot.file_values is None else snapshot.file_values.get(key)
        )
        os_value = snapshot.os_values.get(key) if snapshot.os_available else None
        if file_value is not None and os_value is not None:
            if file_value != os_value:
                raise RepairRefusedError("competing values prevent reconstruction")
            entries[key] = SecretAuthority(preferred)
        elif file_value is not None:
            entries[key] = SecretAuthority.FILE
        elif os_value is not None:
            entries[key] = SecretAuthority.OS
    return AuthorityRegistry(version=AUTHORITY_VERSION, entries=entries)


def _snapshot_tier_value(
    snapshot: _SecretSnapshot,
    key: str,
    tier: str,
) -> str | None:
    if tier == "file":
        return (
            None if snapshot.file_values is None else snapshot.file_values.get(key)
        )
    return snapshot.os_values.get(key)


def _assert_tier_unchanged(
    snapshot: _SecretSnapshot,
    key: str,
    tier: str,
    *,
    os_store: sk.OSKeystore,
) -> None:
    expected = _snapshot_tier_value(snapshot, key, tier)
    if tier == "file":
        current = sk._read_file_store_readonly(snapshot.root).get(key)
    else:
        current = os_store.get(key)
    if current != expected:
        raise RepairRefusedError("secret storage state changed after planning")


def _assert_path_unchanged(snapshot: _SecretSnapshot, path: Path) -> None:
    if _path_state(path) != snapshot.path_states.get(str(path)):
        raise RepairRefusedError("secret storage state changed after planning")


def _assert_direct_repair_path(
    root: Path,
    path: Path,
    *,
    allow_root: bool = False,
    allow_symlink: bool = False,
) -> Path:
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    if not ((allow_root and path == root) or path.parent == root):
        raise RepairRefusedError("repair target must be a direct child")
    info = _lstat(path)
    if info is None:
        raise RepairRefusedError("secret storage state changed after planning")
    if stat.S_ISLNK(info.st_mode):
        if allow_symlink:
            return path
        raise RepairRefusedError("refusing to repair permissions through a symlink")
    if not (
        stat.S_ISREG(info.st_mode)
        or (allow_root and path == root and stat.S_ISDIR(info.st_mode))
    ):
        raise RepairRefusedError("repair target has unsupported file type")
    return path


def apply_secret_repair(
    plan: RepairPlan,
    *,
    confirm_reset: bool = False,
) -> RepairReport:
    """Apply a previously generated plan after locked state revalidation."""
    if not isinstance(plan, RepairPlan):
        raise TypeError("plan must be a RepairPlan")
    binding = _plan_binding(plan)
    if plan.blocked_findings:
        raise RepairRefusedError("repair plan contains blocked findings")
    reset_requested = any(
        action.code == "RESET_UNRECOVERABLE" for action in plan.actions
    )
    if reset_requested and not confirm_reset:
        raise RepairRefusedError("unrecoverable reset requires confirmation")
    if not plan.actions:
        return RepairReport(applied=(), quarantine_paths=(), failed=())

    profile_identity = sk._active_profile_identity()
    root = sk._secrets_root(profile_identity)
    _assert_snapshot_bound(_inspect_secrets(), binding)
    root_info = _lstat(root)
    if root_info is not None and (
        stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode)
    ):
        raise RepairRefusedError("secret storage root is not a direct directory")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root_info is None:
        sk._ensure_private_permissions(root, 0o700)
    applied: list[RepairAction] = []
    quarantine_paths: list[Path] = []

    # Lock creation is the one unavoidable pre-revalidation mutation for an
    # OS-only profile that has never had a file store. It contains no secret
    # data. Existing locks are opened without chmod so permission drift remains
    # visible to the comparison below.
    with sk._store_lock(root, secure=False):
        snapshot = _inspect_secrets()
        _assert_snapshot_bound(snapshot, binding)
        move_to = binding.move_to

        priorities = {
            "REPAIR_PERMISSIONS": 0,
            "CLEAN_ABANDONED_TEMP": 1,
            "REBUILD_AUTHORITY": 2,
            "DELETE_STALE_COPY": 3,
            "RESUME_MOVE": 4,
            "MOVE_SECRET": 5,
            "REBUILD_FILE_STORE": 6,
            "RESET_UNRECOVERABLE": 7,
        }
        ordered = sorted(
            plan.actions,
            key=lambda action: (
                priorities.get(action.code, 99),
                action.key or "",
                action.source or "",
            ),
        )
        file_store: sk.FileKeystore | None = None
        os_store = sk.OSKeystore(profile_identity)
        for action in ordered:
            try:
                if action.code == "REPAIR_PERMISSIONS":
                    assert action.source is not None and action.destination is not None
                    path = _assert_direct_repair_path(
                        root,
                        Path(action.source),
                        allow_root=True,
                    )
                    _assert_path_unchanged(snapshot, path)
                    try:
                        current_mode = stat.S_IMODE(path.lstat().st_mode)
                    except OSError as exc:
                        raise RepairRefusedError(
                            "secret storage state changed after planning"
                        ) from exc
                    if current_mode != snapshot.path_modes.get(action.source):
                        raise RepairRefusedError(
                            "secret storage state changed after planning"
                        )
                    sk._ensure_private_permissions(
                        path, int(action.destination, 8)
                    )
                elif action.code == "CLEAN_ABANDONED_TEMP":
                    assert action.source is not None
                    path = _assert_direct_repair_path(
                        root,
                        Path(action.source),
                        allow_symlink=True,
                    )
                    _assert_path_unchanged(snapshot, path)
                    path.unlink()
                elif action.code == "REBUILD_AUTHORITY":
                    if action.key is None:
                        authority_path = root / AUTHORITY_FILE
                        _assert_path_unchanged(snapshot, authority_path)
                        if snapshot.file_values is not None:
                            current_file_values = sk._read_file_store_readonly(root)
                            if current_file_values != snapshot.file_values:
                                raise RepairRefusedError(
                                    "secret storage state changed after planning"
                                )
                        for key in snapshot.keys:
                            if snapshot.os_available:
                                _assert_tier_unchanged(
                                    snapshot,
                                    key,
                                    "os",
                                    os_store=os_store,
                                )
                        quarantine = _quarantine_artifacts(
                            root,
                            [root / AUTHORITY_FILE],
                            finding_codes=("AUTHORITY_CORRUPT",),
                        )
                        if quarantine is not None:
                            quarantine_paths.append(quarantine)
                        assert action.destination in {"os", "file"}
                        registry = _reconstructed_registry(
                            snapshot, action.destination
                        )
                        sk._write_authority_registry(root, registry)
                    else:
                        assert action.destination in {"os", "file"}
                        assert action.source in {"os", "file"}
                        _assert_tier_unchanged(
                            snapshot,
                            action.key,
                            action.source,
                            os_store=os_store,
                        )
                        registry = _load_authority_registry_readonly(root)
                        sk._write_authority_registry(
                            root,
                            _registry_with(
                                registry,
                                {action.key: SecretAuthority(action.destination)},
                            ),
                        )
                elif action.code == "DELETE_STALE_COPY":
                    assert action.key is not None and action.source in {"os", "file"}
                    _assert_tier_unchanged(
                        snapshot,
                        action.key,
                        action.source,
                        os_store=os_store,
                    )
                    if file_store is None:
                        file_store = sk.FileKeystore(root)
                    tier = SecretAuthority(action.source)
                    sk._delete_tier(
                        tier,
                        action.key,
                        file_store=file_store,
                        os_store=os_store,
                    )
                    if sk._read_tier(
                        tier,
                        action.key,
                        file_store=file_store,
                        os_store=os_store,
                    ) is not None:
                        raise sk.KeystoreError("stale-copy deletion verification failed")
                elif action.code == "RESUME_MOVE":
                    assert action.key is not None and action.destination in {"os", "file"}
                    _assert_tier_unchanged(
                        snapshot,
                        action.key,
                        action.destination,
                        os_store=os_store,
                    )
                    registry = _load_authority_registry_readonly(root)
                    sk._write_authority_registry(
                        root,
                        _registry_with(
                            registry,
                            {action.key: SecretAuthority(action.destination)},
                        ),
                    )
                elif action.code == "MOVE_SECRET":
                    assert action.key is not None and action.source in {"os", "file"}
                    _assert_tier_unchanged(
                        snapshot,
                        action.key,
                        action.source,
                        os_store=os_store,
                    )
                    assert action.destination in {"os", "file"}
                    sk._move_secret_locked(
                        action.key,
                        SecretAuthority(action.destination),
                        profile_identity=profile_identity,
                        root=root,
                        mode=snapshot.configured_mode,
                        failure_label="keystore repair move",
                    )
                elif action.code in {"REBUILD_FILE_STORE", "RESET_UNRECOVERABLE"}:
                    for artifact in (root / sk._KEY_FILE, root / sk._DATA_FILE):
                        _assert_path_unchanged(snapshot, artifact)
                    for key, authority in snapshot.authorities.items():
                        if authority is SecretAuthority.FILE:
                            _assert_tier_unchanged(
                                snapshot,
                                key,
                                "os",
                                os_store=os_store,
                            )
                    _require_persistent_container_storage(root)
                    quarantine = _quarantine_artifacts(
                        root,
                        [root / sk._KEY_FILE, root / sk._DATA_FILE],
                        finding_codes=("FILE_STORE_CORRUPT",),
                    )
                    if quarantine is not None:
                        quarantine_paths.append(quarantine)
                    _initialize_clean_file_store(root)
                    file_store = sk.FileKeystore(root)
                    registry = _load_authority_registry_readonly(root)
                    file_keys = tuple(
                        key
                        for key, authority in (registry.entries if registry else {}).items()
                        if authority is SecretAuthority.FILE
                    )
                    if action.code == "REBUILD_FILE_STORE":
                        recovered = {
                            key: snapshot.os_values[key]
                            for key in file_keys
                            if snapshot.os_values.get(key) is not None
                        }
                        if len(recovered) != len(file_keys):
                            raise sk.KeystoreError("healthy tier no longer covers file keys")
                        file_store._set_many_unlocked(recovered)
                        for key, value in recovered.items():
                            if file_store._get_unlocked(key) != value:
                                raise sk.KeystoreError(
                                    "rebuilt file-store verification failed"
                                )
                        for key in recovered:
                            os_store._delete_unlocked(key)
                            if os_store.get(key) is not None:
                                raise sk.KeystoreError(
                                    "healthy-tier cleanup verification failed"
                                )
                    else:
                        sk._write_authority_registry(
                            root,
                            _registry_with(
                                registry,
                                {key: SecretAuthority.CLEARED for key in file_keys},
                            ),
                        )
                else:
                    raise sk.KeystoreError(f"unknown repair action {action.code}")
                applied.append(action)
            except RepairRefusedError:
                raise
            except Exception as exc:
                failure = _finding(
                    "REPAIR_FAILED",
                    "error",
                    action.key,
                    f"{action.code} failed ({type(exc).__name__})",
                )
                return RepairReport(
                    applied=tuple(sorted(applied, key=lambda item: (item.code, item.key or ""))),
                    quarantine_paths=tuple(quarantine_paths),
                    failed=(failure,),
                )

    return RepairReport(
        applied=tuple(sorted(applied, key=lambda item: (item.code, item.key or ""))),
        quarantine_paths=tuple(quarantine_paths),
        failed=(),
    )


def _print_finding(finding: SecretFinding, *, stream=None) -> None:
    stream = stream or sys.stdout
    logical_key = f" {finding.key}" if finding.key is not None else ""
    print(f"[{finding.code}]{logical_key}: {finding.message}", file=stream)


def _handle_secrets_doctor(_args) -> int:
    report = diagnose_secrets()
    print(f"Configured secret storage mode: {report.configured_mode}")
    for key, authority in sorted(report.authorities.items()):
        print(f"authority {key}: {authority}")
    if not report.findings:
        print("No secret storage findings.")
    for finding in report.findings:
        _print_finding(finding)
    return 1 if any(item.severity == "error" for item in report.findings) else 0


def _handle_secrets_repair(args) -> int:
    apply = bool(getattr(args, "apply", False))
    reset = bool(getattr(args, "reset_unrecoverable", False))
    yes = bool(getattr(args, "yes", False))
    if yes and not (reset and apply):
        print(
            "Error: --yes requires --reset-unrecoverable --apply",
            file=sys.stderr,
        )
        return 2
    plan = plan_secret_repair(
        move_to=getattr(args, "move_to", None),
        reset_unrecoverable=reset,
    )
    for action in plan.actions:
        key = f" {action.key}" if action.key else ""
        route = ""
        if action.source or action.destination:
            route = f" ({action.source or '-'} -> {action.destination or '-'})"
        print(f"[{action.code}]{key}{route}")
    for finding in plan.blocked_findings:
        _print_finding(finding)
    if plan.blocked_findings:
        return 1
    if not apply:
        if not plan.actions:
            print("No repair actions required.")
        return 0

    confirm_reset = False
    if reset:
        if yes:
            confirm_reset = True
        elif not sys.stdin.isatty():
            print(
                "Error: noninteractive reset requires --yes",
                file=sys.stderr,
            )
            return 2
        else:
            answer = input("Reset unrecoverable secret storage? [y/N] ")
            confirm_reset = answer.strip().lower() in {"y", "yes"}
            if not confirm_reset:
                print("Repair cancelled.")
                return 1
    try:
        report = apply_secret_repair(plan, confirm_reset=confirm_reset)
    except RepairRefusedError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for action in report.applied:
        key = f" {action.key}" if action.key else ""
        print(f"applied [{action.code}]{key}")
    for path in report.quarantine_paths:
        print(f"quarantine: {path}")
    for finding in report.failed:
        _print_finding(finding, stream=sys.stderr)
    return 1 if report.failed else 0


def register_cli(subparsers) -> None:
    """Register doctor and repair under an existing secrets subparser set."""
    doctor = subparsers.add_parser(
        "doctor", help="Inspect secret storage without making changes"
    )
    doctor.set_defaults(func=_handle_secrets_doctor)
    repair = subparsers.add_parser(
        "repair",
        help="Plan or explicitly apply secret-storage repairs",
        allow_abbrev=False,
    )
    repair.add_argument("--apply", action="store_true", help="Apply the printed plan")
    repair.add_argument("--move-to", choices=("os", "file"))
    repair.add_argument("--reset-unrecoverable", action="store_true")
    repair.add_argument("--yes", action="store_true")
    repair.set_defaults(func=_handle_secrets_repair)


def parse_readonly_cli(argv: list[str]):
    """Parse the dependency-light early doctor/default-repair command graph."""
    parser = argparse.ArgumentParser(prog="hermes")
    commands = parser.add_subparsers(dest="command", required=True)
    secrets_parser = commands.add_parser("secrets")
    register_cli(secrets_parser.add_subparsers(dest="secrets_command", required=True))
    return parser.parse_args(argv)


__all__ = [
    "DoctorReport",
    "RepairAction",
    "RepairPlan",
    "RepairRefusedError",
    "RepairReport",
    "SecretFinding",
    "apply_secret_repair",
    "diagnose_secrets",
    "plan_secret_repair",
    "register_cli",
]
