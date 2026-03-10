"""
Unit tests for relocate_guests in class Calculations.
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
    free_pct = (free / total) * 100
    assigned_pct = used_pct
    return ProxLbData.Node.Metric(
        total=total,
        assigned=int(used),
        used=used,
        free=free,
        assigned_percent=assigned_pct,
        free_percent=free_pct,
        used_percent=used_pct,
        pressure_some_percent=0.0,
        pressure_full_percent=0.0,
        pressure_some_spikes_percent=0.0,
        pressure_full_spikes_percent=0.0,
        pressure_hot=False,
    )


def _make_node(name: str, total_gb: float, used_gb: float) -> ProxLbData.Node:
    mem = _make_node_metric(total_gb, used_gb)
    cpu = _make_node_metric(4, 1)
    disk = _make_node_metric(100, 10)
    return ProxLbData.Node(
        name=name,
        pve_version="9",
        maintenance=False,
        pressure_hot=False,
        cpu=cpu,
        memory=mem,
        disk=disk,
    )


def _make_guest_metric(memory_gb: float) -> ProxLbData.Guest.Metric:
    used = memory_gb * GB
    return ProxLbData.Guest.Metric(
        total=int(used),
        used=used,
        pressure_some_percent=0.0,
        pressure_full_percent=0.0,
        pressure_some_spikes_percent=0.0,
        pressure_full_spikes_percent=0.0,
        pressure_hot=False,
    )


def _make_guest(
    name: str, node: str, memory_gb: float, guest_id: int = 100
) -> ProxLbData.Guest:
    mem = _make_guest_metric(memory_gb)
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
        id=guest_id,
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


def _make_affinity_group(
    guest_name: str, memory_gb: float
) -> ProxLbData.Groups.Affinity:
    used = memory_gb * GB
    return ProxLbData.Groups.Affinity(
        counter=1,
        guests=[guest_name],
        cpu=ProxLbData.Groups.Affinity.Metric(total=1, used=0.1),
        disk=ProxLbData.Groups.Affinity.Metric(total=int(GB), used=int(0.5 * GB)),
        memory=ProxLbData.Groups.Affinity.Metric(total=int(used), used=used),
    )


def _build_stacking_scenario() -> ProxLbData:
    """
    Scenario that triggers the stacking bug:

      Node A: 90%  (9.0 / 10 GB)  -- source, heavily loaded
      Node B:  5%  (0.5 / 10 GB)  -- initial target, most free
      Node C: 30%  (3.0 / 10 GB)  -- mid-range

    Guests on Node A (sorted smaller-first):
      g-small:  3.5 GB  (moves first)
      g-large:  4.5 GB  (moves second)

    Without the fix (stale target):
      Both go to B -> B = 85%, A = 10%. Spread: 75%.

    With the fix (recalculated target):
      g-small -> B (5%):  B = 40%, A = 55%
      g-large -> C (30%): C = 75%, A = 10%
      Spread: 75 - 10 = 65%, but no node hit 85%.
    """
    nodes = {
        "node-A": _make_node("node-A", total_gb=10.0, used_gb=9.0),
        "node-B": _make_node("node-B", total_gb=10.0, used_gb=0.5),
        "node-C": _make_node("node-C", total_gb=10.0, used_gb=3.0),
    }
    guests = {
        "g-small": _make_guest("g-small", "node-A", memory_gb=3.5, guest_id=101),
        "g-large": _make_guest("g-large", "node-A", memory_gb=4.5, guest_id=102),
    }
    affinity = {
        "g-small": _make_affinity_group("g-small", memory_gb=3.5),
        "g-large": _make_affinity_group("g-large", memory_gb=4.5),
    }

    return ProxLbData(
        guests=guests,
        ha_rules={},
        nodes=nodes,
        pools={},
        groups=ProxLbData.Groups(affinity=affinity),
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
            ),
        ),
    )


def test_no_node_overloaded_beyond_source() -> None:
    """
    Without the fix, stacking all guests on node-B would push it to 85%,
    nearly as bad as the original source (90%). The fix must prevent any
    target node from exceeding the original source load.
    """
    proxlb_data = _build_stacking_scenario()

    max_load_before = max(
        n.metric(BalancingResource.Memory).used_percent
        for n in proxlb_data.nodes.values()
    )

    Calculations.get_most_free_node(proxlb_data)
    Calculations.relocate_guests(proxlb_data)

    for name, node in proxlb_data.nodes.items():
        assert node.metric(BalancingResource.Memory).used_percent <= max_load_before + 0.1, (
            f"Node {name} ended up at "
            f"{node.metric(BalancingResource.Memory).used_percent:.1f}%, "
            f"exceeding the initial max of {max_load_before:.1f}%"
        )


def test_spread_improves_after_relocation() -> None:
    """
    The projected spread after relocation must be strictly better
    than the initial spread.
    """
    proxlb_data = _build_stacking_scenario()

    percents_before = [
        n.metric(BalancingResource.Memory).used_percent
        for n in proxlb_data.nodes.values()
    ]
    spread_before = max(percents_before) - min(percents_before)

    Calculations.get_most_free_node(proxlb_data)
    Calculations.relocate_guests(proxlb_data)

    percents_after = [
        n.metric(BalancingResource.Memory).used_percent
        for n in proxlb_data.nodes.values()
    ]
    spread_after = max(percents_after) - min(percents_after)

    assert spread_after < spread_before, (
        f"Spread should improve: before={spread_before:.1f}pp, "
        f"after={spread_after:.1f}pp"
    )


def test_real_world_scenario() -> None:
    """
    Real-world cluster state that triggered the stacking bug:

      Node 158: 57%  (8.9 / 15.6 GB)
      Node 166: 11%  (1.7 / 15.6 GB)
      Node 172: 74%  (11.5 / 15.6 GB)

    With smaller-first ordering, the small guests move first and the
    balanciness re-check stops before the cluster gets worse.
    The spread must improve, and no node should exceed the initial max.
    """
    mem_total = 15.6
    nodes = {
        "node-158": _make_node("node-158", total_gb=mem_total, used_gb=8.9),
        "node-166": _make_node("node-166", total_gb=mem_total, used_gb=1.7),
        "node-172": _make_node("node-172", total_gb=mem_total, used_gb=11.5),
    }
    guests = {
        "itc-ballast-03": _make_guest("itc-ballast-03", "node-172", 7.60, 101),
        "itc-ballast-02": _make_guest("itc-ballast-02", "node-172", 1.99, 102),
        "itc-grml": _make_guest("itc-grml", "node-172", 0.03, 103),
    }
    affinity = {
        name: _make_affinity_group(name, g.memory.total / GB)
        for name, g in guests.items()
    }
    proxlb_data = ProxLbData(
        guests=guests,
        ha_rules={},
        nodes=nodes,
        pools={},
        groups=ProxLbData.Groups(affinity=affinity),
        meta=ProxLbData.Meta(
            proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
            cluster_non_pve9=False,
            balancing=ProxLbData.Meta.Balancing(
                method=BalancingResource.Memory,
                balanciness=50,
                enable=True,
                balance=True,
                balance_types=[Config.GuestType.Vm],
                balance_larger_guests_first=False,
            ),
        ),
    )

    percents_before = [
        n.metric(BalancingResource.Memory).used_percent
        for n in proxlb_data.nodes.values()
    ]
    spread_before = max(percents_before) - min(percents_before)

    Calculations.get_most_free_node(proxlb_data)
    Calculations.relocate_guests(proxlb_data)

    percents_after = [
        n.metric(BalancingResource.Memory).used_percent
        for n in proxlb_data.nodes.values()
    ]
    spread_after = max(percents_after) - min(percents_after)

    assert spread_after < spread_before, (
        f"Spread should improve: before={spread_before:.1f}pp, "
        f"after={spread_after:.1f}pp"
    )

    max_after = max(percents_after)
    initial_max = max(percents_before)
    assert max_after <= initial_max + 0.1, (
        f"No node should exceed original max load, "
        f"got {max_after:.1f}% vs initial max {initial_max:.1f}%"
    )
