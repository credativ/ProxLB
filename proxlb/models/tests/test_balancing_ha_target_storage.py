"""
Unit tests for the HA-managed guest handling of the target storage remap.

Migrations of HA-managed guests are routed through the Proxmox HA stack
(hamigrate), which does not forward the 'targetstorage' / 'target-storage'
parameter to the migration task it spawns. On node-local storage clusters that
task then fails with "storage 'X' is not available on node 'Y'". These tests
verify that guests are skipped when a remap would apply to an HA-managed
guest, that non-HA guests still receive the remap, and that the HA resource
list is fetched once per pass and degrades safely on API errors.
"""

__author__ = "YorkHost"
__license__ = "GPL-3.0"


import proxmoxer
from typing import Optional
from unittest.mock import MagicMock, patch

from proxlb.models.balancing import Balancing


def _guest(guest_id: int) -> MagicMock:
    g = MagicMock()
    g.id = guest_id
    g.name = f"guest-{guest_id}"
    g.node_current = "node1"
    g.node_target = "node2"
    return g


def _proxlb_data(guest_name: str, guest: MagicMock) -> MagicMock:
    data = MagicMock()
    data.meta.balancing.live = True
    data.meta.balancing.with_local_disks = True
    data.meta.balancing.with_conntrack_state = False
    data.meta.balancing.ha_managed_sids = None
    data.guests = {guest_name: guest}
    return data


def _api(ha_sids: Optional[list[str]] = None) -> MagicMock:
    api = MagicMock()
    api.cluster.ha.resources.get.return_value = [{"sid": sid} for sid in (ha_sids or [])]
    return api


@patch.object(Balancing, "_resolve_target_storage", return_value="local")
def test_ha_managed_vm_with_remap_is_skipped(mock_resolve: MagicMock) -> None:
    """A remap on an HA-managed VM must skip the migration entirely."""
    api = _api(["vm:101"])
    data = _proxlb_data("vm1", _guest(101))

    job_id = Balancing._exec_rebalancing_vm(api, data, "vm1")

    assert job_id is None
    api.nodes.return_value.qemu.return_value.migrate.return_value.post.assert_not_called()


@patch.object(Balancing, "_resolve_target_storage", return_value="local")
def test_non_ha_vm_with_remap_migrates_with_targetstorage(mock_resolve: MagicMock) -> None:
    """A non-HA VM must be migrated with the resolved 'targetstorage'."""
    api = _api(["vm:999"])  # some other guest is HA-managed
    data = _proxlb_data("vm1", _guest(101))

    job_id = Balancing._exec_rebalancing_vm(api, data, "vm1")

    post = api.nodes.return_value.qemu.return_value.migrate.return_value.post
    post.assert_called_once()
    assert post.call_args.kwargs["targetstorage"] == "local"
    assert job_id is not None


@patch.object(Balancing, "_resolve_target_storage", return_value=None)
def test_ha_managed_vm_without_remap_migrates_normally(mock_resolve: MagicMock) -> None:
    """Without a remap, HA-managed guests keep the default migration behaviour."""
    api = _api(["vm:101"])
    data = _proxlb_data("vm1", _guest(101))

    Balancing._exec_rebalancing_vm(api, data, "vm1")

    post = api.nodes.return_value.qemu.return_value.migrate.return_value.post
    post.assert_called_once()
    assert "targetstorage" not in post.call_args.kwargs
    api.cluster.ha.resources.get.assert_not_called()


@patch.object(Balancing, "_resolve_target_storage", return_value="local")
def test_ha_managed_ct_with_remap_is_skipped(mock_resolve: MagicMock) -> None:
    """A remap on an HA-managed CT must skip the migration entirely."""
    api = _api(["ct:201"])
    data = _proxlb_data("ct1", _guest(201))

    job_id = Balancing._exec_rebalancing_ct(api, data, "ct1")

    assert job_id is None
    api.nodes.return_value.lxc.return_value.migrate.return_value.post.assert_not_called()


@patch.object(Balancing, "_resolve_target_storage", return_value="local")
def test_non_ha_ct_with_remap_migrates_with_target_storage(mock_resolve: MagicMock) -> None:
    """A non-HA CT must be migrated with the resolved 'target-storage'."""
    api = _api([])
    data = _proxlb_data("ct1", _guest(201))

    Balancing._exec_rebalancing_ct(api, data, "ct1")

    post = api.nodes.return_value.lxc.return_value.migrate.return_value.post
    post.assert_called_once()
    assert post.call_args.kwargs["target-storage"] == "local"


def test_ha_resources_fetched_once_per_pass() -> None:
    """The HA resource list must be fetched lazily and cached for the pass."""
    api = _api(["vm:1"])
    data = MagicMock()
    data.meta.balancing.ha_managed_sids = None

    assert Balancing._is_ha_managed(api, data, "vm", 1) is True
    assert Balancing._is_ha_managed(api, data, "vm", 2) is False
    assert Balancing._is_ha_managed(api, data, "ct", 1) is False
    api.cluster.ha.resources.get.assert_called_once()


def test_ha_resources_error_assumes_not_managed() -> None:
    """An API error while listing HA resources must degrade to 'not HA-managed'."""
    api = MagicMock()
    api.cluster.ha.resources.get.side_effect = proxmoxer.core.ResourceException(
        500, "Internal Server Error", "boom")
    data = MagicMock()
    data.meta.balancing.ha_managed_sids = None

    assert Balancing._is_ha_managed(api, data, "vm", 1) is False
    # the failed lookup is cached as an empty list, not retried per guest
    assert Balancing._is_ha_managed(api, data, "vm", 2) is False
    assert api.cluster.ha.resources.get.call_count == 1
