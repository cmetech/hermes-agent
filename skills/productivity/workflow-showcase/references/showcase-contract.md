<showcase_contract>
The catalog and package bytes are authenticated by bundled digests before use.
A run uses the ordinary workflow loader, immutable input snapshot, digest trust,
idempotent admission, coordinator/scheduler, RunStore, interaction, artifact,
and reporting contracts. Immutable run metadata identifies showcase ownership.

Catalog operations use a showcase ID. Admission returns a durable run ID. All
general lifecycle and evidence commands use that run ID, plus current
interaction and state-version fields where the runtime command contract asks
for them.

Evidence references are opaque. Reports never expose source input paths,
prompts, reasoning, credentials, unrestricted arguments, or client-invented
authorization scope. A shell-spawned CLI's provenance is a profile-local
administrative claim, not authenticated remote identity.
</showcase_contract>
