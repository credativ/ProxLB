import time
from typing import Any
from unittest.mock import MagicMock

from proxlb.models.backups import Backups


def _proxmox_api(tasks: list[dict[str, object]], backup_jobs: list[dict[str, object]]) -> MagicMock:
    api = MagicMock()
    api.nodes.return_value.tasks.get.return_value = tasks
    api.cluster.backup.get.return_value = backup_jobs
    return api


def test_get_backup_jobs_running_task_of_single_guest_blocks_this_guest() -> None:
    proxmox_api = _proxmox_api([{"type": "vzdump", "id": "101", "starttime": 1}], [])

    backup_jobs = Backups.get_backup_jobs(proxmox_api, {"node1": MagicMock()}, 15)

    assert Backups.is_backup_active_or_imminent(backup_jobs, 101) is True
    assert Backups.is_backup_active_or_imminent(backup_jobs, 102) is False


def test_get_backup_jobs_running_task_of_multiple_guests_blocks_all_guests() -> None:
    # The id of a vzdump task is empty when the job covers more than one guest
    proxmox_api = _proxmox_api([{"type": "vzdump", "id": "", "starttime": 1}], [])

    backup_jobs = Backups.get_backup_jobs(proxmox_api, {"node1": MagicMock()}, 15)

    assert Backups.is_backup_active_or_imminent(backup_jobs, 101) is True


def test_get_backup_jobs_scheduled_job_within_lead_time_blocks_its_guests() -> None:
    proxmox_api = _proxmox_api([], [{"id": "backup-1", "enabled": 1, "vmid": "101,102", "next-run": time.time() + 600}])

    backup_jobs = Backups.get_backup_jobs(proxmox_api, {"node1": MagicMock()}, 15)

    assert Backups.is_backup_active_or_imminent(backup_jobs, 102) is True
    assert Backups.is_backup_active_or_imminent(backup_jobs, 103) is False


def test_get_backup_jobs_ignores_disabled_and_later_jobs() -> None:
    proxmox_api = _proxmox_api([], [
        {"id": "backup-1", "enabled": 0, "all": 1, "next-run": time.time() + 600},
        {"id": "backup-2", "enabled": 1, "all": 1, "next-run": time.time() + 7200},
    ])

    assert Backups.get_backup_jobs(proxmox_api, {"node1": MagicMock()}, 15) == {}


def test_set_guest_ignore_marks_guests_with_backup_as_ignored() -> None:
    proxmox_api = _proxmox_api([{"type": "vzdump", "id": "101", "starttime": 1}, {"type": "vzdump", "id": "103", "starttime": 1}], [])
    proxlb_config = MagicMock()
    proxlb_config.balancing.backup_guard.enable = True
    proxlb_config.balancing.backup_guard.lead_minutes = 15
    nodes: dict[str, Any] = {"node1": MagicMock(maintenance=False), "node2": MagicMock(maintenance=True)}
    guests: dict[str, Any] = {
        "vm101": MagicMock(id=101, ignore=False, node_current="node1"),
        "vm102": MagicMock(id=102, ignore=False, node_current="node1"),
        "vm103": MagicMock(id=103, ignore=False, node_current="node2"),
    }

    Backups.set_guest_ignore(proxmox_api, proxlb_config, nodes, guests)

    assert guests["vm101"].ignore is True
    assert guests["vm102"].ignore is False
    # Guests on a node in maintenance are not ignored, the solver finds no plan otherwise
    assert guests["vm103"].ignore is False
