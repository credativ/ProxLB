"""
The Nodes class retrieves all running nodes in a Proxmox cluster
and collects their resource metrics.

Methods:
    __init__:
        Initializes the Nodes class.

    get_nodes(proxmox_api: ProxmoxApi, proxlb_config: Dict[str, Any]) -> Dict[str, Any]:
        Gets metrics of all nodes in a Proxmox cluster.

    set_node_maintenance(proxlb_config: Dict[str, Any], node_name: str) -> Dict[str, Any]:
        Sets Proxmox nodes to a maintenance mode if required.

    set_node_ignore(proxlb_config: Dict[str, Any], node_name: str) -> Dict[str, Any]:
        Sets Proxmox nodes to be ignored if requested.
"""

__author__ = "Florian Paul Azim Hoberg <gyptazy>"
__copyright__ = "Copyright (C) 2025 Florian Paul Azim Hoberg (@gyptazy)"
__license__ = "GPL-3.0"


import time
from typing import TYPE_CHECKING, Dict
from proxlb.utils.config_parser import Config
from proxlb.utils.logger import SystemdLogger
from proxlb.utils.proxlb_data import ProxLbData
from proxlb.utils.proxmox_api import ProxmoxApi
from proxlb.utils.rrd import NodeRrdKey, RrdDatasets

if TYPE_CHECKING:
    from proxlb.utils.rrd import NodeRrdDatasets

BalancingResource = Config.Balancing.Resource

logger = SystemdLogger()


class Nodes:
    """
    The Nodes class retrieves all running nodes in a Proxmox cluster
    and collects their resource metrics.
    """
    def __init__(self) -> None:
        """
        Initializes the Nodes class with the provided ProxLB data.
        """

    @staticmethod
    def get_nodes(proxmox_api: ProxmoxApi, proxlb_config: Config) -> Dict[str, ProxLbData.Node]:
        """
        Get metrics of all nodes in a Proxmox cluster.

        This method retrieves metrics for all available nodes in the Proxmox cluster.
        It iterates over each node and collects resource metrics including CPU, memory, and disk usage.

        Args:
            proxmox_api (any): The Proxmox API client instance.
            proxmox_config (Dict[str, Any]): A dictionary containing the ProxLB configuration.
            nodes (Dict[str, Any]): A dictionary containing information about the nodes in the Proxmox cluster.

        Returns:
            Dict[str, Any]: A dictionary containing metrics and information for all running nodes.
        """
        logger.debug("Starting: get_nodes.")
        nodes: dict[str, ProxLbData.Node] = {}

        for node in proxmox_api.nodes.get():
            # Ignoring a node results into ignoring all placed guests on the ignored node!
            if node["status"] == "online" and not Nodes.set_node_ignore(proxlb_config, node["node"]):

                cpu_used = node["cpu"] * node["maxcpu"]
                # node["cpu"] is a utilization fraction (0.0–1.0)
                cpu_free = (node["maxcpu"]) - (node["cpu"] * node["maxcpu"])
                disk_free = node["maxdisk"] - node["disk"]
                disk_used = node["disk"]
                memory_used = node["mem"]
                memory_free = node["maxmem"] - node["mem"]
                node_rrd = Nodes.get_node_rrd_datasets(proxmox_api, node["node"])

                nodes[node["node"]] = ProxLbData.Node(
                    name=node["node"],
                    pve_version=Nodes.get_node_pve_version(proxmox_api, node["node"]),
                    pressure_hot=False,
                    maintenance=False,
                    cpu=ProxLbData.Node.Metric(
                        total=node["maxcpu"],
                        assigned=0,
                        used=cpu_used,
                        free=cpu_free,
                        assigned_percent=0,
                        free_percent=cpu_free / node["maxcpu"] * 100,
                        used_percent=cpu_used / node["maxcpu"] * 100,
                        pressure_some_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressurecpusome"),
                        pressure_full_percent=0.0,  # There is no pressure_full for nodes, only for guests
                        pressure_some_spikes_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressurecpusome", spikes=True),
                        pressure_full_spikes_percent=0.0,
                        pressure_hot=False,
                    ),
                    disk=ProxLbData.Node.Metric(
                        total=node["maxdisk"],
                        assigned=0,
                        used=node["disk"],
                        free=disk_free,
                        assigned_percent=0,
                        free_percent=disk_free / node["maxdisk"] * 100,
                        used_percent=disk_used / node["maxdisk"] * 100,
                        pressure_some_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressureiosome"),
                        pressure_full_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressureiofull"),
                        pressure_some_spikes_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressureiosome", spikes=True),
                        pressure_full_spikes_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressureiofull", spikes=True),
                        pressure_hot=False,
                    ),
                    memory=ProxLbData.Node.Metric(
                        total=Nodes.set_node_resource_reservation(node["node"], node["maxmem"], proxlb_config, BalancingResource.Memory),
                        assigned=0,
                        used=memory_used,
                        free=memory_free,
                        assigned_percent=0,
                        free_percent=memory_free / node["maxmem"] * 100,
                        used_percent=memory_used / node["maxmem"] * 100,
                        pressure_some_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressurememorysome"),
                        pressure_full_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressurememoryfull"),
                        pressure_some_spikes_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressurememorysome", spikes=True),
                        pressure_full_spikes_percent=Nodes.get_node_rrd_value(node_rrd, node["node"], "pressurememoryfull", spikes=True),
                        pressure_hot=False,
                    ),
                )

                # Evaluate if node should be set to maintenance mode
                if Nodes.set_node_maintenance(proxmox_api, proxlb_config, node["node"]):
                    nodes[node["node"]].maintenance = True

        logger.debug(f"Node metrics collected: {nodes}")
        logger.debug("Finished: get_nodes.")
        return nodes

    @staticmethod
    def set_node_maintenance(proxmox_api: ProxmoxApi, proxlb_config: Config, node_name: str) -> bool:
        """
        Set nodes to maintenance mode based on the provided configuration.

        This method updates the nodes dictionary to mark certain nodes as being in maintenance mode
        based on the configuration provided in proxlb_config.

        Args:
            proxmox_api (any): The Proxmox API client instance.
            proxlb_config (Dict[str, Any]): A dictionary containing the ProxLB configuration, including maintenance nodes.
            node_name: (str): The current node name within the outer iteration.

        Returns:
            Bool: Returns a bool if the provided node name is present in the maintenance section of the config file.
        """
        logger.debug("Starting: set_node_maintenance.")

        # Evaluate maintenance mode by config
        if node_name in proxlb_config.proxmox_cluster.maintenance_nodes:
            logger.info(f"Node: {node_name} has been set to maintenance mode (by ProxLB config).")
            return True
        else:
            logger.debug(f"Node: {node_name} is not in maintenance mode by ProxLB config.")

        # Evaluate maintenance mode by Proxmox HA
        for ha_element in proxmox_api.cluster.ha.status.current.get():
            if ha_element.get("status"):
                if "maintenance mode" in ha_element.get("status"):
                    if ha_element.get("node") == node_name:
                        logger.info(f"Node: {node_name} has been set to maintenance mode (by Proxmox HA API).")
                        return True
                    else:
                        logger.debug(f"Node: {node_name} is not in maintenance mode by Proxmox HA API.")

        logger.debug("Finished: set_node_maintenance.")
        return False

    @staticmethod
    def set_node_ignore(proxlb_config: Config, node_name: str) -> bool:
        """
        Set nodes to be ignored based on the provided configuration.

        This method updates the nodes dictionary to mark certain nodes as being ignored
        based on the configuration provided in proxlb_config.

        Args:
            proxlb_config (Dict[str, Any]): A dictionary containing the ProxLB configuration, including maintenance nodes.
            node_name: (str): The current node name within the outer iteration.

        Returns:
            Bool: Returns a bool if the provided node name is present in the ignore section of the config file.
        """
        logger.debug("Starting: set_node_ignore.")

        # FIXME: This method does not actually update anything

        if node_name in proxlb_config.proxmox_cluster.ignore_nodes:
            logger.info(f"Node: {node_name} has been set to be ignored. Not adding node!")
            return True

        logger.debug("Finished: set_node_ignore.")
        return False

    @staticmethod
    def get_node_rrd_datasets(proxmox_api: ProxmoxApi, node_name: str) -> 'NodeRrdDatasets':
        """
        Fetches the RRD data for a node once, covering both the average and maximum
        (spike) consolidation functions.

        A single Proxmox RRD response already contains every metric (cpu, memory, disk
        usage as well as all of their pressure figures) for the requested timeframe.
        Fetching it once per consolidation function (AVERAGE/MAX) and deriving all
        individual values from it via get_node_rrd_value() avoids querying the same
        endpoint repeatedly (previously up to 12 times per node).

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            node_name (str): The name of the node.

        Returns:
            NodeRrdDatasets: The average and maximum RRD data entries for the node.
        """
        logger.debug("Starting: get_node_rrd_datasets.")

        time.sleep(0.1)
        try:
            logger.debug(f"Getting average RRD data from node: {node_name}.")
            rrd_average = proxmox_api.nodes(node_name).rrddata.get(timeframe="hour", cf="AVERAGE")
        except Exception:
            logger.error(f"Failed to retrieve AVERAGE RRD data for node: {node_name}. Using 0.0 as value.")
            rrd_average = []

        time.sleep(0.1)
        try:
            logger.debug(f"Getting spike RRD data from node: {node_name}.")
            rrd_max = proxmox_api.nodes(node_name).rrddata.get(timeframe="hour", cf="MAX")
        except Exception:
            logger.error(f"Failed to retrieve MAX RRD data for node: {node_name}. Using 0.0 as value.")
            rrd_max = []

        logger.debug("Finished: get_node_rrd_datasets.")
        return RrdDatasets(average=rrd_average, maximum=rrd_max)

    @staticmethod
    def get_node_rrd_value(rrd_datasets: 'NodeRrdDatasets', node_name: str, rrd_key: NodeRrdKey, spikes: bool = False) -> float:
        """
        Derives a single rrd data metric (CPU, memory, disk pressure) of a node from the
        datasets already fetched via get_node_rrd_datasets(). This performs no API call
        itself.

        Args:
            rrd_datasets (NodeRrdDatasets): The RRD entries fetched for both consolidation functions.
            node_name (str): The name of the node.
            rrd_key (NodeRrdKey): The rrd field to read.
            spikes (bool, optional): Whether to consider spikes in the calculation. Defaults to False.

        Returns:
            float: The calculated average usage value for the specified resource.
        """
        logger.debug("Starting: get_node_rrd_value.")
        node_data_rrd = rrd_datasets.maximum if spikes else rrd_datasets.average

        if not node_data_rrd:
            logger.debug("Finished: get_node_rrd_value.")
            return 0.0

        if spikes:
            # RRD data is collected every minute, so we look at the last 6 entries
            # and take the maximum value to represent the spike
            rrd_data_value = max(
                [value for row in node_data_rrd if (value := row.get(rrd_key)) is not None][-6:],
                default=0.0,
            )
        else:
            # Calculate the average value from the RRD data entries
            rrd_data_value = sum(value for entry in node_data_rrd if (value := entry.get(rrd_key)) is not None) / len(node_data_rrd)

        logger.debug(f"RRD data (spike: {spikes}) for {rrd_key} from node: {node_name}: {rrd_data_value}")
        logger.debug("Finished: get_node_rrd_value.")
        return rrd_data_value

    @staticmethod
    def get_node_pve_version(proxmox_api: ProxmoxApi, node_name: str) -> str:
        """
        Return the Proxmox VE (PVE) version for a given node by querying the Proxmox API.

        This function calls proxmox_api.nodes(node_name).version.get() and extracts the
        'version' field from the returned mapping.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            node_name (str): The name of the node hosting the guest.

        Returns:
            str: The PVE version for the specified node.

        Raises:
        Exception: If the proxmox_api call fails, returns an unexpected structure, or the
                   'version' field is missing or is not a string. Callers should
                    handle or propagate exceptions as appropriate.
        """
        logger.debug("Starting: get_node_pve_version.")
        time.sleep(0.1)

        try:
            logger.debug(f"Trying to get PVE version for node: {node_name}.")
            version = proxmox_api.nodes(node_name).version.get()
        except Exception:
            logger.error(f"Failed to get PVE version for node: {node_name}.")
            raise

        logger.debug(f"Got version {version['version']} for node {node_name}.")
        logger.debug("Finished: get_node_pve_version.")
        ret = version["version"]
        # https://pve.proxmox.com/pve-docs-7/api-viewer/#/nodes/{node}/version
        # https://pve.proxmox.com/pve-docs-8/api-viewer/#/nodes/{node}/version
        # https://pve.proxmox.com/pve-docs/api-viewer/#/nodes/{node}/version
        assert isinstance(ret, str), f"{ret} is not a str"
        return ret

    @staticmethod
    def set_node_resource_reservation(node_name: str, resource_value: int, proxlb_config: Config, resource_type: BalancingResource) -> int:
        """
        Check if there is a configured resource reservation for the current node and apply it as needed.
        Checks for a node specific config first, then if there is any configured default and if neither then nothing is reserved.
        Reservations are applied by directly modifying the resource value.

        Args:
            node_name (str):                    The name of the node.
            resource_value (int):               The total resource value in bytes.
            proxlb_config (Dict[str, Any]):     A dictionary containing the ProxLB configuration.
            resource_type (str):                The type of resource ('memory', 'disk', etc.).

        Returns:
            int:                                The resource value after applying any configured reservations.
        """
        logger.debug("Starting: apply_resource_reservation")

        if reserve_cfg := proxlb_config.balancing.node_resource_reserve:
            node_resource_reservation = reserve_cfg.get(node_name, {}).get(resource_type, 0)
            default_resource_reservation = reserve_cfg.get("defaults", {}).get(resource_type, 0)
        else:
            node_resource_reservation = 0
            default_resource_reservation = 0

        # Apply node specific reservation if set
        if node_resource_reservation > 0:
            if resource_value < (node_resource_reservation * 1024 ** 3):
                logger.critical(f"Configured resource reservation for node {node_name} of type {resource_type} with {node_resource_reservation} GB is higher than available resource value {resource_value / (1024 ** 3):.2f} GB. Not applying...")
                return resource_value
            else:
                logger.debug(f"Applying node specific reservation for {node_name} of type {resource_type} with {node_resource_reservation} GB.")
                resource_value_new = resource_value - (node_resource_reservation * 1024 ** 3)
                logger.debug(f'Switched resource value for node {node_name} of type {resource_type} from {resource_value / (1024 ** 3):.2f} GB to {resource_value_new / (1024 ** 3):.2f} GB after applying reservation.')
                logger.debug(f"Before: {resource_value} | After: {resource_value_new}")
                return resource_value_new

        # Apply default reservation if set and no node specific reservation has been performed
        elif default_resource_reservation > 0:
            if resource_value < (default_resource_reservation * 1024 ** 3):
                logger.critical(f"Configured default reservation for node {node_name} of type {resource_type} with {default_resource_reservation} GB is higher than available resource value {resource_value / (1024 ** 3):.2f} GB. Not applying...")
                return resource_value
            else:
                logger.debug(f"Applying default reservation for {node_name} of type {resource_type} with {default_resource_reservation} GB.")
                resource_value_new = resource_value - (default_resource_reservation * 1024 ** 3)
                logger.debug(f'Switched resource value for node {node_name} of type {resource_type} from {resource_value / (1024 ** 3):.2f} GB to {resource_value_new / (1024 ** 3):.2f} GB after applying reservation.')
                logger.debug(f"Before: {resource_value} | After: {resource_value_new}")
                return resource_value_new

        else:
            logger.debug(f"No default or node specific resource reservation for node {node_name} found. Skipping...")
            logger.debug("Finished: apply_resource_reservation")
            return resource_value
