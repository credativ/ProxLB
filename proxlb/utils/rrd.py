from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, TypeVar

RrdEntry = TypeVar("RrdEntry")


@dataclass
class RrdDatasets(Generic[RrdEntry]):
    average: list[RrdEntry]
    maximum: list[RrdEntry]


# The rrd fields we read, named as in pve-cluster.git src/pmxcfs/status.c.
NodeRrdKey: TypeAlias = Literal[
    "pressurecpusome",
    "pressureiosome",
    "pressureiofull",
    "pressurememorysome",
    "pressurememoryfull",
]

GuestRrdKey: TypeAlias = Literal[
    "cpu",
    "pressurecpusome",
    "pressurecpufull",
    "pressureiosome",
    "pressureiofull",
    "pressurememorysome",
    "pressurememoryfull",
]

if TYPE_CHECKING:
    from proxmoxer_types.v9.core import ProxmoxAPI

    NodeRrdDatasets: TypeAlias = RrdDatasets[ProxmoxAPI.Nodes.Node.Rrddata._Get.TypedDict]
    GuestRrdDatasets: TypeAlias = (
        RrdDatasets[ProxmoxAPI.Nodes.Node.Qemu.Vmid.Rrddata._Get.TypedDict]
        | RrdDatasets[ProxmoxAPI.Nodes.Node.Lxc.Vmid.Rrddata._Get.TypedDict]
    )
