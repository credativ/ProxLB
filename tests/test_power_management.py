"""
Unit tests for the experimental power management (issue #12).
"""

__license__ = "GPL-3.0"


from pathlib import Path
from typing import Any

from proxlb.models.power_management import ClusterInfo, PowerManagement, PowerState
from proxlb.utils.config_parser import Config
from proxlb.utils.proxlb_data import ProxLbData
from .utils import MINIMAL_DATA, create_guest, create_node

NOW = 1_000_000.0


def _nodes(**used_percent: float) -> dict[str, ProxLbData.Node]:
    nodes = {}
    for name, percent in used_percent.items():
        node = create_node(name)
        for metric in (node.memory, node.cpu):
            metric.total = 100
            metric.used = percent
            metric.used_percent = percent
        nodes[name] = node
    return nodes


def _config(**kwargs: Any) -> Config.PowerManagement:
    values: dict[str, Any] = {"enable": True, "dry_run": False, "nodes": ["n1", "n2", "n3", "n4"], "min_online_nodes": 2}
    values.update(kwargs)
    return Config.PowerManagement(**values)


def _cluster(online: list[str], total: int = 4) -> ClusterInfo:
    return ClusterInfo(nodes_total=total, nodes_online=online)


def test_low_load_drains_least_used_eligible_node() -> None:
    nodes = _nodes(n1=20, n2=10, n3=15, n4=25)
    actions = PowerManagement.plan(_config(), nodes, {}, _cluster(list(nodes)), PowerState(), NOW)
    assert [(a.action, a.node) for a in actions] == [("drain", "n2")]


def test_no_drain_when_projected_load_too_high() -> None:
    nodes = _nodes(n1=35, n2=30, n3=35, n4=35)  # without one node: 135/300 = 45% >= 40%
    assert PowerManagement.plan(_config(), nodes, {}, _cluster(list(nodes)), PowerState(), NOW) == []


def test_only_eligible_nodes_and_not_local_node() -> None:
    nodes = _nodes(n1=20, n2=10, n3=15, n4=25)
    config = _config(nodes=["n2", "n3"])
    actions = PowerManagement.plan(config, nodes, {}, _cluster(list(nodes)), PowerState(), NOW, local_node="n2")
    assert [(a.action, a.node) for a in actions] == [("drain", "n3")]


def test_quorum_and_min_online_nodes_are_kept() -> None:
    nodes = _nodes(n1=5, n2=5, n3=5)
    # 3 of 5 nodes online: quorum is 3, so no node may go down.
    assert PowerManagement.plan(_config(), nodes, {}, _cluster(list(nodes), total=5), PowerState(), NOW) == []
    # min_online_nodes is respected as well.
    assert PowerManagement.plan(_config(min_online_nodes=3), nodes, {}, _cluster(list(nodes), total=3), PowerState(), NOW) == []


def test_cooldown_blocks_new_drain() -> None:
    nodes = _nodes(n1=20, n2=10, n3=15, n4=25)
    state = PowerState(last_action=NOW - 10)
    assert PowerManagement.plan(_config(), nodes, {}, _cluster(list(nodes)), state, NOW) == []


def test_drained_node_is_shut_down_when_empty() -> None:
    nodes = _nodes(n1=20, n2=10, n3=15, n4=25)
    state = PowerState(draining={"n2": NOW - 60})
    actions = PowerManagement.plan(_config(), nodes, {"n2": 1}, _cluster(list(nodes)), state, NOW)
    assert actions == []  # still a guest on it
    actions = PowerManagement.plan(_config(), nodes, {}, _cluster(list(nodes)), state, NOW)
    assert [(a.action, a.node) for a in actions] == [("shutdown", "n2")]


def test_drain_timeout_cancels_drain() -> None:
    nodes = _nodes(n1=20, n2=10, n3=15, n4=25)
    state = PowerState(draining={"n2": NOW - 7200})
    actions = PowerManagement.plan(_config(drain_timeout=3600), nodes, {"n2": 2}, _cluster(list(nodes)), state, NOW)
    assert [(a.action, a.node) for a in actions] == [("cancel_drain", "n2")]


def test_high_load_wakes_node_shut_down_by_proxlb() -> None:
    nodes = _nodes(n1=80, n3=80, n4=80)
    state = PowerState(powered_off={"n2": NOW - 7200})
    actions = PowerManagement.plan(_config(), nodes, {}, _cluster(["n1", "n3", "n4"]), state, NOW)
    assert [(a.action, a.node) for a in actions] == [("wake", "n2")]
    # Not again while the woken node is still booting.
    state.last_wake = NOW - 60
    assert PowerManagement.plan(_config(), nodes, {}, _cluster(["n1", "n3", "n4"]), state, NOW) == []


def test_high_load_never_wakes_nodes_not_shut_down_by_proxlb() -> None:
    nodes = _nodes(n1=80, n3=80, n4=80)
    assert PowerManagement.plan(_config(), nodes, {}, _cluster(["n1", "n3", "n4"]), PowerState(), NOW) == []


def test_high_load_cancels_drain() -> None:
    nodes = _nodes(n1=80, n2=80, n3=80, n4=80)
    state = PowerState(draining={"n2": NOW - 60})
    actions = PowerManagement.plan(_config(), nodes, {"n2": 3}, _cluster(list(nodes)), state, NOW)
    assert [(a.action, a.node) for a in actions] == [("cancel_drain", "n2")]


class FakeApi:
    """Minimal fake of the proxmoxer API object."""

    def __init__(self, online: list[str], total: int) -> None:
        self.online = online
        self.total = total
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        outer = self

        class _Status:
            @staticmethod
            def get() -> list[dict[str, Any]]:
                return [{"type": "cluster", "quorate": 1}] + [
                    {"type": "node", "name": f"n{i}", "online": int(f"n{i}" in outer.online)} for i in range(1, outer.total + 1)
                ]

        class _Cluster:
            status = _Status()

        self.cluster = _Cluster()

    def nodes(self, name: str) -> Any:
        outer = self

        class _Endpoint:
            def __init__(self, path: str) -> None:
                self.path = path

            def post(self, **kwargs: Any) -> str:
                outer.calls.append((name, self.path, kwargs))
                return "UPID"

        class _Node:
            status = _Endpoint("status")
            wakeonlan = _Endpoint("wakeonlan")

        return _Node()


def _proxlb_data(nodes: dict[str, ProxLbData.Node], guests_on: dict[str, int]) -> ProxLbData:
    data = MINIMAL_DATA.model_copy(deep=True)
    data.nodes = nodes
    for node_name, count in guests_on.items():
        for i in range(count):
            guest = create_guest(f"{node_name}-vm{i}", node_name, node_name)
            data.guests[guest.name] = guest
    return data


def test_run_drain_then_shutdown(tmp_path: Path) -> None:
    state_file = str(tmp_path / "pm.json")
    proxlb_config = Config(
        proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
        power_management=_config(state_file=state_file),
    )
    api = FakeApi(online=["n1", "n2", "n3", "n4"], total=4)

    # Run 1: n2 has a guest, it is drained and marked as maintenance.
    data = _proxlb_data(_nodes(n1=20, n2=10, n3=15, n4=25), {"n2": 1})
    actions = PowerManagement.run(api, data, proxlb_config)  # type: ignore[arg-type]
    assert [(a.action, a.node) for a in actions] == [("drain", "n2")]
    assert data.nodes["n2"].maintenance is True
    assert api.calls == []

    # Run 2: n2 is empty now and is shut down via the API.
    data = _proxlb_data(_nodes(n1=20, n2=10, n3=15, n4=25), {})
    actions = PowerManagement.run(api, data, proxlb_config)  # type: ignore[arg-type]
    assert [(a.action, a.node) for a in actions] == [("shutdown", "n2")]
    assert api.calls == [("n2", "status", {"command": "shutdown"})]
    assert "n2" not in data.nodes
    assert PowerManagement.load_state(state_file).powered_off.keys() == {"n2"}


def test_run_dry_run_changes_nothing(tmp_path: Path) -> None:
    state_file = str(tmp_path / "pm.json")
    proxlb_config = Config(
        proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
        power_management=_config(state_file=state_file, dry_run=True),
    )
    api = FakeApi(online=["n1", "n2", "n3", "n4"], total=4)
    data = _proxlb_data(_nodes(n1=20, n2=10, n3=15, n4=25), {"n2": 1})
    actions = PowerManagement.run(api, data, proxlb_config)  # type: ignore[arg-type]
    assert [(a.action, a.node) for a in actions] == [("drain", "n2")]
    assert data.nodes["n2"].maintenance is False
    assert api.calls == []
    assert not Path(state_file).exists()


def test_run_disabled_by_default() -> None:
    proxlb_config = Config(proxmox_api=Config.ProxmoxAPI(hosts=[], user=""))
    data = _proxlb_data(_nodes(n1=1, n2=1), {})
    assert PowerManagement.run(None, data, proxlb_config) == []  # type: ignore[arg-type]
