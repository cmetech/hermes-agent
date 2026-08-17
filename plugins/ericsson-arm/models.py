"""Stable, redacted error and identity types for the Artifactory connector."""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass
import re
from typing import Any

SAFE_ERROR_MESSAGES = {
    "invalid_configuration": "Artifactory configuration is invalid",
    "invalid_input": "Artifactory request input is invalid",
    "authentication": "Artifactory authentication failed",
    "permission": "Artifactory permission denied",
    "not_found": "Artifactory content was not found",
    "conflict": "Artifactory content changed since it was read",
    "rate_limited": "Artifactory rate limit was reached",
    "transient": "Artifactory service is temporarily unavailable",
    "write_ambiguous": "Artifactory write outcome is unknown",
    "invalid_remote_data": "Artifactory returned invalid data",
    "cancelled": "Artifactory request was cancelled",
    "deadline": "Artifactory request deadline was exceeded",
    "capacity": "Artifactory result exceeded a safe limit",
    "circuit_open": "Artifactory calls are paused after repeated failures",
    "confirmation_required": "Artifactory change needs explicit confirmation",
    # Distinct from ``authentication``: this is the mTLS certificate at
    # Cloudflare Access, not the Artifactory token at the origin.
    "edge_authentication": (
        "Artifactory edge access was refused before the request reached "
        "Artifactory"
    ),
    "certificate_invalid": (
        "Artifactory client certificate is missing, expired, or unreadable"
    ),
}

# Exception-derived remediation is an output boundary. Only a connector-owned
# literal can cross it; remote text and token-bearing values are always dropped.
_SAFE_REMEDIATIONS = frozenset({
    "Update the Artifactory token.",
    (
        "The arm token is missing, expired, or invalid. Update the arm "
        "personal access token in the connector's configuration."
    ),
    (
        "The arm token is valid but lacks permission for this resource. "
        "Check the token's scopes, or that your account can see the project "
        "or space."
    ),
    (
        "The arm resource does not exist, or the token cannot see it. Verify "
        "the identifier, then verify the token's access."
    ),
    (
        "The arm connector configuration is invalid. Re-check the base URL "
        "and authentication mode."
    ),
    (
        "arm is rate limiting this client. It will retry automatically; if it "
        "persists, reduce how often this tool is called."
    ),
    (
        "Repeated failures against arm have tripped this connector's circuit "
        "breaker, so further calls are being refused locally. Check whether "
        "arm is reachable and healthy, then retry."
    ),
    (
        "Access to this Artifactory was refused at the edge, before the request "
        "reached Artifactory. This is normally an expired or missing mTLS client "
        "certificate rather than a problem with the Artifactory token. Check the "
        "client certificate and key configured for this profile."
    ),
    (
        "Artifactory redirected the request instead of answering it. Check that "
        "the base URL names the Artifactory origin."
    ),
    (
        "Artifactory returned HTML where JSON was expected, which normally means "
        "an authentication interstitial answered instead of the API."
    ),
    (
        "Do not put .limit() in the query; AQL accepts only one and the "
        "connector supplies it. Use max_results instead."
    ),
    "source_file must be an absolute path.",
    "source_file does not name a readable file.",
    "This profile confines uploads to its configured deploy source root.",
    (
        "The file is larger than this profile's maximum upload size. Raise it "
        "in the profile if this is expected."
    ),
    "Artifactory did not return a deploy result.",
    "Artifactory returned no checksums to verify against.",
    (
        "The sha256 checksum Artifactory reported does not match the file that "
        "was sent. Do not treat this artefact as published."
    ),
})
_CERTIFICATE_EXPIRY_TEMPLATE = (
    "The client certificate expired on {expired_on}. Renew it and update "
    "the certificate and key paths in this profile. Until then every "
    "request is refused at the edge before it reaches Artifactory."
)
_CERTIFICATE_EXPIRY_REMEDIATION = re.compile(
    r"\AThe client certificate expired on (?P<expired_on>\d{4}-\d{2}-\d{2})\. "
    r"Renew it and update the certificate and key paths in this profile\. "
    r"Until then every request is refused at the edge before it reaches "
    r"Artifactory\.\Z"
)


def certificate_expiry_remediation(expired_on: object) -> str | None:
    """Return the only date-bearing remediation allowed across this boundary."""
    if type(expired_on) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expired_on):
        return None
    try:
        date.fromisoformat(expired_on)
    except ValueError:
        return None
    return _CERTIFICATE_EXPIRY_TEMPLATE.format(expired_on=expired_on)


def safe_remediation(value: object) -> str | None:
    """Return only connector-owned remediation guidance."""
    if type(value) is not str:
        return None
    if value in _SAFE_REMEDIATIONS:
        return value
    match = _CERTIFICATE_EXPIRY_REMEDIATION.fullmatch(value)
    if match is None:
        return None
    return certificate_expiry_remediation(match.group("expired_on"))


class ArmError(RuntimeError):
    """Stable classified failure that never includes remote or secret text."""

    def __init__(self, category: object, *, remediation: object = None) -> None:
        self.category = (
            category
            if type(category) is str and category in SAFE_ERROR_MESSAGES
            else "transient"
        )
        self.remediation = safe_remediation(remediation)
        super().__init__(SAFE_ERROR_MESSAGES[self.category])


@dataclass(frozen=True, slots=True)
class ArmAuth:
    origin: str
    # Every path in this connector lives under /artifactory/. Xray would add
    # a second root and is deliberately out of this cut.
    api_root: str
    auth_header_name: str
    auth_header_value: str
    # Retained separately so later redaction can strip both the bare token and
    # its complete Authorization header value.
    token: str
    # ssl.SSLContext or None. Kept loose so this identity module imports no ssl.
    tls_context: Any
    # Unix seconds from the client certificate's notAfter, or None when absent.
    certificate_not_after: float | None
    request_timeout_seconds: int
    default_max_results: int
    max_deploy_bytes: int
    deploy_root: str | None
