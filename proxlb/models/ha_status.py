"""
The HaStatus class retrieves all HA status information on a Proxmox cluster
including from the HA manager.
"""

__author__ = "Florian Paul Azim Hoberg <gyptazy>"
__copyright__ = "Copyright (C) 2025 Florian Paul Azim Hoberg (@gyptazy)"
__license__ = "GPL-3.0"


import socket
from proxlb.utils.logger import SystemdLogger
from proxlb.utils.proxmox_api import ProxmoxApi

logger = SystemdLogger()


class HaStatus:
    """
    The HaStatus class retrieves all HA status information on a Proxmox cluster
    including from the HA manager.

    Methods:
        __init__:
            Initializes the HaStatus class.

        get_ha_manager(proxmox_api: any) -> Any:
            Retrieve HA status information from the Proxmox cluster.
            Returns a str of the HA manager node or "unknown" if it cannot be evaluated.

        is_node_ha_manager(proxmox_api: any) -> bool:
            Check if the local executing node is the HA manager.
    """
    def __init__(self) -> None:
        """
        Initializes the HA Status class with the provided ProxLB data.
        """

    @staticmethod
    def get_ha_manager(proxmox_api: ProxmoxApi) -> str:
        """
        Retrieve the HA Manager node from a Proxmox cluster.

        Queries the Proxmox API for HA manager information.

        Args:
            proxmox_api (any):      Proxmox API client instance.

        Returns:
            str:                    The name of the HA manager node if
                                    available, otherwise "unknown".
        """
        logger.debug("Starting: get_ha_manager.")

        manager_status = proxmox_api.cluster.ha.status.manager_status.get()
        master_node = manager_status.get("manager_status", {})

        if master_node:
            ha_master_node = master_node.get("master_node", "unknown")
            logger.debug(f"HA manager node: {master_node}")
        else:
            ha_master_node = "unknown"
            logger.debug("HA manager node could not be evaluated. Setting to unknown.")

        logger.debug("Finished: get_ha_manager.")
        return str(ha_master_node)

    @staticmethod
    def is_node_ha_manager(proxmox_api: ProxmoxApi) -> bool:
        """
        Check if the local executing node is the HA manager.

        Args:
            ha_master_node (str):   The name of the HA manager node.

        Returns:
            bool:                   True if the node is the HA manager, False otherwise.
        """
        logger.debug("Starting: is_node_ha_manager.")

        ha_master_node = HaStatus.get_ha_manager(proxmox_api)

        if ha_master_node == socket.gethostname():
            logger.debug(f"Cluster Master is: {ha_master_node} | We are: {socket.gethostname()}) | This node is the HA manager.")
            is_manager = True
        else:
            logger.debug(f"Cluster Master is: {ha_master_node} | We are: {socket.gethostname()}) | This node is NOT the HA manager.")
            is_manager = False

        logger.debug("Finished: is_node_ha_manager.")
        return is_manager
