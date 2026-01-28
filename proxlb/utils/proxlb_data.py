from typing import Any, Literal, Optional, TypeVar, assert_never
from pydantic import BaseModel, Field
from utils.config_parser import Config

ConfigType = TypeVar("ConfigType", bound=Config)

class ProxLbData(BaseModel):
    class Meta(Config):
        class Balancing(Config.Balancing):
            balance_next_node: str = ""
            balance_next_guest: str = ""
            balance: bool = False
            balance_reason: str = 'resources'
            parallel_jobs: int  = 5
            processed_guests_psi: list[str] = Field(default_factory=list)
        cluster_non_pve9: bool
        balancing: Balancing
        statistics: Optional[
            dict[
                Literal["before", "after"],
                dict[
                    Literal["cpu", "disk", "memory"],
                    str
                ]
            ]
        ] = None

        @classmethod
        def from_config(cls: type[ConfigType], config: Config, **kwargs: Any) -> ConfigType:
            return cls(**config.model_dump(by_alias=True), **kwargs)

    class Groups(BaseModel):
        affinity: dict[str, Any] = Field(default_factory=dict)
        anti_affinity: dict[str, Any] = Field(default_factory=dict)
        maintenance: list[str] = Field(default_factory=list)

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
        id: str
        node_current: str
        node_target: str
        processed: bool
        pressure_hot: bool
        tags: list[str]
        pools: list[str]
        ha_rules: list[dict[str, Any]]
        affinity_groups: list[str]
        anti_affinity_groups: list[str]
        ignore: bool
        node_relationships: list[str]
        node_relationships_strict: bool
        type: Literal["vm", "ct"]

        def metric(self, name: Literal["cpu", "disk", "memory"]) -> Metric:
            if name == "cpu": return self.cpu
            if name == "disk": return self.disk
            if name == "memory": return self.memory
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
        def metric(self, name: Literal["cpu", "disk", "memory"]) -> Metric:
            if name == "cpu": return self.cpu
            if name == "disk": return self.disk
            if name == "memory": return self.memory
            assert_never(name)

    class Pool(BaseModel):
        name: str
        members: list[str] = Field(default_factory=list)

    class HaRule(BaseModel):
        rule: str
        type: Literal["affinity", "anti-affinity"]
        nodes: list[str]
        members: list[int]

    groups: Groups
    guests: dict[str, Guest]
    ha_rules: dict[str, HaRule]
    meta: Meta
    nodes: dict[str, Node]
    pools: dict[str, Pool]
