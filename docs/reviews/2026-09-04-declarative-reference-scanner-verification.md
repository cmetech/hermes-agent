# Task 6 verification report

Status: feature verification passes after one concrete integration-gate fix.
The complete repository suite remains red with 103 test failures and one
resource-exhaustion file; every one of those 103 exact failing node IDs is
reproduced at starting commit `74fed08f`, with the same underlying cause
families. No runtime scanner, parser, compiler, include, scheduler, Studio, or
other-worktree source was changed during verification.

## Revision and continuity

- Branch: `feat/workflow-reference-scanner-contract`
- HEAD during verification: `74fed08f91014ca7fc80ee9ea4427568ec95b04f`
- Starting commit: `74fed08f91014ca7fc80ee9ea4427568ec95b04f`
- `git diff --check`: exit 0 after the Task 6 gate correction.
- `git diff 74fed08f -- plugins/workflow/bash_rendering.py
  plugins/workflow/resources.py plugins/workflow/conditions.py
  plugins/workflow/schema.py`: 0 bytes, exit 0.
- `language_schema.py` AST audit: existing changed definitions are only
  `InterpolationSurfaceSpec`, `_require_contract_bounds`,
  `semantic_rule_descriptors`, and `workflow_authoring_contract`; added
  definitions are only `_contract_publication_limits`,
  `_reference_scanner_scope_policy`, `_uses_reference_scanner_contract`, and
  `reference_scanner_interpolation_surface`; no definition was removed. The
  diff adds inert inventory annotations, metadata projection, publication
  selection, reader/size limits, and derived semantic-rule paths. Existing
  scanner/candidate/parser function ASTs are unchanged.
- The exact v6 amendment test proves that definition schema, sidecar schema,
  editor projection version, normalizer version, and node kinds equal the
  captured v6 baseline. Its only allowed changed paths are reader version,
  scanner publication, digest, publication limits, and strict-reference field
  paths.

## Required test gates

All pytest execution used `scripts/run_tests.sh`.

The final required commands were:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_baselines.py tests/plugins/workflow/test_reference_scanner_contract.py tests/plugins/workflow/test_reference_scanner_conformance.py tests/plugins/workflow/test_language_conformance.py tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_phase6_language.py -q
scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_conformance.py -q -k supported_python_unicode_observation_matrix
scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -k 'scanner or corpus_resources' -q
scripts/run_tests.sh tests/plugins/workflow -q
scripts/run_tests.sh tests/scripts/test_workflow_merge_gate.py -q
scripts/run_tests.sh -q
```

| Gate | Result |
| --- | --- |
| Focused publication/baseline group: six files covering scanner baselines, contract, conformance, language publication, CLI limits, and Phase 6 language | exit 0; 506 passed, 0 failed; no skips or flaky files reported |
| Supported-Python matrix, explicit standalone selection | exit 0; 1 passed, 0 failed; no skips or flaky files reported |
| Explicit installed integration command, `-m integration -k 'scanner or corpus_resources'` | exit 0; 2 passed, 0 failed; no skips or flaky files reported |
| Full workflow suite after prerequisite repair | exit 0; 136 files, 6,305 passed, 0 failed, 44 skipped; no flaky files reported; 182.0 s |
| Workflow merge-gate file after its registration fix | exit 0; 51 passed, 0 failed; no skips or flaky files reported; 38.2 s |
| Complete repository suite after prerequisite repair | exit 1; 3,750 files, 51,850 passed, 103 failed, 657 skipped; 32 failing files plus one resource-exhaustion file; no flaky files reported; 730.3 s |

The 44 workflow skips are host markers: Linux-only and Windows-only tests are
skipped on this Darwin host and remain assigned to their CI lanes. The full
workflow run includes include-v4 and scheduler-v3 coverage and the supported
Python matrix.

The initial full workflow run finished with 6,289 passed, 0 failed, 42 skipped
but exit 1 because `test_local_handoff_e2e.py` and
`test_remote_handoff_e2e.py` could not collect without `aiohttp`. The exact
unchanged repository pin `aiohttp==3.14.3` was installed into this worktree's
real local `.venv`; the complete workflow rerun then passed. An offline cache
attempt was recorded first and failed honestly because that wheel was absent.

The first complete repository run, before the remaining optional prerequisites
were provisioned, ended at 51,654 passed, 173 failed, 660 skipped, with 44
failing files and 13 collection-error files. Its tracebacks named already
declared pins. The following exact repository versions were installed in the
same local `.venv`: `agent-client-protocol==0.9.0`, `daytona==0.155.0`,
`fal-client==0.13.1`, `parallel-web==0.4.2`,
`hindsight-client==0.6.1`, and `defusedxml==0.7.1`. Dependency declarations
were not changed. The authoritative complete-suite result is the post-repair
51,850/103/657 result above.

## Supported interpreter identities

The standalone matrix test created clean per-version environments, installed
its cached `pyyaml`/`jsonschema` prerequisites offline, executed the real
observation helper, checked each profile's applicable literal count, and
proved that observation did not rewrite the resource.

| Interpreter | Unicode database | Published profile identity |
| --- | --- | --- |
| CPython 3.11.16 | 14.0.0 | `python-3.11-unicode-14.0.0` |
| CPython 3.12.14 | 15.0.0 | `python-3.12-unicode-15.0.0` |
| CPython 3.13.15 | 15.1.0 | `python-3.13-unicode-15.1.0` |

## Publication and artifact measurements

All values below were recalculated from the final working tree, not copied
from an earlier task report.

### Immutable historical authoring pairs

| Artifact | Canonical bytes | SHA-256 | Baseline equality |
| --- | ---: | --- | --- |
| `legacy-1.json` | 227,906 | `cfe6d0e58c4589b3f30292a2676dfa2ed3f99c68b0e06ab9db6ff1aa28a78446` | exact |
| `legacy-2.json` | 227,906 | `e42f3403329a1513d1a5c857c6367a212ae00bc911701f5c7a14a3d69fac1e45` | exact |
| `archon-1.json` | 224,201 | `de20da803ed6b073565b768d13cc5d98af07056a2a71cbf23f0738f58b08f1b3` | exact |
| `archon-2.json` | 224,201 | `2b82101f8d7df05658758cca9fc7a953f2bd94d25df492d9a76d7081542f5751` | exact |
| `archon-3.json` | 236,325 | `2950684e6bfe2f5192177809140b99149e4ef1a6471ed3a9b7cf201d98a48899` | exact |
| `archon-4.json` | 242,648 | `be44ec97cc010d25cc00bde43d54c5da948023a247be24c974a0936d9f183959` | exact |
| `archon-5.json` | 243,698 | `cea17e92c695125c889e4d6e859bcaee08381c7a58694f35873a6872d518aec1` | exact |

The captured Archon v6 characterization baseline remains 283,522 bytes with
SHA-256
`171fe68bc4c8c4e5ca8a27be7212d8af556c263739f37a17235344a8e57717cc`;
the amendment is constrained by the allowed-path test described above.

### Current publications

| Profile/publication | Canonical bytes | Pretty bytes | Canonical SHA-256 |
| --- | ---: | ---: | --- |
| legacy schema | 227,906 | 502,879 | `e42f3403329a1513d1a5c857c6367a212ae00bc911701f5c7a14a3d69fac1e45` |
| legacy corpus | 7,265 | 8,927 | `c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b` |
| Archon schema | 315,756 | 663,582 | `61221e6d0bdff9a2922ce8f11e9558c4a39f1872a60ae70324acd14dbc140ff4` |
| Archon corpus | 200,496 | 255,731 | `4edad4397a35f5d437e59eed71c74988e3825df6cdcef387703740b6171fa306` |

- Legacy corpus: format 1, 11 cases, exact fixture equality, 152,735 bytes
  below its 160,000-byte bound, and no corpus digest/extension sections.
- Archon schema: 12,244 bytes total headroom under 328,000 and 8,244 bytes
  usable headroom after the required 4,000-byte reserve.
- `reference_scanner_v1`: 31,879 canonical bytes, SHA-256
  `7e7af0d64e8825effbfeb0f6945d6cf51d9338e5d88221e034c0475913200a7b`,
  and 121 bytes headroom under its 32,000-byte section bound.
- Archon corpus counts/headroom: workflows 54/10, scanner 132/124,
  substitution 23/41, structured paths 29/35.
- Archon corpus byte headroom: 183,504 canonical bytes under 384,000 and
  512,269 pretty bytes under 768,000.
- Legacy contract digest:
  `sha256:65a4574abc9dadfaea7c1ef9df7a00e3782c07de0b7581c98719894b7aee152d`.
- Archon contract digest:
  `sha256:02830acc6e3c797cec14e7debc3b2d15f91eca514bb9d2ffe31bd3b74e2549ed`.
- Archon corpus digest:
  `sha256:df14cc2dc8adec213e98a126360943b9b831426ef43304632a7582288ecd6384`.
- Literal scanner resource: 105,623 raw bytes, raw SHA-256
  `08ec47fef16c70aac38fe09a5df1b26045e434432772594862e54f3314d3d76e`;
  61,895 canonical bytes, canonical SHA-256
  `8e9d4588b3642d48fd8cd51bbdfcbc96b216d211b1254b001f51f5f287c3f949`.
- Case manifest: 72,915 raw bytes, SHA-256
  `cc36f1f9c448c03923e7a5b5a158846d61366ea9f48705c91414f46350904625`.

The explicit installed integration test independently proves direct-wheel,
sdist, and sdist-built-wheel resource byte equality; real installs of both
wheel origins; source/install compact and pretty parity for schema and corpus
under both profiles; installed module/resource origins outside the checkout;
and successful console execution with a demonstrated socket-denial guard and
`PYTHONPATH` absent.

## Feature regression found and resolved

The first repository run found
`tests/scripts/test_workflow_merge_gate.py::test_base_gate_executes_the_release_contract_through_fixture_commands`.
The gate's exhaustive base-phase inventory omitted the three new test files:

- `tests/plugins/workflow/test_reference_scanner_baselines.py`
- `tests/plugins/workflow/test_reference_scanner_contract.py`
- `tests/plugins/workflow/test_reference_scanner_conformance.py`

The correction adds exactly those three paths to
`scripts/test_workflow_merge_gate.sh`. The targeted RED was 1 failed/50
deselected; targeted GREEN was 1 passed; the complete affected file is now 51
passed, and `bash -n` plus `git diff --check` pass. The second complete
repository run began before this three-line correction but had not reached the
scripts section; it executed the corrected gate successfully. The fresh
standalone 51-test run is the definitive post-fix evidence.

## Remaining complete-suite failures and baseline comparison

The post-prerequisite complete suite has 103 failing node IDs in 32 files.
The exact list is preserved below. A temporary
source archive of exact starting commit `74fed08f` was run with the same local
Python and canonical wrapper, limited to those 32 files plus the resource-error
file. None of the 32 failing test files is in the feature diff. The baseline
run produced 1,739 passed, 113 failed, 186 skipped, the same 32 failing files,
and the same resource-error file. Set comparison proves:

- current failing node IDs: 103;
- baseline failing node IDs: 113;
- exact intersection: all 103 current node IDs;
- current-only node IDs: 0;
- archive-only node IDs: 10, all in the already-failing update tests because
  a plain archive has no Git repository metadata.

To remove that archive-context ambiguity, a second independent archive was
initialized as its own temporary Git repository, without copying/linking the
feature checkout's `.git` or touching shared refs. Running
`test_cmd_update.py` and `test_update_check.py` reproduced all 13 current
failure node IDs in those files with the same control-flow/mock-command causes;
one baseline-only expired-cache test remained. Both temporary baseline trees
were removed after capture.

The persistent failures are grouped below. Every failing node ID is enumerated verbatim below; this table summarizes the compared current and baseline causes.

| File (failed tests) | Observed cause, reproduced at `74fed08f` |
| --- | --- |
| `tests/agent/test_prompt_cache_ttl_propagation.py` (1) | source-shape assertion finds no `_try_activate_fallback` sites |
| `tests/agent/test_surrogate_chokepoints.py` (1) | expected source substring is absent (`ValueError`) |
| `tests/computer_use/test_cua_no_overlay.py` (1) | macOS `CuaDriver.app` prerequisite is absent |
| `tests/cron/test_cron_profile_isolation.py` (1) | fake agent rejects the current `task_id` call shape, leaving the asserted completion false |
| `tests/cron/test_cron_direct_api_call_watchdog.py` (12) | cost-budget poison/timeout contract assertions fail, led by `CostBudgetPoisoned` |
| `tests/gateway/test_buzz_adapter.py` (1) | Darwin raises `ENAMETOOLONG` while constructing the deliberately deep redaction path |
| `tests/hermes_cli/test_computer_use_cli.py` (5) | missing/incompatible computer-use driver status and install return-code expectations disagree |
| `tests/hermes_cli/test_early_recovery.py` (1) | `cryptography` is already loaded before the asserted early-install boundary |
| `tests/hermes_cli/test_dashboard_admin_endpoints.py` (1) | startup orphan-reaper is observed twice instead of once |
| `tests/hermes_cli/test_cmd_update.py` (12) | update branch/migration/profile/npm mock-control expectations end in unexpected `SystemExit`, missing exit, or output mismatch |
| `tests/hermes_cli/test_kanban_review_lifecycle_complete.py` (3) | exhausted mocked sequence and stale mutation conflict |
| `tests/hermes_cli/test_model_switch_openai_api_mode.py` (1) | provider identity is `meta-ai`, expected `meta` |
| `tests/hermes_cli/test_model_alias_credentials_83612.py` (4) | alias `theta` is missing or resolves to the OpenRouter fallback |
| `tests/hermes_cli/test_sessions_pin.py` (1) | partial-missing pin operation returns 0, expected 1 |
| `tests/hermes_cli/test_uninstall_cleanup.py` (1) | Windows bin-path marker assertion rejects the returned path list |
| `tests/hermes_cli/test_update_check.py` (1) | mock receives unexpected `git merge-base --is-ancestor` command |
| `tests/hermes_cli/test_update_launchd_fleet_restart.py` (2) | incomplete-restart hints do not contain the expected launchctl/systemctl text |
| `tests/hermes_cli/test_update_secret_import_lock.py` (1) | update path imports `cryptography` unexpectedly |
| `tests/hermes_cli/test_update_self_lock.py` (1) | subprocess proves `cryptography._rust` was eagerly loaded |
| `tests/hermes_cli/test_update_yes_flag.py` (3) | update prompt/migration paths raise unexpected `SystemExit` |
| `tests/hermes_cli/test_windows_standard_user_secret_gate_contract.py` (6) | expected `.github/workflows/ci.yml` is absent in both feature and baseline source trees |
| `tests/run_agent/test_continuation_ceiling_wedge.py` (1) | continuation-mark expectation is false |
| `tests/run_agent/test_plugin_stream_hooks.py` (1) | test callback does not accept the current `endpoint_url` keyword |
| `tests/scripts/test_windows_footguns_full_repo_scan.py` (1) | 97 preexisting Windows-footgun findings; failure output contains no reference-scanner path |
| `tests/state/test_no_locked_readers_gate.py` (1) | preexisting `get_existing_session_conversation` writer-lock finding |
| `tests/test_guest_durability_barriers.py` (1) | synchronous barrier call count is 2, expected 1 |
| `tests/test_install_npm_deps_gate.py` (1) | expected root marker is not written after the successful npm branch |
| `tests/skills/test_authoring_standards.py` (20) | preexisting skills lack required frontmatter and/or exceed the 60-character description hardline |
| `tests/test_moa_prepared_request_leak_78382.py` (2) | cost-budget poison triggers before the asserted request-key behavior |
| `tests/test_plugin_skills.py` (13) | expected plugin skills/cache fields are absent (`linked_files`, `readiness_status`, provenance/guard messages) |
| `tests/test_lazy_secrets_dispatch.py` (1) | subprocess observes `cryptography._rust` before update dispatch |
| `tests/tools/test_terminal_bounded_execute.py` (1) | test `_wait` fake lacks the current `control_sentinel` keyword |

`tests/gateway/test_api_server_runs.py` is reported separately because the
parallel wrapper cannot produce a normal test count after the subprocess
exhausts file descriptors. The trace reaches 49 passes followed by one failure
and seven errors, then pytest cleanup raises `OSError: [Errno 24] Too many open
files`. A fresh standalone canonical-wrapper run reproduces the same condition
and exits 1; the starting-commit archive does too. This is an independently
established environment/resource failure, not a workflow feature regression.

## Evidence provenance

The controller inspected the canonical-wrapper summaries, exact failing-node set comparison, runtime diff, and artifact measurements. The original execution logs reside in this plan's local SDD verification workspace. This committed record preserves the results and causes; the exact current failure identifiers below remain available independently of scratch cleanup.

Workflow Studio acceptance requirements S8/S9/S10 remain explicitly deferred.
Hermes tests do not prove single indexing, the 250-node/500-edge canvas bound,
or the UTF-16 editor boundary. No Studio source was inspected or changed as
part of this verification.

## Exact current failure identifiers reproduced at the starting commit

```text
FAILED tests/agent/test_prompt_cache_ttl_propagation.py::TestFailoverRestartsPreflight::test_every_fallback_activation_restarts_preflight
FAILED tests/agent/test_surrogate_chokepoints.py::test_conversation_loop_sanitizes_api_kwargs_after_build
FAILED tests/computer_use/test_cua_no_overlay.py::TestEmbeddedDaemonOverlayFlag::test_serve_process_disables_overlay_when_policy_requires_it
FAILED tests/cron/test_cron_profile_isolation.py::test_cron_connector_uses_ambient_profile_and_denies_interactive_approval
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_stalled_inline_call_is_aborted_and_raises_retryable_timeout
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_watchdog_abort_never_surfaces_as_interrupted_error
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_watchdog_kill_feeds_the_cross_turn_stale_circuit_breaker
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_retry_after_a_watchdog_kill_gets_a_fresh_pool_and_succeeds
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_healthy_call_is_untouched_by_the_watchdog
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_local_endpoint_infinite_budget_leaves_the_watchdog_disarmed
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_watchdog_uses_the_same_budget_as_the_interrupt_worker_path
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_interrupt_abort_is_not_misclassified_as_provider_staleness
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_late_stale_timer_after_completion_is_inert
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_inline_call_passes_hard_read_timeout_to_the_sdk
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_inline_call_does_not_override_explicit_timeout
FAILED tests/cron/test_cron_direct_api_call_watchdog.py::test_infinite_budget_does_not_inject_a_hard_timeout
FAILED tests/gateway/test_buzz_adapter.py::TestInboundMediaAuthorizationGate::test_live_media_redacts_long_path_before_bounding
FAILED tests/hermes_cli/test_computer_use_cli.py::test_computer_use_status_returns_nonzero_when_driver_is_missing
FAILED tests/hermes_cli/test_computer_use_cli.py::test_computer_use_status_returns_nonzero_for_incompatible_standard_driver
FAILED tests/hermes_cli/test_computer_use_cli.py::test_computer_use_status_returns_nonzero_for_incompatible_custom_driver
FAILED tests/hermes_cli/test_computer_use_cli.py::test_computer_use_install_checks_resulting_runtime_contract[False-1]
FAILED tests/hermes_cli/test_computer_use_cli.py::test_computer_use_install_returns_nonzero_for_unrepairable_custom_override
FAILED tests/hermes_cli/test_early_recovery.py::test_core_marker_triggers_install_before_any_native_import
FAILED tests/hermes_cli/test_dashboard_admin_endpoints.py::test_desktop_lifespan_reaps_orphan_gateways_on_startup
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateBranchFallback::test_update_defaults_to_otto_from_feature_branch
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateBranchFallback::test_yes_on_fork_without_upstream_does_not_claim_up_to_date
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateBranchFallback::test_fork_upstream_sync_that_moves_head_runs_post_update_steps
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateBranchFallback::test_update_non_interactive_runs_safe_config_migrations
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateMigrationPrompt::test_version_bump_only_applies_silently_without_prompt
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateMigrationPrompt::test_version_bump_only_surfaces_migration_resets
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateMigrationPrompt::test_new_options_are_listed_by_name_before_prompt
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateProfileSkillSync::test_active_profile_included_in_skill_sync
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateProfileSkillSync::test_single_profile_default_is_synced
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateBranchFlag::test_branch_flag_switches_from_different_branch
FAILED tests/hermes_cli/test_cmd_update.py::TestCmdUpdateBranchFlag::test_branch_flag_tracks_remote_when_branch_absent_locally
FAILED tests/hermes_cli/test_cmd_update.py::TestNodeRuntimeNpmResolution::test_wsl_update_skips_windows_npm_build_paths
FAILED tests/hermes_cli/test_kanban_review_lifecycle_complete.py::test_interrupted_review_runs_retry_in_review_phase[spawn_failure]
FAILED tests/hermes_cli/test_kanban_review_lifecycle_complete.py::test_interrupted_review_runs_retry_in_review_phase[stale_heartbeat]
FAILED tests/hermes_cli/test_kanban_review_lifecycle_complete.py::test_goal_run_status_is_bound_to_original_run
FAILED tests/hermes_cli/test_model_switch_openai_api_mode.py::test_stale_chat_overridden_on_meta_direct
FAILED tests/hermes_cli/test_model_alias_credentials_83612.py::TestDirectAliasCredentialLoading::test_api_key_and_key_env_are_loaded_from_config
FAILED tests/hermes_cli/test_model_alias_credentials_83612.py::TestModelSwitchUsesAliasCredential::test_alias_api_key_is_sent_to_alias_host
FAILED tests/hermes_cli/test_model_alias_credentials_83612.py::TestAliasCacheIsProfileScoped::test_second_profile_does_not_inherit_the_first_profiles_key
FAILED tests/hermes_cli/test_model_alias_credentials_83612.py::TestAliasCacheIsProfileScoped::test_key_rotation_in_place_is_picked_up
FAILED tests/hermes_cli/test_sessions_pin.py::test_pin_multiple_ids_one_missing
FAILED tests/hermes_cli/test_uninstall_cleanup.py::test_bin_is_a_path_marker
FAILED tests/hermes_cli/test_update_check.py::test_check_for_updates_official_ssh_origin_uses_https_probe
FAILED tests/hermes_cli/test_update_launchd_fleet_restart.py::TestIncompleteWarningMentionsLaunchctl::test_launchd_labels_get_launchctl_hint
FAILED tests/hermes_cli/test_update_launchd_fleet_restart.py::TestIncompleteWarningMentionsLaunchctl::test_systemd_units_keep_systemctl_hint
FAILED tests/hermes_cli/test_update_secret_import_lock.py::test_complete_update_dispatch_does_not_import_cryptography
FAILED tests/hermes_cli/test_update_self_lock.py::TestUpdateEntrypointImportHygiene::test_update_dispatch_does_not_load_cryptography
FAILED tests/hermes_cli/test_update_yes_flag.py::TestUpdateYesConfigMigration::test_yes_auto_migrates_without_input
FAILED tests/hermes_cli/test_update_yes_flag.py::TestUpdateYesConfigMigration::test_no_yes_flag_still_prompts_in_tty
FAILED tests/hermes_cli/test_update_yes_flag.py::TestUnicodeDecodeErrorInUpdatePrompts::test_unicode_decode_error_in_tty_skips_and_prints_hint
FAILED tests/hermes_cli/test_windows_standard_user_secret_gate_contract.py::test_workflow_gates_native_job_by_python_lane_and_aggregates_its_result
FAILED tests/hermes_cli/test_windows_standard_user_secret_gate_contract.py::test_aggregate_requires_native_success_for_python_relevant_runs[affected-success]
FAILED tests/hermes_cli/test_windows_standard_user_secret_gate_contract.py::test_aggregate_requires_native_success_for_python_relevant_runs[unaffected-skip]
FAILED tests/hermes_cli/test_windows_standard_user_secret_gate_contract.py::test_aggregate_requires_native_success_for_python_relevant_runs[affected-skip]
FAILED tests/hermes_cli/test_windows_standard_user_secret_gate_contract.py::test_aggregate_requires_native_success_for_python_relevant_runs[affected-failure]
FAILED tests/hermes_cli/test_windows_standard_user_secret_gate_contract.py::test_aggregate_requires_native_success_for_python_relevant_runs[affected-cancellation]
FAILED tests/run_agent/test_continuation_ceiling_wedge.py::TestContinuationCeilingWedge::test_continuation_requests_carry_no_marks
FAILED tests/run_agent/test_plugin_stream_hooks.py::test_bedrock_reasoning_delta_reaches_plugin_only_observer
FAILED tests/scripts/test_windows_footguns_full_repo_scan.py::test_full_repo_scan_has_no_unsuppressed_windows_footguns
FAILED tests/state/test_no_locked_readers_gate.py::TestNoPureReadersUnderWriterLock::test_no_locked_pure_readers
FAILED tests/test_guest_durability_barriers.py::test_guest_barriers_leave_synchronous_alone_when_unset
FAILED tests/test_install_npm_deps_gate.py::test_install_sh_wires_gate_at_both_npm_sites
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/ericsson/confluence-research]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/ericsson/gitlab]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/ericsson/jira]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/ericsson/jira-to-gitlab]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/ericsson/onboard-ericsson-capabilities]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/ericsson/opportunity-visuals]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/ericsson/sharepoint]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/gateway-toolcall-parity]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/productivity/workflow]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/productivity/workflow-showcase]
FAILED tests/skills/test_authoring_standards.py::test_required_frontmatter_fields[skills/software-development/workflow-builder]
FAILED tests/skills/test_authoring_standards.py::test_description_hardline[skills/ericsson/confluence-research]
FAILED tests/skills/test_authoring_standards.py::test_description_hardline[skills/ericsson/gitlab]
FAILED tests/skills/test_authoring_standards.py::test_description_hardline[skills/ericsson/jira]
FAILED tests/skills/test_authoring_standards.py::test_description_hardline[skills/ericsson/jira-to-gitlab]
FAILED tests/skills/test_authoring_standards.py::test_description_hardline[skills/ericsson/sharepoint]
FAILED tests/skills/test_authoring_standards.py::test_description_hardline[skills/gateway-toolcall-parity]
FAILED tests/skills/test_authoring_standards.py::test_description_hardline[skills/productivity/workflow]
FAILED tests/skills/test_authoring_standards.py::test_description_hardline[skills/productivity/workflow-showcase]
FAILED tests/skills/test_authoring_standards.py::test_description_hardline[skills/software-development/workflow-builder]
FAILED tests/test_moa_prepared_request_leak_78382.py::test_moa_key_stripped_from_native_client
FAILED tests/test_moa_prepared_request_leak_78382.py::test_no_moa_key_when_absent
FAILED tests/test_plugin_skills.py::TestSkillViewQualifiedName::test_resolves_plugin_skill
FAILED tests/test_plugin_skills.py::TestSkillViewQualifiedName::test_reads_supporting_file_with_containment
FAILED tests/test_plugin_skills.py::TestSkillViewQualifiedName::test_platform_gate_applies_before_supporting_file
FAILED tests/test_plugin_skills.py::TestSkillViewQualifiedName::test_rejects_supporting_file_escape
FAILED tests/test_plugin_skills.py::TestSkillViewQualifiedName::test_plugin_skill_usage_reports_installed_provenance
FAILED tests/test_plugin_skills.py::TestSkillViewQualifiedName::test_plugin_exists_but_skill_missing
FAILED tests/test_plugin_skills.py::TestSkillViewQualifiedName::test_stale_entry_self_heals
FAILED tests/test_plugin_skills.py::TestSkillViewPluginGuards::test_disabled_plugin
FAILED tests/test_plugin_skills.py::TestSkillViewPluginGuards::test_platform_mismatch
FAILED tests/test_plugin_skills.py::TestSkillViewPluginGuards::test_injection_logged_but_served
FAILED tests/test_plugin_skills.py::TestBundleContextBanner::test_banner_present
FAILED tests/test_plugin_skills.py::TestBundleContextBanner::test_banner_lists_siblings_not_self
FAILED tests/test_plugin_skills.py::TestBundleContextBanner::test_original_content_preserved
FAILED tests/test_lazy_secrets_dispatch.py::TestUpdatePathE2E::test_main_update_check_crypto_absent_in_sys_modules
FAILED tests/tools/test_terminal_bounded_execute.py::test_execute_parent_interrupt_still_kills_wait_on_deadline_worker
```
