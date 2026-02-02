from typing import Any, Literal, Optional, TypeVar, assert_never
from pydantic import BaseModel
from .config_parser import Config

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
