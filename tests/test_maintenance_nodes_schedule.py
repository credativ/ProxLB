from datetime import datetime

from proxlb.utils.config_parser import Config
from proxlb.utils.helper import Helper


def _schedule(
    duration: int,
    pre_migration: int,
    schedules: dict[str, list[str]],
) -> Config.ProxmoxCluster.MaintenanceNodesSchedule:
    return Config.ProxmoxCluster.MaintenanceNodesSchedule.model_validate(
        {
            "duration": duration,
            "pre-migration": pre_migration,
            "schedules": schedules,
        }
    )


def _config() -> Config:
    return Config(
        proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
        proxmox_cluster=Config.ProxmoxCluster(
            maintenance_nodes=["static-node"],
            maintenance_nodes_schedule=_schedule(
                duration=3,
                pre_migration=10,
                schedules={
                    "scheduled-node": ["Monday, 8:00"],
                },
            ),
        ),
    )


def test_maintenance_schedule_adds_node_before_start() -> None:
    proxlb_config = _config()

    Helper.apply_maintenance_nodes_schedule(
        proxlb_config,
        now=datetime(2026, 5, 4, 7, 55),
    )

    assert proxlb_config.proxmox_cluster.maintenance_nodes == [
        "static-node",
        "scheduled-node",
    ]


def test_maintenance_schedule_removes_node_after_duration() -> None:
    proxlb_config = _config()

    Helper.apply_maintenance_nodes_schedule(
        proxlb_config,
        now=datetime(2026, 5, 4, 7, 55),
    )
    Helper.apply_maintenance_nodes_schedule(
        proxlb_config,
        now=datetime(2026, 5, 4, 11, 0),
    )

    assert proxlb_config.proxmox_cluster.maintenance_nodes == ["static-node"]


def test_maintenance_schedule_handles_pre_migration_across_week_boundary() -> None:
    proxlb_config = Config(
        proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
        proxmox_cluster=Config.ProxmoxCluster(
            maintenance_nodes_schedule=_schedule(
                duration=1,
                pre_migration=10,
                schedules={
                    "scheduled-node": ["Monday, 0:00"],
                },
            ),
        ),
    )

    Helper.apply_maintenance_nodes_schedule(
        proxlb_config,
        now=datetime(2026, 5, 3, 23, 55),
    )

    assert proxlb_config.proxmox_cluster.maintenance_nodes == ["scheduled-node"]


def test_invalid_maintenance_schedule_is_ignored() -> None:
    proxlb_config = Config(
        proxmox_api=Config.ProxmoxAPI(hosts=[], user=""),
        proxmox_cluster=Config.ProxmoxCluster(
            maintenance_nodes=["static-node"],
            maintenance_nodes_schedule=_schedule(
                duration=1,
                pre_migration=10,
                schedules={
                    "scheduled-node": ["Moonday, 8:00"],
                },
            ),
        ),
    )

    Helper.apply_maintenance_nodes_schedule(
        proxlb_config,
        now=datetime(2026, 5, 4, 7, 55),
    )

    assert proxlb_config.proxmox_cluster.maintenance_nodes == ["static-node"]
