"""
The Guests class retrieves all running guests on the Proxmox cluster across all available nodes.
It handles both VM and CT guest types, collecting their resource metrics.
"""

__author__ = "Florian Paul Azim Hoberg <gyptazy>"
__copyright__ = "Copyright (C) 2025 Florian Paul Azim Hoberg (@gyptazy)"
__license__ = "GPL-3.0"


from typing import TYPE_CHECKING, Dict
from proxlb.utils.logger import SystemdLogger
from proxlb.utils.proxmox_api import ProxmoxApi
from proxlb.utils.config_parser import Config
from proxlb.utils.proxlb_data import ProxLbData
from proxlb.utils.rrd import GuestRrdKey, RrdDatasets
from proxlb.models.pools import Pools
from proxlb.models.ha_rules import HaRules
from proxlb.models.ha_status import HaStatus
from proxlb.models.storage import Storage
from proxlb.models.tags import Tags
import time

if TYPE_CHECKING:
    from proxlb.utils.rrd import GuestRrdDatasets

GuestType = Config.GuestType

logger = SystemdLogger()


class Guests:
    """
    The Guests class retrieves all running guests on the Proxmox cluster across all available nodes.
    It handles both VM and CT guest types, collecting their resource metrics.

    Methods:
        __init__:
            Initializes the Guests class.

        get_guests(proxmox_api: any, nodes: Dict[str, Any]) -> Dict[str, Any]:
            Retrieves metrics for all running guests (both VMs and CTs) across all nodes in the Proxmox cluster.
            It collects resource metrics such as CPU, memory, and disk usage, as well as tags and affinity/anti-affinity groups.
    """
    def __init__(self) -> None:
        """
        Initializes the Guests class with the provided ProxLB data.
        """

    @staticmethod
    def get_guests(proxmox_api: ProxmoxApi, pools: Dict[str, ProxLbData.Pool], ha_rules: Dict[str, ProxLbData.HaRule], nodes: Dict[str, ProxLbData.Node], storage: Dict[str, ProxLbData.Storage], proxlb_config: Config) -> Dict[str, ProxLbData.Guest]:
        """
        Get metrics of all guests in a Proxmox cluster.

        This method retrieves metrics for all running guests (both VMs and CTs) across all nodes in the Proxmox cluster.
        It iterates over each node and collects resource metrics for each running guest, including CPU, memory, and disk usage.
        Additionally, it retrieves tags and affinity/anti-affinity groups for each guest.

        Args:
            proxmox_api (any): The Proxmox API client instance.
            pools (Dict[str, Any]): A dictionary containing information about the pools in the Proxmox cluster.
            ha_rules (Dict[str, Any]): A dictionary containing information about the HA rules in the
            nodes (Dict[str, Any]): A dictionary containing information about the nodes in the Proxmox cluster.
            storage (Dict[str, Any]): A dictionary containing the storages collected by Storage.get_storage.
            proxmox_config (Dict[str, Any]): A dictionary containing the ProxLB configuration.

        Returns:
            Dict[str, Any]: A dictionary containing metrics and information for all running guests.
        """
        logger.debug("Starting: get_guests.")
        guests: Dict[str, ProxLbData.Guest] = {}
        ha_managed_sids = HaStatus.get_ha_managed_sids(proxmox_api)

        # Guest objects are always only in the scope of a node.
        # Therefore, we need to iterate over all nodes to get all guests.
        for node in nodes.keys():

            # VM objects: Iterate over all VMs on the current node by the qemu API object.
            # Unlike the nodes we need to keep them even when being ignored to create proper
            # resource metrics for rebalancing to ensure that we do not overprovisiong the node.
            for guest in proxmox_api.nodes(node).qemu.get():
                if guest['status'] == 'running':

                    guest_tags = Tags.get_tags_from_guests(proxmox_api, node, guest['vmid'], GuestType.Vm)
                    guest_pools = Pools.get_pools_for_guest(guest['name'], pools)
                    guest_ha_rules = HaRules.get_ha_rules_for_guest(guest['name'], ha_rules, guest['vmid'])
                    guest_rrd = Guests.get_guest_rrd_datasets(proxmox_api, node, guest['vmid'], guest['name'], GuestType.Vm)

                    guests[guest['name']] = ProxLbData.Guest(
                        name=guest['name'],
                        cpu=ProxLbData.Guest.Metric(
                            total=int(guest['cpus']),
                            used=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'cpu'),
                            pressure_some_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurecpusome'),
                            pressure_full_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurecpufull'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurecpusome', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurecpufull', spikes=True),
                            pressure_hot=False,
                        ),
                        disk=ProxLbData.Guest.Metric(
                            total=guest['maxdisk'],
                            used=guest['disk'],
                            pressure_some_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressureiosome'),
                            pressure_full_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressureiofull'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressureiosome', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressureiofull', spikes=True),
                            pressure_hot=False,
                        ),
                        memory=ProxLbData.Guest.Metric(
                            total=guest['maxmem'],
                            used=guest['mem'],
                            pressure_some_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurememorysome'),
                            pressure_full_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurememoryfull'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurememorysome', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurememoryfull', spikes=True),
                            pressure_hot=False,
                        ),
                        id=guest['vmid'],
                        node_current=node,
                        node_target=node,
                        processed=False,
                        pressure_hot=False,
                        tags=guest_tags,
                        pools=guest_pools,
                        ha_rules=guest_ha_rules,
                        affinity_groups=Tags.get_affinity_groups(guest_tags, guest_pools, guest_ha_rules, proxlb_config),
                        anti_affinity_groups=Tags.get_anti_affinity_groups(guest_tags, guest_pools, guest_ha_rules, proxlb_config),
                        ignore=Tags.get_ignore(guest_tags, guest['name'], proxlb_config),
                        node_relationships=Tags.get_node_relationships(guest_tags, nodes, guest_pools, guest_ha_rules, proxlb_config),
                        node_relationships_strict=Pools.get_pool_node_affinity_strictness(proxlb_config, guest_pools),
                        type=GuestType.Vm,
                        disks=Storage.get_disks_for_guest(guest['vmid'], storage),
                        ha_managed=f"vm:{guest['vmid']}" in ha_managed_sids,
                    )

                    logger.debug(f"Resources of Guest {guest['name']} (type VM) added: {guests[guest['name']]}")
                else:
                    logger.debug(f'Metric for VM {guest["name"]} ignored because VM is not running.')

            # CT objects: Iterate over all VMs on the current node by the lxc API object.
            # Unlike the nodes we need to keep them even when being ignored to create proper
            # resource metrics for rebalancing to ensure that we do not overprovisiong the node.
            for guest in proxmox_api.nodes(node).lxc.get():
                if guest['status'] == 'running':

                    guest_tags = Tags.get_tags_from_guests(proxmox_api, node, guest['vmid'], GuestType.Ct)
                    guest_pools = Pools.get_pools_for_guest(guest['name'], pools)
                    guest_ha_rules = HaRules.get_ha_rules_for_guest(guest['name'], ha_rules, guest['vmid'])
                    guest_rrd = Guests.get_guest_rrd_datasets(proxmox_api, node, guest['vmid'], guest['name'], GuestType.Ct)

                    guests[guest['name']] = ProxLbData.Guest(
                        cpu=ProxLbData.Guest.Metric(
                            total=int(guest['cpus']),
                            used=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'cpu'),
                            pressure_some_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurecpusome'),
                            pressure_full_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurecpufull'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurecpusome', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurecpufull', spikes=True),
                            pressure_hot=False,
                        ),
                        disk=ProxLbData.Guest.Metric(
                            total=guest['maxdisk'],
                            used=guest['disk'],
                            pressure_some_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressureiosome'),
                            pressure_full_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressureiofull'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressureiosome', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressureiofull', spikes=True),
                            pressure_hot=False,
                        ),
                        memory=ProxLbData.Guest.Metric(
                            total=guest['maxmem'],
                            used=guest['mem'],
                            pressure_some_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurememorysome'),
                            pressure_full_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurememoryfull'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurememorysome', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_value(guest_rrd, guest['name'], 'pressurememoryfull', spikes=True),
                            pressure_hot=False,
                        ),
                        name=guest['name'],
                        id=guest['vmid'],
                        node_current=node,
                        node_target=node,
                        processed=False,
                        pressure_hot=False,
                        tags=guest_tags,
                        pools=guest_pools,
                        ha_rules=guest_ha_rules,
                        affinity_groups=Tags.get_affinity_groups(guest_tags, guest_pools, guest_ha_rules, proxlb_config),
                        anti_affinity_groups=Tags.get_anti_affinity_groups(guest_tags, guest_pools, guest_ha_rules, proxlb_config),
                        ignore=Tags.get_ignore(guest_tags, guest['name'], proxlb_config),
                        node_relationships=Tags.get_node_relationships(guest_tags, nodes, guest_pools, guest_ha_rules, proxlb_config),
                        node_relationships_strict=Pools.get_pool_node_affinity_strictness(proxlb_config, guest_pools),
                        type=GuestType.Ct,
                        disks=Storage.get_disks_for_guest(guest['vmid'], storage),
                        ha_managed=f"ct:{guest['vmid']}" in ha_managed_sids,
                    )

                    logger.debug(f"Resources of Guest {guest['name']} (type CT) added: {guests[guest['name']]}")
                else:
                    logger.debug(f'Metric for CT {guest["name"]} ignored because CT is not running.')

        logger.debug("Finished: get_guests.")
        return guests

    @staticmethod
    def get_guest_rrd_datasets(proxmox_api: ProxmoxApi, node_name: str, vm_id: int, vm_name: str, guest_type: Config.GuestType) -> 'GuestRrdDatasets':
        """
        Fetches the RRD data for a guest VM or CT once, covering both the average and
        maximum (spike) consolidation functions.

        A single Proxmox RRD response already contains every metric (cpu, memory, disk
        usage as well as all of their pressure figures) for the requested timeframe.
        Fetching it once per consolidation function (AVERAGE/MAX) and deriving all
        individual values from it via get_guest_rrd_value() avoids querying the same
        endpoint repeatedly (previously up to 13 times per guest).

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            node_name (str): The name of the node hosting the guest.
            vm_id (int): The ID of the guest VM or CT.
            vm_name (str): The name of the guest VM or CT.
            guest_type (GuestType): Whether the guest is a VM (qemu) or CT (lxc).

        Returns:
            GuestRrdDatasets: The average and maximum RRD data entries for the guest.
        """
        logger.debug("Starting: get_guest_rrd_datasets.")

        if guest_type == GuestType.Vm:
            guest_api_endpoint = proxmox_api.nodes(node_name).qemu(vm_id)
        else:
            guest_api_endpoint = proxmox_api.nodes(node_name).lxc(vm_id)

        time.sleep(0.1)
        try:
            logger.debug(f"Getting average RRD data from guest: {vm_name}.")
            rrd_average = guest_api_endpoint.rrddata.get(timeframe="hour", cf="AVERAGE")
        except Exception:
            logger.error(f"Failed to retrieve AVERAGE RRD data for guest: {vm_name} (ID: {vm_id}) on node: {node_name}. Using 0.0 as value.")
            rrd_average = []

        time.sleep(0.1)
        try:
            logger.debug(f"Getting spike RRD data from guest: {vm_name}.")
            rrd_max = guest_api_endpoint.rrddata.get(timeframe="hour", cf="MAX")
        except Exception:
            logger.error(f"Failed to retrieve MAX RRD data for guest: {vm_name} (ID: {vm_id}) on node: {node_name}. Using 0.0 as value.")
            rrd_max = []

        logger.debug("Finished: get_guest_rrd_datasets.")
        return RrdDatasets(average=rrd_average, maximum=rrd_max)

    @staticmethod
    def get_guest_rrd_value(rrd_datasets: 'GuestRrdDatasets', vm_name: str, rrd_key: GuestRrdKey, spikes: bool = False) -> float:
        """
        Derives a single rrd data metric (CPU, memory, disk usage or pressure) of a guest
        VM or CT from the datasets already fetched via get_guest_rrd_datasets(). This
        performs no API call itself.

        Args:
            rrd_datasets (GuestRrdDatasets): The RRD entries fetched for both consolidation functions.
            vm_name (str): The name of the guest VM or CT.
            rrd_key (GuestRrdKey): The rrd field to read.
            spikes (bool, optional): Whether to consider spikes in the calculation. Defaults to False.

        Returns:
            float: The calculated average usage value for the specified resource.
        """
        logger.debug("Starting: get_guest_rrd_value.")
        guest_data_rrd = rrd_datasets.maximum if spikes else rrd_datasets.average

        if not guest_data_rrd:
            logger.debug("Finished: get_guest_rrd_value.")
            return float(0.0)

        logger.debug(f"Getting RRD data (spike: {spikes}) for {rrd_key} from guest: {vm_name}.")
        if spikes:
            # RRD data is collected every minute, so we look at the last 6 entries
            # and take the maximum value to represent the spike
            _rrd_data_value = [row[rrd_key] for row in guest_data_rrd if rrd_key in row and row[rrd_key] is not None]  # pyright: ignore[reportTypedDictNotRequiredAccess]
            rrd_data_value = max(_rrd_data_value[-6:], default=0.0)
        else:
            # Calculate the average value from the RRD data entries
            rrd_data_value = sum(entry[rrd_key] for entry in guest_data_rrd if rrd_key in entry and entry[rrd_key] is not None) / len(guest_data_rrd)  # pyright: ignore[reportTypedDictNotRequiredAccess]

        logger.debug(f"RRD data (spike: {spikes}) for {rrd_key} from guest: {vm_name}: {rrd_data_value}")
        logger.debug("Finished: get_guest_rrd_value.")
        return rrd_data_value
