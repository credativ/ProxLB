"""
The Storage class retrieves the storage topology of a Proxmox cluster and
attributes guest disk usage to storages.

It sweeps the node/storage matrix once: `nodes(*).storage.get()` provides the
per-node capacity and availability of every storage, and
`nodes(*).storage(*).content.get()` provides the volumes owned by each guest.
Shared storages are content-listed only once (they are a single instance),
node-local storages once per node instance. Only content types belonging to
guests ('images' for VMs, 'rootdir' for CTs) are requested, so storages that
hold only backups, ISOs or templates are never content-listed at all.
"""

import time
from typing import TYPE_CHECKING, Dict
from proxlb.utils.logger import SystemdLogger
from proxlb.utils.proxlb_data import ProxLbData
from proxlb.utils.proxmox_api import ProxmoxApi

if TYPE_CHECKING:
    from proxmoxer_types.v9.core import ProxmoxAPI
    NodeStorages = list[ProxmoxAPI.Nodes.Node.Storage._Get.TypedDict]
    StorageContent = list[ProxmoxAPI.Nodes.Node.Storage.Storage.Content._Get.TypedDict]

logger = SystemdLogger()

# Volume content types that belong to guests and travel with them on
# migration. Everything else (backup, iso, vztmpl, snippets, import) is
# irrelevant for balancing and deliberately never queried.
GUEST_CONTENT_TYPES = ("images", "rootdir")


class Storage:
    """
    The Storage class retrieves the storage topology of a Proxmox cluster and
    attributes guest disk usage to storages.

    Methods:
        __init__:
            Initializes the Storage class.

        get_storage(proxmox_api: ProxmoxApi, nodes: Dict[str, ProxLbData.Node]) -> Dict[str, ProxLbData.Storage]:
            Collects per-node storage status and per-guest disk usage for all
            storages in the cluster.

        get_disks_for_guest(guest_id: int, storage: Dict[str, ProxLbData.Storage]) -> Dict[str, int]:
            Returns the disk bytes of a single guest, keyed by storage id.
    """
    def __init__(self) -> None:
        """
        Initializes the Storage class with the provided ProxLB data.
        """

    @staticmethod
    def get_storage(proxmox_api: ProxmoxApi, nodes: Dict[str, ProxLbData.Node]) -> Dict[str, ProxLbData.Storage]:
        """
        Collect storage topology and guest disk usage for the cluster.

        This method iterates over all nodes and gathers each storage's
        per-node capacity/availability, then lists the guest volumes
        ('images' and 'rootdir' content) to attribute disk bytes to guests.
        Shared storages are listed once, node-local storages per node.

        API errors degrade gracefully: a node or storage that cannot be
        queried is logged and skipped, so partial data is returned rather
        than none.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            nodes (Dict[str, ProxLbData.Node]): The nodes collected by Nodes.get_nodes.

        Returns:
            Dict[str, ProxLbData.Storage]: Storage entities keyed by storage id.
        """
        logger.debug("Starting: get_storage.")
        storage: Dict[str, ProxLbData.Storage] = {}

        # 1. Per-node storage status: capacity, availability and the
        #    entity-level attributes (type, shared flag, content types).
        for node_name in nodes.keys():
            time.sleep(0.1)
            try:
                node_storages: 'NodeStorages' = proxmox_api.nodes(node_name).storage.get()
            except Exception as proxmox_api_error:
                logger.error(f"Failed to list storages on node: {node_name}: {proxmox_api_error}. Skipping node.")
                continue

            for entry in node_storages:
                storage_name = entry["storage"]
                if storage_name not in storage:
                    storage[storage_name] = ProxLbData.Storage(
                        name=storage_name,
                        type=entry["type"],
                        shared=bool(entry.get("shared", False)),
                        content=[content_type for content_type in entry["content"].split(",") if content_type],
                    )
                storage[storage_name].nodes[node_name] = ProxLbData.Storage.NodeStatus(
                    total=entry.get("total", 0),
                    used=entry.get("used", 0),
                    avail=entry.get("avail", 0),
                    active=bool(entry.get("active", False)),
                    enabled=bool(entry.get("enabled", True)),
                )

        # 2. Guest volumes: shared storages hold a single volume set, so one
        #    active node is asked; node-local storages are independent
        #    instances and every active one is asked.
        for storage_name, storage_entity in storage.items():
            content_types = [content_type for content_type in GUEST_CONTENT_TYPES if content_type in storage_entity.content]
            if not content_types:
                logger.debug(f"Storage: {storage_name} holds no guest content. Skipping content listing.")
                continue

            active_nodes = [node_name for node_name, status in storage_entity.nodes.items() if status.active and status.enabled]
            if not active_nodes:
                logger.debug(f"Storage: {storage_name} is not active on any node. Skipping content listing.")
                continue

            query_nodes = active_nodes[:1] if storage_entity.shared else active_nodes
            for node_name in query_nodes:
                for content_type in content_types:
                    time.sleep(0.1)
                    try:
                        volumes: 'StorageContent' = proxmox_api.nodes(node_name).storage(storage_name).content.get(content=content_type)
                    except Exception as proxmox_api_error:
                        logger.error(f"Failed to list {content_type} content of storage: {storage_name} on node: {node_name}: {proxmox_api_error}. Skipping.")
                        continue

                    for volume in volumes:
                        guest_id = volume.get("vmid")
                        if guest_id is None:
                            continue
                        disks = storage_entity.guest_disks.setdefault(guest_id, ProxLbData.Storage.GuestDisks())
                        # Allocated: actual consumption where the storage
                        # reports it (thin storages), provisioned size
                        # otherwise.
                        used = volume.get("used")
                        disks.allocated += used if used is not None else volume["size"]
                        disks.provisioned += volume["size"]

        logger.debug(f"Storage topology collected: {storage}")
        logger.debug("Finished: get_storage.")
        return storage

    @staticmethod
    def get_disks_for_guest(guest_id: int, storage: Dict[str, ProxLbData.Storage]) -> Dict[str, int]:
        """
        Return the allocated disk bytes of a single guest, keyed by storage id.

        Args:
            guest_id (int): The vmid of the guest.
            storage (Dict[str, ProxLbData.Storage]): Storages from get_storage.

        Returns:
            Dict[str, int]: Disk bytes per storage id; storages without
                volumes of this guest are omitted.
        """
        return {
            storage_name: storage_entity.guest_disks[guest_id].allocated
            for storage_name, storage_entity in storage.items()
            if guest_id in storage_entity.guest_disks
        }
