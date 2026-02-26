"""
The Guests class retrieves all running guests on the Proxmox cluster across all available nodes.
It handles both VM and CT guest types, collecting their resource metrics.
"""

__author__ = "Florian Paul Azim Hoberg <gyptazy>"
__copyright__ = "Copyright (C) 2025 Florian Paul Azim Hoberg (@gyptazy)"
__license__ = "GPL-3.0"


from typing import Dict, Optional
from proxlb.utils.logger import SystemdLogger
from proxlb.utils.proxmox_api import ProxmoxApi
from proxlb.utils.config_parser import Config
from proxlb.utils.proxlb_data import ProxLbData
from proxlb.models.pools import Pools
from proxlb.models.ha_rules import HaRules
from proxlb.models.tags import Tags
import time

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
    def get_guests(proxmox_api: ProxmoxApi, pools: Dict[str, ProxLbData.Pool], ha_rules: Dict[str, ProxLbData.HaRule], nodes: Dict[str, ProxLbData.Node], proxlb_config: Config) -> Dict[str, ProxLbData.Guest]:
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
            meta (Dict[str, Any]): A dictionary containing metadata information.
            proxmox_config (Dict[str, Any]): A dictionary containing the ProxLB configuration.

        Returns:
            Dict[str, Any]: A dictionary containing metrics and information for all running guests.
        """
        logger.debug("Starting: get_guests.")
        guests: Dict[str, ProxLbData.Guest] = {}

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

                    guests[guest['name']] = ProxLbData.Guest(
                        name=guest['name'],
                        cpu=ProxLbData.Guest.Metric(
                            total=int(guest['cpus']),
                            used=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', None),
                            pressure_some_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', 'some'),
                            pressure_full_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', 'full'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', 'some', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', 'full', spikes=True),
                            pressure_hot=False,
                        ),
                        disk=ProxLbData.Guest.Metric(
                            total=guest['maxdisk'],
                            used=guest['disk'],
                            pressure_some_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'disk', 'some'),
                            pressure_full_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'disk', 'full'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'disk', 'some', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'disk', 'full', spikes=True),
                            pressure_hot=False,
                        ),
                        memory=ProxLbData.Guest.Metric(
                            total=guest['maxmem'],
                            used=guest['mem'],
                            pressure_some_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'memory', 'some'),
                            pressure_full_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'memory', 'full'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'memory', 'some', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'memory', 'full', spikes=True),
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
                        ignore=Tags.get_ignore(guest_tags),
                        node_relationships=Tags.get_node_relationships(guest_tags, nodes, guest_pools, guest_ha_rules, proxlb_config),
                        node_relationships_strict=Pools.get_pool_node_affinity_strictness(proxlb_config, guest_pools),
                        type=GuestType.Vm,
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

                    guests[guest['name']] = ProxLbData.Guest(
                        cpu=ProxLbData.Guest.Metric(
                            total=int(guest['cpus']),
                            used=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', None),
                            pressure_some_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', 'some'),
                            pressure_full_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', 'full'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', 'some', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'cpu', 'full', spikes=True),
                            pressure_hot=False,
                        ),
                        disk=ProxLbData.Guest.Metric(
                            total=guest['maxdisk'],
                            used=guest['disk'],
                            pressure_some_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'disk', 'some'),
                            pressure_full_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'disk', 'full'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'disk', 'some', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'disk', 'full', spikes=True),
                            pressure_hot=False,
                        ),
                        memory=ProxLbData.Guest.Metric(
                            total=guest['maxmem'],
                            used=guest['mem'],
                            pressure_some_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'memory', 'some'),
                            pressure_full_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'memory', 'full'),
                            pressure_some_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'memory', 'some', spikes=True),
                            pressure_full_spikes_percent=Guests.get_guest_rrd_data(proxmox_api, node, guest['vmid'], guest['name'], 'memory', 'full', spikes=True),
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
                        ignore=Tags.get_ignore(guest_tags),
                        node_relationships=Tags.get_node_relationships(guest_tags, nodes, guest_pools, guest_ha_rules, proxlb_config),
                        node_relationships_strict=Pools.get_pool_node_affinity_strictness(proxlb_config, guest_pools),
                        type=GuestType.Ct,
                    )

                    logger.debug(f"Resources of Guest {guest['name']} (type CT) added: {guests[guest['name']]}")
                else:
                    logger.debug(f'Metric for CT {guest["name"]} ignored because CT is not running.')

        logger.debug("Finished: get_guests.")
        return guests

    @staticmethod
    def get_guest_rrd_data(proxmox_api: ProxmoxApi, node_name: str, vm_id: int, vm_name: str, object_name: str, object_type: Optional[str], spikes: bool = False) -> float:
        """
        Retrieves the rrd data metrics for a specific resource (CPU, memory, disk) of a guest VM or CT.

        Args:
            proxmox_api (Any): The Proxmox API client instance.
            node_name (str): The name of the node hosting the guest.
            vm_id (int): The ID of the guest VM or CT.
            vm_name (str): The name of the guest VM or CT.
            object_name (str): The resource type to query (e.g., 'cpu', 'memory', 'disk').
            object_type (str, optional): The pressure type ('some', 'full') or None for average usage.
            spikes (bool, optional): Whether to consider spikes in the calculation. Defaults to False.

        Returns:
            float: The calculated average usage value for the specified resource.
        """
        logger.debug("Starting: get_guest_rrd_data.")
        time.sleep(0.1)

        try:
            if spikes:
                logger.debug(f"Getting spike RRD data for {object_name} from guest: {vm_name}.")
                guest_data_rrd = proxmox_api.nodes(node_name).qemu(vm_id).rrddata.get(timeframe="hour", cf="MAX")
            else:
                logger.debug(f"Getting average RRD data for {object_name} from guest: {vm_name}.")
                guest_data_rrd = proxmox_api.nodes(node_name).qemu(vm_id).rrddata.get(timeframe="hour", cf="AVERAGE")
        except Exception:
            logger.error(f"Failed to retrieve RRD data for guest: {vm_name} (ID: {vm_id}) on node: {node_name}. Using 0.0 as value.")
            logger.debug("Finished: get_guest_rrd_data.")
            return float(0.0)

        if object_type:

            lookup_key = f"pressure{object_name}{object_type}"
            if spikes:
                # RRD data is collected every minute, so we look at the last 6 entries
                # and take the maximum value to represent the spike
                logger.debug(f"Getting RRD data (spike: {spikes}) of pressure for {object_name} {object_type} from guest: {vm_name}.")
                _rrd_data_value = [row.get(lookup_key) for row in guest_data_rrd if row.get(lookup_key) is not None]
                rrd_data_value = max(_rrd_data_value[-6:], default=0.0)
            else:
                # Calculate the average value from the RRD data entries
                logger.debug(f"Getting RRD data (spike: {spikes}) of pressure for {object_name} {object_type} from guest: {vm_name}.")
                rrd_data_value = sum(entry.get(lookup_key, 0.0) for entry in guest_data_rrd) / len(guest_data_rrd)

        else:
            logger.debug(f"Getting RRD data of cpu usage from guest: {vm_name}.")
            rrd_data_value = sum(entry.get("cpu", 0.0) for entry in guest_data_rrd) / len(guest_data_rrd)

        logger.debug(f"RRD data (spike: {spikes}) for {object_name} from guest: {vm_name}: {rrd_data_value}")
        logger.debug("Finished: get_guest_rrd_data.")
        return rrd_data_value
