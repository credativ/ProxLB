from typing import TYPE_CHECKING

from proxlb.models.nodes import Nodes
from proxlb.utils.rrd import RrdDatasets

if TYPE_CHECKING:
    from proxlb.utils.rrd import NodeRrdDatasets


def test_get_node_zfs_arc_size_uses_latest_entry_with_value() -> None:
    node_rrd: 'NodeRrdDatasets' = RrdDatasets(
        average=[{"time": 1, "arcsize": 100.0}, {"time": 2, "arcsize": 200.0}, {"time": 3}],
        maximum=[],
    )

    assert Nodes.get_node_zfs_arc_size(node_rrd, "node1") == 200.0


def test_get_node_zfs_arc_size_returns_zero_without_arcsize() -> None:
    # Nodes running PVE < 9 do not provide the arcsize in their rrd data
    node_rrd: 'NodeRrdDatasets' = RrdDatasets(average=[{"time": 1}, {"time": 2}], maximum=[])

    assert Nodes.get_node_zfs_arc_size(node_rrd, "node1") == 0.0
