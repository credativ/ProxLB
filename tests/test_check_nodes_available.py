from unittest.mock import MagicMock

import proxmoxer
import requests

from proxlb.utils.helper import Helper


def _proxmox_api(cluster_status: list[dict[str, object]] | None = None, error: Exception | None = None) -> MagicMock:
    api = MagicMock()
    if error is not None:
        api.cluster.status.get.side_effect = error
    else:
        api.cluster.status.get.return_value = cluster_status or []
    return api


def test_check_nodes_available_returns_true_when_all_nodes_online() -> None:
    proxmox_api = _proxmox_api([
        {"type": "node", "name": "node1", "online": 1},
        {"type": "node", "name": "node2", "online": 1},
    ])

    result = Helper.check_nodes_available(proxmox_api, {"node1": MagicMock(), "node2": MagicMock()})

    assert result is True


def test_check_nodes_available_returns_false_when_node_offline() -> None:
    proxmox_api = _proxmox_api([
        {"type": "node", "name": "node1", "online": 1},
        {"type": "node", "name": "node2", "online": 0},
    ])

    result = Helper.check_nodes_available(proxmox_api, {"node1": MagicMock(), "node2": MagicMock()})

    assert result is False


def test_check_nodes_available_returns_false_when_node_missing_from_cluster_status() -> None:
    proxmox_api = _proxmox_api([
        {"type": "node", "name": "node1", "online": 1},
    ])

    result = Helper.check_nodes_available(proxmox_api, {"node1": MagicMock(), "node2": MagicMock()})

    assert result is False


def test_check_nodes_available_returns_false_on_connection_error() -> None:
    proxmox_api = _proxmox_api(error=requests.exceptions.ConnectionError("unreachable"))

    result = Helper.check_nodes_available(proxmox_api, {"node1": MagicMock(), "node2": MagicMock()})

    assert result is False


def test_check_nodes_available_returns_false_on_proxmox_resource_exception() -> None:
    proxmox_api = _proxmox_api(error=proxmoxer.core.ResourceException(500, "Internal Server Error", "cluster gone"))

    result = Helper.check_nodes_available(proxmox_api, {"node1": MagicMock()})

    assert result is False


def test_check_nodes_available_returns_true_for_empty_node_set() -> None:
    proxmox_api = _proxmox_api()

    result = Helper.check_nodes_available(proxmox_api, {})

    assert result is True
