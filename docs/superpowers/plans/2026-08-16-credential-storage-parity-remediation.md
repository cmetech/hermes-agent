# Credential Storage Parity Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the final credential-exposure, revocation, recovery, filesystem, container, dry-run, and dependency-integrity gaps before pushing the Hermes credential-storage feature branch.

**Architecture:** A durable per-key authority registry makes `os`, `file`, and `cleared` states authoritative across restarts; mutations use the existing profile transaction lock and never demote after an OS timeout. A read-only doctor and explicitly mutating repair CLI diagnose, reconcile, move, quarantine, or reset stores without manual file deletion. Shared child-environment filtering, Windows ACL enforcement, and container mount inspection provide the remaining security boundaries.

**Tech Stack:** Python 3.11, `keyring`, `cryptography` AES-GCM, stdlib file locking and atomic replacement, PowerShell ACL APIs on Windows, argparse, pytest through `scripts/run_tests.sh`, uv lockfiles.

## Global Constraints

- Work only on `feat/hermes-credential-storage-parity`; never develop on literal `main` and never merge this branch into `base` in this session.
- Preserve every unrelated untracked file. Stage only the files named by the active task.
- Use `scripts/run_tests.sh` for every Python test invocation; never invoke pytest directly.
- Follow RED/GREEN TDD exactly and confirm each RED fails for the behavior the task adds before changing production code.
- No plugin PAT may remain in `os.environ` or enter any unrelated child-process environment.
- Never silently fall back to plaintext `.env`; preserve legacy `load_env()` resolution until explicit migration.
- OS-keystore probes and reads are bounded; OS `set` and `delete` remain synchronous because mutations cannot be cancelled safely.
- Keep fake keyrings and patched environments installed until abandoned probe/read workers finish.
- The encrypted-file tier keeps a complete process and cross-process transaction lock with atomic replacement.
- Read-only operations create no profile directories, files, locks, keychain items, or metadata.
- Ambiguous authority, competing secret values, unresolved persistence, ACL failure, or partial revocation fails explicitly.
- Secret values never appear in CLI output, logs, findings, repair plans, assertions, or committed fixtures.
- Every task ends with a separate commit and a specification-compliance review followed by a code-quality review before the next task begins.

## File and Interface Map

- `hermes_cli/plugin_secret_keys.py`: exact plugin-secret predicate and environment-copy filter shared by every child spawn.
- `hermes_cli/secret_authority.py`: versioned authority registry, tombstones, corruption errors, atomic metadata replacement; no backend selection.
- `hermes_cli/secret_keystore.py`: backend I/O, profile lifecycle, transaction coordination, authority-aware read/write/delete/move facade.
- `hermes_cli/secrets_repair.py`: read-only diagnosis, deterministic repair plans, quarantine/reset application, and CLI handlers.
- `hermes_cli/windows_permissions.py`: stdlib-only current-user Windows DACL application and inspection.
- `hermes_cli/container_storage.py`: container detection and uncached mount-persistence evidence.
- `hermes_cli/config.py` and `hermes_cli/env_loader.py`: real `.env` replacement boundaries and stable persistence errors.
- `hermes_cli/plugin_configuration.py`: static logical-key inventory and legacy `.env` clear orchestration.
- `hermes_cli/main.py`: `hermes secrets doctor` and `hermes secrets repair` parser registration.

---

### Task 1: Keep plugin secrets out of every helper environment

**Files:**
- Modify: `hermes_cli/plugin_secret_keys.py`
- Modify: `agent/secret_sources/bitwarden.py`
- Modify: `agent/secret_sources/command.py`
- Modify: `tests/test_command_secret_source.py`
- Modify: `tests/test_bitwarden_secrets.py`
- Modify: `tests/hermes_cli/test_env_loader.py`

**Interfaces:**
- Produces: `without_plugin_secret_keys(environment: Mapping[str, str]) -> dict[str, str]`.
- Consumes later: Task 4 uses the same helper for the Windows PowerShell ACL child.

- [ ] **Step 1: Add failing environment-isolation regressions**

Add a real command-helper test whose parent environment contains a valid plugin
storage key and whose `/bin/sh` child prints only whether the key exists:

```python
def test_real_helper_never_inherits_plugin_secret(monkeypatch):
    key = "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"
    monkeypatch.setenv(key, "legacy-pat")
    observed = _run_helper(
        f'if [ -n "${{{key}+present}}" ]; then printf present; else printf absent; fi',
        "REQUESTED_KEY",
        1.0,
        64,
    )
    assert observed == "absent"
```

Add Bitwarden tests that capture `env` for `_run_bws_list()` and the Linux
`ldd --version` call and assert that exact plugin keys are absent while
`BWS_ACCESS_TOKEN`, ordinary non-secret variables, and profile-home behavior
remain correct. Extend the real startup regression to assert the helper saw
`absent` before `load_hermes_dotenv()` returned.

- [ ] **Step 2: Run the RED tests**

Run:

```bash
scripts/run_tests.sh tests/test_command_secret_source.py tests/test_bitwarden_secrets.py tests/hermes_cli/test_env_loader.py -v
```

Expected: the new helper and Bitwarden assertions fail because the children
currently receive copies of the unsanitized source environment.

- [ ] **Step 3: Implement one exact-key environment filter and use it at all three spawn sites**

Add this public helper beside `is_plugin_secret_key()`:

```python
def without_plugin_secret_keys(
    environment: Mapping[str, str],
) -> dict[str, str]:
    return {
        key: value
        for key, value in environment.items()
        if not is_plugin_secret_key(key)
    }
```

In command and Bitwarden source code, filter the copied source environment
immediately before adding the one credential the helper intentionally needs.
Pass a filtered `env=` to Bitwarden's Linux `ldd` call instead of inheriting
`os.environ`. Do not move the final startup scrub or change secret-source
precedence.

- [ ] **Step 4: Run focused and guard suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/test_command_secret_source.py tests/test_bitwarden_secrets.py tests/test_env_loader_secret_sources.py tests/agent/test_subprocess_env_guard.py tests/hermes_cli/test_env_loader.py -v
```

Expected: all pass; the real child sees no plugin key and the final process
environment remains scrubbed.

- [ ] **Step 5: Commit Task 1**

```bash
git add hermes_cli/plugin_secret_keys.py agent/secret_sources/bitwarden.py agent/secret_sources/command.py tests/test_command_secret_source.py tests/test_bitwarden_secrets.py tests/hermes_cli/test_env_loader.py
git commit -m "fix: isolate plugin secrets from helper processes"
```

---

### Task 2: Add durable authority and non-resurrecting revocation

**Files:**
- Create: `hermes_cli/secret_authority.py`
- Modify: `hermes_cli/secret_keystore.py`
- Modify: `hermes_cli/plugin_configuration.py`
- Create: `tests/hermes_cli/test_secret_authority.py`
- Modify: `tests/hermes_cli/test_secret_keystore.py`
- Modify: `tests/hermes_cli/test_plugin_configuration_storage.py`

**Interfaces:**
- Produces: `SecretAuthority`, a `str` enum with `OS`, `FILE`, and `CLEARED` states.
- Produces: `load_authority_registry(root: Path) -> AuthorityRegistry | None` and `encode_authority_registry(registry: AuthorityRegistry) -> bytes`; `secret_keystore` owns locked atomic replacement.
- Produces: `resolve_secret(key: str, *, legacy_value: str | None = None) -> str | None`, `get_authority(key: str) -> SecretAuthority | None`, `move_secret(key: str, destination: Literal["os", "file"]) -> None`, and authority-aware existing facade functions.
- Consumes: existing `_store_lock()`, `OSKeystore`, `FileKeystore`, `_resolve_mode()`, and atomic private writes.
- Consumed later: Task 3 diagnosis and repair APIs.

- [ ] **Step 1: Write authority-registry RED tests**

Cover a versioned JSON document and strict corruption behavior:

```python
def test_authority_registry_round_trip_is_atomic_and_read_only(tmp_path):
    root = tmp_path / "secrets"
    assert load_authority_registry(root) is None
    assert not root.exists()
    root.mkdir()
    registry = AuthorityRegistry(
        version=1,
        entries={"K": SecretAuthority.OS, "OLD": SecretAuthority.CLEARED},
    )
    (root / "authority.json").write_bytes(encode_authority_registry(registry))
    assert load_authority_registry(root) == registry

def test_corrupt_authority_registry_fails_closed(tmp_path):
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "authority.json").write_text('{"version":1,"entries":{"K":"bogus"}}')
    with pytest.raises(AuthorityRegistryError):
        load_authority_registry(root)
```

Assert replacement failure preserves the previous complete registry and leaves
no secret value in the metadata.

- [ ] **Step 2: Write backend-transition and service RED tests**

Add executable sequences for:

```python
def test_off_mode_delete_is_an_explicit_failure(monkeypatch):
    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "off")
    reset_backend_cache()
    with pytest.raises(KeystoreError, match="disabled"):
        delete_secret(KEY)
```

Also cover file authority remaining stable when OS later becomes healthy,
equal and differing pre-registry copies, unknown file data with unavailable OS
failing closed, mode mismatch on mutation, multi-key compensation, and
OS-to-file/file-to-OS move failure at write, verify, delete, and metadata
commit boundaries.

At the service layer, change the previous “keep both copies intact” expectation:
legacy plaintext is removed first, a later keystore refusal returns
`PluginConfigurationError`, and retry completes revocation without restoring
plaintext.

- [ ] **Step 3: Run the authority RED tests**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_secret_authority.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_plugin_configuration_storage.py -v
```

Expected: import/behavior failures show that no registry, tombstone, move API,
or durable authority exists and that OS timeouts still demote to file.

- [ ] **Step 4: Implement the registry**

Create `secret_authority.py` as a pure, read-only schema module with these
definitions and canonical encoding rules:

```python
class SecretAuthority(str, Enum):
    OS = "os"
    FILE = "file"
    CLEARED = "cleared"

class AuthorityRegistryError(ValueError):
    pass

@dataclass(frozen=True)
class AuthorityRegistry:
    version: int
    entries: Mapping[str, SecretAuthority]

def encode_authority_registry(registry: AuthorityRegistry) -> bytes:
    payload = {
        "version": registry.version,
        "authorities": {
            key: state.value for key, state in sorted(registry.entries.items())
        },
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
```

`load_authority_registry()` returns `None` without touching an absent root. It
uses a duplicate-rejecting JSON object-pairs hook and accepts only the exact
top-level fields, version `1`, nonempty string keys, and valid states. It rejects
extra fields and never filters malformed entries. `secret_keystore` writes the
encoded bytes using its existing private atomic writer while holding
`keystore.lock`, so flush, `fsync`, replacement, permissions, and temp cleanup
remain one transaction boundary.

- [ ] **Step 5: Implement authority-aware coordination**

Refactor the facade, not the backend primitives. Add the exact public
signatures listed in this task's Interfaces block and keep `get_secret(key)` as
the compatibility wrapper `resolve_secret(key)`.

Use the existing profile root and `_store_lock()` as the cross-process
transaction boundary for backend changes plus registry replacement. Add
unlocked file helpers so the coordinator does not recursively reacquire the
file lock. Keep OS set/delete synchronous. `_mark_os_unhealthy()` now only
latches health; it never replaces `state.backend`.

Give `_store_lock()` an explicit non-creating read mode. Registered/file reads
may open the already-created lock, but a missing lock beside existing metadata
is corruption and fails closed for ordinary resolution; `doctor` reports it.
No get, resolve, or authority inspection may create the root, lock, or registry.

Registered reads use only their authority; `cleared` returns `None`. `off`
returns absent for reads but raises for every mutation. New writes resolve
pre-registry state, write and verify the selected tier, then commit authority.
Moves use destination write/verify, source delete/absence, registry commit,
with compensation and an “outcome uncertain” error if rollback fails. Batch
writes snapshot all affected values and authority states before changing any
key and restore both on failure.

Use a presence-aware live lookup before calling
`resolve_secret(storage_key, legacy_value=plaintext.get(storage_key))`.
Treat live and durable authority as separate precedence layers: managed env >
installed secret scope > external secret source > durable `os`/`file`/`cleared`
authority > legacy plaintext compatibility. Mapping presence, not truthiness,
establishes live authority. Therefore an explicitly present empty string from
managed env, secret scope, or an external source suppresses every lower layer;
it never consults durable storage or legacy plaintext. An empty legacy
plaintext value is not authority and remains equivalent to absent compatibility
data. Registered `os`/`file` authority ignores a legacy value, `cleared`
returns absent without consulting it, and only an unregistered key preserves
legacy compatibility. Replace existing “legacy always wins” tests with
separate live-override, unregistered-compatibility, and
registered/tombstone-authority contracts.

- [ ] **Step 6: Make service clear retryable and normalize every ordinary failure**

Make `secret_keystore.delete_secret()` own legacy `.env` removal inside the
profile transaction through a local import of `remove_env_value()` with
`strict=True` and `mirror_process_env=False`. It removes plaintext first,
deletes and verifies the authoritative copy, then commits `cleared`. It catches
`Exception`, never `BaseException`, and normalizes to `KeystoreError`; it never
restores plaintext as compensation. `PluginConfigurationService.clear_secret()`
becomes one facade call and translates only `KeystoreError` to its stable
`PluginConfigurationError`.

- [ ] **Step 7: Run focused and integration suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_secret_authority.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_plugin_configuration_storage.py tests/hermes_cli/test_plugin_configuration_api.py tests/hermes_cli/test_tools_config.py -v
```

Expected: all pass, including restart and injected transaction failures; no
fake worker survives its patch.

- [ ] **Step 8: Commit Task 2**

```bash
git add hermes_cli/secret_authority.py hermes_cli/secret_keystore.py hermes_cli/plugin_configuration.py tests/hermes_cli/test_secret_authority.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_plugin_configuration_storage.py
git commit -m "fix: make secret authority and revocation durable"
```

---

### Task 3: Add read-only doctor and transactional repair CLI

**Files:**
- Create: `hermes_cli/secrets_repair.py`
- Modify: `hermes_cli/secret_keystore.py`
- Modify: `hermes_cli/plugin_configuration.py`
- Modify: `hermes_cli/main.py`
- Create: `tests/hermes_cli/test_secrets_repair.py`
- Modify: `tests/hermes_cli/test_argparse_flag_propagation.py`

**Interfaces:**
- Produces: immutable `SecretFinding`, `DoctorReport`, `RepairAction`, `RepairPlan`, and `RepairReport` dataclasses.
- Produces: `diagnose_secrets() -> DoctorReport`, `plan_secret_repair(*, move_to: Literal["os", "file"] | None = None, reset_unrecoverable: bool = False) -> RepairPlan`, and `apply_secret_repair(plan: RepairPlan, *, confirm_reset: bool = False) -> RepairReport`.
- Produces: `PluginConfigurationService.secret_storage_keys() -> list[str]`, a static manifest-derived inventory that reads no values.
- Consumes: Task 2 registry, `move_secret()`, backend primitives, and transaction lock.

- [ ] **Step 1: Write doctor RED tests**

Define stable codes and prove the entire profile snapshot is unchanged:

```python
def test_doctor_is_byte_for_byte_read_only(profile, fake_keyring):
    before = snapshot_tree(profile)
    report = diagnose_secrets()
    assert snapshot_tree(profile) == before
    assert fake_keyring.set_calls == []
    assert fake_keyring.delete_calls == []
    assert all("secret-value" not in finding.message for finding in report.findings)
```

Cover `AUTHORITY_CORRUPT`, `AUTHORITY_MODE_MISMATCH`, `STALE_DUPLICATE`,
`COMPETING_VALUES`, `FILE_STORE_CORRUPT`, `PERMISSION_DRIFT`,
`ABANDONED_TEMP`, and `OS_UNAVAILABLE` without printing values or creating an
absent root. Task 5 adds `CONTAINER_STORAGE_UNPROVEN` after the shared mount
inspector exists.

- [ ] **Step 2: Write repair RED tests**

Cover dry planning versus `--apply`, unambiguous registry reconstruction,
stale-copy deletion, interrupted-move resume/rollback, temporary cleanup,
permission repair, quarantine before replacement, healthy-tier reconstruction,
and unrecoverable reset requiring both explicit flags:

Also cover corrupt-registry tombstone loss. An absent known plugin key may have
had a `cleared` entry in the damaged registry, so default repair must report a
value-free `AUTHORITY_TOMBSTONE_AMBIGUOUS` finding and refuse reconstruction.
`--reset-unrecoverable --apply` without `--yes` must change nothing;
`--reset-unrecoverable --apply --yes` must quarantine the corrupt registry,
reconstruct independently unambiguous tier entries, and durably record
`cleared` for absent known keys so stale legacy plaintext cannot reactivate.

```python
def test_unrecoverable_reset_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    plan = plan_secret_repair(reset_unrecoverable=True)
    with pytest.raises(RepairRefusedError):
        apply_secret_repair(plan, confirm_reset=False)
    report = apply_secret_repair(plan, confirm_reset=True)
    assert len(report.quarantine_paths) == 1
    assert report.quarantine_paths[0].is_dir()
    assert get_authority(KEY) == "cleared"
```

Inject a failure after quarantine rename and prove repair can be retried without
deleting the quarantine or selecting a competing value.

- [ ] **Step 3: Write parser and output RED tests**

Require these forms:

```text
hermes secrets doctor
hermes secrets repair
hermes secrets repair --apply
hermes secrets repair --move-to os --apply
hermes secrets repair --reset-unrecoverable --apply --yes
```

Assert default repair is non-mutating, ambiguous plans return nonzero, and
human output contains stable codes and logical keys but no values.

- [ ] **Step 4: Run the recovery RED tests**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_secrets_repair.py tests/hermes_cli/test_argparse_flag_propagation.py -v
```

Expected: imports and parser assertions fail because the recovery module and
subcommands do not exist.

- [ ] **Step 5: Implement diagnosis and deterministic plans**

Use these public shapes:

```python
@dataclass(frozen=True)
class SecretFinding:
    code: str
    severity: Literal["info", "warning", "error"]
    key: str | None
    message: str

@dataclass(frozen=True)
class DoctorReport:
    configured_mode: str
    authorities: Mapping[str, str]
    findings: tuple[SecretFinding, ...]

@dataclass(frozen=True)
class RepairAction:
    code: str
    key: str | None
    source: str | None
    destination: str | None

@dataclass(frozen=True)
class RepairPlan:
    actions: tuple[RepairAction, ...]
    blocked_findings: tuple[SecretFinding, ...]

@dataclass(frozen=True)
class RepairReport:
    applied: tuple[RepairAction, ...]
    quarantine_paths: tuple[Path, ...]
    failed: tuple[SecretFinding, ...]
```

Diagnosis uses only bounded gets and filesystem reads. It never invokes the
mutating OS round-trip probe. Plans sort actions by code and key for stable
output. Under corrupt authority, an inventory key absent from every healthy,
fully observed tier is not inferred as unregistered because its lost state may
have been a tombstone; default planning blocks on that ambiguity. Application
revalidates the finding, inventory, tier values, and artifact identities under
the transaction lock before each mutation; changed state aborts rather than
applying a stale plan.

- [ ] **Step 6: Implement quarantine, move, reset, and CLI registration**

Quarantine uses `secrets/quarantine/<UTC timestamp>-<nonce>/`, atomic rename,
private permissions, and a manifest containing paths and finding codes but no
values. `--reset-unrecoverable` quarantines corrupt file artifacts, records
affected registered file keys as `cleared`, and initializes a clean encrypted
store only after confirmation and container-persistence checks.

The same explicit reset flag governs corrupt-registry tombstone loss. With
`--yes`, repair quarantines the corrupt authority file and writes one complete
registry containing reconstructed authorities for surviving unambiguous values
and `cleared` for absent known plugin keys. Without `--yes`, noninteractive
apply refuses before mutation. Public findings, plans, reports, and manifests
remain value-free.

Register doctor and repair beneath the existing secrets parser. `repair`
without `--apply` prints the plan only. `--yes` is accepted only with
`--reset-unrecoverable --apply` and is required when stdin is noninteractive.

Generalize `main.py`'s dependency-light workflow-schema startup classifier into
an early read-only classifier that also recognizes `secrets doctor` and
`secrets repair` without `--apply`. These commands must skip dotenv loading,
file logging, early install recovery, bytecode sweeping, brand startup, and the
normal parser graph. Add a real `.venv/bin/hermes` subprocess test that
snapshots a temporary profile before and after doctor/default repair and proves
byte-for-byte equality. Inject a fake `keyring` module for that subprocess; do
not touch the developer keychain.

- [ ] **Step 7: Run recovery and keystore suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_secrets_repair.py tests/hermes_cli/test_secret_authority.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_argparse_flag_propagation.py -v
```

Expected: all pass and snapshot assertions prove doctor/default repair are
read-only.

- [ ] **Step 8: Commit Task 3**

```bash
git add hermes_cli/secrets_repair.py hermes_cli/secret_keystore.py hermes_cli/plugin_configuration.py hermes_cli/main.py tests/hermes_cli/test_secrets_repair.py tests/hermes_cli/test_argparse_flag_propagation.py
git commit -m "feat: add secret store diagnosis and repair"
```

---

### Task 4: Enforce Windows ACLs on real replacement paths and keystore artifacts

**Files:**
- Create: `hermes_cli/windows_permissions.py`
- Modify: `hermes_cli/config.py`
- Modify: `hermes_cli/env_loader.py`
- Modify: `hermes_cli/secret_keystore.py`
- Modify: `hermes_cli/secret_authority.py`
- Modify: `hermes_cli/secrets_repair.py`
- Create: `tests/hermes_cli/test_windows_permissions.py`
- Modify: `tests/hermes_cli/test_secure_file_windows.py`
- Modify: `tests/hermes_cli/test_config.py`
- Modify: `tests/hermes_cli/test_env_loader.py`
- Modify: `tests/hermes_cli/test_secret_keystore.py`
- Modify: `tests/hermes_cli/test_secrets_repair.py`

**Interfaces:**
- Produces: `WindowsAclError`, `restrict_file_to_current_user(path: Path) -> None`, `restrict_directory_to_current_user(path: Path) -> None`, and read-only ACL inspection.
- Consumes: Task 1 `without_plugin_secret_keys()` for the PowerShell child.

- [ ] **Step 1: Write low-level ACL RED tests**

Assert hostile paths and SID travel only through child environment data; file
rules purge inheritance/explicit ACEs and grant one current-user file rule;
directory rules additionally use `ContainerInherit,ObjectInherit` plus traverse,
create, and delete-child rights. Missing/invalid SID, missing PowerShell,
timeout, and nonzero exit must raise `WindowsAclError`.

- [ ] **Step 2: Write real-caller RED tests**

Patch Windows platform behavior and run actual temp-file replacement paths.
Assert ACL application after existing `.env` save, clear, config sanitize,
startup sanitize, key/ciphertext replacement, lock creation, authority
replacement, OS mutation-root creation, quarantine rename, and second writes.
Assert the PowerShell child environment has no plugin key. ACL failure becomes
`ConfigurationPersistenceError` or `KeystoreError` at the owning boundary.

- [ ] **Step 3: Run the ACL RED tests**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_windows_permissions.py tests/hermes_cli/test_secure_file_windows.py tests/hermes_cli/test_config.py tests/hermes_cli/test_env_loader.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_secrets_repair.py -k "acl or Windows or sanitize_env_file or save_env_value or remove_env_value or authority or quarantine" -v
```

Expected: the module import fails and real replacement tests show that existing
files bypass the current best-effort ACL helper.

- [ ] **Step 4: Implement the stdlib-only ACL module**

Use the exact raising and inspection dataclass below. Export
`restrict_file_to_current_user(path: Path) -> None`,
`restrict_directory_to_current_user(path: Path) -> None`,
`inspect_file_acl(path: Path) -> WindowsAclInspection`, and
`inspect_directory_acl(path: Path) -> WindowsAclInspection`.

```python
class WindowsAclError(RuntimeError):
    pass

@dataclass(frozen=True)
class WindowsAclInspection:
    secure: bool
    detail: str | None
```

Build the PowerShell script from constants only. Pass path and SID as data in
a Task-1-filtered environment. Protect from inheritance without copying ACEs,
purge every explicit rule, then add exactly one current-user rule. Do not use
localized account names or treat `chmod` as a Windows boundary.

- [ ] **Step 5: Wire every create and replacement boundary**

Keep general config-file best-effort behavior where compatibility requires it,
but make credential `.env` persistence strict. After every actual replacement,
apply the ACL to the resolved target returned by `atomic_replace()`. Wrap ACL
errors as `ConfigurationPersistenceError` in config paths and `KeystoreError`
in keystore paths. Secure directory, lock, key, ciphertext, authority, and
quarantine destinations. Startup sanitation remains best-effort but must call
the shared boundary and leave a diagnosable drift finding on failure.

- [ ] **Step 6: Run ACL and persistence suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_windows_permissions.py tests/hermes_cli/test_secure_file_windows.py tests/hermes_cli/test_config.py tests/hermes_cli/test_env_loader.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_secrets_repair.py -v
```

Expected: all pass on mocked Windows and unchanged POSIX mode tests.

- [ ] **Step 7: Commit Task 4**

```bash
git add hermes_cli/windows_permissions.py hermes_cli/config.py hermes_cli/env_loader.py hermes_cli/secret_keystore.py hermes_cli/secret_authority.py hermes_cli/secrets_repair.py tests/hermes_cli/test_windows_permissions.py tests/hermes_cli/test_secure_file_windows.py tests/hermes_cli/test_config.py tests/hermes_cli/test_env_loader.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_secrets_repair.py
git commit -m "fix: enforce credential ACLs after replacement"
```

---

### Task 5: Initialize only on proven persistent container storage

**Files:**
- Create: `hermes_cli/container_storage.py`
- Modify: `hermes_constants.py`
- Modify: `hermes_cli/config.py`
- Modify: `hermes_cli/security_audit_startup.py`
- Modify: `hermes_cli/secret_keystore.py`
- Modify: `hermes_cli/secrets_repair.py`
- Create: `tests/hermes_cli/test_container_storage.py`
- Modify: `tests/hermes_cli/test_secret_keystore.py`
- Modify: `tests/hermes_cli/test_secrets_repair.py`
- Modify: `tests/hermes_cli/test_security_audit_startup.py`
- Modify: `tests/test_hermes_constants.py`
- Create: `tests/docker/test_secret_keystore_persistence.py`

**Interfaces:**
- Produces: `PersistenceState`, `MountPersistence`, `is_container()`, and `inspect_mount_persistence(path: Path, *, mountinfo_path: Path = Path("/proc/self/mountinfo")) -> MountPersistence`.
- Consumed by: file-key initialization and Task 3 doctor/reset.

- [ ] **Step 1: Write parser and classification RED tests**

Use synthetic mountinfo for deepest enclosing mount selection, escaped
mountpoints (`\\040`, `\\011`, `\\012`, `\\134`), nonexistent `secrets`
children, persistent bind/volume mounts, and `overlay`, `fuse-overlayfs`,
`tmpfs`, `ramfs`, and `aufs` refusal. Root-only, malformed, missing, or
unrelated mountinfo is `UNKNOWN`, not success. Cover Docker, Podman,
Kubernetes, cgroup, containerd, CRI-O, desktop-child detection, and a process
detected only from a union root. Exercise `is_container()` and persistence
inspection from the same inputs rather than patching the runtime classifier.

Extend doctor tests with `CONTAINER_STORAGE_UNPROVEN` and assert mount evidence
is read fresh on each diagnosis without writing profile state.

- [ ] **Step 2: Write file-store RED tests**

```python
def test_fresh_persistent_container_store_initializes(tmp_path, monkeypatch):
    monkeypatch.setattr(container_storage, "is_container", lambda: True)
    monkeypatch.setattr(
        container_storage,
        "inspect_mount_persistence",
        lambda path: MountPersistence(PERSISTENT, tmp_path, "ext4", "/dev/x", "volume"),
    )
    FileKeystore(tmp_path / "secrets").set("K", "v")
    assert FileKeystore(tmp_path / "secrets").get("K") == "v"
```

Add `EPHEMERAL` and `UNKNOWN` tests that raise `KeystoreError` and leave no
master key.

- [ ] **Step 3: Run the container RED tests**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_container_storage.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_secrets_repair.py tests/hermes_cli/test_security_audit_startup.py tests/test_hermes_constants.py -k "container or mount or persistent" -v
```

Expected: import failure and the existing blanket container refusal rejects
the proven persistent case.

- [ ] **Step 4: Implement shared detection and integrate key creation**

Define:

```python
class PersistenceState(Enum):
    PERSISTENT = "persistent"
    EPHEMERAL = "ephemeral"
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class MountPersistence:
    state: PersistenceState
    mount_point: Path | None
    fs_type: str | None
    source: str | None
    reason: str
```

Parse `/proc/self/mountinfo` without importing test-runner code. Select the
deepest enclosing mount and derive runtime kind from that same parsed evidence.
A distinct non-ephemeral bind mount or named volume is persistent evidence for
positively identified Docker and Podman; container root alone is insufficient.
A union-root-only container is ambiguous, as is every container not positively
identified as Docker or Podman. Kubernetes and otherwise ambiguous runtimes
cannot distinguish a disk-backed `emptyDir`, PVC-like mount, or genuinely
durable generic mount from mountinfo alone, so all such distinct non-memory
mounts are `UNKNOWN` unless the operator has verified the storage class and
retention policy and explicitly set
`security.container_persistence_acknowledged: true` in the active profile's
`config.yaml`. Read that narrow setting directly and afresh without importing
the full config loader, adding a `HERMES_*` variable, or caching evidence. The
acknowledgement cannot override known `overlay`, `fuse-overlayfs`, `tmpfs`,
`ramfs`, or `aufs` evidence. Missing evidence fails closed.
`hermes_constants.is_container()` becomes a compatibility delegate and config
retains its `HERMES_SKIP_CHMOD` permission-policy override.

In `_load_or_create_key()`, allow a new key inside a container only for
`PERSISTENT`. Error text for `UNKNOWN`/`EPHEMERAL` names the inspected path and
the doctor/repair command. Do not cache mount evidence.

Wire the same inspection into doctor and unrecoverable reset. Reset cannot
initialize a clean file store on `UNKNOWN` or `EPHEMERAL` evidence.
Doctor tells Kubernetes/ambiguous-runtime operators to verify durable backing
before setting the acknowledgement in `config.yaml`.

- [ ] **Step 5: Run container and keystore suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_container_storage.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_secrets_repair.py tests/hermes_cli/test_security_audit_startup.py tests/test_hermes_constants.py -v
```

If Docker is available, additionally run:

```bash
scripts/run_tests.sh tests/docker/test_secret_keystore_persistence.py --file-timeout 600 -v
```

Expected: unit suites pass; the Docker test either passes or is explicitly
reported unavailable without being represented as completed evidence.

- [ ] **Step 6: Commit Task 5**

```bash
git add hermes_cli/container_storage.py hermes_constants.py hermes_cli/config.py hermes_cli/security_audit_startup.py hermes_cli/secret_keystore.py hermes_cli/secrets_repair.py tests/hermes_cli/test_container_storage.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_secrets_repair.py tests/hermes_cli/test_security_audit_startup.py tests/test_hermes_constants.py tests/docker/test_secret_keystore_persistence.py
git commit -m "fix: initialize secrets on persistent container volumes"
```

---

### Task 6: Make migration dry-run strictly non-probing

**Files:**
- Modify: `hermes_cli/main.py`
- Modify: `hermes_cli/secret_keystore.py`
- Modify: `hermes_cli/secrets_migrate.py`
- Modify: `tests/hermes_cli/test_secrets_migrate.py`
- Modify: `tests/hermes_cli/test_startup_fast_guards.py`

**Interfaces:**
- Produces: `get_configured_mode() -> Literal["auto", "os", "file", "off"]`, a read-only config lookup that never resolves a backend.

- [ ] **Step 1: Write the handler RED test**

```python
def test_dry_run_never_resolves_or_probes_backend(capsys):
    args = mock.Mock(dry_run=True)
    report = MigrationReport(migrated=[KEY], failed=[], dry_run=True)
    with mock.patch("hermes_cli.secrets_migrate.migrate_secrets", return_value=report), \
         mock.patch.object(sk, "get_backend", side_effect=AssertionError("resolved")), \
         mock.patch.object(sk, "probe_os_keystore", side_effect=AssertionError("probed")), \
         mock.patch.object(sk, "get_configured_mode", return_value="auto"):
        assert _handle_secrets_migrate(args) == 0
    assert "configured auto mode; backend not probed" in capsys.readouterr().out
```

Snapshot an absent profile and assert the handler creates nothing.
Add a real `.venv/bin/hermes secrets migrate --dry-run` subprocess regression
with a temporary profile and fake keyring module; snapshot every profile path
before and after and require exact equality.

- [ ] **Step 2: Run the dry-run RED tests**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_secrets_migrate.py -v
```

Expected: the handler calls `get_backend()` and fails the new assertion.

- [ ] **Step 3: Implement configured-mode reporting**

Expose the existing read-only mode resolution as `get_configured_mode()`.
During dry-run, print the configured mode and “backend not probed”; never call
`get_backend()`. Real migration may continue reporting the committed backend
after it has actually performed writes.

Extend Task 3's dependency-light early read-only classifier to recognize only
the exact `secrets migrate --dry-run` form. It must apply the profile override
but skip dotenv loading, logging, early recovery, cache cleanup, brand startup,
and generic parser construction before dispatching the read-only handler.

- [ ] **Step 4: Run migration and startup suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_secrets_migrate.py tests/hermes_cli/test_startup_fast_guards.py tests/hermes_cli/test_env_loader.py -v
```

- [ ] **Step 5: Commit Task 6**

```bash
git add hermes_cli/main.py hermes_cli/secret_keystore.py hermes_cli/secrets_migrate.py tests/hermes_cli/test_secrets_migrate.py tests/hermes_cli/test_startup_fast_guards.py
git commit -m "fix: keep secret migration dry-runs non-probing"
```

---

### Task 7: Regenerate and verify the frozen dependency lock

**Files:**
- Modify: `uv.lock`

**Interfaces:**
- Consumes: `keyring>=25.6.0` already declared in `pyproject.toml`.
- Produces: a root-package dependency record and resolved transitive packages accepted by frozen installs.

- [ ] **Step 1: Confirm the stale-lock RED**

Run:

```bash
uv lock --check --python .venv/bin/python
```

Expected: exit 1 with `The lockfile at uv.lock needs to be updated`.

- [ ] **Step 2: Regenerate the lock with the repository interpreter**

Run:

```bash
uv lock --python .venv/bin/python
```

Inspect the diff and verify that it adds the root `keyring` dependency plus
only resolver-required transitive changes. Do not upgrade unrelated packages
manually.

- [ ] **Step 3: Verify frozen-lock GREEN and installation metadata**

Run:

```bash
uv lock --check --python .venv/bin/python
scripts/run_tests.sh tests/hermes_cli/test_secret_keystore.py -k "keyring or OSKeystore or Probe" -v
```

Expected: both commands exit 0.

- [ ] **Step 4: Commit Task 7**

```bash
git add uv.lock
git commit -m "build: refresh credential storage dependency lock"
```

---

### Task 8: Repeat platform verification and record final evidence

**Files:**
- Modify: `docs/verification/2026-08-15-credential-storage-parity.md`

**Interfaces:**
- Consumes: all prior task commits and the execution gates in `docs/2026-08-15-hermes-credential-storage-execution-prompt.md`.
- Produces: final reproducible evidence only; no production-code changes.

- [ ] **Step 1: Run all focused remediation suites together**

Run:

```bash
scripts/run_tests.sh tests/test_command_secret_source.py tests/test_bitwarden_secrets.py tests/hermes_cli/test_env_loader.py tests/hermes_cli/test_secret_authority.py tests/hermes_cli/test_secret_keystore.py tests/hermes_cli/test_plugin_configuration_storage.py tests/hermes_cli/test_secrets_repair.py tests/hermes_cli/test_windows_permissions.py tests/hermes_cli/test_container_storage.py tests/hermes_cli/test_secrets_migrate.py -v
```

Expected: zero failures.

- [ ] **Step 2: Run the real startup regression**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_env_loader.py::test_startup_scrubs_legacy_plugin_secrets_but_load_env_still_reads_them -v
```

Expected: one pass and a real `load_hermes_dotenv()` call.

- [ ] **Step 3: Run the complete suite**

Run:

```bash
scripts/run_tests.sh
```

Expected: zero failures. Record file count, pass count, duration, worker count,
and any retry-only flakes separately. A failure blocks documentation and push.

- [ ] **Step 4: Repeat macOS CLI, Electron, and scheduler gates**

Create one disposable `HERMES_HOME` and a unique disposable plugin storage key;
precheck that its exact OS account is absent. Using the repository
`.venv/bin/python`, verify production configure/readback, authority `os`, no
logical key in `.env` or `os.environ`, and no plaintext in authority metadata.

Rebuild the current Electron bundle, launch the real renderer and the actual
`.venv/bin/python -m hermes_cli.main serve` backend, open Settings → Plugins,
and verify the field shows `Secret set` with no second Keychain prompt.

With no GUI process running, create a disposable due builtin cron job in the
same profile, run `hermes cron tick`, and require durable history with
`source=builtin` plus output proving `backend=os` and the same repository
interpreter. A direct/manual cron execution does not count.

- [ ] **Step 5: Exercise recovery on disposable data**

Create ACL/permission drift, a stale equal duplicate, an abandoned temp, and a
recoverable corrupt file tier inside the disposable profile. Prove `doctor`
changes no bytes, default `repair` changes no bytes, `repair --apply` resolves
each finding under lock, and an unrecoverable reset requires confirmation and
quarantines rather than deletes artifacts. Never use the developer's real
credential account.

- [ ] **Step 6: Clean and negatively verify every disposable artifact**

Remove only the exact created cron job and Keychain account. Terminate only
processes whose exact environment identifies the disposable profile. Move the
exact profile and quarantine test directory to Trash when available. Verify
the account, logical environment key, profile path, owned processes, cron job,
and generated build artifacts are absent. Preserve all pre-existing untracked
paths.

- [ ] **Step 7: Update and commit verification evidence**

Record commands, platform/interpreter/backend, UI and prompt result, scheduler
execution/source, doctor/repair evidence, focused and full-suite results,
cleanup, and honest Windows/Linux/container outstanding rows. Do not claim an
environment that was not actually exercised.

```bash
git add docs/verification/2026-08-15-credential-storage-parity.md
git commit -m "docs: verify credential storage remediation"
```

- [ ] **Step 8: Final whole-branch review and push gate**

Request a fresh whole-branch security/code review from baseline
`bce1f704bbb426d4fd9810e8af76f1c0c798cbef` through Task 8 HEAD. Resolve every
Critical/Important finding through a new RED/GREEN commit and repeat affected
verification. When review is clean, verify tracked status is clean and push:

```bash
git push -u origin feat/hermes-credential-storage-parity
git rev-list --count origin/feat/hermes-credential-storage-parity..feat/hermes-credential-storage-parity
```

Expected: push succeeds and the final count is `0`. Do not merge into `base`.
