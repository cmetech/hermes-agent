# Glean MCP authentication assessment and continuation runbook

**Status:** Investigation complete enough for a controlled live OAuth test; no
repository behavior has been changed.

**Recorded:** 2026-09-04

**Reader:** An engineer continuing the investigation on a machine where Hermes
can reach Ericsson services and the Glean MCP connection currently reports
`not authorized`.

**Post-read action:** Determine whether the failure comes from Hermes' current
static bearer-token configuration, then test the endpoint through Hermes'
existing MCP OAuth client without exposing credentials.

## Executive finding

`super-cli` does not authenticate to or call Glean. It provisions a URL-only
Glean MCP entry for Kiro, and Kiro is the process that connects to the server.

The Glean endpoint currently advertises the standard protected-resource OAuth
flow used by MCP:

1. The client makes an unauthenticated MCP request.
2. The server returns `401` with a `WWW-Authenticate` challenge identifying its
   protected-resource metadata and the `mcp` scope.
3. The client discovers the authorization server.
4. The client obtains an OAuth client identity, performs browser authorization
   with PKCE, and receives access and refresh tokens.
5. The client retries MCP requests with an OAuth bearer token.

Hermes is currently seeded differently: it expects a manually supplied
`GLEAN_API_TOKEN` and sends it as a static `Authorization` header. Hermes already
has MCP OAuth discovery, PKCE, dynamic client registration, token persistence,
refresh, and reauthentication. The leading candidate change is therefore to
configure Glean with `auth: oauth`, not to port authentication code from
`super-cli`.

This conclusion is strong but not yet an end-to-end success claim. A Hermes
client has not completed the Glean authorization page, registered a client,
listed tools, or performed a permitted search as part of this investigation.

## Evidence and confidence boundaries

### Decompiled binary

The inspected binary was `super-cli` 0.14.1:

| Property | Value |
| --- | --- |
| Commit | `6645cd0bb56cc54aa4f1d49095490832c9528dbb` |
| SHA-256 | `72ce9d9ad14b451b53a7f0f06786d75336a302562a8ed6d0dbafc2cb7657cc6a` |
| Format | ARM64 Mach-O |
| Toolchain | Go 1.26.0 |
| Link flags | `-s -w` |

The hash matched the repository's existing `super-cli` migration inventory.
Although the binary was stripped, Go's runtime function table retained package,
function, and source names. GoReSym recovered 11,561 functions, and targeted
ARM64 disassembly recovered exact string constants and direct call sites.

The retained Glean surface consists only of `super-cli/internal/glean.init`.
There are no retained Glean client methods and no `cmd/glean` command. The only
live Glean-related commands are the Kiro MCP installer, uninstaller, and status
reporter.

### Current external observations

The endpoint and Kiro observations were repeated on 2026-09-04:

- Kiro CLI version: `2.21.0`.
- Kiro's Glean MCP entry contained the endpoint URL and HTTP transport only; it
  contained no headers or environment variables.
- An unauthenticated request to the Glean MCP URL returned an OAuth bearer
  challenge with `scope="mcp"`.
- The public authorization-server metadata advertised Authorization Code,
  Refresh Token, and Client Credentials grants, dynamic client registration,
  public clients, and PKCE `S256`.
- Current Kiro binaries contain MCP OAuth protected-resource discovery,
  authorization-server discovery, dynamic registration, loopback callback,
  PKCE, token caching, refresh, and reauthorization paths.

The inspection did **not** read or copy Kiro token contents. It did not complete
the browser flow, invoke dynamic registration, or call a Glean search tool. The
exact identity provider behind the authorization page and the resulting token
claims remain to be observed during the controlled live test.

## Keep the three authentication domains separate

Three independent authentication mechanisms are easy to conflate.

### 1. `super-cli login`: ContextCore platform identity

`super-cli login` authenticates against Ericsson's Azure Entra tenant. It is
used for the ContextCore backend and as the seed for supported backend token
exchanges. The binary contains interactive Authorization Code + PKCE, Device
Code, and CI Client Credentials flows.

The service credential registry accepts ARM, Confluence, EVMS, GitLab, and
Jira. Glean has no credential slot. The retained backend token-exchange code is
used by supported SSO services such as EVMS; no Glean call path reaches it.

Therefore a successful `super-cli login` is not evidence of Glean MCP
authorization.

### 2. `super-cli kiro login`: Kiro application identity

`super-cli kiro login` shells out to Kiro's login command with the Ericsson AWS
IAM Identity Center provider and verifies the expected Ericsson Kiro profile.
This signs the Kiro application in. It does not install a Glean access token in
the MCP configuration.

### 3. Glean MCP OAuth: resource-server authorization

The Glean MCP endpoint runs its own OAuth protected-resource flow. Kiro's MCP
client follows that flow after it receives the server's `401` challenge. The
authorization page may reuse an existing browser SSO session, but the resulting
Glean resource token is not a `super-cli` Entra token copied into Kiro's MCP
configuration.

## What `super-cli` does for Glean

The Kiro MCP installer performs only local configuration work:

```text
resolve home directory
→ build the Kiro MCP settings path
→ read existing JSON if present
→ merge the ContextCore and Glean entries
→ serialize JSON
→ create the settings directory
→ write the settings file
```

Its disassembled call graph contains no network client, ContextCore backend
call, credential lookup, authorization-header construction, or token exchange.
The resulting logical entries are:

```text
contextcore  https://context.connected-operations.ericsson.net/mcp
glean        https://be.everyday-assistant.ericsson.net/mcp/EEA-KIRO-MCP
```

The Glean entry has a URL, HTTP transport, and enabled state. It has no
`Authorization` header and no environment-variable reference.

`super-cli status` is also not an authentication probe. It checks whether Kiro
has the expected MCP entries and whether their URLs match known production or
non-production values. A green status does not prove that Kiro completed OAuth
or that Glean accepts its requests.

## Actual Kiro-to-Glean request flow

The live endpoint challenge was:

```http
HTTP/2 401
WWW-Authenticate: Bearer resource_metadata="https://be.everyday-assistant.ericsson.net/.well-known/oauth-protected-resource/mcp/EEA-KIRO-MCP", scope="mcp", error="invalid_token", error_description="Authentication required"
```

The protected-resource metadata identified:

```json
{
  "resource": "https://be.everyday-assistant.ericsson.net/mcp/EEA-KIRO-MCP",
  "authorization_servers": [
    "https://be.everyday-assistant.ericsson.net/oauth"
  ],
  "scopes_supported": ["mcp"]
}
```

The authorization-server metadata identified these relevant capabilities:

```json
{
  "issuer": "https://be.everyday-assistant.ericsson.net/oauth",
  "authorization_endpoint": "https://be.everyday-assistant.ericsson.net/oauth/authorize",
  "token_endpoint": "https://be.everyday-assistant.ericsson.net/oauth/token",
  "registration_endpoint": "https://be.everyday-assistant.ericsson.net/oauth/register",
  "grant_types_supported": [
    "authorization_code",
    "client_credentials",
    "refresh_token"
  ],
  "token_endpoint_auth_methods_supported": [
    "none",
    "client_secret_basic",
    "client_secret_post",
    "private_key_jwt"
  ],
  "code_challenge_methods_supported": ["S256"]
}
```

Together with Kiro's retained OAuth implementation, the expected flow is:

```text
super-cli                         Kiro                         Glean
    |                              |                             |
    |-- write URL-only config ---->|                             |
    |                              |-- MCP request, no token --->|
    |                              |<-- 401 + metadata URL -------|
    |                              |-- discover resource/AS ----->|
    |                              |-- register/identify client ->|
    |                              |-- browser auth + PKCE ------>|
    |                              |<-- access + refresh tokens ---|
    |                              |-- MCP request + Bearer ----->|
```

## How Hermes currently differs

The Ericsson capability seed currently produces this logical configuration:

```yaml
mcp_servers:
  glean:
    enabled: false
    url: https://be.everyday-assistant.ericsson.net/mcp/EEA-KIRO-MCP
    headers:
      Authorization: "Bearer ${GLEAN_API_TOKEN}"
```

The capability manifest and onboarding material declare `GLEAN_API_TOKEN` as a
required protected static secret. When the server is enabled, Hermes resolves
the placeholder from the active profile's secret scope and passes the header to
the HTTP MCP transport.

This path has no token acquisition or refresh lifecycle. If the variable is
unset, its literal placeholder remains unresolved. If it is set to an expired,
wrong-audience, or wrong-scope token, Hermes sends that token and receives an
authorization failure. Since the server lacks `auth: oauth`, Hermes does not
activate its OAuth provider to follow the challenge.

A valid Glean-issued bearer token might still work in this mode. The problem is
that the repository does not document an authoritative token issuance and
refresh process, and this is not how `super-cli` provisions Kiro.

## Hermes already has the matching OAuth client

Hermes' existing MCP OAuth implementation provides:

- `WWW-Authenticate` and protected-resource metadata discovery;
- authorization-server metadata discovery;
- MCP client identification, with dynamic registration fallback;
- Authorization Code with PKCE and a loopback or dashboard callback;
- profile-isolated token, client-registration, and metadata persistence;
- access-token expiry tracking and refresh-token handling;
- reconnection and one-time recovery after authorization failures;
- explicit `hermes mcp login` and `hermes mcp reauth` operations; and
- a non-interactive guard requiring the initial browser authorization to be
  completed interactively before a gateway reuses cached tokens.

OAuth state is stored below the active Hermes home in `mcp-tokens`. Token files
are created with user-only permissions and the parent directory is protected.

The relevant focused test suite passed 11 tests during this investigation. It
covers bidirectional OAuth request handling, `WWW-Authenticate` resource
metadata, cold-start metadata discovery, token expiry, and refresh behavior.
That verifies the Hermes implementation in isolation; it does not replace the
live Glean login test.

## Detailed comparison

| Concern | `super-cli` + Kiro | Current Hermes Glean seed | Hermes OAuth mode |
| --- | --- | --- | --- |
| Process contacting Glean | Kiro | Hermes | Hermes |
| `super-cli` role | Writes URL-only configuration | None | None required |
| Initial request | Kiro can begin without a Glean token | Static bearer header | No token; follows challenge |
| OAuth resource discovery | Kiro supports it | Not used | Supported |
| Authorization-server discovery | Kiro supports it | Not used | Supported |
| Client registration | Kiro supports dynamic registration | Not applicable | Dynamic registration fallback |
| Interactive grant | Authorization Code + PKCE | None | Authorization Code + PKCE |
| Requested scope | Server advertises `mcp` | Encoded in manually obtained token | Server-provided by default |
| Refresh | Kiro has refresh paths | Manual token replacement | Persisted refresh token |
| Credential ownership | Kiro OAuth storage | Hermes protected static secret | Hermes profile OAuth storage |
| First headless use | Requires prior Kiro authorization | Works while token remains valid | Requires prior interactive login |
| Reauthorization | Kiro MCP client | Replace static token | `hermes mcp login/reauth` |
| `super-cli status` | Configuration/drift only | Not applicable | Not applicable |

## Interpreting `not authorized`

First determine which layer emitted the error.

### Transport-level failure before tool discovery

If startup or `tools/list` fails with HTTP `401`, `403`, `invalid_token`, or
`not authorized`, likely causes are:

- the current static token is absent;
- the token expired;
- the token has the wrong audience or lacks the `mcp` scope;
- the unresolved `${GLEAN_API_TOKEN}` placeholder was sent literally; or
- OAuth was intended, but the server is not configured with `auth: oauth`.

### OAuth setup failure

If the log mentions browser authorization, dynamic registration, callback,
cached OAuth state, or non-interactive execution, the OAuth path is active.
Likely causes are:

- initial login was attempted from a non-interactive gateway before
  `hermes mcp login glean` completed;
- the browser callback did not return;
- dynamic registration rejected the Hermes client metadata;
- cached client registration is no longer accepted; or
- a refresh token was rejected and reauthorization is required.

### Tool-level permission failure after successful discovery

If Hermes connects and lists Glean tools but an individual search returns `not
authorized`, transport authentication succeeded. Investigate Glean content
permissions, user entitlements, requested operation, and token scopes rather
than MCP connection setup.

## Work-laptop continuation runbook

These steps intentionally avoid printing token values. Redact bearer tokens,
authorization codes, cookies, email addresses, and internal document content
before committing or sharing logs.

The examples assume the default Hermes home, `~/.hermes`. If a profile or
custom Hermes home is active, use that profile's config, logs, and token
directory consistently.

### 1. Record exact versions and repository state

```bash
git branch --show-current
git rev-parse HEAD
git status --short --branch
hermes --version
super-cli version
kiro-cli --version
shasum -a 256 "$(command -v super-cli)"
```

Do not reset unrelated work to match the versions in this report. Record the
differences so later results can be attributed to the correct build.

### 2. Inspect configuration without exposing secrets

```bash
rg -n -A8 -B1 '^\s+glean:' ~/.hermes/config.yaml

if rg -q '^GLEAN_API_TOKEN=' ~/.hermes/.env 2>/dev/null; then
  echo 'GLEAN_API_TOKEN entry: present'
else
  echo 'GLEAN_API_TOKEN entry: absent'
fi
```

Record whether the block contains `enabled`, `headers`, `auth`, and `oauth`.
Do not print the `.env` line or run a broad `env` command into a shared log.

If Kiro is installed, record its MCP entry with values reduced to non-secret
configuration:

```bash
jq '.mcpServers.glean
    | {url, type, disabled,
       has_headers: has("headers"),
       has_env: has("env")}' \
  ~/.kiro/settings/mcp.json
```

### 3. Capture the authorization failure safely

Reproduce once, then extract only relevant log lines:

```bash
hermes mcp test glean

rg -n -i \
  'glean|not authorized|unauthorized|invalid_token|oauth|401|403' \
  ~/.hermes/logs/agent.log | tail -200
```

Record:

- whether the failure occurs during connection, initialization, tool listing,
  or an individual tool call;
- the HTTP status;
- whether `WWW-Authenticate` or `resource_metadata` appears;
- whether Hermes reports static headers or OAuth; and
- whether any Glean tools were discovered before the error.

### 4. Confirm the endpoint still advertises the same OAuth contract

These calls are unauthenticated and should not include a bearer token:

```bash
GLEAN_MCP_URL='https://be.everyday-assistant.ericsson.net/mcp/EEA-KIRO-MCP'
GLEAN_PRM_URL='https://be.everyday-assistant.ericsson.net/.well-known/oauth-protected-resource/mcp/EEA-KIRO-MCP'
GLEAN_ASM_URL='https://be.everyday-assistant.ericsson.net/.well-known/oauth-authorization-server/oauth'

curl -sS -D - -o /dev/null "$GLEAN_MCP_URL" \
  | rg -i '^(HTTP/|www-authenticate:)'
curl -sS "$GLEAN_PRM_URL" \
  | jq '{resource, authorization_servers, scopes_supported}'
curl -sS "$GLEAN_ASM_URL" \
  | jq '{issuer, authorization_endpoint, token_endpoint,
         registration_endpoint, grant_types_supported,
         token_endpoint_auth_methods_supported,
         code_challenge_methods_supported, scopes_supported}'
```

Stop and reassess if the endpoint no longer returns a protected-resource
challenge, the issuer changed, the `mcp` scope disappeared, or dynamic client
registration is no longer advertised.

### 5. Run a controlled Hermes OAuth trial

Back up the active config, then edit only the Glean block:

```bash
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.before-glean-oauth
```

The trial configuration should be:

```yaml
mcp_servers:
  glean:
    enabled: true
    url: https://be.everyday-assistant.ericsson.net/mcp/EEA-KIRO-MCP
    auth: oauth
```

Remove the entire static `headers` block for this trial. Do not configure both
the static `Authorization` header and OAuth; that would make it unclear which
credential reached the server.

Run the login from an interactive user session:

```bash
hermes mcp login glean
hermes mcp test glean
```

Expected successful behavior:

1. Hermes discovers the protected-resource and authorization-server metadata.
2. A browser opens for authorization.
3. The callback returns to Hermes.
4. OAuth token state is created for the `glean` server.
5. `hermes mcp test glean` connects and lists one or more tools.

Inspect token-state existence and permissions without reading its contents:

```bash
ls -la ~/.hermes/mcp-tokens/glean* 2>/dev/null
```

If the login fails, retain the exact sanitized error and the relevant log
window. In particular, distinguish registration rejection from browser-login,
callback, token-exchange, refresh, and MCP initialization failures.

To restore the original local configuration after the experiment:

```bash
cp ~/.hermes/config.yaml.before-glean-oauth ~/.hermes/config.yaml
```

### 6. Validate actual access only after authentication succeeds

After tool discovery succeeds, run one permitted narrow read-only search. Do
not infer search authorization from a successful `tools/list` alone. Record:

- the discovered tool name and input shape;
- whether the call succeeds for the signed-in user;
- whether source links are returned;
- whether inaccessible content is omitted or produces a permission error; and
- whether a gateway restart reuses the cached token without another browser
  prompt.

Do not commit search results or internal document content to this repository.

## Decision after the live trial

If the controlled OAuth test succeeds, the smallest durable change is:

1. Change the Glean capability seed from a static bearer header to
   `auth: oauth`.
2. Remove `GLEAN_API_TOKEN` from the capability's required static secrets.
3. Update onboarding to enable Glean and run `hermes mcp login glean`.
4. Keep the server disabled by default until the user explicitly enables and
   authorizes it.
5. Add a targeted migration or explicit repair step for existing configs.

The final item is required because capability staging deliberately preserves an
existing MCP server block and only fills a missing or blank managed URL. Merely
changing the baked seed will not remove an existing `Authorization` header or
add `auth: oauth` for users who already staged Glean.

Do not add a Glean-specific OAuth implementation, reuse the `super-cli` Entra
token, or route Glean through ContextCore token exchange. The generic Hermes MCP
OAuth implementation already owns this behavior.

If dynamic registration is rejected specifically for Hermes, capture the
registration response and determine whether the endpoint requires a
pre-registered client or Kiro-specific client metadata. That would change only
the OAuth client configuration decision, not the finding that `super-cli` is a
provisioner rather than the Glean client.

If unattended service access is a requirement, treat it as a separate decision.
The endpoint advertises Client Credentials, but a service identity has different
ownership, scope, storage, and rotation requirements from the interactive user
flow examined here.

## Completion criteria for the investigation

The investigation can move from “probable configuration mismatch” to a verified
recommendation when the work-laptop run records all of the following:

- the original sanitized `not authorized` failure and its exact layer;
- the endpoint's current protected-resource and authorization-server metadata;
- whether the Glean authorization server accepts Hermes dynamic registration;
- whether browser authorization returns a token with the required `mcp` access;
- whether Hermes can initialize the MCP session and list tools;
- whether one narrow permitted search succeeds; and
- whether a restarted non-interactive Hermes process reuses or refreshes the
  cached OAuth state.

Until then, the evidence supports changing the default to OAuth, but the live
trial remains the acceptance test.
