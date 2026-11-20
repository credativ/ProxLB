"""A pool with a pin list and without type is a pure node pool (issue #133)."""
import unittest

from proxlb.models.pools import Pools
from proxlb.models.tags import Tags
from proxlb.utils.config_parser import Config
from tests.utils import create_node


def config(**pool: object) -> Config:
    return Config(
        proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
        balancing=Config.Balancing(pools={"zone_vdi": Config.Balancing.Pool(**pool)}),  # type: ignore[arg-type]
    )


NODES = {name: create_node(name) for name in ("proxve4", "proxve5", "proxve6", "proxve7")}


class TestPoolNodePinning(unittest.TestCase):
    def test_pool_without_type_only_restricts_nodes(self) -> None:
        cfg = config(pin=["proxve4", "proxve5", "proxve6"])
        self.assertIsNone(cfg.balancing.pools["zone_vdi"].type)  # type: ignore[index]
        self.assertEqual(Tags.get_affinity_groups([], ["zone_vdi"], [], cfg), [])
        self.assertEqual(Tags.get_anti_affinity_groups([], ["zone_vdi"], [], cfg), [])
        self.assertEqual(Tags.get_node_relationships([], NODES, ["zone_vdi"], [], cfg),
                         ["proxve4", "proxve5", "proxve6"])
        self.assertTrue(Pools.get_pool_node_affinity_strictness(cfg, ["zone_vdi"]))

    def test_type_null_and_strict_false(self) -> None:
        cfg = config(pin=["proxve4", "unknown"], type=None, strict=False)
        self.assertEqual(Tags.get_affinity_groups([], ["zone_vdi"], [], cfg), [])
        self.assertEqual(Tags.get_node_relationships([], NODES, ["zone_vdi"], [], cfg), ["proxve4"])
        self.assertFalse(Pools.get_pool_node_affinity_strictness(cfg, ["zone_vdi"]))

    def test_type_still_adds_groups(self) -> None:
        cfg = config(pin=["proxve4"], type="anti-affinity")
        self.assertEqual(Tags.get_anti_affinity_groups([], ["zone_vdi"], [], cfg), ["zone_vdi"])
        self.assertEqual(Tags.get_affinity_groups([], ["zone_vdi"], [], cfg), [])


if __name__ == "__main__":
    unittest.main()
