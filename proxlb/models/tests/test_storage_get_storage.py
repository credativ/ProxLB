"""
Unit tests for Storage.get_storage() and Storage.get_disks_for_guest().

These tests verify the storage topology sweep: per-node status collection,
the shared flag semantics (shared storages are content-listed once, local
ones per node instance), the content type filtering (only guest content is
ever listed), the used/size fallback when attributing volumes to guests, and
graceful degradation on API errors.
"""

from typing import Any, cast
from unittest.mock import patch

from proxlb.models.storage import Storage
from proxlb.utils.proxlb_data import ProxLbData
from proxlb.utils.proxmox_api import ProxmoxApi

_GiB = 1024 ** 3

NodeStorages = dict[str, list[dict[str, Any]]]
Contents = dict[tuple[str, str, str], list[dict[str, Any]]]


class _FakeContentApi:
    def __init__(self, api: "_FakeProxmoxApi", node: str, storage: str) -> None:
        self._api, self._node, self._storage = api, node, storage

    def get(self, content: str) -> list[dict[str, Any]]:
        self._api.content_calls.append((self._node, self._storage, content))
        return self._api.contents[(self._node, self._storage, content)]


class _FakeStorageItemApi:
    def __init__(self, api: "_FakeProxmoxApi", node: str, storage: str) -> None:
        self.content = _FakeContentApi(api, node, storage)


class _FakeStorageApi:
    def __init__(self, api: "_FakeProxmoxApi", node: str) -> None:
        self._api, self._node = api, node

    def get(self) -> list[dict[str, Any]]:
        return self._api.node_storages[self._node]

    def __call__(self, storage_name: str) -> _FakeStorageItemApi:
        return _FakeStorageItemApi(self._api, self._node, storage_name)


class _FakeNodeApi:
    def __init__(self, api: "_FakeProxmoxApi", node: str) -> None:
        self.storage = _FakeStorageApi(api, node)


class _FakeProxmoxApi:
    """Stand-in for the node/storage/content part of the Proxmox API.

    Unknown nodes or (node, storage, content) keys raise KeyError, which
    doubles as the API error path in the degradation tests.
    """

    def __init__(self, node_storages: NodeStorages, contents: Contents) -> None:
        self.node_storages = node_storages
        self.contents = contents
        self.content_calls: list[tuple[str, str, str]] = []

    def nodes(self, node: str) -> _FakeNodeApi:
        return _FakeNodeApi(self, node)


def _nodes(*names: str) -> dict[str, ProxLbData.Node]:
    """get_storage only iterates the keys, so stub values suffice."""
    return cast(dict[str, ProxLbData.Node], {name: None for name in names})


def _cluster_api() -> _FakeProxmoxApi:
    """Two-node cluster: a local dir storage, a shared rbd storage, a
    backup-only storage and a storage that is nowhere active."""
    local = {"storage": "local", "type": "dir", "content": "images,rootdir,iso", "active": 1, "enabled": 1}
    ceph = {"storage": "ceph", "type": "rbd", "shared": 1, "content": "images", "active": 1, "enabled": 1}
    backup = {"storage": "backup", "type": "pbs", "shared": 1, "content": "backup", "active": 1, "enabled": 1}
    return _FakeProxmoxApi(
        node_storages={
            "pve1": [
                {**local, "total": 100 * _GiB, "used": 40 * _GiB, "avail": 60 * _GiB},
                {**ceph, "total": 500 * _GiB, "used": 200 * _GiB, "avail": 300 * _GiB},
                {**backup, "total": 1000 * _GiB, "used": 10 * _GiB, "avail": 990 * _GiB},
            ],
            "pve2": [
                {**local, "total": 100 * _GiB, "used": 70 * _GiB, "avail": 30 * _GiB},
                {**ceph, "total": 500 * _GiB, "used": 200 * _GiB, "avail": 300 * _GiB},
                {"storage": "slow", "type": "dir", "content": "images", "active": 0},
            ],
        },
        contents={
            ("pve1", "local", "images"): [
                {"volid": "local:101/vm-101-disk-0.qcow2", "vmid": 101, "size": 10 * _GiB, "used": 5 * _GiB, "format": "qcow2"},
                {"volid": "local:101/vm-101-disk-1.qcow2", "vmid": 101, "size": 2 * _GiB, "format": "qcow2"},
                {"volid": "local:iso/other.img", "size": 1 * _GiB, "format": "raw"},
            ],
            ("pve1", "local", "rootdir"): [
                {"volid": "local:201/subvol-201-disk-0", "vmid": 201, "size": 8 * _GiB, "used": 0, "format": "subvol"},
            ],
            ("pve2", "local", "images"): [
                {"volid": "local:102/vm-102-disk-0.qcow2", "vmid": 102, "size": 4 * _GiB, "used": 3 * _GiB, "format": "qcow2"},
            ],
            ("pve2", "local", "rootdir"): [],
            ("pve1", "ceph", "images"): [
                {"volid": "ceph:vm-101-disk-0", "vmid": 101, "size": 20 * _GiB, "format": "raw"},
            ],
            ("pve2", "ceph", "images"): [
                {"volid": "ceph:vm-101-disk-0", "vmid": 101, "size": 20 * _GiB, "format": "raw"},
            ],
        },
    )


def _collect(api: _FakeProxmoxApi, *node_names: str) -> dict[str, ProxLbData.Storage]:
    with patch("proxlb.models.storage.time.sleep"):
        return Storage.get_storage(cast(ProxmoxApi, api), _nodes(*node_names))


def test_entity_attributes_and_per_node_status_are_collected() -> None:
    """Storage entities carry type, shared flag and content; per-node status carries independent capacities."""
    storage = _collect(_cluster_api(), "pve1", "pve2")

    assert storage["local"].shared is False  # 'shared' key absent must default to False
    assert storage["ceph"].shared is True
    assert storage["local"].type == "dir"
    assert storage["local"].content == ["images", "rootdir", "iso"]
    assert storage["local"].nodes["pve1"].avail == 60 * _GiB
    assert storage["local"].nodes["pve2"].avail == 30 * _GiB
    assert storage["slow"].nodes.keys() == {"pve2"}  # config 'nodes' restriction: absent node means unavailable


def test_shared_storage_is_content_listed_exactly_once() -> None:
    """A shared storage is a single volume set; listing it per node would double-count guests."""
    api = _cluster_api()
    storage = _collect(api, "pve1", "pve2")

    ceph_calls = [call for call in api.content_calls if call[1] == "ceph"]
    assert len(ceph_calls) == 1
    assert storage["ceph"].guest_disks.keys() == {101}
    assert storage["ceph"].guest_disks[101].allocated == 20 * _GiB


def test_local_storage_is_content_listed_per_active_node_and_merged() -> None:
    """Node-local instances are independent; each active one is listed and vmid sums are merged."""
    api = _cluster_api()
    storage = _collect(api, "pve1", "pve2")

    local_nodes = {call[0] for call in api.content_calls if call[1] == "local"}
    assert local_nodes == {"pve1", "pve2"}
    assert storage["local"].guest_disks[102].allocated == 3 * _GiB


def test_only_guest_content_types_are_listed() -> None:
    """Backup-only storages are never content-listed; 'iso' is never requested on mixed storages."""
    api = _cluster_api()
    _collect(api, "pve1", "pve2")

    assert all(call[1] != "backup" for call in api.content_calls)
    assert all(call[2] in ("images", "rootdir") for call in api.content_calls)


def test_inactive_storage_is_not_content_listed() -> None:
    """A storage without any active node instance has no listable volumes."""
    api = _cluster_api()
    storage = _collect(api, "pve1", "pve2")

    assert all(call[1] != "slow" for call in api.content_calls)
    assert storage["slow"].guest_disks == {}


def test_volume_bytes_prefer_used_and_fall_back_to_size() -> None:
    """'used' reflects actual allocation and wins where reported (an explicit
    0 is meaningful for thin volumes); 'size' is the fallback."""
    storage = _collect(_cluster_api(), "pve1", "pve2")

    # vm-101 on local: disk-0 reports used=5G, disk-1 reports no 'used' -> size=2G
    assert storage["local"].guest_disks[101].allocated == 7 * _GiB
    # ct-201: explicit used=0 must not fall back to size
    assert storage["local"].guest_disks[201].allocated == 0


def test_volumes_without_vmid_are_ignored() -> None:
    """Volumes not owned by a guest must not be attributed to anyone."""
    storage = _collect(_cluster_api(), "pve1", "pve2")

    owners = set(storage["local"].guest_disks)
    assert owners == {101, 102, 201}


def test_api_errors_degrade_to_partial_data() -> None:
    """An unreachable node is skipped; data from the remaining nodes is returned."""
    storage = _collect(_cluster_api(), "pve1", "pve2", "pve3")

    assert storage["local"].nodes.keys() == {"pve1", "pve2"}
    assert storage["local"].guest_disks[101].allocated == 7 * _GiB


def test_get_disks_for_guest_maps_storages_to_bytes() -> None:
    """The per-guest view spans all storages holding volumes of the guest and omits the others."""
    storage = _collect(_cluster_api(), "pve1", "pve2")

    assert Storage.get_disks_for_guest(101, storage) == {"local": 7 * _GiB, "ceph": 20 * _GiB}
    assert Storage.get_disks_for_guest(102, storage) == {"local": 3 * _GiB}
    assert Storage.get_disks_for_guest(999, storage) == {}


def test_provisioned_sums_collected_alongside_allocated() -> None:
    """'provisioned' always sums the volume 'size': it differs from
    'allocated' exactly where a thin storage reports 'used'."""
    storage = _collect(_cluster_api(), "pve1", "pve2")

    # vm-101 on local: sizes 10G + 2G; the allocated view is 5G + 2G.
    assert storage["local"].guest_disks[101].provisioned == 12 * _GiB
    assert storage["local"].guest_disks[101].allocated == 7 * _GiB
    # ct-201: used=0 but provisioned 8G.
    assert storage["local"].guest_disks[201].provisioned == 8 * _GiB
    # ceph volume reports no 'used': both views agree on the size.
    assert storage["ceph"].guest_disks[101].provisioned == storage["ceph"].guest_disks[101].allocated == 20 * _GiB
