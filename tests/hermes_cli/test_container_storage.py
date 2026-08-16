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
    ("mount_line", "expected_reason_fragment"),
    [
        (
            _mountinfo_line(
                "/opt/data", fs_type="ext4", source="/dev/nvme0n1p1"
            ),
            "volume",
        ),
        (
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
    tmp_path, mount_line, expected_reason_fragment
):
    mountinfo = _write_mountinfo(
        tmp_path / "mountinfo",
        _mountinfo_line("/", fs_type="overlay", source="overlay") + mount_line,
    )

    result = inspect_mount_persistence(
        Path("/opt/data/secrets/not-created-yet"), mountinfo_path=mountinfo
    )

    assert result.state is PersistenceState.PERSISTENT
    assert result.mount_point == Path("/opt/data")
    assert expected_reason_fragment in result.reason


def test_mountinfo_escapes_are_decoded_for_nonexistent_children(tmp_path):
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
