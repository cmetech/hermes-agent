"""Memory-only, bounded request receipts for one canonical profile home.

Receipts survive result eviction for 24 hours from the request's issued time,
and longer while active. No unexpired receipt is evicted for capacity. The
process epoch/secret outlive disposable profile services; no retired-home map
is retained. Callers share ``lock`` with the registry's admission boundary.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
from typing import Literal

from pydantic import TypeAdapter

from hermes_constants import hermes_home_key
from .lifecycle_models import (
    LifecycleSubject,
    SUBJECT_TYPES_BY_KIND,
    TrustSelection,
)

_PROCESS_EPOCH = secrets.token_hex(16)
_PROCESS_SECRET = secrets.token_bytes(32)
_REQUEST_ID = re.compile(r"wmreq_([0-9a-f]{32})_([0-9]{13})_([0-9a-f]{32})", re.ASCII)
_SUBJECT = TypeAdapter(LifecycleSubject)
_SELECTION = TypeAdapter(TrustSelection | None)
_BODY_BYTES_MAX = 64 * 1024
_RECEIPTS_MAX = 4096


class MarketplaceOperationRegistryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LifecycleAdmissionError(MarketplaceOperationRegistryError):
    """Safe admission rejection; never contains submitted bytes or fingerprints."""


def _reject(code: str) -> None:
    raise LifecycleAdmissionError(code, "Workflow marketplace admission unavailable.")


def canonical_profile_key(value: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value or len(value) > 4096:
        raise ValueError("operation profile key is invalid")
    return hermes_home_key(Path(value).expanduser().resolve(strict=False))


def _body_bytes(body: object) -> bytes:
    # Shape validation precedes this boundary. Still reject Python-only values
    # rather than silently changing them while calculating request identity.
    def check(value, depth=0):
        if depth > 32:
            raise ValueError("request body is invalid")
        if type(value) is dict:
            if len(value) > _BODY_BYTES_MAX or any(
                type(key) is not str for key in value
            ):
                raise ValueError("request body is invalid")
            for child in value.values():
                check(child, depth + 1)
        elif type(value) is list:
            if len(value) > _BODY_BYTES_MAX:
                raise ValueError("request body is invalid")
            for child in value:
                check(child, depth + 1)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("request body is invalid")

    try:
        check(body)
        encoded = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        if len(encoded) > _BODY_BYTES_MAX:
            raise ValueError("request body is invalid")
        return encoded
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("request body is invalid") from None


@dataclass(slots=True)
class AdmissionReceipt:
    request_id: str
    operation_id: str
    kind: str
    subject: LifecycleSubject
    selection: TrustSelection | None
    issued_at: datetime
    active: bool = True
    _fingerprint: bytes = field(default=b"", repr=False)
    _secret: bytes = field(default=b"", repr=False)

    def require_same_request(self, kind, canonical_body, subject, selection):
        # Comparison deliberately precedes kind/selection compatibility checks:
        # changed requests receive one safe conflict without original metadata.
        candidate = _fingerprint(self._secret, kind, canonical_body, subject, selection)
        if not hmac.compare_digest(candidate, self._fingerprint):
            _reject("marketplace_request_conflict")


def _fingerprint(secret, kind, body, subject, selection):
    subject = _SUBJECT.validate_python(subject)
    selection = _SELECTION.validate_python(selection)
    header = json.dumps(
        {
            "kind": kind,
            "subject": subject.model_dump(mode="json"),
            "selection": selection.model_dump(mode="json") if selection else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hmac.digest(secret, header + b"\0" + _body_bytes(body), "sha256")


@dataclass(frozen=True, slots=True)
class AdmissionReservation:
    state: Literal["new", "found"]
    receipt: AdmissionReceipt = field(repr=False)


class LifecycleAdmissionStore:
    def __init__(
        self,
        *,
        profile_key: str,
        epoch: str = _PROCESS_EPOCH,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        random_hex: Callable[[], str] = lambda: secrets.token_hex(16),
    ):
        if not isinstance(epoch, str) or re.fullmatch(r"[0-9a-f]{32}", epoch) is None:
            raise ValueError("registry epoch is invalid")
        self.profile_key = canonical_profile_key(profile_key)
        self.epoch = epoch
        self.clock = clock
        self.random_hex = random_hex
        self.lock = threading.RLock()
        self._receipts: dict[tuple[str, str], AdmissionReceipt] = {}

    def _now(self):
        return self.clock().astimezone(timezone.utc)

    def _random(self):
        value = self.random_hex()
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
            raise ValueError("operation random value is invalid")
        return value

    def new_request_id(self):
        return f"wmreq_{self.epoch}_{int(self._now().timestamp() * 1000):013d}_{self._random()}"

    def direct_selector_id(
        self, repository_url: str, ref: str | None, package_path: str | None
    ) -> str:
        """Bind an already validated canonical selector without exposing its bytes."""
        value = json.dumps(
            [self.epoch, repository_url, ref, package_path],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hmac.new(
            _PROCESS_SECRET,
            b"hermes.marketplace.direct-selector.v2\0" + value,
            hashlib.sha256,
        ).hexdigest()

    def _issued(self, request_id):
        match = (
            _REQUEST_ID.fullmatch(request_id) if isinstance(request_id, str) else None
        )
        if match is None:
            raise ValueError("request ID is invalid")
        if match[1] != self.epoch:
            _reject("marketplace_epoch_changed")
        return datetime.fromtimestamp(int(match[2]) / 1000, timezone.utc)

    def require_new_request_window(self, request_id):
        issued = self._issued(request_id)
        age = self._now() - issued
        if age > timedelta(minutes=5) or age < -timedelta(seconds=30):
            _reject("marketplace_request_expired")
        return issued

    def prune(self):
        with self.lock:
            now = self._now()
            for key, receipt in tuple(self._receipts.items()):
                if not receipt.active and now >= receipt.issued_at + timedelta(
                    hours=24
                ):
                    del self._receipts[key]

    def lookup(self, actor, profile_key, request_id):
        with self.lock:
            self._issued(request_id)
            self.prune()
            if canonical_profile_key(profile_key) != self.profile_key:
                return None
            return self._receipts.get((actor, request_id))

    def reserve(
        self, request_id, actor, profile_key, kind, canonical_body, subject, selection
    ):
        with self.lock:
            if (
                not isinstance(actor, str)
                or not actor
                or len(actor) > 256
                or actor != actor.strip()
                or "\0" in actor
            ):
                raise ValueError("operation actor is invalid")
            if canonical_profile_key(profile_key) != self.profile_key:
                raise ValueError("operation profile key is inconsistent")
            existing = self.lookup(actor, profile_key, request_id)
            if existing is not None:
                existing.require_same_request(kind, canonical_body, subject, selection)
                return AdmissionReservation("found", existing)
            issued = self.require_new_request_window(request_id)
            if len(self._receipts) >= _RECEIPTS_MAX:
                _reject("marketplace_admission_capacity")
            subject = _SUBJECT.validate_python(subject)
            selection = _SELECTION.validate_python(selection)
            if subject.type not in SUBJECT_TYPES_BY_KIND.get(kind, ()):
                raise ValueError("operation kind/subject mismatch")
            if (kind in {"trust_prepare", "trust_confirm", "trust_revoke"}) != (
                selection is not None
            ):
                raise ValueError("operation kind/selection mismatch")
            fingerprint = _fingerprint(
                _PROCESS_SECRET, kind, canonical_body, subject, selection
            )
            profile_digest = hashlib.sha256(self.profile_key.encode()).hexdigest()[:12]
            receipt = AdmissionReceipt(
                request_id,
                f"wmop_{profile_digest}_{self._random()}",
                kind,
                subject.model_copy(deep=True),
                selection.model_copy(deep=True) if selection else None,
                issued,
                _fingerprint=fingerprint,
                _secret=_PROCESS_SECRET,
            )
            self._receipts[(actor, request_id)] = receipt
            return AdmissionReservation("new", receipt)

    def finish(self, receipt):
        with self.lock:
            receipt.active = False

    def has_live_receipts(self):
        with self.lock:
            self.prune()
            return bool(self._receipts)
