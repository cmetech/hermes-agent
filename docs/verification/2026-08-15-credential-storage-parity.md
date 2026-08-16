# Credential Storage Parity — Platform Verification

**Verification date:** 2026-08-16

**Hermes branch:** `feat/hermes-credential-storage-parity`

**Verified commit:** `1ede2f2674694b8aa6ceba0bf84a628e8198c51f`

**Status:** macOS PASS; Windows, Linux, and Container OUTSTANDING

## Platform matrix

| Platform | Status | Verification date | Owner | Evidence |
|---|---|---|---|---|
| macOS 26.5 (Darwin 25.5.0, arm64) | **PASS** | 2026-08-16 | Hermes maintainers | OS Keychain backend, production CLI and live Electron reads, built-in scheduler read, recovery workflows, startup regression, focused suites, and full repository suite all passed. |
| Windows | **OUTSTANDING** | 2026-08-16 | Hermes maintainers — platform verification follow-up | Not run: backend, prompt behavior, CLI/desktop interpreters, environment isolation, recovery, and full suite remain outstanding. |
| Linux | **OUTSTANDING** | 2026-08-16 | Hermes maintainers — platform verification follow-up | Not run: backend, prompt behavior, CLI/desktop interpreters, environment isolation, recovery, and full suite remain outstanding. |
| Container | **OUTSTANDING** | 2026-08-16 | Hermes maintainers — platform verification follow-up | Not run: backend, prompt behavior, CLI/desktop interpreters, environment isolation, recovery, and full suite remain outstanding. |

The outstanding rows are not inferred passes. Each requires execution on its
named platform.

## macOS credential evidence

The gate used a newly generated disposable `HERMES_HOME` and the
`ericsson-gitlab` plugin's `pat` field. Before writing anything, a direct
Keychain lookup proved that the derived account
`hermes-profile-bbdb376a682f9b6a3cf6bfee4a49d0c6652a655216e977a63014d1712a7d0670`
did not exist. The dummy value was generated in-process, was never printed or
passed on a command line, and did not overwrite a user credential.

- The production backend resolved to `os`, and the durable authority was
  `os`.
- The CLI invocation interpreter was
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python`.
- Production `PluginConfigurationService.update` and `detail` calls wrote and
  read the disposable value successfully. The detail API reported
  `is_set=true` without returning the value.
- The logical key
  `HERMES_PLUGIN_C1E0736797ECCC74564A133264A91695_PAT` was absent from both the
  disposable `.env` and `os.environ`. The dummy plaintext was absent from all
  profile files, including authority metadata.

An observer-only formatting error (`AttributeError`) occurred after the first
production write. No result was claimed from that attempt: the exact account
was immediately cleared, direct lookup proved it absent, and the complete
write/read/isolation check was rerun successfully from a clean precondition.

## Desktop and prompt behavior

The current desktop bundle was rebuilt and launched as the actual Electron app
against the same disposable profile. Using the real renderer, Settings →
Plugins showed `ericsson-gitlab`, `Personal access token`, `Required`, and
`Secret set`. The live result was observed twice, including a screenshot
inspection. No credential dialog appeared and no new `SecurityAgent` or
`CoreServicesUIAgent` process appeared, covering both the initial and repeated
read.

The desktop spawned exactly one headless backend with this interpreter and
command:

```text
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python -m hermes_cli.main serve --host 127.0.0.1 --port 0
```

Its environment contained the exact disposable `HERMES_HOME`. All processes
owned by that exact profile were stopped before the scheduler gate.

## Detached scheduler evidence

With no profile-owned GUI, backend, gateway, or ticker process running, the
production CLI created one due one-shot, no-agent job. An actual
`hermes cron tick` executed it; no direct/manual job execution was used.

- Job: `credential-storage-remediation-scheduler-gate` (`7a4755dce228`)
- Execution: `e9d985fee1e84f788cb276357d625704`
- Durable history source: `builtin`
- Completion time: `2026-08-16T18:41:00.977349-04:00`
- Result marker:
  `CREDENTIAL_GATE_OK backend=os interpreter=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python`

The scheduled script resolved the disposable credential through the
production `PluginConfigurationService.detail` path. It did not print the
credential and ran without a GUI.

## Recovery evidence

All recovery tests used only the disposable profile and credential account.
The initial drift combined an equal stale file-tier duplicate, an abandoned
authority temp file, a `0755` secrets directory, and `0644` secret files.

- `hermes secrets doctor` reported `ABANDONED_TEMP`, `PERMISSION_DRIFT`, and
  `STALE_DUPLICATE`.
- Default `hermes secrets repair` planned `CLEAN_ABANDONED_TEMP`,
  `DELETE_STALE_COPY`, and `REPAIR_PERMISSIONS` without mutation. A byte-and-
  mode snapshot remained exactly
  `764bb139ccc7ffd272edc0f90c885efa864f2b79c408479727df20c5e9d5c518`
  before doctor, after doctor, and after default repair.
- `repair --apply` performed the plan under the production lock. The directory
  became `0700`, secret files became `0600`, the stale duplicate and abandoned
  temp disappeared, and the next doctor run was clean.

For recoverable corruption, authority was moved to the file tier, a healthy
equal OS copy was retained, and the disposable file master key was corrupted.
Doctor reported `FILE_STORE_CORRUPT`; apply rebuilt the file store from the OS
copy and quarantined the corrupt encrypted store, key, and value-free manifest
under `20260816T224210.609662Z-26bb0cbe`. Doctor was clean afterward.

For an entirely unrecoverable disposable file tier, noninteractive
`--reset-unrecoverable --apply` without `--yes` failed with exit code 2 and the
required confirmation error. Its byte-and-mode snapshot remained
`b0ed1b5793d6ae6bb33ac5a9477276a5f4f7ef797dac5ab654faa927fde2e495`.
Repeating with `--yes` applied `RESET_UNRECOVERABLE`, set authority to
`cleared`, and quarantined the remaining encrypted store and manifest under
`20260816T224238.611266Z-1d26aa92`; it did not delete the earlier recovery
quarantine. The final doctor run was clean.

## Automated test evidence

The exact ten-file remediation suite passed:

```text
scripts/run_tests.sh tests/test_command_secret_source.py tests/test_bitwarden_secrets.py tests/hermes_cli/test_env_loader.py tests/hermes_cli/test_secret_authority.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_plugin_configuration_storage.py tests/hermes_cli/test_secrets_repair.py tests/hermes_cli/test_windows_permissions.py tests/hermes_cli/test_container_storage.py tests/hermes_cli/test_secrets_migrate.py -v
10 files, 380 passed, 0 failed in 17.2s (14 workers)
```

The exact real-startup regression passed:

```text
scripts/run_tests.sh tests/hermes_cli/test_env_loader.py::test_startup_scrubs_legacy_plugin_secrets_but_load_env_still_reads_them -v
1 file, 1 passed, 0 failed in 0.4s (14 workers)
```

The complete suite passed on the verified commit:

```text
scripts/run_tests.sh
2834 files, 33831 passed, 0 failed in 999.6s (14 workers)
```

Four files were retry-only flakes and passed on attempt 2:

- `tests/run_agent/test_primary_runtime_restore.py`
- `tests/plugins/workflow/test_performance_bounds.py`
- `tests/plugins/workflow/test_retry.py`
- `tests/scripts/test_workflow_upstream_merge.py`

Baseline `bce1f704` had 2,827 files and zero failures; the verified candidate
has seven additional test files and no additional failures.

## Blocked runs and reviewed fixes

The earlier gate at `511bf205edc4fd2935eb10cc26ab68a1dcfa02a2` was not
accepted: the repository suite exposed eight workflow-detail failures caused
by speculative configuration reads expanding and persisting profile defaults.
No platform verification record was committed from that run. The reviewed
corrections were:

- `6021ad3c0` — non-mutating mode reads
- `80ebda865` — malformed-config read side-effect suppression
- `3c85ae48b` — speculative-expansion warning suppression

The first Task 8 run at `8e169de170e125790c221dc79482f0f59b170a52`
also remained blocked. Its focused and startup gates passed, but the full suite
reported 33,818 passes and one deterministic authority-precedence failure in
964.4 seconds:

```text
tests/hermes_cli/test_plugin_setup_actions.py::test_connector_secret_authority_precedence_is_managed_scope_external_file
```

`tests/scripts/test_workflow_upstream_merge.py` was a retry-only flake during
that run. No platform action or documentation update followed the failed gate.
Commit `1ede2f267` (`fix: preserve external secret authority precedence`)
resolved the deterministic failure. Every automated and macOS platform gate
was then restarted and passed at the verified commit.

## Cleanup

After evidence capture, only gate-owned artifacts were removed:

- the exact job `7a4755dce228` was removed and the disposable registry reported
  no scheduled jobs;
- the production service cleared the exact Keychain account, followed by a
  direct absent lookup;
- the logical key was absent from the process environment;
- no process retained the exact disposable `HERMES_HOME`;
- desktop branding files generated by the build were restored to their exact
  pre-gate tracked state;
- generated `plugins/model-providers/otto` and the entire disposable root,
  including profile, screenshots, helper, scheduler output, and recovery
  quarantines, were moved to macOS Trash.

Three rejected cron-creation probes (absolute script path, unsupported `1s`
schedule, and stale past timestamp) produced no job and no run before the
successful gate.

A final negative check found one previously launched, detached
`gateway restart` process (plus its two children) still carrying the exact
disposable `HERMES_HOME`; it had recreated profile runtime state after the
first Trash move. Only those exact-profile processes were terminated, and the
recreated exact profile was moved separately to Trash.

Negative verification confirmed the original disposable profile path absent,
the exact Keychain account absent, zero exact-profile processes, no generated
provider directory, an empty disposable cron registry before profile removal,
and a tracked-clean checkout. Pre-existing unrelated untracked paths were not
modified.
