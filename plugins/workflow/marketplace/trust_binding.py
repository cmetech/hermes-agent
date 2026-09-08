"""Exact distribution identity binding for marketplace workflow trust."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from plugins.workflow.models import WorkflowPackage
from plugins.workflow.trust import (
    WorkflowPackageDigest,
    WorkflowResourceReadBudget,
)

from .package import load_distribution


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_WORKFLOW_RELATIVE_PATH_CHARS = 4096
_DOMAIN = b"hermes-workflow-marketplace-v1\0"


def effective_marketplace_digest(
    *,
    distribution_digest: str,
    workflow_relative_path: str,
    closure_digest: str,
) -> str:
    """Bind one verified distribution, workflow member, and executable closure."""
    if (
        not isinstance(distribution_digest, str)
        or _SHA256.fullmatch(distribution_digest) is None
        or not isinstance(closure_digest, str)
        or _SHA256.fullmatch(closure_digest) is None
    ):
        raise ValueError("marketplace trust digests must be lowercase SHA-256")
    if (
        not isinstance(workflow_relative_path, str)
        or not workflow_relative_path
        or len(workflow_relative_path) > _MAX_WORKFLOW_RELATIVE_PATH_CHARS
        or "\\" in workflow_relative_path
        or "\0" in workflow_relative_path
        or workflow_relative_path.startswith("~")
    ):
        raise ValueError("marketplace workflow path must be canonical and bounded")
    logical_path = PurePosixPath(workflow_relative_path)
    if (
        logical_path.is_absolute()
        or any(part in {"", ".", ".."} for part in logical_path.parts)
        or logical_path.as_posix() != workflow_relative_path
    ):
        raise ValueError("marketplace workflow path must be canonical and contained")
    canonical = (
        _DOMAIN
        + distribution_digest.encode("ascii")
        + b"\0"
        + workflow_relative_path.encode("utf-8")
        + b"\0"
        + closure_digest.encode("ascii")
    )
    return hashlib.sha256(canonical).hexdigest()


def bind_marketplace_package_digest(
    package: WorkflowPackage,
    base: WorkflowPackageDigest,
    *,
    read_budget: WorkflowResourceReadBudget | None,
) -> WorkflowPackageDigest:
    """Authenticate all package-owned bytes and return the effective trust key."""
    binding = package.marketplace_binding
    if binding is None:
        return base
    distribution = load_distribution(
        package.root,
        expected_digest=binding.distribution_digest,
        read_budget=read_budget,
    )
    return WorkflowPackageDigest(
        effective_marketplace_digest(
            distribution_digest=distribution.digest,
            workflow_relative_path=binding.workflow_relative_path,
            closure_digest=base.sha256,
        ),
        tuple(
            sorted(set(base.covered_relative_paths).union(distribution.covered_paths))
        ),
    )


__all__ = [
    "bind_marketplace_package_digest",
    "effective_marketplace_digest",
]
