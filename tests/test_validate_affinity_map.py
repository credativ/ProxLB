import unittest
from tests.utils import create_guest, create_node
from proxlb.models.calculations import Calculations
from proxlb.utils.config_parser import Config
from proxlb.utils.proxlb_data import ProxLbData


class TestValidateAffinityMap(unittest.TestCase):
    def _build_cluster(self, guests_list):
        nodes = {
            "pve01": create_node("pve01"),
            "pve02": create_node("pve02"),
        }
        guests = {g.name: g for g in guests_list}
        groups = ProxLbData.Groups()
        # build groups affinity and anti_affinity
        for g in guests_list:
            for tag in g.affinity_groups:
                if tag not in groups.affinity:
                    metric = ProxLbData.Groups.Affinity.Metric(total=1, used=1.0)
                    groups.affinity[tag] = ProxLbData.Groups.Affinity(
                        guests=[g.name], cpu=metric, disk=metric, memory=metric
                    )
                else:
                    groups.affinity[tag].guests.append(g.name)
                    groups.affinity[tag].counter += 1

            for tag in g.anti_affinity_groups:
                if tag not in groups.anti_affinity:
                    groups.anti_affinity[tag] = ProxLbData.Groups.AntiAffinity(guests=[g.name])
                else:
                    groups.anti_affinity[tag].guests.append(g.name)
                    groups.anti_affinity[tag].counter += 1

        return ProxLbData(
            guests=guests,
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
                    balance=False,
                    enforce_affinity=False,
                    balance_types=[Config.GuestType.Vm],
                    balance_larger_guests_first=False,
                ),
            ),
        )

    def test_violation_on_last_guest_sets_enforce_affinity_true(self):
        # When the violation only occurs on the last guest in proxlb_data.guests
        # (for example, the last guest is on a node in maintenance mode),
        # enforce_affinity must be set to True.
        vm1 = create_guest("vm1", "pve01", "pve01")
        vm1.affinity_groups = ["vm1"]
        data = self._build_cluster([vm1])
        # Put pve01 in maintenance
        data.nodes["pve01"].maintenance = True

        Calculations.validate_affinity_map(data)

        self.assertTrue(
            data.meta.balancing.enforce_affinity,
            "enforce_affinity should be True when constraint is violated on the last guest"
        )

    def test_no_violation_leaves_enforce_affinity_false(self):
        vm1 = create_guest("vm1", "pve01", "pve01")
        vm1.anti_affinity_groups = ["plb_anti_affinity_db"]
        vm2 = create_guest("vm2", "pve02", "pve02")
        vm2.anti_affinity_groups = ["plb_anti_affinity_db"]

        data = self._build_cluster([vm1, vm2])
        Calculations.validate_affinity_map(data)

        self.assertFalse(data.meta.balancing.enforce_affinity)


if __name__ == "__main__":
    unittest.main()
