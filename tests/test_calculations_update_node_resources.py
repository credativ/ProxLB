"""
Unit-testing static method update_node_resources in class Calculations
in calculations.py for edge cases where no nodes are available for balancing.
"""

__author__ = "Peter Dreuw <archandha>"
__copyright__ = "Copyright (C) 2026 Peter Dreuw (@archandha) for credativ GmbH"
__license__ = "GPL-3.0"


from proxlb.models.calculations import Calculations
from proxlb.utils.config_parser import Config
from .utils import MINIMAL_DATA, create_node, create_guest

BalancingResource = Config.Balancing.Resource


def test_min_usage_with_empty_nodes() -> None:
    """
    Test the case where there are no nodes available (empty nodes dict).
    """
    proxlb_data = MINIMAL_DATA.model_copy()

    Calculations.update_node_resources(proxlb_data)

    assert proxlb_data == MINIMAL_DATA, "Proxlb data should not be modified when no nodes are available."


def test_min_usage_with_no_suitable_nodes() -> None:
    """
    Test the case where there are nodes, but none are suitable for balancing.
    """
    node1 = create_node("node1")
    node2 = create_node("node2")
    guest = create_guest("guest", node_current="node1", node_target="node2")

    proxlb_data = MINIMAL_DATA.model_copy()
    proxlb_data.nodes = {"node1": node1, "node2": node2}
    proxlb_data.guests = {"guest": guest}

    node1.metric(BalancingResource.Memory).used_percent = 10
    node2.metric(BalancingResource.Memory).used_percent = 20

    proxlb_data.meta.balancing.balance_next_guest = "guest"

    proxlb_data_verify = proxlb_data.model_dump(mode="json")
    Calculations.update_node_resources(proxlb_data)

    assert proxlb_data.model_dump(mode="json") == proxlb_data_verify, \
        "Proxlb data should not be modified when no suitable nodes are available."

    proxlb_data.meta.balancing.balance_next_node = "node2"
    Calculations.update_node_resources(proxlb_data)

    assert proxlb_data.model_dump(mode="json") != proxlb_data_verify
