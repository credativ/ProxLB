"""
Unit tests for relocate_guests in class Calculations.

Reproduces the real-world scenario where all guests from the most loaded
node were moved to the same (least loaded) target, overloading it instead
of distributing guests across available nodes.

Before the fix, get_most_free_node was only called once globally. After the
fix it is recalculated before each guest migration so that updated node
statistics are taken into account.
"""

__author__ = "Alexander Wirt <formorer>"
__copyright__ = "Copyright (C) 2026 Alexander Wirt for credativ GmbH"
__license__ = "GPL-3.0"


from proxlb.models.calculations import Calculations


GB = 1024 ** 3


def _make_node(name, mem_used_gb, mem_total_gb):
    """Helper to build a node dict with consistent fields."""
    used = mem_used_gb * GB
    total = mem_total_gb * GB
    free = total - used
    return {
        "name": name,
        "maintenance": False,
        "ignore": False,
        # Memory
        "memory_total": total,
        "memory_used": used,
        "memory_free": free,
        "memory_used_percent": (used / total) * 100,
        "memory_assigned": used,
        "memory_assigned_percent": (used / total) * 100,
        # CPU (minimal, not the balancing metric here)
        "cpu_total": 4,
        "cpu_used": 1,
        "cpu_free": 3,
        "cpu_used_percent": 25.0,
        "cpu_assigned": 1,
        "cpu_assigned_percent": 25.0,
        # Disk (minimal)
        "disk_total": 100 * GB,
        "disk_used": 10 * GB,
        "disk_free": 90 * GB,
        "disk_used_percent": 10.0,
        "disk_assigned": 10 * GB,
        "disk_assigned_percent": 10.0,
    }


def _make_guest(name, mem_used_gb, node, guest_id=100):
    """Helper to build a guest dict."""
    used = mem_used_gb * GB
    return {
        "name": name,
        "id": guest_id,
        "type": "vm",
        "node_current": node,
        "node_target": node,
        "node_relationships": [],
        "node_relationships_strict": False,
        "processed": False,
        "ignore": False,
        "memory_total": used,
        "memory_used": used,
        "cpu_total": 1,
        "cpu_used": 0.1,
        "disk_total": 1 * GB,
        "disk_used": 0.5 * GB,
    }


def _build_proxlb_data(nodes, guests, balanciness=5):
    """Build a complete proxlb_data dict from nodes and guests."""
    affinity_groups = {}
    for guest_name, guest in guests.items():
        affinity_groups[guest_name] = {
            "guests": [guest_name],
            "counter": 1,
            "memory_used": guest["memory_used"],
        }

    return {
        "nodes": nodes,
        "guests": guests,
        "groups": {
            "affinity": affinity_groups,
            "anti_affinity": {},
            "maintenance": [],
        },
        "meta": {
            "balancing": {
                "method": "memory",
                "mode": "used",
                "balanciness": balanciness,
                "balance": True,
                "balance_next_node": None,
                "balance_next_guest": "",
                "balance_types": ["vm", "ct"],
                "balance_larger_guests_first": False,
                "enable": True,
            }
        },
    }


def _build_stacking_scenario():
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
        "node-A": _make_node("node-A", 9.0, 10.0),
        "node-B": _make_node("node-B", 0.5, 10.0),
        "node-C": _make_node("node-C", 3.0, 10.0),
    }
    guests = {
        "g-small": _make_guest("g-small", 3.5, "node-A", 101),
        "g-large": _make_guest("g-large", 4.5, "node-A", 102),
    }
    return _build_proxlb_data(nodes, guests, balanciness=5)


def test_target_node_is_recalculated_between_migrations():
    """
    After moving g-small to node-B, node-B is no longer the most free.
    The fix ensures get_most_free_node is called again, so g-large
    goes to node-C instead of also stacking on node-B.
    """
    proxlb_data = _build_stacking_scenario()

    Calculations.get_most_free_node(proxlb_data)
    assert proxlb_data["meta"]["balancing"]["balance_next_node"] == "node-B"

    Calculations.relocate_guests(proxlb_data)

    targets = {
        name: g["node_target"]
        for name, g in proxlb_data["guests"].items()
    }

    # g-small should go to node-B (initially most free at 5%)
    assert targets["g-small"] == "node-B", (
        f"Expected g-small -> node-B, got {targets['g-small']}"
    )

    # After g-small moves, node-B is at 40%, node-C is at 30%.
    # With the fix, g-large should go to node-C (now most free).
    assert targets["g-large"] == "node-C", (
        f"Expected g-large -> node-C (most free after first migration), "
        f"got {targets['g-large']}"
    )


def test_no_node_overloaded_beyond_source():
    """
    Without the fix, stacking all guests on node-B would push it to 85%,
    nearly as bad as the original source (90%). The fix must prevent any
    target node from exceeding the original source load.
    """
    proxlb_data = _build_stacking_scenario()

    max_load_before = max(
        n["memory_used_percent"] for n in proxlb_data["nodes"].values()
    )

    Calculations.get_most_free_node(proxlb_data)
    Calculations.relocate_guests(proxlb_data)

    for name, node in proxlb_data["nodes"].items():
        assert node["memory_used_percent"] <= max_load_before + 0.1, (
            f"Node {name} ended up at {node['memory_used_percent']:.1f}%, "
            f"exceeding the initial max of {max_load_before:.1f}%"
        )


def test_spread_improves_after_relocation():
    """
    The projected spread after relocation must be strictly better
    than the initial spread.
    """
    proxlb_data = _build_stacking_scenario()

    mem_percents_before = [
        n["memory_used_percent"] for n in proxlb_data["nodes"].values()
    ]
    spread_before = max(mem_percents_before) - min(mem_percents_before)

    Calculations.get_most_free_node(proxlb_data)
    Calculations.relocate_guests(proxlb_data)

    mem_percents_after = [
        n["memory_used_percent"] for n in proxlb_data["nodes"].values()
    ]
    spread_after = max(mem_percents_after) - min(mem_percents_after)

    assert spread_after < spread_before, (
        f"Spread should improve: before={spread_before:.1f}pp, "
        f"after={spread_after:.1f}pp"
    )


def test_original_report_scenario():
    """
    Reproduces the cluster state from the original bug report:

      Node 158: 57%  (8.9 / 15.6 GB)
      Node 166: 11%  (1.7 / 15.6 GB)
      Node 172: 74%  (11.5 / 15.6 GB)

    With smaller-first ordering, the small guests move first and the
    balanciness re-check stops before the cluster gets worse.
    The spread must improve, and no node should exceed the initial max.
    """
    mem_total = 15.6
    nodes = {
        "node-158": _make_node("node-158", 8.9, mem_total),
        "node-166": _make_node("node-166", 1.7, mem_total),
        "node-172": _make_node("node-172", 11.5, mem_total),
    }
    guests = {
        "itc-ballast-03": _make_guest("itc-ballast-03", 7.60, "node-172", 101),
        "itc-ballast-02": _make_guest("itc-ballast-02", 1.99, "node-172", 102),
        "itc-grml": _make_guest("itc-grml", 0.03, "node-172", 103),
    }
    proxlb_data = _build_proxlb_data(nodes, guests, balanciness=50)

    spread_before = max(
        n["memory_used_percent"] for n in proxlb_data["nodes"].values()
    ) - min(
        n["memory_used_percent"] for n in proxlb_data["nodes"].values()
    )

    Calculations.get_most_free_node(proxlb_data)
    Calculations.relocate_guests(proxlb_data)

    spread_after = max(
        n["memory_used_percent"] for n in proxlb_data["nodes"].values()
    ) - min(
        n["memory_used_percent"] for n in proxlb_data["nodes"].values()
    )

    assert spread_after < spread_before, (
        f"Spread should improve: before={spread_before:.1f}pp, "
        f"after={spread_after:.1f}pp"
    )

    max_before = max(
        n["memory_used_percent"] for n in proxlb_data["nodes"].values()
    )
    assert max_before <= 73.8, (
        f"No node should exceed original max load, got {max_before:.1f}%"
    )
