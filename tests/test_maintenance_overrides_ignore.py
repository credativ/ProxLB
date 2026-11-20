"""Tests for balancing.maintenance_overrides_ignore (issue #130)."""
import unittest

from proxlb.models.calculations import Calculations
from proxlb.models.groups import Groups
from proxlb.models.guests import Guests
from proxlb.utils.config_parser import Config
from proxlb.utils.proxlb_data import ProxLbData
from tests.utils import create_guest, create_node


def config(enabled: bool) -> Config:
    return Config(
        proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
        proxmox_cluster=Config.ProxmoxCluster(maintenance_nodes=["pve01"]),
        balancing=Config.Balancing(maintenance_overrides_ignore=enabled),
    )


def cluster() -> tuple[dict[str, ProxLbData.Node], dict[str, ProxLbData.Guest]]:
    """A and B: ignored + anti-affinity 'foo'. A runs on pve01 (maintenance), B on pve02."""
    nodes = {name: create_node(name) for name in ("pve01", "pve02", "pve03")}
    nodes["pve01"].maintenance = True
    nodes["pve02"].memory.used_percent = 10.0  # emptiest node: plain balancing would pick it
    nodes["pve02"].memory.free = 90.0
    nodes["pve03"].memory.used_percent = 40.0
    nodes["pve03"].memory.free = 60.0
    guests = {
        "A": create_guest("A", "pve01", "pve01"),
        "B": create_guest("B", "pve02", "pve02"),
        "C": create_guest("C", "pve03", "pve03"),
    }
    for name in ("A", "B"):
        guests[name].ignore = True
        guests[name].anti_affinity_groups = ["plb_anti_affinity_foo"]
    return nodes, guests


class TestMaintenanceOverridesIgnore(unittest.TestCase):
    def test_disabled_by_default_keeps_guests_ignored(self) -> None:
        self.assertFalse(Config.Balancing().maintenance_overrides_ignore)
        nodes, guests = cluster()
        self.assertEqual(Guests.apply_maintenance_override(guests, nodes, config(False)), [])
        self.assertTrue(guests["A"].ignore)
        self.assertTrue(guests["B"].ignore)

    def test_enabled_only_guests_on_maintenance_nodes(self) -> None:
        nodes, guests = cluster()
        self.assertEqual(Guests.apply_maintenance_override(guests, nodes, config(True)), ["A"])
        self.assertFalse(guests["A"].ignore)  # on the maintenance node: no longer ignored
        self.assertTrue(guests["B"].ignore)   # elsewhere: still ignored, never balanced

    def test_guest_leaves_maintenance_node_and_respects_anti_affinity(self) -> None:
        nodes, guests = cluster()
        Guests.apply_maintenance_override(guests, nodes, config(True))
        groups = Groups.get_groups(guests, nodes)
        self.assertIn("A", groups.maintenance)
        data = ProxLbData(
            guests=guests, ha_rules={}, nodes=nodes, pools={}, groups=groups,
            meta=ProxLbData.Meta(
                proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
                cluster_non_pve9=False,
                balancing=ProxLbData.Meta.Balancing(
                    method=Config.Balancing.Resource.Memory,
                    mode=Config.Balancing.Mode.Used,
                    balanciness=5, enable=True, balance=False, enforce_affinity=True,
                    balance_types=[Config.GuestType.Vm], balance_larger_guests_first=False,
                ),
            ),
        )
        Calculations.relocate_guests_on_maintenance_nodes(data)
        Calculations.relocate_guests(data)
        # A must leave pve01 and must not join B (anti-affinity 'foo') on the emptier pve02
        self.assertEqual(data.guests["A"].node_target, "pve03")
        self.assertEqual(data.guests["B"].node_target, "pve02")  # B stays ignored and is not moved


if __name__ == "__main__":
    unittest.main()
