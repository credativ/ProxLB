from typing import Any, Literal, Optional, TypeVar, assert_never
from pydantic import BaseModel
from proxlb.utils.config_parser import Config

ConfigType = TypeVar("ConfigType", bound="Config")

BalancingResource = Config.Balancing.Resource
AffinityType = Config.AffinityType
GuestType = Config.GuestType


class ProxLbData(BaseModel):

    class Meta(Config):

        class Balancing(Config.Balancing):
            balance_next_node: Optional[str] = None
            balance_next_guest: str = ""
            balance: bool = False
            balance_reason: str = 'resources'
            parallel_jobs: int = 5
            processed_guests_psi: list[str] = []

        balancing: Balancing = Balancing()  # pyright: ignore [reportIncompatibleVariableOverride]
        cluster_non_pve9: bool
        statistics: Optional[
            dict[
                Literal["before", "after"],
                dict[BalancingResource, str]
            ]
        ] = None

        @classmethod
        def from_config(cls: type[ConfigType], config: Config, **kwargs: Any) -> ConfigType:
            return cls(**config.model_dump(by_alias=True), **kwargs)

    class Groups(BaseModel):
        class Affinity(BaseModel):
            class Metric(BaseModel):
                total: int
                used: float
            counter: int = 1
            guests: list[str]
            cpu: Metric
            disk: Metric
            memory: Metric

        class AntiAffinity(BaseModel):
            guests: list[str]
            counter: int = 1
            used_nodes: list[str] = []

        affinity: dict[str, Affinity] = {}
        anti_affinity: dict[str, AntiAffinity] = {}
        maintenance: list[str] = []

    class Guest(BaseModel):
        class Metric(BaseModel):
            total: int
            used: float
            pressure_some_percent: float
            pressure_full_percent: float
            pressure_some_spikes_percent: float
            pressure_full_spikes_percent: float
            pressure_hot: bool
        cpu: Metric
        disk: Metric
        memory: Metric
        name: str
        id: int
        node_current: str
        node_target: str
        processed: bool
        pressure_hot: bool
        tags: list[str]
        pools: list[str]
        ha_rules: list["ProxLbData.HaRule"]
        affinity_groups: list[str]
        anti_affinity_groups: list[str]
        ignore: bool
        node_relationships: list[str]
        node_relationships_strict: bool
        type: GuestType
        # Disk bytes attributed to this guest per storage id, from the storage
        # content listings (actual allocation where the storage reports it,
        # provisioned size otherwise). Unlike the 'disk' metric above this is
        # populated for QEMU guests as well and carries the storage id, so
        # consumers can distinguish shared from node-local volumes via
        # ProxLbData.storage. Empty when storage collection is unavailable.
        disks: dict[str, int]
        # True when the guest is managed by the Proxmox HA stack
        # (/cluster/ha/resources). HA-routed migrations do not forward a
        # target storage parameter, so such guests cannot be remapped.
        ha_managed: bool

        def metric(self, name: BalancingResource) -> Metric:
            if name == BalancingResource.Cpu:
                return self.cpu
            if name == BalancingResource.Disk:
                return self.disk
            if name == BalancingResource.Memory:
                return self.memory
            assert_never(name)

    class Node(BaseModel):
        class Metric(BaseModel):
            total: int
            assigned: int
            used: float
            free: float
            assigned_percent: float
            free_percent: float
            used_percent: float
            pressure_some_percent: float
            pressure_full_percent: float
            pressure_some_spikes_percent: float
            pressure_full_spikes_percent: float
            pressure_hot: bool
        name: str
        pve_version: str
        pressure_hot: bool
        maintenance: bool
        cpu: Metric
        disk: Metric
        memory: Metric

        def metric(self, name: BalancingResource) -> Metric:
            if name == BalancingResource.Cpu:
                return self.cpu
            if name == BalancingResource.Disk:
                return self.disk
            if name == BalancingResource.Memory:
                return self.memory
            assert_never(name)

    class Pool(BaseModel):
        name: str
        members: list[str] = []

    class Storage(BaseModel):
        """
        A storage entity from the cluster-wide storage configuration.

        Storage ids are cluster-global: a non-shared storage (e.g. 'local')
        is one definition instantiated independently on every node listed in
        'nodes', while a shared storage is a single instance visible from all
        of them. The per-node status therefore carries identical numbers for
        shared storages and independent capacities for local ones.
        """
        class NodeStatus(BaseModel):
            total: int = 0
            used: int = 0
            avail: int = 0
            active: bool = False
            enabled: bool = True

        class GuestDisks(BaseModel):
            # Bytes actually consumed ('used' where the storage reports it,
            # provisioned size otherwise — on thick storages the two
            # coincide).
            allocated: int = 0
            # Configured upper bound: the sum of the volume sizes.
            provisioned: int = 0

        name: str
        type: str
        # Proxmox trusts this configuration flag to decide whether a
        # migration needs to copy disks; consumers should do the same.
        shared: bool = False
        content: list[str] = []
        # Per-node availability and capacity. Note that storages restricted
        # to specific nodes (config 'nodes' option) still appear in the other
        # nodes' listings with zeroed capacity and active/enabled False, so
        # consumers must filter on those flags, not on key presence.
        nodes: dict[str, NodeStatus] = {}
        # Disk bytes of each guest on this storage, keyed by guest id (the
        # Proxmox 'vmid'; VMs and CTs share one id namespace), summed over
        # all 'images' (VM) and 'rootdir' (CT) volumes owned by that guest.
        # For non-shared storages the sums span all node instances, so
        # leftover volumes on other nodes are (conservatively) attributed
        # as well.
        guest_disks: dict[int, GuestDisks] = {}

    class HaRule(BaseModel):
        rule: str
        type: AffinityType
        nodes: list[str]
        members: list[int]

    groups: Groups
    guests: dict[str, Guest]
    ha_rules: dict[str, HaRule]
    meta: Meta
    nodes: dict[str, Node]
    pools: dict[str, Pool]
    storage: dict[str, Storage]
