"""
Unit tests for val_anti_affinity in class Calculations.

Reproduces the real-world scenario where a guest of an anti-affinity group was
relocated onto the node of one of its group members. The target node was picked
as the first node of the cluster that was not yet listed in used_nodes, which
starts out empty. Neither the nodes of the other group members nor the node that
was chosen by the resource based balancing were taken into account.

Before the fix, the target node of the balancing was always overwritten by the
first node in the cluster. After the fix it is kept whenever it is free of other
group members, and replaced by the most free node that is free of them otherwise.
"""

__author__ = "Peter Dreuw <peter.dreuw@credativ.de>"
__copyright__ = "Copyright (C) 2026 Peter Dreuw for credativ GmbH"
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


def _make_node(
    name: str, used_gb: float, total_gb: float = 16.0, maintenance: bool = False
) -> ProxLbData.Node:
    return ProxLbData.Node(
        name=name,
        pve_version="9",
        maintenance=maintenance,
        pressure_hot=False,
        cpu=_make_node_metric(4, 1),
        memory=_make_node_metric(total_gb, used_gb),
        disk=_make_node_metric(100, 10),
    )


def _make_guest_metric(total: int, used: float) -> ProxLbData.Guest.Metric:
    return ProxLbData.Guest.Metric(
        total=total,
        used=used,
        pressure_some_percent=0.0,
        pressure_full_percent=0.0,
        pressure_some_spikes_percent=0.0,
        pressure_full_spikes_percent=0.0,
        pressure_hot=False,
    )


def _make_guest(
    name: str, node: str, memory_gb: float, guest_id: int, anti_affinity_groups: list[str]
) -> ProxLbData.Guest:
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
        anti_affinity_groups=anti_affinity_groups,
        ignore=False,
        node_relationships=[],
        node_relationships_strict=False,
        type=Config.GuestType.Vm,
        cpu=_make_guest_metric(1, 0.1),
        memory=_make_guest_metric(int(memory_gb * GB), memory_gb * GB),
        disk=_make_guest_metric(int(GB), 0.5 * GB),
    )


def _build_data(
    nodes: dict[str, ProxLbData.Node],
    guests: dict[str, ProxLbData.Guest],
) -> ProxLbData:
    anti_affinity: dict[str, ProxLbData.Groups.AntiAffinity] = {}

    for guest_name, guest_meta in guests.items():
        for group_name in guest_meta.anti_affinity_groups:
            if group_name not in anti_affinity:
                anti_affinity[group_name] = ProxLbData.Groups.AntiAffinity(guests=[guest_name])
            else:
                anti_affinity[group_name].guests.append(guest_name)
                anti_affinity[group_name].counter += 1

    return ProxLbData(
        guests=guests,
        ha_rules={},
        nodes=nodes,
        pools={},
        groups=ProxLbData.Groups(anti_affinity=anti_affinity),
        meta=ProxLbData.Meta(
            proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
            cluster_non_pve9=False,
            balancing=ProxLbData.Meta.Balancing(
                method=BalancingResource.Memory,
                balanciness=10,
                enable=True,
                balance=True,
                balance_types=[Config.GuestType.Vm],
            ),
        ),
    )


def _build_homelab_scenario() -> ProxLbData:
    """
    Cluster state that triggered the bug on a three node homelab cluster:

      pve1: 50%  -- holds plb-t1, first node of the cluster
      pve2: 70%  -- holds plb-t2, most loaded node, source of the migration
      pve3: 10%  -- most free node, the expected target

    plb-t1 and plb-t2 are members of the same anti-affinity group, so plb-t2
    must never be relocated to pve1.
    """
    nodes = {
        "pve1": _make_node("pve1", used_gb=8.0),
        "pve2": _make_node("pve2", used_gb=11.2),
        "pve3": _make_node("pve3", used_gb=1.6),
    }
    guests = {
        "plb-t1": _make_guest("plb-t1", "pve1", 1.0, 9001, ["plb-test-anti"]),
        "plb-t2": _make_guest("plb-t2", "pve2", 1.0, 9002, ["plb-test-anti"]),
    }
    return _build_data(nodes, guests)


def test_target_node_of_group_member_is_not_used() -> None:
    """
    pve3 is the most free node and free of group members, so it must be kept.
    Before the fix, pve1 was chosen because it is the first node of the cluster
    and used_nodes was still empty, which placed plb-t2 next to plb-t1.
    """
    proxlb_data = _build_homelab_scenario()

    Calculations.get_most_free_node(proxlb_data)
    assert proxlb_data.meta.balancing.balance_next_node == "pve3"

    Calculations.val_anti_affinity(proxlb_data, "plb-t2")

    assert proxlb_data.meta.balancing.balance_next_node == "pve3", (
        f"Expected plb-t2 -> pve3, got "
        f"{proxlb_data.meta.balancing.balance_next_node} "
        f"(pve1 holds its anti-affinity partner plb-t1)"
    )


def test_blocked_target_is_replaced_by_most_free_free_node() -> None:
    """
    If the most free node holds a group member, the most free of the remaining
    nodes must be used instead of the first node of the cluster.

      pve1: 70%  -- source, holds the guest
      pve2: 40%  -- expected target
      pve3: 10%  -- most free, but holds the anti-affinity partner
    """
    nodes = {
        "pve1": _make_node("pve1", used_gb=11.2),
        "pve2": _make_node("pve2", used_gb=6.4),
        "pve3": _make_node("pve3", used_gb=1.6),
    }
    guests = {
        "guest-a": _make_guest("guest-a", "pve1", 1.0, 101, ["group-1"]),
        "guest-b": _make_guest("guest-b", "pve3", 1.0, 102, ["group-1"]),
    }
    proxlb_data = _build_data(nodes, guests)

    Calculations.get_most_free_node(proxlb_data)
    assert proxlb_data.meta.balancing.balance_next_node == "pve3"

    Calculations.val_anti_affinity(proxlb_data, "guest-a")

    assert proxlb_data.meta.balancing.balance_next_node == "pve2", (
        f"Expected guest-a -> pve2, got "
        f"{proxlb_data.meta.balancing.balance_next_node}"
    )


def test_planned_target_of_group_member_is_respected() -> None:
    """
    A group member that was already planned for a migration blocks its target
    node, not the node it is currently running on.

      pve1: 70%  -- source, holds guest-a
      pve2: 40%  -- holds guest-b, which is already planned to move to pve3
      pve3: 10%  -- most free, but reserved for guest-b
    """
    nodes = {
        "pve1": _make_node("pve1", used_gb=11.2),
        "pve2": _make_node("pve2", used_gb=6.4),
        "pve3": _make_node("pve3", used_gb=1.6),
    }
    guests = {
        "guest-a": _make_guest("guest-a", "pve1", 1.0, 101, ["group-1"]),
        "guest-b": _make_guest("guest-b", "pve2", 1.0, 102, ["group-1"]),
    }
    proxlb_data = _build_data(nodes, guests)
    proxlb_data.guests["guest-b"].node_target = "pve3"

    Calculations.get_most_free_node(proxlb_data)
    assert proxlb_data.meta.balancing.balance_next_node == "pve3"

    Calculations.val_anti_affinity(proxlb_data, "guest-a")

    assert proxlb_data.meta.balancing.balance_next_node == "pve2", (
        f"Expected guest-a -> pve2 because pve3 is reserved for guest-b, got "
        f"{proxlb_data.meta.balancing.balance_next_node}"
    )


def test_group_members_are_spread_over_distinct_nodes() -> None:
    """
    Three members of the same group that are all stacked on the same node must
    end up on three distinct nodes.
    """
    nodes = {
        "pve1": _make_node("pve1", used_gb=12.0),
        "pve2": _make_node("pve2", used_gb=1.6),
        "pve3": _make_node("pve3", used_gb=1.6),
    }
    guests = {
        "guest-a": _make_guest("guest-a", "pve1", 1.0, 101, ["group-1"]),
        "guest-b": _make_guest("guest-b", "pve1", 1.0, 102, ["group-1"]),
        "guest-c": _make_guest("guest-c", "pve1", 1.0, 103, ["group-1"]),
    }
    proxlb_data = _build_data(nodes, guests)

    for guest_name in ("guest-a", "guest-b", "guest-c"):
        Calculations.get_most_free_node(proxlb_data)
        Calculations.val_anti_affinity(proxlb_data, guest_name)
        proxlb_data.meta.balancing.balance_next_guest = guest_name
        Calculations.update_node_resources(proxlb_data)
        proxlb_data.guests[guest_name].processed = True

    targets = [guest.node_target for guest in proxlb_data.guests.values()]

    assert len(set(targets)) == 3, (
        f"Expected all three group members on distinct nodes, got {targets}"
    )


def test_no_relocation_without_a_valid_node() -> None:
    """
    If every node either holds a group member, is in maintenance or cannot host
    the guest, no target node must be handed out at all.

      pve1: holds the guest and its anti-affinity partner
      pve2: in maintenance
      pve3: too small for the guest
    """
    nodes = {
        "pve1": _make_node("pve1", used_gb=8.0),
        "pve2": _make_node("pve2", used_gb=1.6, maintenance=True),
        "pve3": _make_node("pve3", used_gb=15.5),
    }
    guests = {
        "guest-a": _make_guest("guest-a", "pve1", 4.0, 101, ["group-1"]),
        "guest-b": _make_guest("guest-b", "pve1", 1.0, 102, ["group-1"]),
    }
    proxlb_data = _build_data(nodes, guests)

    Calculations.get_most_free_node(proxlb_data)
    Calculations.val_anti_affinity(proxlb_data, "guest-a")

    assert proxlb_data.meta.balancing.balance_next_node is None, (
        f"Expected no target node, got "
        f"{proxlb_data.meta.balancing.balance_next_node}"
    )

    proxlb_data.meta.balancing.balance_next_guest = "guest-a"
    Calculations.update_node_resources(proxlb_data)

    assert proxlb_data.guests["guest-a"].node_target == "pve1", (
        "Guest must stay on its current node when no valid target node is left"
    )


def test_guest_without_anti_affinity_keeps_its_target() -> None:
    """
    A guest that is not part of any anti-affinity group must keep the target
    node that was chosen by the resource based balancing.
    """
    proxlb_data = _build_homelab_scenario()
    proxlb_data.guests["plb-t3"] = _make_guest("plb-t3", "pve2", 1.0, 9003, [])

    Calculations.get_most_free_node(proxlb_data)
    Calculations.val_anti_affinity(proxlb_data, "plb-t3")

    assert proxlb_data.meta.balancing.balance_next_node == "pve3"


def test_single_member_group_keeps_its_target() -> None:
    """
    An anti-affinity group with a single member cannot be violated, so the
    target node of the balancing must be kept.
    """
    nodes = {
        "pve1": _make_node("pve1", used_gb=8.0),
        "pve2": _make_node("pve2", used_gb=11.2),
        "pve3": _make_node("pve3", used_gb=1.6),
    }
    guests = {
        "guest-a": _make_guest("guest-a", "pve2", 1.0, 101, ["group-1"]),
    }
    proxlb_data = _build_data(nodes, guests)

    Calculations.get_most_free_node(proxlb_data)
    Calculations.val_anti_affinity(proxlb_data, "guest-a")

    assert proxlb_data.meta.balancing.balance_next_node == "pve3"


def test_ignored_guest_does_not_reserve_a_node() -> None:
    """
    An ignored guest is never relocated. It must not reserve a node for its
    anti-affinity group, otherwise it keeps a valid target node away from the
    group members that are really moved.
    """
    proxlb_data = _build_homelab_scenario()
    proxlb_data.guests["plb-t2"].ignore = True

    proxlb_data.meta.balancing.balance_next_node = "pve3"
    Calculations.val_anti_affinity(proxlb_data, "plb-t2")

    assert proxlb_data.groups.anti_affinity["plb-test-anti"].used_nodes == []

    # The partner must still be able to use pve3
    proxlb_data.meta.balancing.balance_next_node = "pve3"
    Calculations.val_anti_affinity(proxlb_data, "plb-t1")

    assert proxlb_data.meta.balancing.balance_next_node == "pve3"


def test_processed_guest_is_not_reassigned() -> None:
    """
    A guest that was already processed in this run must not be touched again.
    """
    proxlb_data = _build_homelab_scenario()
    proxlb_data.guests["plb-t2"].processed = True

    proxlb_data.meta.balancing.balance_next_node = "pve1"
    Calculations.val_anti_affinity(proxlb_data, "plb-t2")

    assert proxlb_data.meta.balancing.balance_next_node == "pve1"
    assert proxlb_data.groups.anti_affinity["plb-test-anti"].used_nodes == []
