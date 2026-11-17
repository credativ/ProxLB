"""
The Backups class retrieves and processes backup job information from a
Proxmox cluster. It provides methods to detect active vzdump tasks and
upcoming scheduled backup jobs, used to guard against migrating VMs during
backup windows.
"""

__author__ = "Florian Paul Azim Hoberg <gyptazy>"
__copyright__ = "Copyright (C) 2025 Florian Paul Azim Hoberg (@gyptazy)"
__license__ = "GPL-3.0"


import time
from typing import TYPE_CHECKING, Dict, List, Optional
from pydantic import BaseModel
from proxlb.utils.config_parser import Config
from proxlb.utils.logger import SystemdLogger
from proxlb.utils.proxmox_api import ProxmoxApi
from proxlb.utils.proxlb_data import ProxLbData

if TYPE_CHECKING:
    from proxmoxer_types.v9.core import ProxmoxAPI
    TaskListEntry = ProxmoxAPI.Nodes.Node.Tasks._Get.TypedDict

logger = SystemdLogger()

# Sentinel VMID used in the backup_jobs map when a backup job covers all VMs
# (i.e. a scheduled job has an empty vmid field in the Proxmox API response)
# or when a running vzdump task does not tell which guests it covers.
_ALL_VMS_SENTINEL = -1


class Backups:
    """
    The Backups class retrieves and processes backup job information from a
    Proxmox cluster. It detects running vzdump tasks and upcoming scheduled
    backup jobs so that the balancer can avoid migrating guests during active
    or imminent backup windows.

    Methods:
        get_backup_jobs(proxmox_api, nodes, lead_minutes) -> Dict[int, BackupJob]:
            Returns a map of VMID -> BackupJob for all running or imminent backups.
            A synthetic entry with VMID -1 is added when a scheduled job covers
            all VMs (empty vmid field) or a running job covers more than one VM.

        is_backup_active_or_imminent(backup_jobs, vmid) -> bool:
            Returns True if the given VMID has a running or imminent backup,
            or if a catch-all backup job is active.

        set_guest_ignore(proxmox_api, proxlb_config, nodes, guests) -> None:
            Marks all guests with a running or imminent backup as ignored.
    """

    class BackupJob(BaseModel):
        """Represents a detected backup job for a single VMID."""
        running: bool                           # True if a vzdump task is currently active
        active: bool                            # True if running OR scheduled within lead_minutes
        vmid: int                               # Proxmox VMID (-1 means all VMs)
        vm_name: str                            # VM name when available, empty string otherwise
        job_start_time: Optional[int] = None    # Unix epoch; starttime for next running jobs

    @staticmethod
    def get_backup_jobs(proxmox_api: ProxmoxApi, nodes: Dict[str, ProxLbData.Node], lead_minutes: int) -> Dict[int, "Backups.BackupJob"]:
        """
        Returns a map of VMID -> BackupJob for all backup activity that should
        block migrations.

        Checks two sources:
        1. Active vzdump tasks on every online node
           (GET /nodes/{node}/tasks?source=active&typefilter=vzdump)
        2. Enabled cluster backup jobs whose next-run falls within lead_minutes
           (GET /cluster/backup)

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            nodes (Dict): The map of cluster nodes from ProxLbData.
            lead_minutes (int): How many minutes ahead to look for scheduled jobs.

        Returns:
            Dict[int, BackupJob]: VMID-keyed map of backup jobs.
            A key of -1 means a job covers all VMs or more than one VM.
        """
        logger.debug("Starting: get_backup_jobs.")
        backup_jobs: Dict[int, Backups.BackupJob] = {}

        # Get active vzdump tasks from each node and record them in the backup_jobs map.
        for node_name in nodes:
            try:
                time.sleep(0.1)
                tasks: list['TaskListEntry'] = proxmox_api.nodes(node_name).tasks.get(source="active", typefilter="vzdump")

                for task in tasks:
                    # The guest of a vzdump task is reported in the id field
                    raw_vmid = task.get("id")

                    if not raw_vmid:
                        # The id is empty for jobs covering more than one guest. Such a
                        # task only locks the guest it currently backs up and does not
                        # tell which guests are still to come.
                        logger.debug(f"Backups: Found active vzdump task for multiple guests on {node_name}.")
                        backup_jobs[_ALL_VMS_SENTINEL] = Backups.BackupJob(running=True, active=True, vmid=_ALL_VMS_SENTINEL, vm_name="", job_start_time=task.get("starttime"))
                        continue

                    try:
                        vmid = int(raw_vmid)
                    except (ValueError, TypeError):
                        logger.warning(
                            f"Backups: Unexpected non-integer vmid {raw_vmid}in active vzdump task on {node_name}. Skipping.")
                        continue

                    logger.debug(f"Backups: Found active vzdump task for VMID {vmid} on {node_name}.")

                    backup_jobs[vmid] = Backups.BackupJob(running=True, active=True, vmid=vmid, vm_name=str(raw_vmid), job_start_time=task.get("starttime"))
            except Exception as exc:
                logger.warning(f"Backups: Failed to query active vzdump tasks on node {node_name}: {exc}")

        # Get schedules backups jobs from the cluster backup API and check if any are
        # enabled and scheduled to run within the lead_minutes window.
        now = int(time.time())
        cutoff = now + lead_minutes * 60

        try:
            jobs = proxmox_api.cluster.backup.get()

            for job in jobs:

                if not job.get("enabled", 0):
                    continue

                next_run = job.get("next-run")

                if next_run is None:
                    continue

                try:
                    next_run_int = int(next_run)
                except (ValueError, TypeError):
                    logger.warning(
                        f"Backups: Unexpected next-run value {next_run} in backup job {job.get('id')}. Skipping.")
                    continue

                if next_run_int > cutoff:
                    continue

                vmid_str = job.get("vmid", "")
                affected_vmids: List[int]

                if not vmid_str:
                    # Empty vmid means the job covers all VMs in the cluster
                    affected_vmids = [_ALL_VMS_SENTINEL]
                else:
                    affected_vmids = []

                    for v in str(vmid_str).split(","):
                        v = v.strip()

                        if not v:
                            continue

                        try:
                            affected_vmids.append(int(v))
                        except ValueError:
                            logger.warning(f"Backups: Invalid VMID '{v}' in backup job {job.get('id')}. Skipping entry.")

                for vmid in affected_vmids:
                    if vmid not in backup_jobs:
                        logger.debug(
                            f"Backups: Scheduled backup job {job.get('id')} covers "
                            f"VMID {vmid}, next-run at {next_run_int} "
                            f"(within {lead_minutes} minute lead window).")
                        backup_jobs[vmid] = Backups.BackupJob(running=False, active=True, vmid=vmid, vm_name="", job_start_time=next_run_int)

        except Exception as exc:
            logger.warning(f"Backups: Failed to query cluster backup jobs: {exc}")

        logger.debug(f"Finished: get_backup_jobs. Found {len(backup_jobs)} protected VMID(s).")
        return backup_jobs

    @staticmethod
    def is_backup_active_or_imminent(backup_jobs: Dict[int, "Backups.BackupJob"], vmid: int) -> bool:
        """
        Returns True if the given VMID should not be migrated due to an active
        or imminent backup.

        Args:
            backup_jobs (Dict): The map returned by get_backup_jobs().
            vmid (int): The VMID of the guest to check.

        Returns:
            bool: True if the guest is protected by backup guard.
        """
        return vmid in backup_jobs or _ALL_VMS_SENTINEL in backup_jobs

    @staticmethod
    def set_guest_ignore(proxmox_api: ProxmoxApi, proxlb_config: Config, nodes: Dict[str, ProxLbData.Node], guests: Dict[str, ProxLbData.Guest]) -> None:
        """
        Marks all guests with a running or imminent backup as ignored. ProxLB
        plans no migration for ignored guests and the solver pins them to their
        current node. Guests on a node in maintenance are left out.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            proxlb_config (Config): The ProxLB configuration object.
            nodes (Dict): The map of cluster nodes from ProxLbData.
            guests (Dict): The map of guests from ProxLbData.
        """
        logger.debug("Starting: set_guest_ignore.")
        backup_guard_cfg = proxlb_config.balancing.backup_guard

        if backup_guard_cfg.enable:
            backup_jobs = Backups.get_backup_jobs(proxmox_api, nodes, backup_guard_cfg.lead_minutes)
            ignored_guests = 0

            for guest_name, guest in guests.items():
                if guest.ignore or not Backups.is_backup_active_or_imminent(backup_jobs, guest.id):
                    continue

                # A guest on a node in maintenance has to be moved away and the solver
                # finds no plan when it must stay there. Such a guest is not ignored
                # and only held back by the check before its migration.
                if nodes[guest.node_current].maintenance:
                    continue

                logger.debug(f"Backups: Ignoring guest {guest_name} (VMID {guest.id}) due to an active or imminent backup job.")
                guest.ignore = True
                ignored_guests += 1

            if ignored_guests:
                logger.info(f"Backups: Backup guard ignores {ignored_guests} guest(s) with an active or imminent backup job.")

        logger.debug("Finished: set_guest_ignore.")
