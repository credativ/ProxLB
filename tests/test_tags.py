import unittest
from unittest.mock import MagicMock
from proxlb.models.tags import Tags
from proxlb.utils.config_parser import Config

GuestType = Config.GuestType


class TestTags(unittest.TestCase):
    def test_get_tags_from_guests_comma_separated(self):
        proxmox_api = MagicMock()
        proxmox_api.nodes.return_value.qemu.return_value.config.get.return_value = {
            "tags": "infra,k8s,plb_anti_affinity_k8s_masters"
        }
        tags = Tags.get_tags_from_guests(proxmox_api, "pve01", 111, GuestType.Vm)
        self.assertEqual(tags, ["infra", "k8s", "plb_anti_affinity_k8s_masters"])

    def test_get_tags_from_guests_comma_with_spaces(self):
        proxmox_api = MagicMock()
        proxmox_api.nodes.return_value.qemu.return_value.config.get.return_value = {
            "tags": "infra, k8s, plb_anti_affinity_k8s_masters"
        }
        tags = Tags.get_tags_from_guests(proxmox_api, "pve01", 111, GuestType.Vm)
        self.assertEqual(tags, ["infra", "k8s", "plb_anti_affinity_k8s_masters"])

    def test_get_tags_from_guests_semicolon_separated(self):
        proxmox_api = MagicMock()
        proxmox_api.nodes.return_value.qemu.return_value.config.get.return_value = {
            "tags": "infra;k8s;plb_anti_affinity_k8s_masters"
        }
        tags = Tags.get_tags_from_guests(proxmox_api, "pve01", 111, GuestType.Vm)
        self.assertEqual(tags, ["infra", "k8s", "plb_anti_affinity_k8s_masters"])

    def test_get_tags_from_guests_lxc_container(self):
        proxmox_api = MagicMock()
        proxmox_api.nodes.return_value.lxc.return_value.config.get.return_value = {
            "tags": "infra,web"
        }
        tags = Tags.get_tags_from_guests(proxmox_api, "pve01", 112, GuestType.Ct)
        self.assertEqual(tags, ["infra", "web"])

    def test_get_tags_from_guests_empty(self):
        proxmox_api = MagicMock()
        proxmox_api.nodes.return_value.qemu.return_value.config.get.return_value = {}
        tags = Tags.get_tags_from_guests(proxmox_api, "pve01", 111, GuestType.Vm)
        self.assertEqual(tags, [])


if __name__ == "__main__":
    unittest.main()
