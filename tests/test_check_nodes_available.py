from unittest.mock import MagicMock

import proxmoxer
import requests

from proxlb.utils.helper import Helper


def _proxmox_api(node_errors: dict[str, Exception] | None = None) -> MagicMock:
    node_errors = node_errors or {}
    api = MagicMock()

    def _node(node_name: str) -> MagicMock:
        node = MagicMock()
        error = node_errors.get(node_name)
        if error is not None:
            node.status.get.side_effect = error
        return node

    api.nodes.side_effect = _node
    return api


def test_check_nodes_available_returns_true_when_all_nodes_respond() -> None:
    proxmox_api = _proxmox_api()

    result = Helper.check_nodes_available(proxmox_api, {"node1": MagicMock(), "node2": MagicMock()})

    assert result is True


def test_check_nodes_available_returns_false_on_connection_error() -> None:
    proxmox_api = _proxmox_api({"node2": requests.exceptions.ConnectionError("unreachable")})

    result = Helper.check_nodes_available(proxmox_api, {"node1": MagicMock(), "node2": MagicMock()})

    assert result is False


def test_check_nodes_available_returns_false_on_proxmox_resource_exception() -> None:
    proxmox_api = _proxmox_api({
        "node1": proxmoxer.core.ResourceException(500, "Internal Server Error", "node gone"),
    })

    result = Helper.check_nodes_available(proxmox_api, {"node1": MagicMock()})

    assert result is False


def test_check_nodes_available_returns_true_for_empty_node_set() -> None:
    proxmox_api = _proxmox_api()

    result = Helper.check_nodes_available(proxmox_api, {})

    assert result is True
