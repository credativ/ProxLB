"""
Unit tests for Balancing._exec_rebalancing().

These tests cover the routing and filtering logic inside _exec_rebalancing():
which guest types trigger a migration, which are silently skipped, and how
unknown types are handled. The streaming queue machinery in balance() is
tested separately in test_balancing_streaming_queue.py.
"""

__author__ = "Peter Dreuw <archandha>"
__copyright__ = "Copyright (C) 2026 Peter Dreuw (@archandha) for credativ GmbH"
__license__ = "GPL-3.0"


import pytest
from unittest.mock import MagicMock, patch

from proxlb.models.balancing import Balancing
from proxlb.utils.config_parser import Config

GuestType = Config.GuestType


def _guest(
        guest_id: int,
        node_current: str,
        node_target: str,
        guest_type: GuestType = GuestType.Vm,
        ignore: bool = False,
) -> MagicMock:
    g = MagicMock()
    g.id = guest_id
    g.type = guest_type
    g.node_current = node_current
    g.node_target = node_target
    g.ignore = ignore
    g.name = f"guest-{guest_id}"
    return g


def _vm(node_current: str = "node1", node_target: str = "node2", ignore: bool = False) -> MagicMock:
    return _guest(101, node_current, node_target, GuestType.Vm, ignore)


def _ct(node_current: str = "node1", node_target: str = "node2", ignore: bool = False) -> MagicMock:
    return _guest(201, node_current, node_target, GuestType.Ct, ignore)


def _proxlb_data(
        guest_name: str,
        guest: MagicMock,
        balance_types: list[GuestType] | None = None,
) -> MagicMock:
    if balance_types is None:
        balance_types = [GuestType.Vm, GuestType.Ct]
    data = MagicMock()
    data.meta.balancing.balance_types = balance_types
    data.meta.balancing.live = True
    data.meta.balancing.with_local_disks = True
    data.meta.balancing.with_conntrack_state = True
    data.guests = {guest_name: guest}
    return data


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_vm_in_balance_types_delegates_to_exec_rebalancing_vm(mock_ct, mock_vm) -> None:
    """A VM guest whose type is listed in balance_types must trigger _exec_rebalancing_vm."""
    mock_vm.return_value = "UPID:node1:vm1"
    proxlb_data = _proxlb_data("vm1", _vm(), balance_types=[GuestType.Vm])

    job_id = Balancing._exec_rebalancing(MagicMock(), proxlb_data, "vm1")

    mock_vm.assert_called_once()
    mock_ct.assert_not_called()
    assert job_id == "UPID:node1:vm1"


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_vm_not_in_balance_types_returns_none(mock_ct, mock_vm) -> None:
    """A VM guest whose type is not listed in balance_types must be skipped (returns None)."""
    proxlb_data = _proxlb_data("vm1", _vm(), balance_types=[GuestType.Ct])

    job_id = Balancing._exec_rebalancing(MagicMock(), proxlb_data, "vm1")

    mock_vm.assert_not_called()
    mock_ct.assert_not_called()
    assert job_id is None


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_ct_in_balance_types_delegates_to_exec_rebalancing_ct(mock_ct, mock_vm) -> None:
    """A CT guest whose type is listed in balance_types must trigger _exec_rebalancing_ct."""
    mock_ct.return_value = "UPID:node1:ct1"
    proxlb_data = _proxlb_data("ct1", _ct(), balance_types=[GuestType.Ct])

    job_id = Balancing._exec_rebalancing(MagicMock(), proxlb_data, "ct1")

    mock_ct.assert_called_once()
    mock_vm.assert_not_called()
    assert job_id == "UPID:node1:ct1"


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_ct_not_in_balance_types_returns_none(mock_ct, mock_vm) -> None:
    """A CT guest whose type is not listed in balance_types must be skipped (returns None)."""
    proxlb_data = _proxlb_data("ct1", _ct(), balance_types=[GuestType.Vm])

    job_id = Balancing._exec_rebalancing(MagicMock(), proxlb_data, "ct1")

    mock_ct.assert_not_called()
    mock_vm.assert_not_called()
    assert job_id is None


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_unknown_guest_type_raises(mock_ct, mock_vm) -> None:
    """An unknown guest type must raise AssertionError via assert_never (all valid types exhausted)."""
    odd_guest = MagicMock()
    odd_guest.id = 301
    odd_guest.type = "unknown"  # not a GuestType member — triggers assert_never
    odd_guest.node_current = "node1"
    odd_guest.node_target = "node2"
    odd_guest.ignore = False
    odd_guest.name = "odd1"
    proxlb_data = _proxlb_data("odd1", odd_guest, balance_types=[GuestType.Vm, GuestType.Ct])

    with pytest.raises((AssertionError, TypeError)):
        Balancing._exec_rebalancing(MagicMock(), proxlb_data, "odd1")

    mock_vm.assert_not_called()
    mock_ct.assert_not_called()
