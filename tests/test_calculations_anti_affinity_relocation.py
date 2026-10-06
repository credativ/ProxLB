import unittest
from tests.utils import create_guest, create_node
from proxlb.models.calculations import Calculations
from proxlb.utils.config_parser import Config
from proxlb.utils.proxlb_data import ProxLbData


class TestAntiAffinityRelocation(unittest.TestCase):
    def test_anti_affinity_avoids_co_located_node(self):
        # 3 nodes: pve01, pve02, pve03
        node1 = create_node("pve01")
        node2 = create_node("pve02")
        node3 = create_node("pve03")
        nodes = {"pve01": node1, "pve02": node2, "pve03": node3}

        # vm1 is on pve01 with group "grp"
        # vm2 is being evaluated for relocation
        vm1 = create_guest("vm1", "pve01", "pve01")
        vm1.anti_affinity_groups = ["plb_anti_affinity_k8s"]
        vm2 = create_guest("vm2", "pve01", "pve01")
        vm2.anti_affinity_groups = ["plb_anti_affinity_k8s"]

        groups = ProxLbData.Groups()
        groups.anti_affinity["plb_anti_affinity_k8s"] = ProxLbData.Groups.AntiAffinity(
            guests=["vm1", "vm2"], counter=2, used_nodes=[]
        )

        data = ProxLbData(
            guests={"vm1": vm1, "vm2": vm2},
            ha_rules={},
            nodes=nodes,
            pools={},
            groups=groups,
            meta=ProxLbData.Meta(
                proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
                cluster_non_pve9=False,
                balancing=ProxLbData.Meta.Balancing(
                    method=Config.Balancing.Resource.Memory,
                    balanciness=5,
                    enable=True,
                    balance=True,
                    enforce_affinity=True,
                    balance_types=[Config.GuestType.Vm],
                    balance_larger_guests_first=False,
                ),
            ),
        )

        # val_anti_affinity for vm2 should NOT assign pve01 because vm1 is on pve01
        Calculations.val_anti_affinity(data, "vm2")
        self.assertIn(
            data.meta.balancing.balance_next_node,
            ["pve02", "pve03"],
            f"Expected target node to be pve02 or pve03, but got {data.meta.balancing.balance_next_node}"
        )

    def test_relocate_anti_affinity_from_non_highest_node(self):
        # pve01 is highest load (70%), pve02 has vm1 and vm2 (anti-affinity conflict)
        node1 = create_node("pve01")
        node1.memory.used_percent = 70.0
        node1.memory.free = 30.0

        node2 = create_node("pve02")
        node2.memory.used_percent = 50.0
        node2.memory.free = 50.0

        node3 = create_node("pve03")
        node3.memory.used_percent = 20.0
        node3.memory.free = 80.0

        nodes = {"pve01": node1, "pve02": node2, "pve03": node3}

        vm1 = create_guest("vm1", "pve02", "pve02")
        vm1.anti_affinity_groups = ["plb_anti_affinity_k8s"]
        vm1.affinity_groups = ["vm1"]
        vm2 = create_guest("vm2", "pve02", "pve02")
        vm2.anti_affinity_groups = ["plb_anti_affinity_k8s"]
        vm2.affinity_groups = ["vm2"]

        groups = ProxLbData.Groups()
        metric = ProxLbData.Groups.Affinity.Metric(total=1, used=1.0)
        groups.affinity["vm1"] = ProxLbData.Groups.Affinity(guests=["vm1"], cpu=metric, disk=metric, memory=metric)
        groups.affinity["vm2"] = ProxLbData.Groups.Affinity(guests=["vm2"], cpu=metric, disk=metric, memory=metric)
        groups.anti_affinity["plb_anti_affinity_k8s"] = ProxLbData.Groups.AntiAffinity(
            guests=["vm1", "vm2"], counter=2, used_nodes=[]
        )

        data = ProxLbData(
            guests={"vm1": vm1, "vm2": vm2},
            ha_rules={},
            nodes=nodes,
            pools={},
            groups=groups,
            meta=ProxLbData.Meta(
                proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
                cluster_non_pve9=False,
                balancing=ProxLbData.Meta.Balancing(
                    method=Config.Balancing.Resource.Memory,
                    mode=Config.Balancing.Mode.Used,
                    balanciness=5,
                    enable=True,
                    balance=False,
                    enforce_affinity=True,
                    balance_types=[Config.GuestType.Vm],
                    balance_larger_guests_first=False,
                ),
            ),
        )

        Calculations.relocate_guests(data)

        # Either vm1 or vm2 should have node_target set to pve03 (or moved away from pve02)
        moved = (data.guests["vm1"].node_target != "pve02") or (data.guests["vm2"].node_target != "pve02")
        self.assertTrue(moved, "At least one guest with anti-affinity violation should have been relocated")


if __name__ == "__main__":
    unittest.main()
