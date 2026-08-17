# Credential Storage Parity — Platform Verification

**Verification date:** 2026-08-16

**Hermes branch:** `feat/hermes-credential-storage-parity`

**Verified code commit:** `d45ca5701ce3347b4dcc431dde9c557fbc1cecf7`

**Verification record commit:** docs-only child of the verified code commit; it
does not change the code that was exercised.

**Status:** macOS PASS; Docker-specific persistence PASS; Windows,
Linux-native, and Kubernetes OUTSTANDING

## Platform matrix

| Platform | Status | Verification date | Evidence |
|---|---|---|---|
| macOS 26.5 (Darwin 25.5.0, arm64) | **PASS** | 2026-08-16 | Production named-profile CLI/Keychain, actual rebuilt Electron renderer, builtin scheduler, complete recovery matrix, automated suites, and exact cleanup passed. |
| Docker 29.4.0, named volume | **PASS — DOCKER ONLY** | 2026-08-16 | A freshly rebuilt current image passed both real persistence tests; a distinct second container read the first container's value from the same unique named volume. Containers and volume were then removed. |
| Windows | **OUTSTANDING** | 2026-08-16 | Native backend, prompt behavior, ACL/reparse behavior, CLI/Desktop interpreter resolution, recovery, and full suite were not run. |
| Linux-native | **OUTSTANDING** | 2026-08-16 | Native OS-keystore behavior, prompt behavior, permissions, CLI/Desktop interpreter resolution, recovery, and full suite were not run. |
| Kubernetes | **OUTSTANDING** | 2026-08-16 | A real cluster, storage class/retention policy, acknowledgement behavior, recovery, and restart persistence were not exercised. |

The Docker result is specific to the tested Docker named-volume environment.
It is not evidence for Linux-native, Kubernetes, Podman, or another container
runtime. Outstanding rows are not inferred passes.

## Verification boundary and accepted automated gates

The branch, immutable code HEAD, and tracked-clean state were checked before
the live macOS pass. The following gates were executed and accepted by the
root verifier against the same immutable code HEAD; the fresh macOS verifier
recorded them and did not rerun them.

The exact focused ten-file suite passed:

```text
scripts/run_tests.sh tests/test_command_secret_source.py tests/test_bitwarden_secrets.py tests/hermes_cli/test_env_loader.py tests/hermes_cli/test_secret_authority.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_plugin_configuration_storage.py tests/hermes_cli/test_secrets_repair.py tests/hermes_cli/test_windows_permissions.py tests/hermes_cli/test_container_storage.py tests/hermes_cli/test_secrets_migrate.py -v
10 files, 436 passed, 0 failed (14 workers)
```

The real-startup regression passed:

```text
scripts/run_tests.sh tests/hermes_cli/test_env_loader.py::test_startup_scrubs_legacy_plugin_secrets_but_load_env_still_reads_them -v
1 file, 1 passed, 0 failed (14 workers)
```

The freshly rebuilt-image real Docker gate passed:

```text
scripts/run_tests.sh tests/docker/test_secret_keystore_persistence.py --file-timeout 600 -v
1 file, 2 passed, 0 failed
```

The rebuilt image was
`sha256:c8120683b57fa1ce805555ee4f305f3c9c4cc3be207269a73e94ef33fcfea486`.
Write container `stoic_haibt` and read container `condescending_davinci` were
distinct and mounted unique volume
`hermes-secret-persistence-87211a09870a458fa281795c367eab4e`; the second
container read the value persisted by the first. Exact and ancestor-wide
cleanup checks later found no matching container or volume.

The mandatory full repository run passed:

```text
scripts/run_tests.sh
2834 files, 33887 passed, 0 failed in 1075.0s (14 workers)
```

Only these two files were retry-only flakes, and both passed on attempt 2:

- `tests/plugins/workflow/test_retry.py`
- `tests/scripts/test_workflow_upstream_merge.py`

Before that full run, `tests/plugins/workflow/test_retry.py` passed in
isolation with retries disabled (`85 passed, 0 failed`), and the targeted
`real_inline_worker_tree_charges_one_durable_provider_ledger` selection passed
with retries disabled (`10 passed, 0 failed`). No workflow file or path differed
from the baseline.

## Fresh macOS production CLI evidence

The live pass used a brand-new global Hermes root outside the developer's real
`~/.hermes`, named profile `credcorrected-50d8c6eb`, and an isolated Desktop
userData directory. Before setup, the global root, profile, userData, exact
Keychain account, cron name, CDP port, build artifacts, and exact-profile
processes were absent or free. `HERMES_PROFILE` was never used.

Profile creation and configuration used the supported production interface:

```text
HERMES_HOME=<GLOBAL_ROOT> .venv/bin/python -m hermes_cli.main profile create credcorrected-50d8c6eb --no-alias --no-skills
HERMES_HOME=<GLOBAL_ROOT> .venv/bin/python -m hermes_cli.main --profile credcorrected-50d8c6eb config set secret_keystore os
```

A profile-local disposable plugin exposed one write-only field. Its value was
generated in-process, never printed, never passed on a command line, and never
placed in an environment variable.

- The exact profile home was `<GLOBAL_ROOT>/profiles/credcorrected-50d8c6eb`.
- The production backend and durable authority were both exactly `os`.
- A fresh `PluginConfigurationService` instance after backend-cache reset read
  the same value; an in-process SHA-256 equality assertion succeeded.
- `detail()` returned `is_set=true` and no `value` member.
- Direct Keychain presence lookup for the exact profile-scoped account
  `hermes-profile-2e983f2266286702b71884a11a1355ff5b2337998d14dc4fee7981119881597c`
  succeeded without requesting or printing the value.
- The logical key was absent from parent and child environments and `.env`.
  A recursive scan found no plaintext in profile files or authority metadata.
- `secrets/` was `0700`; live authority and lock files were `0600`.
- The interpreter was the repository
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python`.

## Corrected actual Electron renderer and prompt behavior

An earlier verifier attempt used a profile-shaped `HERMES_HOME` together with
unsupported `HERMES_PROFILE`. Desktop intentionally normalizes its input to a
global root and selects named profiles through isolated
`active-profile.json`; that invalid launch therefore reached the default root.
This was a verifier-procedure error, not a product failure, and all of its exact
processes were cleaned.

For the corrected pass, `npm run build` produced the current renderer and
Electron bundles, transformed 14,977 modules, staged native `node-pty`, and
passed its postbuild assertion. The 12 build-owned tracked rewrites were
restored exactly from the immutable code HEAD before launch. The isolated
userData file contained valid JSON with exactly:

```json
{"profile":"credcorrected-50d8c6eb"}
```

The actual Electron application was then launched with only the supported
authority inputs:

```text
HERMES_HOME=<GLOBAL_ROOT>
HERMES_DESKTOP_USER_DATA_DIR=<ISOLATED_USER_DATA>
HERMES_DESKTOP_HERMES_ROOT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
HERMES_DESKTOP_CDP_PORT=50528
```

The freshly built renderer loaded from `apps/desktop/dist/index.html` and was
observed through bounded localhost CDP. The actual named-profile child argv was:

```text
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python -m hermes_cli.main --profile credcorrected-50d8c6eb serve --host 127.0.0.1 --port 0
```

Its interpreter and repository backend were exact. After `--profile` was
applied, a real descendant spawned by that serve process inherited
`HERMES_HOME=<GLOBAL_ROOT>/profiles/credcorrected-50d8c6eb`, with
`HERMES_PROFILE` absent. This process-level inheritance proves the backend's
effective named-profile environment.

The actual renderer reached `#/settings?tab=plugins`. Its live Plugins view
showed the unique disposable plugin and field as `Secret set`; a full renderer
reload repeated the same assertions. No credential value appeared in captured
output. The prompt baseline and both reads contained only the same pre-existing
CoreServicesUIAgent PID/create-time tuple and no SecurityAgent, so there were
zero new Keychain prompt/dialog processes.

Two corrected-pass launch diagnostics also had no product significance: one
relative Electron binary typo exited 127, and the production-stamped bundle
correctly withheld CDP until the generated development main bundle was used to
inspect the same freshly built renderer. Each diagnostic process tree was
cleaned before the accepted run.

## Builtin scheduler evidence

With Electron, CDP, backend, gateway, and exact-profile process counts all
zero, the production CLI created one due one-shot no-agent job under the named
profile. Only an actual `cron tick` executed it; no direct/manual helper
invocation was used.

- Job: `credential-final-50d8c6eb` (`e4b85da3ea3e`)
- Execution: `e1ed6c0efd3046f08a9a797a921e63c8`
- Durable history source/status: `builtin` / `completed`
- Claimed: `2026-08-16T23:24:25.969360-04:00`
- Finished: `2026-08-16T23:24:26.211823-04:00`
- Value-free result marker:
  `CREDENTIAL_CORRECTED_GATE_OK backend=os interpreter=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python profile_home_ok=true`

The scheduled helper imported the repository production credential service,
read the durable field without printing it, required `backend=os` and
`authority=os`, and asserted the exact named-profile home. Its recorded PID was
absent after completion; exact-profile process count and GUI prompt delta were
both zero.

## Recovery evidence

All recovery cases were confined to the disposable named profile and its exact
account. Findings, plans, reports, and quarantine manifests were value-free.

For combined permission, duplicate, and temporary-artifact drift, doctor
reported `ABANDONED_TEMP`, six `PERMISSION_DRIFT` findings, and
`STALE_DUPLICATE`. Default repair planned `CLEAN_ABANDONED_TEMP`,
`DELETE_STALE_COPY`, and six `REPAIR_PERMISSIONS` actions. A whole-profile
content/metadata snapshot covering path, type, mode, uid, gid, inode, size,
mtime, and file bytes remained exactly
`7dfdeeb077cf35b55c2d47bdf358ecad85886b08378b9aaff2408a7106af286d`
before doctor, after doctor, and after default repair. Apply removed the equal
nonauthoritative copy and abandoned temp, restored `0700`/`0600`, and the next
doctor was clean.

For corrupt-tier reconstruction, file authority had an equal healthy OS
recovery copy when the 32-byte file master key was corrupted. Doctor reported
`FILE_STORE_CORRUPT`; apply performed `REBUILD_FILE_STORE`, rebuilt a readable
file authority from OS, removed the recovery duplicate, and quarantined the
corrupt key, ciphertext, and value-free manifest under
`20260817T032822.779758Z-45835ad2`. Doctor was clean afterward.

For unambiguous corrupt-authority reconstruction, all five static inventory
keys had file-only disposable values before authority metadata was corrupted.
Doctor reported `AUTHORITY_CORRUPT`; default apply performed
`REBUILD_AUTHORITY`, reconstructed five `file` entries, retained readable
values, and quarantined the corrupt authority plus value-free manifest under
`20260817T033001.516447Z-ac3e90fb`. The earlier quarantine remained intact and
doctor was clean.

For corrupt-authority tombstone ambiguity, the target first had durable
`cleared` state and no tier value, while a process-generated stale legacy
plaintext entry remained in `.env` but was suppressed. Doctor reported
`AUTHORITY_CORRUPT` and `AUTHORITY_TOMBSTONE_AMBIGUOUS`; default repair exited
1 and refused reconstruction. Noninteractive
`--reset-unrecoverable --apply` without `--yes` exited 2 with the required
confirmation error. A byte/metadata snapshot of `.env`, `config.yaml`, and the
complete secrets tree remained exactly
`88a641a7975b5ed428a8e5339eef383980fb48f5e3fefdd4b3c7eaa832a89730`
across the refusal.

Confirmed `--reset-unrecoverable --apply --yes` applied
`RESET_UNRECOVERABLE_AUTHORITY`, quarantined the corrupt authority plus
value-free manifest under `20260817T033223.743778Z-dae75972`, reconstructed the
four surviving `file` entries, and wrote durable `cleared` for the absent
target. The stale legacy entry remained present yet both production resolution
and `detail()` suppressed it. The target was absent from file and OS tiers, all
three quarantines remained recoverable until cleanup, and the following doctor
was clean.

## Cleanup and negative proof

Only exact pass-owned artifacts were removed.

- Supported cleanup cleared all five disposable recovery keys and legacy
  entries, leaving doctor clean before profile removal.
- Exact cron job `e4b85da3ea3e` was removed; the named-profile registry listed
  no scheduled jobs.
- Direct lookup proved the exact Keychain account absent.
- Exact disposable/profile process count was zero; all recorded Electron,
  backend, detached restart, and scheduler PIDs were absent; CDP port 50528 had
  no listener. The prompt-agent baseline remained unchanged.
- The entire temporary GLOBAL root/profile/userData, scheduler helper/output,
  and all quarantines were moved to recoverable macOS Trash. Their original
  `/private/tmp/hermes-credential-corrected.40a108` path is absent.
- Precheck-absent `apps/desktop/dist`, `apps/desktop/build`, and
  `plugins/model-providers/otto` were moved to the same Trash tree. Their
  repository paths and Desktop TypeScript build-info artifacts are absent.
- Docker cleanup negatives found zero current-image ancestor containers, zero
  exact test container names, zero matching volumes, and the exact unique
  volume absent.

The developer's real default/global `~/.hermes` was unchanged from the
read-only precheck baseline:

- `config.yaml`: mode `0600`, size `7926`, mtime-ns
  `1786660052965968961`, SHA-256
  `1f57ef590e479e81c8312c80fe657082d93bde19aaa4955cb13e12725051ed5b`;
- `.env`: mode `0600`, size `23389`, mtime-ns
  `1783260935902097407`, SHA-256
  `cedd601f9d888bba40673ad90845fb7d38006fd2adc06026f42aed37ededca34`;
- `profiles/` remained empty; both active-profile filename variants and
  `~/.hermes/secrets` remained absent.

Unrelated state and pre-existing untracked paths were preserved. The tracked
verification record is the only intentional repository change after all live
gates and cleanup passed.
