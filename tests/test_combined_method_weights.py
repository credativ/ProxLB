"""
Unit tests for the combined balancing method (`balancing.method_weights`, issue #13).
"""

__license__ = "GPL-3.0"


from proxlb.models.calculations import Calculations
from proxlb.utils.config_parser import Config
from proxlb.utils.proxlb_data import ProxLbData
from .utils import MINIMAL_DATA, create_guest, create_node

BalancingResource = Config.Balancing.Resource
BalancingMode = Config.Balancing.Mode


def _data(cpu1: float, mem1: float, cpu2: float, mem2: float) -> ProxLbData:
    proxlb_data = MINIMAL_DATA.model_copy(deep=True)
    node1 = create_node("node1")
    node2 = create_node("node2")
    node1.cpu.used_percent, node1.memory.used_percent = cpu1, mem1
    node2.cpu.used_percent, node2.memory.used_percent = cpu2, mem2
    proxlb_data.nodes = {"node1": node1, "node2": node2}
    proxlb_data.guests = {"vm1": create_guest("vm1", "node1", "node1")}
    proxlb_data.groups.affinity = {
        "vm1": ProxLbData.Groups.Affinity(
            guests=["vm1"],
            cpu=ProxLbData.Groups.Affinity.Metric(total=1, used=1.0),
            disk=ProxLbData.Groups.Affinity.Metric(total=1, used=1.0),
            memory=ProxLbData.Groups.Affinity.Metric(total=1, used=1.0),
        )
    }
    return proxlb_data


def test_default_without_weights_is_unchanged() -> None:
    """Without method_weights only the single method (memory) counts."""
    proxlb_data = _data(cpu1=90, mem1=20, cpu2=10, mem2=40)
    assert proxlb_data.meta.balancing.active_method_weights() is None
    assert Calculations.node_load(proxlb_data, proxlb_data.nodes["node1"]) == 20
    assert Calculations.get_most_free_node(proxlb_data) is proxlb_data.nodes["node1"]


def test_weights_change_target_node() -> None:
    """With cpu and memory weighted equally, node2 is the less loaded node."""
    proxlb_data = _data(cpu1=90, mem1=20, cpu2=10, mem2=40)
    proxlb_data.meta.balancing.method_weights = {BalancingResource.Cpu: 1, BalancingResource.Memory: 1}
    assert Calculations.node_load(proxlb_data, proxlb_data.nodes["node1"]) == 55
    assert Calculations.node_load(proxlb_data, proxlb_data.nodes["node2"]) == 25
    assert Calculations.get_most_free_node(proxlb_data) is proxlb_data.nodes["node2"]


def test_weights_are_normalised_and_non_positive_ignored() -> None:
    balancing = Config.Balancing(method_weights={BalancingResource.Memory: 3, BalancingResource.Cpu: 1, BalancingResource.Disk: 0})
    assert balancing.active_method_weights() == {BalancingResource.Memory: 0.75, BalancingResource.Cpu: 0.25}
    assert Config.Balancing(method_weights={BalancingResource.Cpu: 0}).active_method_weights() is None


def test_balanciness_uses_combined_value() -> None:
    """Memory alone is balanced (20 vs 25), but the combined load is not."""
    proxlb_data = _data(cpu1=90, mem1=20, cpu2=10, mem2=25)
    Calculations.get_balanciness(proxlb_data)
    assert proxlb_data.meta.balancing.balance is False

    proxlb_data.meta.balancing.method_weights = {BalancingResource.Cpu: 1, BalancingResource.Memory: 1}
    Calculations.get_balanciness(proxlb_data)
    assert proxlb_data.meta.balancing.balance is True


def test_threshold_of_each_weighted_resource_is_checked() -> None:
    """A cpu_threshold triggers balancing when cpu is part of the weights."""
    proxlb_data = _data(cpu1=85, mem1=50, cpu2=80, mem2=50)
    proxlb_data.meta.balancing.cpu_threshold = 80
    Calculations.get_balanciness(proxlb_data)
    assert proxlb_data.meta.balancing.balance is False  # only memory is the method

    proxlb_data.meta.balancing.method_weights = {BalancingResource.Cpu: 1, BalancingResource.Memory: 1}
    Calculations.get_balanciness(proxlb_data)
    assert proxlb_data.meta.balancing.balance is True


def test_config_parses_method_weights() -> None:
    balancing = Config.Balancing.model_validate({"method_weights": {"memory": 2, "cpu": 1}})
    assert balancing.method_weights == {BalancingResource.Memory: 2.0, BalancingResource.Cpu: 1.0}
    meta = ProxLbData.Meta.from_config(
        Config(proxmox_api=Config.ProxmoxAPI(hosts=[], user=""), balancing=balancing),
        cluster_non_pve9=False,
    )
    assert meta.balancing.active_method_weights() is not None
