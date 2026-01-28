from dataclasses import dataclass, field
from typing import Any, Literal, Optional, TypeVar, TypedDict
from pydantic import Field
from utils.config_parser import Config

ConfigType = TypeVar("ConfigType", bound=Config)

@dataclass
class ProxLbData:
    class Meta(Config):
        class Balancing(Config.Balancing):
            balance_next_node: str
            balance_next_guest: str
            balance: bool
            balance_reason: str
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
        def from_config(cls: type[ConfigType], config: Config) -> ConfigType:
            return cls(**config.model_dump())

    @dataclass
    class Groups:
        affinity: dict[str, Any] = field(default_factory=dict)
        anti_affinity: dict[str, Any] = field(default_factory=dict)
        maintenance: list[str] = field(default_factory=list)

    meta: Meta
    nodes: dict[str, Any]
    groups: Groups
    guests: dict[str, Any]
