"""
Unit tests for Calculations.validate_node_resources.

validate_node_resources checks whether the target node has enough free
memory to physically host the guest.  It returns True when
guest.memory.used < node.memory.free, and False otherwise (or when no
target node is set).
"""

__author__ = "Alexander Wirt <formorer>"
__copyright__ = "Copyright (C) 2026 Alexander Wirt for credativ GmbH"
__license__ = "GPL-3.0"

from proxlb.models.calculations import Calculations
from proxlb.utils.config_parser import Config
from proxlb.utils.proxlb_data import ProxLbData

GB = 1024 ** 3
BalancingResource = Config.Balancing.Resource


def _make_node_metric(total_gb: float, used_gb: float) -> ProxLbData.Node.Metric:
    total = int(total_gb * GB)
    used = used_gb * GB
    free = total - used
    used_pct = (used / total) * 100
    return ProxLbData.Node.Metric(
        total=total,
        assigned=int(used),
        used=used,
        free=free,
        assigned_percent=used_pct,
        free_percent=(free / total) * 100,
        used_percent=used_pct,
        pressure_some_percent=0.0,
        pressure_full_percent=0.0,
        pressure_some_spikes_percent=0.0,
        pressure_full_spikes_percent=0.0,
        pressure_hot=False,
    )


def _make_node(name: str, total_gb: float, used_gb: float) -> ProxLbData.Node:
    mem = _make_node_metric(total_gb, used_gb)
    cpu = _make_node_metric(16, 2)
    disk = _make_node_metric(500, 100)
    return ProxLbData.Node(
        name=name,
        pve_version="9",
        maintenance=False,
        pressure_hot=False,
        cpu=cpu,
        memory=mem,
        disk=disk,
    )


def _make_guest(name: str, node: str, memory_gb: float) -> ProxLbData.Guest:
    used = memory_gb * GB
    mem = ProxLbData.Guest.Metric(
        total=int(used),
        used=used,
        pressure_some_percent=0.0,
        pressure_full_percent=0.0,
        pressure_some_spikes_percent=0.0,
        pressure_full_spikes_percent=0.0,
        pressure_hot=False,
    )
    cpu = ProxLbData.Guest.Metric(
        total=1, used=0.1,
        pressure_some_percent=0.0, pressure_full_percent=0.0,
        pressure_some_spikes_percent=0.0, pressure_full_spikes_percent=0.0,
        pressure_hot=False,
    )
    disk = ProxLbData.Guest.Metric(
        total=int(GB), used=0.5 * GB,
        pressure_some_percent=0.0, pressure_full_percent=0.0,
        pressure_some_spikes_percent=0.0, pressure_full_spikes_percent=0.0,
        pressure_hot=False,
    )
    return ProxLbData.Guest(
        name=name,
        id=100,
        node_current=node,
        node_target=node,
        processed=False,
        pressure_hot=False,
        tags=[],
        pools=[],
        ha_rules=[],
        affinity_groups=[name],
        anti_affinity_groups=[],
        ignore=False,
        node_relationships=[],
        node_relationships_strict=False,
        type=Config.GuestType.Vm,
        cpu=cpu,
        memory=mem,
        disk=disk,
    )


def _build_proxlb_data(
    node_total_gb: float,
    node_used_gb: float,
    guest_memory_gb: float,
    target_node: str = "node1",
) -> ProxLbData:
    nodes = {target_node: _make_node(target_node, node_total_gb, node_used_gb)}
    guests = {"vm100": _make_guest("vm100", target_node, guest_memory_gb)}
    return ProxLbData(
        guests=guests,
        ha_rules={},
        nodes=nodes,
        pools={},
        groups=ProxLbData.Groups(affinity={}),
        meta=ProxLbData.Meta(
            proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
            cluster_non_pve9=False,
            balancing=ProxLbData.Meta.Balancing(
                method=BalancingResource.Memory,
                balanciness=5,
                enable=True,
                balance=True,
                balance_types=[Config.GuestType.Vm],
                balance_larger_guests_first=False,
                balance_next_node=target_node,
            ),
        ),
    )


def test_guest_fits_in_free_memory() -> None:
    """Node has 25 GB free; 10 GB guest fits."""
    proxlb_data = _build_proxlb_data(
        node_total_gb=100, node_used_gb=75, guest_memory_gb=10
    )
    assert Calculations.validate_node_resources(proxlb_data, "vm100") is True


def test_guest_exceeds_free_memory() -> None:
    """Node has 25 GB free; 30 GB guest does not fit."""
    proxlb_data = _build_proxlb_data(
        node_total_gb=100, node_used_gb=75, guest_memory_gb=30
    )
    assert Calculations.validate_node_resources(proxlb_data, "vm100") is False


def test_guest_exactly_equals_free_memory() -> None:
    """Guest memory equals free memory exactly — must be rejected (not strictly less)."""
    proxlb_data = _build_proxlb_data(
        node_total_gb=100, node_used_gb=75, guest_memory_gb=25
    )
    assert Calculations.validate_node_resources(proxlb_data, "vm100") is False


def test_no_target_node_returns_false() -> None:
    """When balance_next_node is None, validation must return False."""
    proxlb_data = _build_proxlb_data(
        node_total_gb=100, node_used_gb=75, guest_memory_gb=10
    )
    proxlb_data.meta.balancing.balance_next_node = None
    assert Calculations.validate_node_resources(proxlb_data, "vm100") is False
