# Ericsson ARM (Artifactory) connector

This connector gives an agent bounded Artifactory evidence for release investigation: AQL search, artefact and folder metadata, build properties, repository enumeration, and approval-gated deploy and delete operations.

## Cloudflare Access and certificates

This instance is behind Cloudflare Access. `artifactory.rosetta.ericssondevops.com` authenticates callers at the edge with an **mTLS client certificate** before any request reaches Artifactory. That certificate is issued per person with roughly one year of validity and it expires silently: Access answers `302` to `cloudflareaccess.com` with `auth_status: FAILED:FAILED:certificate has expired`, and any consumer that does not read the redirect reports something unrelated instead.

The connector checks `notAfter` at configuration time in `auth.py` and classifies an Access challenge as `edge_authentication` rather than `authentication` in `client.py`, because the credential that failed is the certificate, not the Artifactory token. Those are two different secrets held by two different systems, and sending an operator to rotate the wrong one costs a day.

### Renewal

The certificate subject looks like `O=rcli-temporary (Group #13093), OU=<user>@ericsson.com, CN=endpoint <id>`, so it is issued by the `rcli` tool. Renew there, then update `client_cert_path` and `client_key_path` in the profile.

## Authentication modes

Which auth header this instance wants is unconfirmed. The token is a JFrog *reference token*, which both `Authorization: Bearer` and the legacy `X-JFrog-Art-Api` accept. This could not be tested against the live instance because the client certificate had expired, so `auth_mode` ships as a profile setting defaulting to `bearer`. Once confirmed, one of the two branches in `auth.py` can be deleted along with the setting.

## Predecessors

This connector supersedes three scripts in `oscar_app/oscar/utils`: `bulk_upload_verify.sh` (deploy), `cleanup_artifactory_releases.sh` (delete, AQL listing), and `pull_images_from_artifactory_repo.sh` (download). The first two are ported here; download is deliberately not — see below. Those scripts remain the right tool for bulk operator work; this connector is for an agent that needs to answer questions about artefacts.

## Deploy source confinement

When `deploy_root` is configured, upload source traversal requires POSIX file descriptors so that symlink and rename checks can remain confined to that root. On Windows or another unsupported platform, configured confinement fails closed: the upload is rejected rather than attempting a weaker path check. Leave `deploy_root` empty to retain unconfined absolute-path deployment; its exact source path remains visible in the approval prompt.

## Deliberately not implemented

| Surface | Why not | What it needs |
|---|---|---|
| `download` | Streaming artefact bytes into a model's context is the wrong representation, and super-cli itself writes to a file rather than emitting them. The sha256 from `arm_artifact_info` is what identifies an artefact. | A bounded *text* read (SBOM, manifest) with a content-type check — a different tool, not this one. |
| `set_properties` / `delete_properties` | Properties drive promotion gates; an agent flipping one could promote an unscanned artefact. | Artifactory's property syntax is `key=v1,v2;key2=v3` — super-cli's `arm.joinComma` and `arm.joinSemicolon` are those two joins. |
| `copy` / `move` | Organisational blast radius, and no agent workflow needs them yet. | `POST /artifactory/api/{copy,move}/{path}?to=` |
| Xray (`summary`, `violations`, `scanArtifact`) | Nothing in OSCAR's pipeline uses it and it may not be licensed on this tenant. | A second `path_prefix` for `/xray/`, which is why the client is written with a single `api_root` that a second base would extend rather than replace. `scanArtifact` builds its `componentID` as `concatstring3(repo, "://", path)` — byte-confirmed, but not Xray's documented component-ID form, so verify against a live instance before trusting it. |
| Permission targets | An audit surface, not an SDLC one, and it hands a model a map of access control. | `GET /artifactory/api/v2/security/permissions[/{name}]` |
| `storage_info` | Operator telemetry; nothing in the agent loop consumes it. | `GET /artifactory/api/storageinfo` |
