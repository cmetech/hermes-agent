# Branded Tool Contract Provider Design

## Problem

Hermes v5.8.1 accepts OTTO v1 operation context only when the runtime provider
name is `otto` or `otto-gateway`. Generated branded releases identify their
Gateway provider with the brand slug, so LOOP24 runs as `provider=loop24` and
fails with `otto_tool_contract_unavailable` before dispatch.

## Approaches Considered

1. Add `loop24` to the private name allowlist. This is small but repeats the
   defect for every future generated brand.
2. Infer Gateway trust from the loopback URL or port. This is rejected because
   endpoint shape is not provider identity and could send OTTO headers to an
   unrelated local service.
3. Declare the supported OTTO contract version on the generated provider
   profile. This is selected because provider profiles already own endpoint
   capabilities and default to no declaration.

## Design

`ProviderProfile` gains an empty-by-default `otto_tool_contract_version`
declaration. The brand provider generator emits `"v1"` for every generated
OTTO Gateway profile. Request construction accepts v1 only when either the
normalized provider name is the existing canonical `otto`/`otto-gateway` name
or its resolved provider profile declares exactly `"v1"`.

Unknown versions, missing profiles, direct providers, and uninspectable API
modes continue to raise the terminal `otto_tool_contract_unavailable` error
before dispatch. The wire header names, payload semantics, echo checks,
fallback rules, prompt cache, messages, and tool authorization are unchanged.

## Testing

- A real registered branded `ProviderProfile` must produce the exact v1 and
  call-role headers.
- The same provider without the declaration must fail before dispatch.
- A non-v1 declaration must fail before dispatch.
- Existing OTTO, direct-provider, wrong-echo, streaming, lifecycle, and
  concurrency suites must remain green.
- Brand generator tests must prove both descriptors emit the capability, and
  each generated brand must pass its release merge/build gates.

## Release

Land the fix on `base`, merge the exact tested base SHA into every discovered
brand branch, regenerate brand overlays, pass the paired gates, push the three
source refs forward-only, and publish v5.8.2 from the exact OTTO and LOOP24
source SHAs. End on `base`.
