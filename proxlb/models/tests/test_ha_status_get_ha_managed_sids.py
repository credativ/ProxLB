"""
Unit tests for HaStatus.get_ha_managed_sids().

These tests verify that the sids of HA-managed resources are extracted from
/cluster/ha/resources and that an API error safely degrades to an empty set,
i.e. no guest is treated as HA-managed.
"""

from unittest.mock import MagicMock

from proxlb.models.ha_status import HaStatus


def test_returns_sids_of_all_ha_resources() -> None:
    """Both VM and CT sids must be returned; entries without a sid are skipped."""
    proxmox_api = MagicMock()
    proxmox_api.cluster.ha.resources.get.return_value = [
        {"sid": "vm:101", "type": "vm"},
        {"sid": "ct:201", "type": "ct"},
        {"type": "broken-entry-without-sid"},
    ]

    assert HaStatus.get_ha_managed_sids(proxmox_api) == {"vm:101", "ct:201"}


def test_returns_empty_set_when_no_resources_are_ha_managed() -> None:
    """A cluster without HA resources must yield an empty set."""
    proxmox_api = MagicMock()
    proxmox_api.cluster.ha.resources.get.return_value = []

    assert HaStatus.get_ha_managed_sids(proxmox_api) == set()


def test_api_error_degrades_to_no_ha_managed_guests() -> None:
    """Failing to list HA resources must not abort collection; it degrades to 'not HA-managed'."""
    proxmox_api = MagicMock()
    proxmox_api.cluster.ha.resources.get.side_effect = Exception("API down")

    assert HaStatus.get_ha_managed_sids(proxmox_api) == set()
