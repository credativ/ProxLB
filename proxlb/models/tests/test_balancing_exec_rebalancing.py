"""
Unit tests for Balancing._exec_rebalancing().

These tests cover the routing and filtering logic inside exec_rebalancing():
which guest types trigger a migration, which are silently skipped, and how
unknown types are handled. The streaming queue machinery in balance() is
tested separately in test_balancing_streaming_queue.py.
"""

__author__ = "Peter Dreuw <archandha>"
__copyright__ = "Copyright (C) 2026 Peter Dreuw (@archandha) for credativ GmbH"
__license__ = "GPL-3.0"


from unittest.mock import MagicMock, patch

from models.balancing import Balancing


def _proxlb_data(guest_name: str, guest: dict, balance_types: list[str] | None = None) -> dict:
    return {
        "meta": {
            "balancing": {
                "balance_types": balance_types if balance_types is not None else ["vm", "ct"],
                "live": True,
                "with_local_disks": True,
            }
        },
        "guests": {guest_name: guest},
    }


def _vm(node_current: str = "node1", node_target: str = "node2", ignore: bool = False) -> dict:
    return {"id": 101, "type": "vm", "node_current": node_current, "node_target": node_target, "ignore": ignore}


def _ct(node_current: str = "node1", node_target: str = "node2", ignore: bool = False) -> dict:
    return {"id": 201, "type": "ct", "node_current": node_current, "node_target": node_target, "ignore": ignore}


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_vm_in_balance_types_delegates_to_exec_rebalancing_vm(mock_ct, mock_vm) -> None:
    """A VM guest whose type is listed in balance_types must trigger exec_rebalancing_vm."""
    mock_vm.return_value = "UPID:node1:vm1"
    proxlb_data = _proxlb_data("vm1", _vm(), balance_types=["vm"])

    job_id = Balancing._exec_rebalancing(MagicMock(), proxlb_data, "vm1")

    mock_vm.assert_called_once()
    mock_ct.assert_not_called()
    assert job_id == "UPID:node1:vm1"


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_vm_not_in_balance_types_returns_none(mock_ct, mock_vm) -> None:
    """A VM guest whose type is not listed in balance_types must be skipped (returns None)."""
    proxlb_data = _proxlb_data("vm1", _vm(), balance_types=["ct"])

    job_id = Balancing._exec_rebalancing(MagicMock(), proxlb_data, "vm1")

    mock_vm.assert_not_called()
    mock_ct.assert_not_called()
    assert job_id is None


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_ct_in_balance_types_delegates_to_exec_rebalancing_ct(mock_ct, mock_vm) -> None:
    """A CT guest whose type is listed in balance_types must trigger exec_rebalancing_ct."""
    mock_ct.return_value = "UPID:node1:ct1"
    proxlb_data = _proxlb_data("ct1", _ct(), balance_types=["ct"])

    job_id = Balancing._exec_rebalancing(MagicMock(), proxlb_data, "ct1")

    mock_ct.assert_called_once()
    mock_vm.assert_not_called()
    assert job_id == "UPID:node1:ct1"


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_ct_not_in_balance_types_returns_none(mock_ct, mock_vm) -> None:
    """A CT guest whose type is not listed in balance_types must be skipped (returns None)."""
    proxlb_data = _proxlb_data("ct1", _ct(), balance_types=["vm"])

    job_id = Balancing._exec_rebalancing(MagicMock(), proxlb_data, "ct1")

    mock_ct.assert_not_called()
    mock_vm.assert_not_called()
    assert job_id is None


@patch.object(Balancing, "_exec_rebalancing_vm")
@patch.object(Balancing, "_exec_rebalancing_ct")
def test_unknown_guest_type_returns_none(mock_ct, mock_vm) -> None:
    """An unknown guest type must not trigger any migration and must return None."""
    guest = {"id": 301, "type": "unknown", "node_current": "node1", "node_target": "node2",
             "name": "odd1", "ignore": False}
    proxlb_data = _proxlb_data("odd1", guest, balance_types=["vm", "ct"])

    job_id = Balancing._exec_rebalancing(MagicMock(), proxlb_data, "odd1")

    mock_vm.assert_not_called()
    mock_ct.assert_not_called()
    assert job_id is None
