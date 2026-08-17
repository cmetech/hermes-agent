"""Container detection and uncached mount-persistence evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import config
from hermes_cli.container_storage import (
    PersistenceState,
    inspect_mount_persistence,
    is_container,
)


def _mountinfo_line(
    mount_point: str,
    *,
    fs_type: str,
    source: str,
    root: str = "/",
    optional_fields: str = "",
) -> str:
    optional = f" {optional_fields}" if optional_fields else ""
    return (
        f"36 25 0:32 {root} {mount_point} rw,relatime{optional} "
        f"- {fs_type} {source} rw\n"
    )


def _write_mountinfo(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_deepest_enclosing_mount_controls_classification(tmp_path):
    mountinfo = _write_mountinfo(
        tmp_path / "mountinfo",
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line("/srv/data", fs_type="ext4", source="/dev/xvda")
        + _mountinfo_line(
            "/srv/data/runtime", fs_type="tmpfs", source="tmpfs"
        ),
    )

    result = inspect_mount_persistence(
        Path("/srv/data/runtime/secrets"), mountinfo_path=mountinfo
    )

    assert result.state is PersistenceState.EPHEMERAL
    assert result.mount_point == Path("/srv/data/runtime")
    assert result.fs_type == "tmpfs"
    assert result.source == "tmpfs"


@pytest.mark.parametrize(
    ("runtime_marker", "mount_line", "expected_reason_fragment"),
    [
        (
            "/.dockerenv",
            _mountinfo_line(
                "/opt/data", fs_type="ext4", source="/dev/nvme0n1p1"
            ),
            "volume",
        ),
        (
            "/run/.containerenv",
            _mountinfo_line(
                "/opt/data",
                fs_type="xfs",
                source="/dev/mapper/host",
                root="/srv/hermes",
                optional_fields="shared:7",
            ),
            "bind",
        ),
    ],
)
def test_distinct_volume_and_bind_mounts_are_persistent(
    monkeypatch, runtime_marker, mount_line, expected_reason_fragment
):
    mountinfo = (
        _mountinfo_line("/", fs_type="overlay", source="overlay") + mount_line
    )
    _patch_container_inputs(
        monkeypatch,
        existing={runtime_marker},
        mountinfo=mountinfo,
    )

    assert is_container() is True
    result = inspect_mount_persistence(
        Path("/opt/data/secrets/not-created-yet"),
        mountinfo_path=Path("/proc/self/mountinfo"),
    )

    assert result.state is PersistenceState.PERSISTENT
    assert result.mount_point == Path("/opt/data")
    assert expected_reason_fragment in result.reason


@pytest.mark.parametrize(
    ("existing", "cgroup"),
    [
        ({"/.dockerenv"}, "0::/\n"),
        ({"/run/.containerenv"}, "0::/\n"),
        (set(), "12:memory:/docker/012345\n"),
        (set(), "0::/machine.slice/libpod-012345.scope\n"),
    ],
    ids=["docker-file", "podman-file", "docker-cgroup", "podman-cgroup"],
)
def test_concrete_runtime_evidence_overrides_generic_container_hint(
    monkeypatch, existing, cgroup
):
    mountinfo = (
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line("/opt/data", fs_type="ext4", source="/dev/xvda")
    )
    _patch_container_inputs(
        monkeypatch,
        existing=existing,
        cgroup=cgroup,
        mountinfo=mountinfo,
    )
    monkeypatch.setenv("HERMES_CONTAINER", "1")

    assert is_container() is True
    result = inspect_mount_persistence(
        Path("/opt/data/secrets"),
        mountinfo_path=Path("/proc/self/mountinfo"),
    )

    assert result.state is PersistenceState.PERSISTENT
    assert "persistent evidence" in result.reason


def test_kubernetes_evidence_overrides_generic_container_hint(monkeypatch):
    mountinfo = (
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line("/opt/data", fs_type="ext4", source="/dev/xvda")
    )
    _patch_container_inputs(monkeypatch, mountinfo=mountinfo)
    monkeypatch.setenv("HERMES_CONTAINER", "1")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")

    assert is_container() is True
    result = inspect_mount_persistence(
        Path("/opt/data/secrets"),
        mountinfo_path=Path("/proc/self/mountinfo"),
    )

    assert result.state is PersistenceState.UNKNOWN
    assert "Kubernetes" in result.reason
    assert "ambiguous container" not in result.reason


def test_kubernetes_disk_backed_emptydir_is_unknown_without_acknowledgement(
    tmp_path, monkeypatch
):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    mountinfo = _write_mountinfo(
        tmp_path / "mountinfo",
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line(
            "/opt/data",
            fs_type="ext4",
            source=(
                "/var/lib/kubelet/pods/pod-id/volumes/"
                "kubernetes.io~empty-dir/hermes"
            ),
        ),
    )

    result = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )

    assert result.state is PersistenceState.UNKNOWN
    assert "Kubernetes" in result.reason
    assert "security.container_persistence_acknowledged" in result.reason


def test_acknowledgement_uses_canonical_overlay_and_refreshes(
    tmp_path, monkeypatch
):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "security:\n  container_persistence_acknowledged: true\n",
        encoding="utf-8",
    )
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()
    managed_config = managed_dir / "config.yaml"
    managed_config.write_text(
        "security:\n  container_persistence_acknowledged: ${ACK_VALUE}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed_dir))
    monkeypatch.setenv("ACK_VALUE", "true")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    mountinfo = _write_mountinfo(
        tmp_path / "mountinfo",
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line("/opt/data", fs_type="ext4", source="/dev/xvda"),
    )

    expanded_string = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )
    managed_config.write_text(
        "security:\n  container_persistence_acknowledged: true\n",
        encoding="utf-8",
    )
    literal_true = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )
    managed_config.write_text(
        "security:\n  container_persistence_acknowledged: false\n",
        encoding="utf-8",
    )
    literal_false = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )

    assert expanded_string.state is PersistenceState.UNKNOWN
    assert literal_true.state is PersistenceState.PERSISTENT
    assert literal_false.state is PersistenceState.UNKNOWN


def test_kubernetes_pvc_acknowledgement_is_read_fresh_each_time(
    tmp_path, monkeypatch
):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    mountinfo = _write_mountinfo(
        tmp_path / "mountinfo",
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line(
            "/opt/data",
            fs_type="ext4",
            source="/dev/disk/by-id/scsi-pvc-volume-id",
        ),
    )

    config_path.write_text(
        "security:\n  container_persistence_acknowledged: false\n",
        encoding="utf-8",
    )
    first = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )
    config_path.write_text(
        "security:\n  container_persistence_acknowledged: true\n",
        encoding="utf-8",
    )
    second = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )
    config_path.write_text(
        "security:\n  container_persistence_acknowledged: false\n",
        encoding="utf-8",
    )
    third = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )

    assert first.state is PersistenceState.UNKNOWN
    assert second.state is PersistenceState.PERSISTENT
    assert "operator acknowledgement" in second.reason
    assert third.state is PersistenceState.UNKNOWN


def test_mountinfo_escapes_are_decoded_for_nonexistent_children(
    tmp_path, monkeypatch
):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "security:\n  container_persistence_acknowledged: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    decoded_mount = Path("/vol with space/tab\tline\nback\\slash")
    encoded_mount = "/vol\\040with\\040space/tab\\011line\\012back\\134slash"
    mountinfo = _write_mountinfo(
        tmp_path / "mountinfo",
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line(
            encoded_mount,
            fs_type="ext4",
            source="/dev/mapper/hermes\\040data",
        ),
    )

    result = inspect_mount_persistence(
        decoded_mount / "secrets" / "missing", mountinfo_path=mountinfo
    )

    assert result.state is PersistenceState.PERSISTENT
    assert result.mount_point == decoded_mount
    assert result.source == "/dev/mapper/hermes data"


@pytest.mark.parametrize("fs_type", ["overlay", "tmpfs", "ramfs", "aufs"])
def test_distinct_ephemeral_filesystems_are_refused(tmp_path, fs_type):
    mountinfo = _write_mountinfo(
        tmp_path / "mountinfo",
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line("/opt/data", fs_type=fs_type, source=fs_type),
    )

    result = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )

    assert result.state is PersistenceState.EPHEMERAL
    assert result.fs_type == fs_type


@pytest.mark.parametrize(
    "mountinfo_text",
    [
        _mountinfo_line("/", fs_type="ext4", source="/dev/root"),
        "not a mountinfo record\n",
        _mountinfo_line("/unrelated", fs_type="ext4", source="/dev/xvda"),
    ],
)
def test_root_only_malformed_and_unrelated_mountinfo_are_unknown(
    tmp_path, mountinfo_text
):
    mountinfo = _write_mountinfo(tmp_path / "mountinfo", mountinfo_text)

    result = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )

    assert result.state is PersistenceState.UNKNOWN
    assert result.reason


def test_missing_mountinfo_is_unknown(tmp_path):
    result = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=tmp_path / "missing"
    )

    assert result.state is PersistenceState.UNKNOWN
    assert result.mount_point is None


@pytest.mark.parametrize(
    "mount_line",
    [
        _mountinfo_line(
            "/opt/data",
            fs_type="ext4",
            source=r"/dev/mapper/hermes\999data",
        ),
        _mountinfo_line(
            "/opt/data",
            fs_type="ext4",
            source="/dev/xvda",
        ).rstrip("\n")
        + " unexpected\n",
    ],
    ids=["invalid-escape", "trailing-field"],
)
def test_invalid_escape_and_trailing_fields_are_unknown(tmp_path, mount_line):
    mountinfo = _write_mountinfo(
        tmp_path / "mountinfo",
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + mount_line,
    )

    result = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )

    assert result.state is PersistenceState.UNKNOWN
    assert "malformed" in result.reason


def test_same_depth_stacked_mounts_are_unknown(tmp_path):
    mountinfo = _write_mountinfo(
        tmp_path / "mountinfo",
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line("/opt/data", fs_type="ext4", source="/dev/xvda")
        + _mountinfo_line("/opt/data", fs_type="tmpfs", source="tmpfs"),
    )

    result = inspect_mount_persistence(
        Path("/opt/data/secrets"), mountinfo_path=mountinfo
    )

    assert result.state is PersistenceState.UNKNOWN
    assert "ambiguous" in result.reason


def test_overlay_root_only_container_requires_acknowledgement_for_generic_mount(
    monkeypatch,
):
    mountinfo = (
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line("/opt/data", fs_type="ext4", source="/dev/xvda")
    )
    _patch_container_inputs(monkeypatch, mountinfo=mountinfo)

    assert is_container() is True
    result = inspect_mount_persistence(
        Path("/opt/data/secrets"),
        mountinfo_path=Path("/proc/self/mountinfo"),
    )

    assert result.state is PersistenceState.UNKNOWN
    assert "ambiguous container" in result.reason
    assert "security.container_persistence_acknowledged" in result.reason


def test_fuse_overlayfs_mount_stays_ephemeral_despite_acknowledgement(
    tmp_path, monkeypatch
):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text(
        "security:\n  container_persistence_acknowledged: true\n",
        encoding="utf-8",
    )
    mountinfo = (
        _mountinfo_line("/", fs_type="overlay", source="overlay")
        + _mountinfo_line(
            "/opt/data",
            fs_type="fuse-overlayfs",
            source="fuse-overlayfs",
        )
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.delenv("HERMES_CONTAINER", raising=False)
    monkeypatch.delenv("HERMES_DESKTOP_CHILD_PID", raising=False)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    def read_text(self, *args, **kwargs):
        if self == Path("/proc/1/cgroup"):
            return "0::/\n"
        if self == Path("/proc/self/mountinfo"):
            return mountinfo
        if self == config_path:
            return config_path.open(encoding="utf-8").read()
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(Path, "read_text", read_text)

    assert is_container() is True
    result = inspect_mount_persistence(
        Path("/opt/data/secrets"),
        mountinfo_path=Path("/proc/self/mountinfo"),
    )

    assert result.state is PersistenceState.EPHEMERAL
    assert result.fs_type == "fuse-overlayfs"


def _patch_container_inputs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    existing: set[str] = frozenset(),
    cgroup: str = "0::/\n",
    mountinfo: str = "",
) -> None:
    monkeypatch.delenv("HERMES_CONTAINER", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("HERMES_DESKTOP_CHILD_PID", raising=False)
    monkeypatch.setattr(Path, "exists", lambda self: str(self) in existing)

    def read_text(self, *args, **kwargs):
        if str(self) == "/proc/1/cgroup":
            return cgroup
        if str(self) == "/proc/self/mountinfo":
            return mountinfo
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(Path, "read_text", read_text)


@pytest.mark.parametrize(
    ("existing", "environment"),
    [
        ({"/.dockerenv"}, {}),
        ({"/run/.containerenv"}, {}),
        (set(), {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}),
        (set(), {"HERMES_CONTAINER": "1"}),
    ],
)
def test_runtime_markers_detect_docker_podman_kubernetes_and_explicit_mode(
    monkeypatch, existing, environment
):
    _patch_container_inputs(monkeypatch, existing=existing)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    assert is_container() is True


@pytest.mark.parametrize(
    "cgroup",
    [
        "12:memory:/docker/012345\n",
        "0::/machine.slice/libpod-012345.scope\n",
        "0::/kubepods.slice/pod012345\n",
        "0::/system.slice/containerd.service\n",
        "0::/crio-012345.scope\n",
        "0::/cri-o/012345\n",
    ],
)
def test_cgroup_markers_detect_supported_container_runtimes(monkeypatch, cgroup):
    _patch_container_inputs(monkeypatch, cgroup=cgroup)

    assert is_container() is True


def test_containerd_mountinfo_detects_cgroup_v2_container(monkeypatch):
    _patch_container_inputs(
        monkeypatch,
        mountinfo="42 31 0:35 /kubepods/containerd/abc /sys/fs/cgroup rw - cgroup2 cgroup rw\n",
    )

    assert is_container() is True


@pytest.mark.parametrize("runtime_name", ["docker", "containerd", "podman"])
def test_unrelated_runtime_named_host_mount_is_not_container(
    monkeypatch, runtime_name
):
    _patch_container_inputs(
        monkeypatch,
        mountinfo=(
            "42 31 8:1 / /var/lib/"
            f"{runtime_name} rw,relatime - ext4 /dev/{runtime_name}-data rw\n"
        ),
    )

    assert is_container() is False


def test_desktop_child_is_not_misclassified_from_parent_cgroup(monkeypatch):
    _patch_container_inputs(monkeypatch, cgroup="0::/docker/parent\n")
    monkeypatch.setenv("HERMES_DESKTOP_CHILD_PID", "1234")

    assert is_container() is False


def test_host_without_container_evidence_is_false(monkeypatch):
    _patch_container_inputs(monkeypatch)

    assert is_container() is False


def test_config_permission_policy_retains_skip_chmod_override(monkeypatch):
    from hermes_cli import container_storage

    monkeypatch.setattr(container_storage, "is_container", lambda: False)
    monkeypatch.setenv("HERMES_SKIP_CHMOD", "1")
    assert config._is_container() is True

    monkeypatch.delenv("HERMES_SKIP_CHMOD")
    assert config._is_container() is False
