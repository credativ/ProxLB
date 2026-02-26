from proxlb.utils.proxlb_data import ProxLbData
from proxlb.utils.config_parser import Config


MINIMAL_DATA = ProxLbData(
    guests={}, ha_rules={}, nodes={}, pools={}, groups=ProxLbData.Groups(),
    meta=ProxLbData.Meta(
        proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
        cluster_non_pve9=False,
    ),
)


def create_node(name: str) -> ProxLbData.Node:
    metric = ProxLbData.Node.Metric(
        total=1,
        assigned=2,
        used=1.0,
        free=2.0,
        assigned_percent=50.0,
        free_percent=50.0,
        used_percent=50.0,
        pressure_some_percent=50.0,
        pressure_full_percent=50.0,
        pressure_some_spikes_percent=50.0,
        pressure_full_spikes_percent=50.0,
        pressure_hot=False,
    )
    return ProxLbData.Node(
        name=name, pve_version="9", pressure_hot=False, maintenance=False,
        cpu=metric.model_copy(),
        disk=metric.model_copy(),
        memory=metric.model_copy(),
    )


def create_guest(name: str, node_current: str, node_target: str) -> ProxLbData.Guest:
    metric = ProxLbData.Guest.Metric(
        total=1,
        used=1,
        pressure_some_percent=50,
        pressure_full_percent=50,
        pressure_some_spikes_percent=50,
        pressure_full_spikes_percent=50,
        pressure_hot=False,
    )
    return ProxLbData.Guest(
        cpu=metric.model_copy(),
        disk=metric.model_copy(),
        memory=metric.model_copy(),
        name=name,
        id=100,
        node_current=node_current,
        node_target=node_target,
        processed=False,
        pressure_hot=False,
        tags=[],
        pools=[],
        ha_rules=[],
        affinity_groups=[],
        anti_affinity_groups=[],
        ignore=False,
        node_relationships=[],
        node_relationships_strict=False,
        type=Config.GuestType.Vm,
    )
