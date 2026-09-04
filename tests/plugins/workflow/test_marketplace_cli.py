from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json

import pytest

from plugins.workflow import machine_contract
from plugins.workflow.cli import register_cli
from plugins.workflow.marketplace.catalog import CatalogPackage, SourceRefreshResult
from plugins.workflow.marketplace.cli import configure_marketplace_parsers
from plugins.workflow.marketplace.models import (
    ExternalRequirements,
    FileDigestChange,
    InstallRequest,
    InstalledPackage,
    InstalledPackageIdentity,
    InstallReview,
    PackageReviewAssessment,
    PackageDiagnostic,
    PackageInspection,
    PackageInspectionResource,
    RemoveReview,
    RequirementChanges,
    StringSetChange,
    TrustReview,
    UpdateCheck,
    UpdateReview,
    WorkflowCompatibilityChanges,
    WorkflowCompatibilityIdentity,
    WorkflowMarketplaceSource,
    WorkflowRiskChanges,
    WorkflowRiskIdentity,
    WorkflowTrustReviewItem,
)
from plugins.workflow.marketplace.package import WorkflowMarketplaceError


_DIGEST = "1" * 64
_RISK_DIGEST = "2" * 64
_REVIEW_DIGEST = "3" * 64
_COMMIT = "4" * 40
_TOKEN = "confirmation-token-value-1234567890"
_NOW = datetime(2026, 9, 4, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    register_cli(parser)
    return parser


def _identity(
    source_key: str = "company", package_id: str = "laptop-support"
) -> InstalledPackageIdentity:
    return InstalledPackageIdentity(sourceKey=source_key, packageId=package_id)


def _requirements() -> ExternalRequirements:
    return ExternalRequirements(
        runtimes=[], tools=[], providers=[], services=[], secrets=[]
    )


def _workflow_review() -> WorkflowTrustReviewItem:
    return WorkflowTrustReviewItem(
        workflowName="laptop-diagnostic",
        definitionPath="workflows/laptop-diagnostic.yaml",
        packageDigest=_DIGEST,
        riskDigest=_RISK_DIGEST,
        trustState="untrusted",
        shellOrScriptNodes=["collect"],
        commandNodes=[],
        approvalNodes=[],
        commandResources=[],
        scriptResources=["scripts/collect.py"],
        mcpResources=[],
        requestedTools=[],
        requestedSkills=[],
        localMcpServers=[],
        remoteMcpServers=[],
        providers=[],
        outwardActionNodes=[],
        requiredSecrets=[],
        externalRequirements=_requirements(),
        packageResourceSet="package",
        compatibility=[],
    )


def _assessment() -> PackageReviewAssessment:
    return PackageReviewAssessment(
        packageDigest=_DIGEST,
        reviewDigest=_REVIEW_DIGEST,
        workflowNames=["laptop-diagnostic"],
        blockers=[],
        advisories=[],
        externalRequirements=_requirements(),
        packageResources=[
            "scripts/collect.py",
            "workflow-package.json",
            "workflows/laptop-diagnostic.yaml",
        ],
    )


def _install_review(
    *, identity: InstalledPackageIdentity | None = None
) -> InstallReview:
    identity = identity or _identity()
    return InstallReview(
        operation="install",
        confirmationToken=_TOKEN,
        reviewDigest=_REVIEW_DIGEST,
        identity=identity,
        sourceName=identity.source_key,
        repositoryUrl="ssh://git@example.test/team/workflows.git",
        configuredRef="main",
        resolvedCommit=_COMMIT,
        packagePath="packages/laptop-support",
        candidateVersion="1.0.0",
        candidateDigest=_DIGEST,
        assessment=_assessment(),
        fileChanges=[
            FileDigestChange(
                path="workflow-package.json",
                kind="added",
                candidateDigest=_DIGEST,
            )
        ],
        workflowReviews=[_workflow_review()],
    )


def _installed(
    *, identity: InstalledPackageIdentity | None = None, version: str = "1.0.0"
) -> InstalledPackage:
    identity = identity or _identity()
    return InstalledPackage(
        identity=identity,
        sourceName=identity.source_key,
        repositoryUrl="ssh://git@example.test/team/workflows.git",
        configuredRef="main",
        resolvedCommit=_COMMIT,
        packagePath=f"packages/{identity.package_id}",
        version=version,
        contractVersion=1,
        distributionDigest=_DIGEST,
        installedAt=_NOW,
        actor="cli",
        workflowPaths=["workflows/laptop-diagnostic.yaml"],
        orphanedSource=False,
    )


def _inspection(*, installed: bool = True) -> PackageInspection:
    installed_package = _installed() if installed else None
    return PackageInspection(
        identifier="company/laptop-support",
        identity=_identity(),
        sourceName="company",
        repositoryUrl="ssh://git@example.test/team/workflows.git",
        configuredRef="main",
        resolvedCommit=_COMMIT,
        verifiedAt=_NOW,
        verified=True,
        sourceState="fresh",
        id="laptop-support",
        version="1.0.0",
        displayName="Laptop Support",
        description="Diagnostic and repair workflows",
        license="MIT",
        publisher="Example Company",
        tags=["diagnostics", "support"],
        packagePath="packages/laptop-support",
        contractVersion=1,
        packageDigest=_DIGEST,
        workflows=[_rich_workflow_review()],
        resources=[
            PackageInspectionResource(path="commands/diagnose.md", types=["command"]),
            PackageInspectionResource(path="mcp/support.yaml", types=["mcp"]),
            PackageInspectionResource(path="scripts/collect.py", types=["script"]),
            PackageInspectionResource(path="workflow-package.json", types=["other"]),
            PackageInspectionResource(
                path="workflows/laptop-diagnostic.hermes.yaml",
                types=["workflow_companion"],
            ),
            PackageInspectionResource(
                path="workflows/laptop-diagnostic.yaml",
                types=["workflow_definition"],
            ),
        ],
        externalRequirements=_rich_requirements(),
        blockers=[],
        advisories=[
            PackageDiagnostic(
                code="missing_runtime",
                message="destination needs python",
                severity="advisory",
            )
        ],
        installStatus="installed" if installed else "not_installed",
        updateStatus="current" if installed else "not_applicable",
        installed=installed_package,
    )


def _empty_changes() -> StringSetChange:
    return StringSetChange(added=[], removed=[])


def _update_review(identity: InstalledPackageIdentity | None = None) -> UpdateReview:
    identity = identity or _identity()
    return UpdateReview(
        operation="update",
        result="update_available",
        confirmationToken=_TOKEN,
        reviewDigest=_REVIEW_DIGEST,
        identity=identity,
        sourceName=identity.source_key,
        repositoryUrl="ssh://git@example.test/team/workflows.git",
        configuredRef="main",
        oldVersion="1.0.0",
        candidateVersion="2.0.0",
        oldCommit="5" * 40,
        candidateCommit=_COMMIT,
        oldDigest="6" * 64,
        candidateDigest=_DIGEST,
        fileChanges=[
            FileDigestChange(
                path="scripts/collect.py",
                kind="modified",
                oldDigest="6" * 64,
                candidateDigest=_DIGEST,
            )
        ],
        workflowChanges=_empty_changes(),
        requirementChanges=RequirementChanges(
            runtimes=_empty_changes(),
            tools=_empty_changes(),
            providers=_empty_changes(),
            services=_empty_changes(),
            secrets=_empty_changes(),
        ),
        riskChanges=WorkflowRiskChanges(added=[], removed=[]),
        compatibilityChanges=WorkflowCompatibilityChanges(added=[], removed=[]),
        assessment=_assessment(),
        workflowReviews=[_workflow_review()],
    )


def _remove_review() -> RemoveReview:
    return RemoveReview(
        operation="remove",
        confirmationToken=_TOKEN,
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        currentVersion="1.0.0",
        currentCommit=_COMMIT,
        distributionDigest=_DIGEST,
        workflowNames=["laptop-diagnostic"],
    )


def _trust_review() -> TrustReview:
    return TrustReview(
        confirmationToken=_TOKEN,
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        sourceName="company",
        version="1.0.0",
        resolvedCommit=_COMMIT,
        distributionDigest=_DIGEST,
        packageResources=[
            "scripts/collect.py",
            "workflow-package.json",
            "workflows/laptop-diagnostic.yaml",
        ],
        workflows=[_workflow_review()],
    )


def _rich_requirements() -> ExternalRequirements:
    return ExternalRequirements(
        runtimes=["python"],
        tools=["git"],
        providers=["anthropic"],
        services=["ticketing"],
        secrets=["SUPPORT_TOKEN"],
    )


def _rich_workflow_review() -> WorkflowTrustReviewItem:
    return _workflow_review().model_copy(
        update={
            "companion_path": "workflows/laptop-diagnostic.hermes.yaml",
            "shell_or_script_nodes": ["collect"],
            "command_nodes": ["diagnose"],
            "approval_nodes": ["approve-fix"],
            "command_resources": ["commands/diagnose.md"],
            "script_resources": ["scripts/collect.py"],
            "mcp_resources": ["mcp/support.yaml"],
            "requested_tools": ["git"],
            "requested_skills": ["support-triage"],
            "local_mcp_servers": ["local-support"],
            "remote_mcp_servers": ["remote-support"],
            "providers": ["anthropic"],
            "outward_action_nodes": ["open-ticket"],
            "required_secrets": ["SUPPORT_TOKEN"],
            "external_requirements": _rich_requirements(),
            "compatibility": [
                PackageDiagnostic(
                    code="provider_authority_missing",
                    message="provider access must be configured",
                    severity="advisory",
                )
            ],
        }
    )


def _rich_assessment() -> PackageReviewAssessment:
    return _assessment().model_copy(
        update={
            "blockers": [
                PackageDiagnostic(
                    code="workflow_contract_blocked",
                    message="contract review blocker",
                    severity="blocker",
                )
            ],
            "advisories": [
                PackageDiagnostic(
                    code="missing_runtime",
                    message="destination needs python",
                    severity="advisory",
                )
            ],
            "external_requirements": _rich_requirements(),
            "package_resources": [
                "commands/diagnose.md",
                "mcp/support.yaml",
                "scripts/collect.py",
                "workflow-package.json",
                "workflows/laptop-diagnostic.hermes.yaml",
                "workflows/laptop-diagnostic.yaml",
            ],
        }
    )


def _rich_install_review() -> InstallReview:
    return _install_review().model_copy(
        update={
            "assessment": _rich_assessment(),
            "workflow_reviews": [_rich_workflow_review()],
            "file_changes": [
                FileDigestChange(
                    path="workflow-package.json",
                    kind="added",
                    candidateDigest=_DIGEST,
                )
            ],
        }
    )


def _rich_update_review() -> UpdateReview:
    return _update_review().model_copy(
        update={
            "file_changes": [
                FileDigestChange(
                    path="added.txt", kind="added", candidateDigest="7" * 64
                ),
                FileDigestChange(
                    path="modified.txt",
                    kind="modified",
                    oldDigest="8" * 64,
                    candidateDigest="9" * 64,
                ),
                FileDigestChange(
                    path="removed.txt", kind="removed", oldDigest="a" * 64
                ),
                FileDigestChange(
                    path="renamed-new.txt",
                    kind="renamed",
                    oldPath="renamed-old.txt",
                    oldDigest="b" * 64,
                    candidateDigest="b" * 64,
                ),
            ],
            "workflow_changes": StringSetChange(
                added=["new-workflow"], removed=["old-workflow"]
            ),
            "requirement_changes": RequirementChanges(
                runtimes=StringSetChange(added=["python"], removed=["node"]),
                tools=StringSetChange(added=["git"], removed=[]),
                providers=StringSetChange(added=["anthropic"], removed=[]),
                services=StringSetChange(added=["ticketing"], removed=[]),
                secrets=StringSetChange(added=["SUPPORT_TOKEN"], removed=[]),
            ),
            "risk_changes": WorkflowRiskChanges(
                added=[
                    WorkflowRiskIdentity(
                        workflowName="new-workflow",
                        packageDigest="c" * 64,
                        riskDigest="d" * 64,
                    )
                ],
                removed=[
                    WorkflowRiskIdentity(
                        workflowName="old-workflow",
                        packageDigest="e" * 64,
                        riskDigest="f" * 64,
                    )
                ],
            ),
            "compatibility_changes": WorkflowCompatibilityChanges(
                added=[
                    WorkflowCompatibilityIdentity(
                        workflowName="new-workflow",
                        code="missing_provider",
                        severity="advisory",
                    )
                ],
                removed=[
                    WorkflowCompatibilityIdentity(
                        workflowName="old-workflow",
                        code="old_blocker",
                        severity="blocker",
                    )
                ],
            ),
            "assessment": _rich_assessment(),
            "workflow_reviews": [_rich_workflow_review()],
        }
    )


def _rich_trust_review() -> TrustReview:
    return _trust_review().model_copy(
        update={
            "package_resources": _rich_assessment().package_resources,
            "workflows": [_rich_workflow_review()],
        }
    )


class FakeMarketplaceService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.failures: dict[tuple[str, str], WorkflowMarketplaceError] = {}
        self.sources = [
            WorkflowMarketplaceSource(
                name="zulu",
                repositoryUrl="https://example.test/zulu.git",
                ref=None,
            ),
            WorkflowMarketplaceSource(
                name="company",
                repositoryUrl="ssh://git@example.test/team/workflows.git",
                ref="main",
            ),
        ]
        self.installed = [
            _installed(identity=_identity("zulu", "second")),
            _installed(),
        ]

    def _record(self, operation: str, *values: object) -> None:
        self.calls.append((operation, *values))
        key = (operation, str(values[0]) if values else "")
        if key in self.failures:
            raise self.failures[key]

    def add_source(self, source: WorkflowMarketplaceSource):
        self._record("add_source", source)
        return source

    def list_sources(self):
        self._record("list_sources")
        return tuple(self.sources)

    def refresh_source(self, name: str):
        self._record("refresh_source", name)
        return SourceRefreshResult(
            source_name=name,
            repository_url="https://example.test/workflows.git",
            state="fresh",
            resolved_commit=_COMMIT,
            verified_at=_NOW,
            package_count=2,
        )

    def remove_source(self, name: str):
        self._record("remove_source", name)
        return next(item for item in self.sources if item.name == name)

    def search(self, query: str, *, source: str | None = None, limit: int = 100):
        self._record("search", query, source, limit)
        return (
            CatalogPackage(
                identifier="company/laptop-support",
                source_name="company",
                repository_url="ssh://git@example.test/team/workflows.git",
                configured_ref="main",
                resolved_commit=_COMMIT,
                verified_at=_NOW,
                state="fresh",
                id="laptop-support",
                version="1.0.0",
                display_name="Laptop Support",
                description="Support workflows",
                license="MIT",
                publisher="Example",
                tags=("support",),
                package_path="packages/laptop-support",
                contract_version=1,
                package_digest=_DIGEST,
            ),
        )

    def inspect(self, identifier: str):
        self._record("inspect", identifier)
        return _inspection()

    def installed_packages(self):
        self._record("installed_packages")
        return tuple(self.installed)

    def check_updates(self, identity: InstalledPackageIdentity | None = None):
        self._record("check_updates", identity)
        identities = (
            [identity]
            if identity is not None
            else [item.identity for item in self.installed]
        )
        return tuple(
            UpdateCheck(
                identity=item,
                status="update_available",
                installedVersion="1.0.0",
                candidateVersion="2.0.0",
            )
            for item in identities
        )

    def prepare_install(self, request: InstallRequest, *, actor: str):
        self._record("prepare_install", request, actor)
        return _install_review()

    def confirm_install(self, token: str, *, actor: str):
        self._record("confirm_install", token, actor)
        return _installed()

    def prepare_update(self, identity: InstalledPackageIdentity, *, actor: str):
        self._record("prepare_update", identity, actor)
        return _update_review(identity)

    def confirm_update(self, token: str, *, actor: str):
        self._record("confirm_update", token, actor)
        return _installed(version="2.0.0")

    def prepare_remove(self, identity: InstalledPackageIdentity, *, actor: str):
        self._record("prepare_remove", identity, actor)
        return _remove_review()

    def confirm_remove(self, token: str, *, actor: str):
        self._record("confirm_remove", token, actor)
        return _installed()

    def review_trust(
        self,
        identity: InstalledPackageIdentity,
        *,
        actor: str,
        workflow_name: str | None = None,
    ):
        self._record("review_trust", identity, actor, workflow_name)
        return _trust_review()

    def grant_trust(self, token: str, *, actor: str):
        self._record("grant_trust", token, actor)
        return {"laptop-diagnostic": "trusted"}

    def revoke_trust(
        self,
        identity: InstalledPackageIdentity,
        *,
        workflow_name: str | None = None,
    ):
        self._record("revoke_trust", identity, workflow_name)
        return 1

    def workflow_trust(self, identity: InstalledPackageIdentity):
        self._record("workflow_trust", identity)
        return {"laptop-diagnostic": "untrusted"}


@pytest.fixture
def service(monkeypatch) -> FakeMarketplaceService:
    instance = FakeMarketplaceService()
    monkeypatch.setattr(
        "plugins.workflow.marketplace.cli._service_for_args", lambda _args: instance
    )
    return instance


def _run(parser, capsys, *arguments: str):
    args = parser.parse_args(list(arguments))
    code = args.func(args)
    captured = capsys.readouterr()
    machine_output = captured.out.lstrip().startswith("{")
    return code, captured, json.loads(captured.out) if machine_output else None


@pytest.mark.parametrize(
    ("argv", "attributes"),
    [
        (
            ["source", "add", "company", "git@example:team/repo.git", "--ref", "main"],
            {"workflow_action": "source", "source_action": "add", "name": "company"},
        ),
        (["source", "list"], {"source_action": "list"}),
        (["source", "refresh"], {"source_action": "refresh", "name": None}),
        (["source", "remove", "company"], {"source_action": "remove"}),
        (["search", "laptop", "--source", "company"], {"query": "laptop"}),
        (["inspect", "company/laptop-support"], {"workflow_action": "inspect"}),
        (
            ["install", "company/laptop-support", "--prepare-only"],
            {"prepare_only": True},
        ),
        (["installed"], {"workflow_action": "installed"}),
        (["check", "company/laptop-support"], {"workflow_action": "check"}),
        (["update", "company/laptop-support", "--yes"], {"yes": True}),
        (["update", "--all", "--prepare-only"], {"all": True}),
        (["uninstall", "company/laptop-support", "--yes"], {"yes": True}),
        (
            [
                "trust",
                "company/laptop-support",
                "--workflow",
                "laptop-diagnostic",
                "--yes",
            ],
            {"workflow": "laptop-diagnostic", "yes": True},
        ),
        (
            ["untrust", "company/laptop-support", "--workflow", "laptop-diagnostic"],
            {"workflow": "laptop-diagnostic"},
        ),
    ],
)
def test_marketplace_parser_contract(argv, attributes) -> None:
    args = _parser().parse_args(argv)
    for name, expected in attributes.items():
        assert getattr(args, name) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["source", "--help"],
        ["source", "add", "--help"],
        ["source", "list", "--help"],
        ["source", "refresh", "--help"],
        ["source", "remove", "--help"],
        ["search", "--help"],
        ["inspect", "--help"],
        ["install", "--help"],
        ["installed", "--help"],
        ["check", "--help"],
        ["update", "--help"],
        ["uninstall", "--help"],
        ["trust", "--help"],
        ["untrust", "--help"],
    ],
)
def test_every_marketplace_command_has_argparse_help(argv, capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        _parser().parse_args(argv)

    assert exited.value.code == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.startswith("usage:")


def test_configure_marketplace_parsers_is_reusable() -> None:
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="workflow_action")
    configure_marketplace_parsers(actions)

    args = parser.parse_args(["search", "support", "--json"])

    assert args.workflow_action == "search"
    assert args.query == "support"
    assert args.json is True


@pytest.mark.parametrize(
    "argv",
    [
        ["install", "company/laptop-support", "--yes", "--prepare-only", "--json"],
        [
            "uninstall",
            "company/laptop-support",
            "--yes",
            "--confirmation-token",
            _TOKEN,
            "--json",
        ],
        ["trust", "company/laptop-support", "--yes", "--prepare-only", "--json"],
    ],
)
def test_confirmation_modes_are_mutually_exclusive_at_parse_time(argv, service) -> None:
    with pytest.raises(SystemExit) as exited:
        _parser().parse_args(argv)

    assert exited.value.code == machine_contract.EXIT_INVOCATION
    assert service.calls == []


def test_install_prepare_only_emits_review_token_without_confirming(
    service, capsys
) -> None:
    code, captured, envelope = _run(
        _parser(),
        capsys,
        "install",
        "company/laptop-support",
        "--prepare-only",
        "--json",
    )

    assert code == 0
    assert captured.err == ""
    assert envelope["ok"] is True
    assert envelope["command"] == "workflow install"
    assert envelope["result"]["status"] == "review_required"
    assert envelope["result"]["confirmation_token"] == _TOKEN
    assert [call[0] for call in service.calls] == ["prepare_install"]


def test_install_yes_prepares_then_confirms_once_and_never_trusts(
    service, capsys
) -> None:
    code, _, envelope = _run(
        _parser(),
        capsys,
        "install",
        "company/laptop-support",
        "--yes",
        "--json",
    )

    assert code == 0
    assert envelope["result"]["status"] == "installed"
    assert envelope["result"]["trust_required"] is True
    assert envelope["result"]["workflow_trust"] == {"laptop-diagnostic": "untrusted"}
    assert [call[0] for call in service.calls] == [
        "prepare_install",
        "confirm_install",
        "workflow_trust",
    ]
    assert "grant_trust" not in [call[0] for call in service.calls]
    assert _TOKEN not in json.dumps(envelope["result"]["package"])


def test_install_confirmation_token_confirms_without_refetching(
    service, capsys
) -> None:
    code, _, envelope = _run(
        _parser(),
        capsys,
        "install",
        "company/laptop-support",
        "--confirmation-token",
        _TOKEN,
        "--json",
    )

    assert code == 0
    assert envelope["result"]["status"] == "installed"
    assert [call[0] for call in service.calls] == [
        "confirm_install",
        "workflow_trust",
    ]


def test_human_prepare_and_token_confirmation_render_bounded_outcomes(
    service, capsys
) -> None:
    code, prepared, _ = _run(
        _parser(),
        capsys,
        "install",
        "company/laptop-support",
        "--prepare-only",
    )

    assert code == 0
    assert prepared.err == ""
    assert "Review required: install company/laptop-support" in prepared.out
    assert f"Confirmation token: {_TOKEN}" in prepared.out
    assert [call[0] for call in service.calls] == ["prepare_install"]

    service.calls.clear()
    code, confirmed, _ = _run(
        _parser(),
        capsys,
        "install",
        "company/laptop-support",
        "--confirmation-token",
        _TOKEN,
    )

    assert code == 0
    assert confirmed.err == ""
    assert "Installed" in confirmed.out
    assert "company/laptop-support" in confirmed.out
    assert "Trust required to run" in confirmed.out
    assert _TOKEN not in confirmed.out
    assert [call[0] for call in service.calls] == [
        "confirm_install",
        "workflow_trust",
    ]


def test_json_mutation_without_confirmation_fails_before_service_call(
    service, capsys
) -> None:
    code, captured, envelope = _run(
        _parser(), capsys, "install", "company/laptop-support", "--json"
    )

    assert code == machine_contract.EXIT_AUTHORIZATION
    assert captured.err == ""
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "confirmation_required"
    assert service.calls == []


def test_interactive_install_shows_review_before_real_confirmation(
    service, capsys, monkeypatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("plugins.workflow.marketplace.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        "plugins.workflow.marketplace.cli._read_confirmation",
        lambda prompt: prompts.append(prompt) or True,
    )

    code, captured, _ = _run(_parser(), capsys, "install", "company/laptop-support")

    assert code == 0
    assert "Review required" in captured.out
    assert "Installed" in captured.out
    assert "company/laptop-support" in captured.out
    assert "Trust required to run" in captured.out
    assert prompts == ["Confirm install? [y/N] "]
    assert [call[0] for call in service.calls] == [
        "prepare_install",
        "confirm_install",
        "workflow_trust",
    ]


def test_interactive_install_review_is_informed_and_decline_does_not_mutate(
    service, capsys, monkeypatch
) -> None:
    review = _rich_install_review()
    monkeypatch.setattr(
        service,
        "prepare_install",
        lambda request, *, actor: (
            service._record("prepare_install", request, actor) or review
        ),
    )
    shown = []
    monkeypatch.setattr("plugins.workflow.marketplace.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        "plugins.workflow.marketplace.cli._read_confirmation",
        lambda _prompt: shown.append(capsys.readouterr().out) or False,
    )

    code, _, _ = _run(_parser(), capsys, "install", "company/laptop-support")

    assert code == 0
    rendered = shown[0]
    for detail in (
        "Source: company",
        "Destination identity: company/laptop-support",
        "Candidate version: 1.0.0",
        f"Candidate commit: {_COMMIT}",
        f"Candidate digest: {_DIGEST}",
        "Package path: packages/laptop-support",
        "File changes (1)",
        "added: workflow-package.json",
        "Candidate workflow risks (1)",
        f"package={_DIGEST}",
        f"risk={_RISK_DIGEST}",
        "External requirements",
        "runtimes: python",
        "Blockers (1)",
        "workflow_contract_blocked",
        "Advisories (1)",
        "missing_runtime",
    ):
        assert detail in rendered
    assert [call[0] for call in service.calls] == ["prepare_install"]


def test_interactive_update_review_renders_every_change_and_decline_is_read_only(
    service, capsys, monkeypatch
) -> None:
    review = _rich_update_review()
    monkeypatch.setattr(
        service,
        "prepare_update",
        lambda identity, *, actor: (
            service._record("prepare_update", identity, actor) or review
        ),
    )
    shown = []
    monkeypatch.setattr("plugins.workflow.marketplace.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        "plugins.workflow.marketplace.cli._read_confirmation",
        lambda _prompt: shown.append(capsys.readouterr().out) or False,
    )

    code, _, _ = _run(_parser(), capsys, "update", "company/laptop-support")

    assert code == 0
    rendered = shown[0]
    for detail in (
        "Old version: 1.0.0",
        "Candidate version: 2.0.0",
        "Old commit:",
        f"Candidate commit: {_COMMIT}",
        "Old digest:",
        f"Candidate digest: {_DIGEST}",
        "added: added.txt",
        "modified: modified.txt",
        "removed: removed.txt",
        "renamed: renamed-old.txt -> renamed-new.txt",
        "Workflow membership changes",
        "+ new-workflow",
        "- old-workflow",
        "Requirement changes",
        "runtimes + python",
        "runtimes - node",
        "Risk changes",
        "new-workflow",
        "Compatibility changes",
        "missing_provider",
        "Blockers (1)",
        "Advisories (1)",
    ):
        assert detail in rendered
    assert [call[0] for call in service.calls] == ["prepare_update"]


def test_interactive_remove_review_names_provenance_and_trust_scope_before_decline(
    service, capsys, monkeypatch
) -> None:
    review = _remove_review()
    monkeypatch.setattr(
        service,
        "prepare_remove",
        lambda identity, *, actor: (
            service._record("prepare_remove", identity, actor) or review
        ),
    )
    shown = []
    monkeypatch.setattr("plugins.workflow.marketplace.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        "plugins.workflow.marketplace.cli._read_confirmation",
        lambda _prompt: shown.append(capsys.readouterr().out) or False,
    )

    code, _, _ = _run(_parser(), capsys, "uninstall", "company/laptop-support")

    assert code == 0
    rendered = shown[0]
    for detail in (
        "Installed identity: company/laptop-support",
        "Current version: 1.0.0",
        f"Current commit: {_COMMIT}",
        f"Distribution digest: {_DIGEST}",
        "Installed provenance removed: company/laptop-support",
        "Trust origin removed: marketplace:company/laptop-support",
        "Workflows removed (1)",
        "laptop-diagnostic",
    ):
        assert detail in rendered
    assert [call[0] for call in service.calls] == ["prepare_remove"]


def test_interactive_trust_review_shows_complete_risk_surface_before_decline(
    service, capsys, monkeypatch
) -> None:
    review = _rich_trust_review()
    monkeypatch.setattr(
        service,
        "review_trust",
        lambda identity, *, actor, workflow_name=None: (
            service._record("review_trust", identity, actor, workflow_name) or review
        ),
    )
    shown = []
    monkeypatch.setattr("plugins.workflow.marketplace.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        "plugins.workflow.marketplace.cli._read_confirmation",
        lambda _prompt: shown.append(capsys.readouterr().out) or False,
    )

    code, _, _ = _run(_parser(), capsys, "trust", "company/laptop-support")

    assert code == 0
    rendered = shown[0]
    for detail in (
        "Source: company",
        "Version: 1.0.0",
        f"Exact commit: {_COMMIT}",
        f"Distribution digest: {_DIGEST}",
        "Package-owned resources (6)",
        "commands/diagnose.md",
        "Workflow: laptop-diagnostic",
        "Definition: workflows/laptop-diagnostic.yaml",
        "Companion: workflows/laptop-diagnostic.hermes.yaml",
        f"Effective package digest: {_DIGEST}",
        f"Risk digest: {_RISK_DIGEST}",
        "Package resource set: package (see shared table above)",
        "Shell/script nodes: collect",
        "Command nodes: diagnose",
        "Approval nodes: approve-fix",
        "Command resources: commands/diagnose.md",
        "Script resources: scripts/collect.py",
        "MCP resources: mcp/support.yaml",
        "Local MCP servers: local-support",
        "Remote MCP servers: remote-support",
        "Requested tools: git",
        "Requested skills: support-triage",
        "Providers: anthropic",
        "Outward action nodes: open-ticket",
        "Required secrets: SUPPORT_TOKEN",
        "runtimes: python",
        "Compatibility findings (1)",
        "provider_authority_missing",
    ):
        assert detail in rendered
    assert [call[0] for call in service.calls] == ["review_trust"]


def test_non_tty_interactive_mutation_refuses_without_preparing(
    service, capsys
) -> None:
    code, captured, _ = _run(_parser(), capsys, "uninstall", "company/laptop-support")

    assert code == machine_contract.EXIT_AUTHORIZATION
    assert captured.out == ""
    assert "explicit confirmation" in captured.err
    assert service.calls == []


@pytest.mark.parametrize(
    ("identifier", "ref", "path"),
    [
        ("company/laptop-support", None, None),
        (
            "https://example.test/team/workflows.git",
            "release",
            "packages/laptop-support",
        ),
    ],
)
def test_install_passes_registered_and_direct_requests_only_to_service(
    identifier, ref, path, service, capsys
) -> None:
    argv = ["install", identifier, "--prepare-only", "--json"]
    if ref is not None:
        argv.extend(["--ref", ref])
    if path is not None:
        argv.extend(["--path", path])

    code, _, _ = _run(_parser(), capsys, *argv)

    assert code == 0
    request = service.calls[0][1]
    assert isinstance(request, InstallRequest)
    assert (request.identifier, request.ref, request.package_path) == (
        identifier,
        ref,
        path,
    )


def test_source_search_inspect_installed_and_check_commands_are_deterministic(
    service, capsys
) -> None:
    parser = _parser()
    code, _, sources = _run(parser, capsys, "source", "list", "--json")
    assert code == 0
    assert [item["name"] for item in sources["result"]["sources"]] == [
        "company",
        "zulu",
    ]

    code, _, search = _run(
        parser, capsys, "search", "support", "--source", "company", "--json"
    )
    assert code == 0
    assert search["result"]["packages"][0]["identifier"] == ("company/laptop-support")

    code, _, detail = _run(
        parser, capsys, "inspect", "company/laptop-support", "--json"
    )
    assert code == 0
    assert detail["result"]["package"]["display_name"] == "Laptop Support"

    code, _, installed = _run(parser, capsys, "installed", "--json")
    assert code == 0
    assert [
        item["identity"]["source_key"] for item in installed["result"]["packages"]
    ] == [
        "company",
        "zulu",
    ]

    code, _, checks = _run(parser, capsys, "check", "--json")
    assert code == 0
    assert [item["identity"]["source_key"] for item in checks["result"]["checks"]] == [
        "company",
        "zulu",
    ]


def test_human_inspect_renders_complete_deterministic_package_detail(
    service, capsys
) -> None:
    code, captured, _ = _run(_parser(), capsys, "inspect", "company/laptop-support")

    assert code == 0
    assert captured.err == ""
    for detail in (
        "Package: company/laptop-support",
        "Display name: Laptop Support",
        "Source: company",
        "Publisher: Example Company",
        "Version: 1.0.0",
        "Description: Diagnostic and repair workflows",
        "Tags: diagnostics, support",
        "License: MIT",
        "Repository: ssh://git@example.test/team/workflows.git",
        "Configured ref: main",
        f"Exact commit: {_COMMIT}",
        f"Verified at: {_NOW}",
        "Verified: yes",
        "Source status: fresh",
        "Package path: packages/laptop-support",
        "Contract version: 1",
        f"Distribution digest: {_DIGEST}",
        "Install status: installed",
        "Update status: current",
        "Package resources (6)",
        "commands/diagnose.md [command]",
        "mcp/support.yaml [mcp]",
        "Workflow: laptop-diagnostic",
        "Definition: workflows/laptop-diagnostic.yaml",
        "Companion: workflows/laptop-diagnostic.hermes.yaml",
        "Current trust: untrusted",
        "Compatibility findings (1)",
        "provider_authority_missing",
        "External requirements",
        "runtimes: python",
        "Advisories (1)",
        "missing_runtime",
        "Installed provenance",
        "Installed version: 1.0.0",
        f"Installed commit: {_COMMIT}",
        "Installed actor: cli",
    ):
        assert detail in captured.out
    assert (
        captured.out.index("commands/diagnose.md [command]")
        < captured.out.index("mcp/support.yaml [mcp]")
        < captured.out.index("scripts/collect.py [script]")
    )


def test_human_inspect_handles_empty_optional_detail(
    service, capsys, monkeypatch
) -> None:
    inspection = _inspection(installed=False).model_copy(
        update={
            "configured_ref": None,
            "workflows": [_workflow_review()],
            "external_requirements": _requirements(),
            "advisories": [],
        }
    )
    monkeypatch.setattr(service, "inspect", lambda _identifier: inspection)

    code, captured, _ = _run(_parser(), capsys, "inspect", "company/laptop-support")

    assert code == 0
    assert "Configured ref: default" in captured.out
    assert "Companion: none" in captured.out
    assert "Install status: not_installed" in captured.out
    assert "Update status: not_applicable" in captured.out
    assert "Advisories (0)" in captured.out
    assert "Installed provenance: none" in captured.out


def test_human_inspect_sanitizes_defensive_source_and_provenance_text(
    service, capsys, monkeypatch
) -> None:
    payload = _inspection().model_dump(mode="json")
    payload["repository_url"] = (
        "https://alice:supersecret@example.test/private.git?token=hidden"
    )
    payload["description"] = "password swordfish at /Users/alice/private/staging"
    payload["package_path"] = "/private/tmp/marketplace-staging"
    payload["installed"]["actor"] = "api-key ultra-secret"
    payload["advisories"][0]["message"] = "token leaked-value"
    monkeypatch.setattr(service, "inspect", lambda _identifier: payload)

    code, captured, _ = _run(_parser(), capsys, "inspect", "company/laptop-support")

    assert code == 0
    rendered = captured.out + captured.err
    for secret in (
        "alice:",
        "supersecret",
        "hidden",
        "swordfish",
        "/Users/alice/private/staging",
        "/private/tmp/marketplace-staging",
        "ultra-secret",
        "leaked-value",
    ):
        assert secret not in rendered
    assert "example.test/private.git" in rendered
    assert "[REDACTED_PATH]" in rendered


def test_source_add_refresh_one_refresh_all_and_remove_delegate_to_service(
    service, capsys
) -> None:
    parser = _parser()
    commands = [
        (
            "source_added",
            [
                "source",
                "add",
                "new",
                "https://example.test/new.git",
                "--ref",
                "main",
                "--json",
            ],
        ),
        ("fresh", ["source", "refresh", "company", "--json"]),
        ("ok", ["source", "refresh", "--json"]),
        ("source_removed", ["source", "remove", "company", "--json"]),
    ]

    statuses = []
    for expected, argv in commands:
        code, _, envelope = _run(parser, capsys, *argv)
        assert code == 0
        statuses.append(envelope["result"]["status"])
        assert envelope["result"]["status"] == expected

    assert statuses == ["source_added", "fresh", "ok", "source_removed"]
    assert [call[0] for call in service.calls] == [
        "add_source",
        "refresh_source",
        "list_sources",
        "refresh_source",
        "refresh_source",
        "remove_source",
    ]


def test_source_refresh_diagnostic_result_is_sanitized(
    service, capsys, monkeypatch
) -> None:
    secret_url = "https://alice:supersecret@example.test/private.git"
    monkeypatch.setattr(
        service,
        "refresh_source",
        lambda name: SourceRefreshResult(
            source_name=name,
            repository_url="https://example.test/workflows.git",
            state="stale",
            resolved_commit=None,
            verified_at=None,
            package_count=0,
            diagnostic_code="source_authentication_failed",
            message=f"fatal: {secret_url} token {_TOKEN}",
        ),
    )

    code, captured, envelope = _run(
        _parser(), capsys, "source", "refresh", "company", "--json"
    )

    assert code == machine_contract.EXIT_AUTHORIZATION
    assert envelope["result"]["refresh"]["diagnostic_code"] == (
        "source_authentication_failed"
    )
    rendered = captured.out + json.dumps(envelope)
    assert "supersecret" not in rendered
    assert "alice:" not in rendered
    assert _TOKEN not in rendered


def test_update_all_reports_deterministic_partial_failure(service, capsys) -> None:
    service.failures[("prepare_update", str(_identity("zulu", "second")))] = (
        WorkflowMarketplaceError("source_unavailable", "source unavailable")
    )

    code, captured, envelope = _run(
        _parser(), capsys, "update", "--all", "--yes", "--json"
    )

    assert code == machine_contract.EXIT_ACTION_FAILED
    assert captured.err == ""
    assert envelope["ok"] is False
    result = envelope["result"]
    assert result["status"] == "partial_failure"
    assert [item["identifier"] for item in result["succeeded"]] == [
        "company/laptop-support"
    ]
    assert [item["identifier"] for item in result["failed"]] == ["zulu/second"]
    assert result["failed"][0]["error"]["code"] == "source_unavailable"


def test_update_all_human_partial_failure_names_successes_and_failures(
    service, capsys
) -> None:
    service.failures[("prepare_update", str(_identity("zulu", "second")))] = (
        WorkflowMarketplaceError("source_unavailable", "source unavailable")
    )

    code, captured, _ = _run(_parser(), capsys, "update", "--all", "--yes")

    assert code == machine_contract.EXIT_ACTION_FAILED
    assert "company/laptop-support" in captured.out
    assert "zulu/second" in captured.out
    assert "partial_failure" in captured.err


def test_update_all_scrubs_each_partial_failure_before_json_projection(
    service, capsys
) -> None:
    secret_url = "https://alice:supersecret@example.test/private.git"
    service.failures[("prepare_update", str(_identity("zulu", "second")))] = (
        WorkflowMarketplaceError(
            "source_authentication_failed",
            f"fatal: {secret_url} denied token {_TOKEN}",
        )
    )

    code, captured, envelope = _run(
        _parser(), capsys, "update", "--all", "--yes", "--json"
    )

    assert code == machine_contract.EXIT_ACTION_FAILED
    assert envelope["result"]["failed"][0]["error"]["code"] == (
        "source_authentication_failed"
    )
    rendered = captured.out + json.dumps(envelope)
    assert "supersecret" not in rendered
    assert "alice:" not in rendered
    assert _TOKEN not in rendered


def test_check_returns_partial_failure_when_one_service_check_failed(
    service, capsys, monkeypatch
) -> None:
    secret_url = "https://alice:supersecret@example.test/private.git"
    failed = UpdateCheck(
        identity=_identity("zulu", "second"),
        status="error",
        installedVersion="1.0.0",
        diagnosticCode="source_authentication_failed",
        message=f"fatal: {secret_url} token {_TOKEN}",
    )
    current = UpdateCheck(
        identity=_identity(),
        status="current",
        installedVersion="1.0.0",
        candidateVersion="1.0.0",
    )
    monkeypatch.setattr(
        service, "check_updates", lambda _identity=None: (failed, current)
    )

    code, _, envelope = _run(_parser(), capsys, "check", "--json")

    assert code == machine_contract.EXIT_ACTION_FAILED
    assert envelope["result"]["status"] == "partial_failure"
    assert [
        item["identity"]["source_key"] for item in envelope["result"]["checks"]
    ] == [
        "company",
        "zulu",
    ]
    rendered = json.dumps(envelope)
    assert "supersecret" not in rendered
    assert "alice:" not in rendered
    assert _TOKEN not in rendered


def test_update_one_uninstall_and_trust_confirmation_modes(service, capsys) -> None:
    parser = _parser()

    code, _, update = _run(
        parser,
        capsys,
        "update",
        "company/laptop-support",
        "--prepare-only",
        "--json",
    )
    assert code == 0
    assert update["result"]["status"] == "review_required"

    code, _, uninstall = _run(
        parser,
        capsys,
        "uninstall",
        "company/laptop-support",
        "--confirmation-token",
        _TOKEN,
        "--json",
    )
    assert code == 0
    assert uninstall["result"]["status"] == "uninstalled"

    code, _, trust_review = _run(
        parser,
        capsys,
        "trust",
        "company/laptop-support",
        "--workflow",
        "laptop-diagnostic",
        "--prepare-only",
        "--json",
    )
    assert code == 0
    assert trust_review["result"]["status"] == "review_required"

    code, _, trusted = _run(
        parser,
        capsys,
        "trust",
        "company/laptop-support",
        "--confirmation-token",
        _TOKEN,
        "--json",
    )
    assert code == 0
    assert trusted["result"]["status"] == "trusted"

    code, _, untrusted = _run(
        parser,
        capsys,
        "untrust",
        "company/laptop-support",
        "--workflow",
        "laptop-diagnostic",
        "--json",
    )
    assert code == 0
    assert untrusted["result"] == {
        "identity": {"package_id": "laptop-support", "source_key": "company"},
        "revoked": 1,
        "status": "untrusted",
        "workflow": "laptop-diagnostic",
    }

    code, _, all_untrusted = _run(
        parser,
        capsys,
        "untrust",
        "company/laptop-support",
        "--json",
    )
    assert code == 0
    assert all_untrusted["result"]["workflow"] is None
    assert service.calls[-1] == ("revoke_trust", _identity(), None)


def test_trust_yes_reviews_then_grants_exactly_once(service, capsys) -> None:
    code, _, envelope = _run(
        _parser(),
        capsys,
        "trust",
        "company/laptop-support",
        "--yes",
        "--json",
    )

    assert code == 0
    assert envelope["result"] == {
        "status": "trusted",
        "workflows": {"laptop-diagnostic": "trusted"},
    }
    assert [call[0] for call in service.calls] == ["review_trust", "grant_trust"]


@pytest.mark.parametrize(
    "argv",
    [
        ["update", "--json"],
        ["update", "company/laptop-support", "--all", "--yes", "--json"],
        [
            "update",
            "--all",
            "--confirmation-token",
            _TOKEN,
            "--json",
        ],
    ],
)
def test_update_target_and_confirmation_ambiguities_fail_before_service(
    argv, service, capsys
) -> None:
    code, _, envelope = _run(_parser(), capsys, *argv)

    assert code == machine_contract.EXIT_INVOCATION
    assert envelope["error"]["code"] == "invalid_request"
    assert service.calls == []


@pytest.mark.parametrize(
    ("code", "exit_code"),
    [
        ("catalog_package_not_found", machine_contract.EXIT_NOT_FOUND),
        ("confirmation_token_invalid", machine_contract.EXIT_AUTHORIZATION),
        ("source_authentication_failed", machine_contract.EXIT_AUTHORIZATION),
        ("installed_package_conflict", machine_contract.EXIT_CONFLICT),
        ("package_digest_mismatch", machine_contract.EXIT_BLOCKING_FINDING),
        ("source_unavailable", machine_contract.EXIT_ACTION_FAILED),
    ],
)
def test_service_error_codes_map_to_stable_exit_categories(
    code, exit_code, service, capsys
) -> None:
    service.failures[("inspect", "company/laptop-support")] = WorkflowMarketplaceError(
        code, "bounded failure"
    )

    actual, _, envelope = _run(
        _parser(), capsys, "inspect", "company/laptop-support", "--json"
    )

    assert actual == exit_code
    assert envelope["error"]["code"] == code


def test_error_output_scrubs_credentials_git_diagnostics_and_confirmation_token(
    service, capsys
) -> None:
    secret_url = "https://alice:supersecret@example.test/private.git"
    service.failures[("confirm_install", _TOKEN)] = WorkflowMarketplaceError(
        "source_authentication_failed",
        f"fatal: repository {secret_url} denied token {_TOKEN}",
    )

    code, captured, envelope = _run(
        _parser(),
        capsys,
        "install",
        "company/laptop-support",
        "--confirmation-token",
        _TOKEN,
        "--json",
    )

    assert code == machine_contract.EXIT_AUTHORIZATION
    rendered = captured.out + captured.err + json.dumps(envelope)
    assert "supersecret" not in rendered
    assert _TOKEN not in rendered
    assert "alice:" not in rendered


@pytest.mark.parametrize("condition", ["expired", "replayed", "cross-actor"])
def test_invalid_confirmation_tokens_have_stable_pass_through_without_refetch(
    condition, service, capsys
) -> None:
    service.failures[("confirm_install", _TOKEN)] = WorkflowMarketplaceError(
        "confirmation_token_invalid",
        f"{condition} confirmation token is invalid",
    )

    code, captured, envelope = _run(
        _parser(),
        capsys,
        "install",
        "company/laptop-support",
        "--confirmation-token",
        _TOKEN,
        "--json",
    )

    assert code == machine_contract.EXIT_AUTHORIZATION
    assert captured.err == ""
    assert envelope["error"]["code"] == "confirmation_token_invalid"
    assert service.calls == [("confirm_install", _TOKEN, "cli")]
    assert "prepare_install" not in captured.out
    assert _TOKEN not in captured.out


def test_marketplace_flag_rejection_does_not_fall_through_to_legacy_or_service(
    service, capsys
) -> None:
    code, _, envelope = _run(
        _parser(),
        capsys,
        "trust",
        "company/laptop-support",
        "--digest",
        _DIGEST,
        "--yes",
        "--json",
    )

    assert code == machine_contract.EXIT_INVOCATION
    assert envelope["error"]["code"] == "invalid_request"
    assert service.calls == []


@pytest.mark.parametrize(
    ("action", "extra_arguments", "legacy_handler"),
    [
        ("trust", ["--digest", _DIGEST], "_cmd_trust"),
        ("untrust", [], "_cmd_untrust"),
    ],
)
def test_legacy_loose_workflow_paths_with_slashes_keep_their_original_routing(
    action, extra_arguments, legacy_handler, monkeypatch, service
) -> None:
    calls = []

    def invoke_legacy(args):
        calls.append(args.name)
        return 29

    monkeypatch.setattr(f"plugins.workflow.cli.{legacy_handler}", invoke_legacy)
    args = _parser().parse_args([
        action,
        "/tmp/workflows/sample.yaml",
        *extra_arguments,
    ])

    assert args.func(args) == 29
    assert calls == ["/tmp/workflows/sample.yaml"]
    assert service.calls == []


@pytest.mark.parametrize(
    ("action", "extra_arguments", "legacy_handler"),
    [
        ("trust", ["--digest", _DIGEST], "_cmd_trust"),
        ("untrust", [], "_cmd_untrust"),
    ],
)
def test_existing_extensionless_relative_file_routes_to_legacy_workflow_command(
    action,
    extra_arguments,
    legacy_handler,
    tmp_path,
    monkeypatch,
    service,
) -> None:
    workflow = tmp_path / "directory" / "workflow"
    workflow.parent.mkdir()
    workflow.write_text("legacy bytes", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(
        f"plugins.workflow.cli.{legacy_handler}",
        lambda args: calls.append(args.name) or 29,
    )

    args = _parser().parse_args([action, "directory/workflow", *extra_arguments])

    assert args.func(args) == 29
    assert calls == ["directory/workflow"]
    assert service.calls == []


def test_existing_extensionless_symlink_matches_legacy_resolver_semantics(
    tmp_path, monkeypatch, service
) -> None:
    target = tmp_path / "workflow-target"
    target.write_text("legacy bytes", encoding="utf-8")
    link = tmp_path / "directory" / "workflow-link"
    link.parent.mkdir()
    link.symlink_to(target)
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(
        "plugins.workflow.cli._cmd_untrust",
        lambda args: calls.append(args.name) or 29,
    )

    args = _parser().parse_args(["untrust", "directory/workflow-link"])

    assert args.func(args) == 29
    assert calls == ["directory/workflow-link"]
    assert service.calls == []


def test_nonexistent_slash_target_defaults_to_marketplace_identity(
    service, capsys
) -> None:
    code, _, envelope = _run(
        _parser(), capsys, "untrust", "directory/workflow", "--json"
    )

    assert code == 0
    assert envelope["result"]["identity"] == {
        "source_key": "directory",
        "package_id": "workflow",
    }
    assert service.calls == [("revoke_trust", _identity("directory", "workflow"), None)]


def test_explicit_installed_package_overrides_existing_path_collision(
    tmp_path, monkeypatch, service, capsys
) -> None:
    collision = tmp_path / "company" / "laptop-support"
    collision.parent.mkdir()
    collision.write_text("legacy bytes", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code, _, envelope = _run(
        _parser(),
        capsys,
        "untrust",
        "company/laptop-support",
        "--installed-package",
        "--json",
    )

    assert code == 0
    assert envelope["result"]["status"] == "untrusted"
    assert service.calls == [("revoke_trust", _identity(), None)]


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/workflows/extensionless",
        "./directory/extensionless",
        "../directory/extensionless",
        "directory/workflow.yaml",
        "directory/workflow.yml",
    ],
)
def test_legacy_path_syntax_parity_does_not_require_file_existence(
    path, monkeypatch, service
) -> None:
    calls = []
    monkeypatch.setattr(
        "plugins.workflow.cli._cmd_untrust",
        lambda args: calls.append(args.name) or 29,
    )
    args = _parser().parse_args(["untrust", path])

    assert args.func(args) == 29
    assert calls == [path]
    assert service.calls == []


def test_explicit_installed_package_disambiguates_single_segment_identity(
    service, capsys
) -> None:
    code, _, envelope = _run(
        _parser(),
        capsys,
        "trust",
        "company/laptop-support",
        "--installed-package",
        "--prepare-only",
        "--json",
    )

    assert code == 0
    assert envelope["result"]["status"] == "review_required"
    assert service.calls[0][0] == "review_trust"
