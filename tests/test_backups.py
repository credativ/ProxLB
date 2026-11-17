import time
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
