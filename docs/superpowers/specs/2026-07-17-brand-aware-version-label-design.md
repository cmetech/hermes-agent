# Brand-aware version label

## Goal

Remove the upstream Hermes product name from OTTO and LOOP24 version output
without changing the neutral upstream identity. A branded installation must
identify the runtime generically as a co-worker agent, while an unbranded
Hermes installation must retain its current name.

## Display contract

- Neutral upstream build: `Hermes Agent v<VERSION> (<RELEASE_DATE>)`
- OTTO build: `Co-worker Agent v<VERSION> (<RELEASE_DATE>)`
- LOOP24 build: `Co-worker Agent v<VERSION> (<RELEASE_DATE>)`

The contract applies to every path that uses the shared version label:

- `<cli> --version`
- `<cli> version`
- startup banner version text
- gateway `/version` responses
- non-interactive version output used by runtime and installation probes

The product label changes only. Version, release date, install metadata,
update status, and git provenance suffixes remain unchanged.

## Design

Add one dependency-light product-label helper beside the existing generated
home identity in `hermes_constants.py`. The helper classifies the committed
build identity, not the executable name or the user's active profile:

- the neutral generated home basename (`hermes` or `.hermes`) maps to
  `Hermes Agent`;
- every branded generated home basename maps to `Co-worker Agent`.

This reuses an identity value that the existing brand generator already owns,
checks, and neutralizes. It avoids adding another descriptor field, another
generator emitter, or executable-name heuristics that would disagree when the
same branded runtime is invoked through compatibility aliases.

Both the regular banner formatter and the Termux ultra-fast version path call
the helper. The banner remains the shared formatter for the normal CLI,
gateway, and startup-banner paths, so the change does not introduce a second
version-string implementation.

## Failure behavior

The helper is deterministic and performs no filesystem access, configuration
load, or network call at runtime. It uses the source-stamped platform default,
so `HERMES_HOME`, profiles, and current working directory cannot accidentally
change product identity. The neutral value is matched explicitly; a generated
non-neutral brand fails toward the generic `Co-worker Agent` label instead of
leaking the upstream product name.

## Verification

Tests will be written before production changes and must demonstrate the
following behaviors:

1. Neutral source identity produces `Hermes Agent`.
2. OTTO and LOOP24 source identities produce `Co-worker Agent`.
3. The shared banner formatter preserves git provenance suffixes.
4. The Termux fast path uses the same label.
5. CLI and gateway version-command regression suites remain green.
6. Brand generator checks pass for OTTO and LOOP24, and neutralization retains
   the upstream label.
7. The customization ledger/checker records any new upstream-owned symbol.

The paired release gate will run the existing base, OTTO, and LOOP24 tests and
build checks before publishing the next stable version. The release source
commits must be the exact tested commits.

## Scope boundaries

This change does not rename Python packages, repositories, internal modules,
environment variables, compatibility commands, install directories, log
messages, or the neutral Hermes product. It adds no model-facing tool and does
not alter prompts, conversation messages, workflow behavior, or prompt caching.
