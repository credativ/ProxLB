"""
Unit-testing static methon get_most_free_node in class Calculations
in calculations.py for edge cases where no nodes are available for balancing.
"""

__author__ = "Peter Dreuw <archandha>"
__copyright__ = "Copyright (C) 2026 Peter Dreuw (@archandha) for credativ GmbH"
__license__ = "GPL-3.0"


from proxlb.models.calculations import Calculations
from proxlb.utils.config_parser import Config
from .utils import MINIMAL_DATA, create_node

BalancingResource = Config.Balancing.Resource


def test_get_most_free_node_crash_repro_fix24() -> None:
    """Test case where all nodes are in maintainance mode"""

    node1 = create_node("node1")
    node2 = create_node("node2")

    proxlb_data = MINIMAL_DATA.model_copy()
    proxlb_data.nodes = {"node1": node1, "node2": node2}

    node1.metric(BalancingResource.Memory).used_percent = 10
    node2.metric(BalancingResource.Memory).used_percent = 20

    assert Calculations.get_most_free_node(proxlb_data=proxlb_data) is node1
    assert proxlb_data.meta.balancing.balance_next_node is node1.name
    node1.maintenance = True
    assert Calculations.get_most_free_node(proxlb_data=proxlb_data) is node2
    assert proxlb_data.meta.balancing.balance_next_node is node2.name
    node2.maintenance = True
    assert Calculations.get_most_free_node(proxlb_data=proxlb_data) is None, \
        "Expected None when no nodes are available for balancing."
    assert proxlb_data.meta.balancing.balance_next_node is None, "Expected balance_next_node to be None."


def test_min_usage_with_empty_nodes() -> None:
    """
    Test the case where there are no nodes available (empty nodes dict).
    """
    # Simulate empty data
    proxlb_data = MINIMAL_DATA.model_copy()

    node = Calculations.get_most_free_node(proxlb_data, return_node=False)

    assert node is None, "Expected None when no nodes are available."
    assert proxlb_data.meta.balancing.balance_next_node is None, "Expected balance_next_node to be None."
