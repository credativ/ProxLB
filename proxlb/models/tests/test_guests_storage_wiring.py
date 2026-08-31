from contextlib import ExitStack
from typing import Any, cast
from unittest.mock import MagicMock, patch

from proxlb.models.guests import Guests
from proxlb.models.ha_rules import HaRules
from proxlb.models.ha_status import HaStatus
from proxlb.models.pools import Pools
from proxlb.models.tags import Tags
from proxlb.utils.proxlb_data import ProxLbData
from proxlb.utils.rrd import RrdDatasets

_GiB = 1024 ** 3


def _guest_listing(vmid: int, name: str) -> dict[str, Any]:
    return {
        "status": "running",
        "vmid": vmid,
        "name": name,
        "cpus": 2,
        "maxdisk": 10 * _GiB,
        "disk": 0,
        "maxmem": 4 * _GiB,
        "mem": 2 * _GiB,
    }


def _storage(allocated_bytes: dict[int, int]) -> dict[str, ProxLbData.Storage]:
    return {
        "local": ProxLbData.Storage(
            name="local",
            type="dir",
            content=["images", "rootdir"],
            guest_disks={
                guest_id: ProxLbData.Storage.GuestDisks(allocated=allocated)
                for guest_id, allocated in allocated_bytes.items()
            },
        ),
    }


def _get_guests(vm_listing: list[dict[str, Any]], ct_listing: list[dict[str, Any]],
                storage: dict[str, ProxLbData.Storage], ha_managed_sids: set[str]) -> dict[str, ProxLbData.Guest]:
    proxmox_api = MagicMock()
    proxmox_api.nodes.return_value.qemu.get.return_value = vm_listing
    proxmox_api.nodes.return_value.lxc.get.return_value = ct_listing

    with ExitStack() as stack:
        stack.enter_context(patch.object(Guests, "get_guest_rrd_datasets",
                                         return_value=RrdDatasets(average=[], maximum=[])))
        stack.enter_context(patch.object(HaStatus, "get_ha_managed_sids", return_value=ha_managed_sids))
        stack.enter_context(patch.object(Tags, "get_tags_from_guests", return_value=[]))
        stack.enter_context(patch.object(Tags, "get_affinity_groups", return_value=[]))
        stack.enter_context(patch.object(Tags, "get_anti_affinity_groups", return_value=[]))
        stack.enter_context(patch.object(Tags, "get_ignore", return_value=False))
        stack.enter_context(patch.object(Tags, "get_node_relationships", return_value=[]))
        stack.enter_context(patch.object(Pools, "get_pools_for_guest", return_value=[]))
        stack.enter_context(patch.object(Pools, "get_pool_node_affinity_strictness", return_value=False))
        stack.enter_context(patch.object(HaRules, "get_ha_rules_for_guest", return_value=[]))
        return Guests.get_guests(
            proxmox_api, {}, {},
            cast(dict[str, ProxLbData.Node], {"node1": None}),
            storage, MagicMock(),
        )
    raise AssertionError("unreachable")


def test_vm_receives_disks_from_storage_collector() -> None:
    """The per-guest disk map from Storage.get_disks_for_guest must land on Guest.disks."""
    guests = _get_guests([_guest_listing(101, "vm1")], [], _storage({101: 7 * _GiB}), set())

    assert guests["vm1"].disks == {"local": 7 * _GiB}


def test_guest_without_volumes_has_empty_disks() -> None:
    """A guest unknown to the storage collector must yield an empty disk map, not an error."""
    guests = _get_guests([_guest_listing(101, "vm1")], [], _storage({}), set())

    assert guests["vm1"].disks == {}


def test_ha_managed_uses_vm_prefix_for_qemu_guests() -> None:
    """A QEMU guest is HA-managed iff 'vm:<vmid>' is among the HA sids."""
    guests = _get_guests([_guest_listing(101, "vm1")], [], _storage({}), {"vm:101"})

    assert guests["vm1"].ha_managed is True


def test_ha_managed_uses_ct_prefix_for_lxc_guests() -> None:
    """An LXC guest must match on 'ct:<vmid>' and must not match a 'vm:' sid of the same vmid."""
    guests = _get_guests([], [_guest_listing(201, "ct1")], _storage({}), {"vm:201"})
    assert guests["ct1"].ha_managed is False

    guests = _get_guests([], [_guest_listing(201, "ct1")], _storage({}), {"ct:201"})
    assert guests["ct1"].ha_managed is True
