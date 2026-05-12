"""
The Balancing class is responsible for processing workloads on Proxmox clusters.
It processes the previously generated data (held in proxlb_data) and moves guests
and other supported types across Proxmox clusters based on the defined values by an operator.
"""


__author__ = "Florian Paul Azim Hoberg <gyptazy>"
__copyright__ = "Copyright (C) 2025 Florian Paul Azim Hoberg (@gyptazy)"
__license__ = "GPL-3.0"


import proxmoxer
import time
from proxlb.utils.logger import SystemdLogger
from proxlb.utils.proxmox_api import ProxmoxApi
from proxlb.utils.config_parser import Config
from proxlb.utils.proxlb_data import ProxLbData
from pydantic import BaseModel
from enum import Enum
from typing import Optional, assert_never

GuestType = Config.GuestType

logger = SystemdLogger()


class Balancing:
    """
    The balancing class is responsible for processing workloads on Proxmox clusters.
    The previously generated data (hold in proxlb_data) will processed and guests and
    other supported types will be moved across Proxmox clusters based on the defined
    values by an operator.

    Methods:
    balance(proxmox_api: ProxmoxApi, proxlb_data: ProxLbData) -> bool:
        Runs the streaming migration queue: starts migrations up to the parallel job limit and
        immediately fills each free slot as a job completes, rather than waiting for an entire
        batch to finish. Returns True if all migrations completed successfully, False otherwise.

    _exec_rebalancing(proxmox_api: ProxmoxApi, proxlb_data: ProxLbData, guest_name: str) -> Optional[str]:
        Dispatches a single guest to the appropriate migration method based on its type.
        Returns the Proxmox job ID on success, None otherwise.

    _exec_rebalancing_vm(proxmox_api: ProxmoxApi, proxlb_data: ProxLbData, guest_name: str) -> Optional[str]:
        Executes the rebalancing of a virtual machine (VM) to a new node within the cluster.

    _exec_rebalancing_ct(proxmox_api: ProxmoxApi, proxlb_data: ProxLbData, guest_name: str) -> Optional[str]:
        Executes the rebalancing of a container (CT) to a new node within the cluster.

    _handle_job_status(proxmox_api: ProxmoxApi, job: RebalancingJob, jobs_to_wait: list, max_retries: int) -> bool:
        Checks a single in-flight job and removes it from jobs_to_wait when done.
        Returns True if the job entered an error state.

    _get_rebalancing_job_status(proxmox_api: ProxmoxApi, job: RebalancingJob) -> BalancingStatus:
        Returns the current BalancingStatus of a migration job.

    get_parallel_job_limit(proxlb_data_meta_balancing: ProxLbData.Meta.Balancing) -> int:
        Returns the maximum number of parallel migration jobs from the balancing config.
    """

    class BalancingStatus(Enum):
        """
        Represents the current status of an in-flight rebalancing operation.
        Used to update the streaming job queue after each status poll.
        """
        RUNNING = "running"
        FINISHED = "finished"
        FAILED = "failed"

    class RebalancingJob(BaseModel):
        """
        Holds tracking information for a single in-flight migration job.
        """
        name: str
        id: int
        current_node: str
        job_id: str
        retry_counter: int = 0

    @staticmethod
    def balance(proxmox_api: ProxmoxApi, proxlb_data: ProxLbData) -> bool:
        """
        Runs the streaming migration queue.

        Keeps up to parallel_job_limit migrations in flight at once and immediately
        submits the next guest as soon as a slot becomes free, rather than waiting
        for an entire chunk to finish.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            proxlb_data (ProxLbData): ProxLB load balancing data.

        Returns:
            bool: True if all migrations completed successfully, False otherwise.
        """
        logger.debug("Starting: balance.")
        parallel_job_limit = Balancing.get_parallel_job_limit(proxlb_data.meta.balancing)

        jobs_to_wait: list[Balancing.RebalancingJob] = []
        max_retries = proxlb_data.meta.balancing.max_job_validation
        error_occurred = False

        logger.debug("Starting: Balancing loop for guests.")
        for guest_name, guest_meta in proxlb_data.guests.items():
            while len(jobs_to_wait) >= parallel_job_limit:
                if Balancing._check_jobs_and_update(proxmox_api, jobs_to_wait, max_retries):
                    error_occurred = True
                if len(jobs_to_wait) >= parallel_job_limit:
                    time.sleep(5)

            job_id = Balancing._exec_rebalancing(proxmox_api, proxlb_data, guest_name)
            if job_id is not None:
                jobs_to_wait.append(Balancing.RebalancingJob(
                    name=guest_name,
                    id=guest_meta.id,
                    current_node=guest_meta.node_current,
                    job_id=job_id,
                ))

        while jobs_to_wait:
            if Balancing._check_jobs_and_update(proxmox_api, jobs_to_wait, max_retries):
              error_occurred = True
            if jobs_to_wait:
              time.sleep(5)
        
        if error_occurred:
            logger.warning(
                "Balancing: Some migrations did not complete successfully. "
                "Please check the logs and Proxmox cluster manually.")
            logger.debug("Finished: balance.")
            return False

        logger.info("Finished: Balancing loop for guests. All guests processed and migrations completed.")
        return True

    @staticmethod
    def _exec_rebalancing(proxmox_api: ProxmoxApi, proxlb_data: ProxLbData, guest_name: str) -> Optional[str]:
        """
        Dispatches a single guest to the appropriate migration method based on its type.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            proxlb_data (ProxLbData): ProxLB load balancing data.
            guest_name (str): The name of the guest to be migrated.

        Returns:
            Optional[str]: The Proxmox job ID if a migration was started, None otherwise.
        """
        logger.debug("Starting: _exec_rebalancing.")
        guest_meta = proxlb_data.guests[guest_name]
        job_id = None

        logger.debug(f"Balancing: Processing guest {guest_name} for potential rebalancing.")

        if guest_meta.node_current != guest_meta.node_target:
            if not guest_meta.ignore:
                if guest_meta.type == GuestType.Vm:
                    if GuestType.Vm in proxlb_data.meta.balancing.balance_types:
                        logger.debug(f"Balancing: Balancing for guest {guest_name} of type VM started.")
                        job_id = Balancing._exec_rebalancing_vm(proxmox_api, proxlb_data, guest_name)
                    else:
                        logger.debug(
                            f"Balancing: Balancing for guest {guest_name} will not be performed. "
                            "Guest is of type VM which is not included in allowed balancing types.")

                elif guest_meta.type == GuestType.Ct:
                    if GuestType.Ct in proxlb_data.meta.balancing.balance_types:
                        logger.debug(f"Balancing: Balancing for guest {guest_name} of type CT started.")
                        job_id = Balancing._exec_rebalancing_ct(proxmox_api, proxlb_data, guest_name)
                    else:
                        logger.debug(
                            f"Balancing: Balancing for guest {guest_name} will not be performed. "
                            "Guest is of type CT which is not included in allowed balancing types.")

                else:
                    logger.critical(
                        f"Balancing: Got unexpected guest type: {guest_meta.type}. "
                        f"Cannot proceed guest: {guest_meta.name}.")
                    assert_never(guest_meta.type)
            else:
                logger.debug(f"Balancing: Guest {guest_name} is ignored and will not be rebalanced.")
        else:
            logger.debug(
                f"Balancing: Guest {guest_name} is already on the target node "
                f"{guest_meta.node_target} and will not be rebalanced.")

        logger.debug("Finished: _exec_rebalancing.")
        return job_id

    @staticmethod
    def _exec_rebalancing_vm(proxmox_api: ProxmoxApi, proxlb_data: ProxLbData, guest_name: str) -> Optional[str]:
        """
        Executes the rebalancing of a virtual machine (VM) to a new node within the cluster.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            proxlb_data (ProxLbData): ProxLB load balancing data.
            guest_name (str): The name of the guest VM to be migrated.

        Returns:
            Optional[str]: The Proxmox job ID if the migration was started, None otherwise.
        """
        logger.debug("Starting: _exec_rebalancing_vm.")
        guest_id = proxlb_data.guests[guest_name].id
        guest_node_current = proxlb_data.guests[guest_name].node_current
        guest_node_target = proxlb_data.guests[guest_name].node_target
        job_id = None

        online_migration = 1 if proxlb_data.meta.balancing.live else 0
        with_local_disks = 1 if proxlb_data.meta.balancing.with_local_disks else 0

        migration_options = {
            'target': guest_node_target,
            'online': online_migration,
            'with-local-disks': with_local_disks,
        }

        # Conntrack state aware migrations are not supported in older
        # PVE versions, so we should not add it by default.
        if proxlb_data.meta.balancing.with_conntrack_state:
            migration_options['with-conntrack-state'] = 1

        try:
            logger.info(
                f"Balancing: Starting to migrate VM guest {guest_name} "
                f"from {guest_node_current} to {guest_node_target}.")
            job_id = proxmox_api.nodes(guest_node_current).qemu(guest_id).migrate().post(**migration_options)
        except proxmoxer.core.ResourceException as proxmox_api_error:
            logger.critical(
                f"Balancing: Failed to migrate guest {guest_name} of type VM due to some Proxmox errors. "
                "Please check if resource is locked or similar.")
            logger.debug(
                f"Balancing: Failed to migrate guest {guest_name} of type VM due to "
                f"some Proxmox errors: {proxmox_api_error}")

        logger.debug("Finished: _exec_rebalancing_vm.")
        return job_id

    @staticmethod
    def _exec_rebalancing_ct(proxmox_api: ProxmoxApi, proxlb_data: ProxLbData, guest_name: str) -> Optional[str]:
        """
        Executes the rebalancing of a container (CT) to a new node within the cluster.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            proxlb_data (ProxLbData): ProxLB load balancing data.
            guest_name (str): The name of the guest CT to be migrated.

        Returns:
            Optional[str]: The Proxmox job ID if the migration was started, None otherwise.
        """
        logger.debug("Starting: _exec_rebalancing_ct.")
        guest_id = proxlb_data.guests[guest_name].id
        guest_node_current = proxlb_data.guests[guest_name].node_current
        guest_node_target = proxlb_data.guests[guest_name].node_target
        job_id = None

        try:
            logger.info(
                f"Balancing: Starting to migrate CT guest {guest_name} "
                f"from {guest_node_current} to {guest_node_target}.")
            job_id = proxmox_api.nodes(guest_node_current).lxc(guest_id).migrate().post(
                target=guest_node_target, restart=1)
        except proxmoxer.core.ResourceException as proxmox_api_error:
            logger.critical(
                f"Balancing: Failed to migrate guest {guest_name} of type CT due to some Proxmox errors. "
                "Please check if resource is locked or similar.")
            logger.debug(
                f"Balancing: Failed to migrate guest {guest_name} of type CT due to some Proxmox errors: "
                f"{proxmox_api_error}")

        logger.debug("Finished: _exec_rebalancing_ct.")
        return job_id

    @staticmethod
    def _check_jobs_and_update(proxmox_api: ProxmoxApi, jobs_to_wait: list['Balancing.RebalancingJob'], max_retries: int) -> bool:
        """
        Checks the status of all in-flight jobs and updates the jobs_to_wait list accordingly.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            jobs_to_wait (list): The list of currently in-flight jobs (mutated in place).
            max_retries (int): Maximum number of status checks before the job is timed out.

        Returns:
            bool: True if any job entered an error state (FAILED or timed out), False otherwise.
        """
        error_occurred = False
        for job in list(jobs_to_wait):
            if Balancing._handle_job_status(proxmox_api, job, jobs_to_wait, max_retries):
                error_occurred = True
        return error_occurred
    
    @staticmethod
    def _handle_job_status(
            proxmox_api: ProxmoxApi,
            job: 'Balancing.RebalancingJob',
            jobs_to_wait: list['Balancing.RebalancingJob'],
            max_retries: int,
    ) -> bool:
        """
        Checks the current status of a single in-flight migration job and updates jobs_to_wait.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            job (RebalancingJob): The job whose status to check.
            jobs_to_wait (list): The list of currently in-flight jobs (mutated in place).
            max_retries (int): Maximum number of status checks before the job is timed out.

        Returns:
            bool: True if the job entered an error state (FAILED or timed out), False otherwise.
        """
        status = Balancing._get_rebalancing_job_status(proxmox_api, job)
        if status == Balancing.BalancingStatus.FINISHED:
            jobs_to_wait.remove(job)
            return False
        if status == Balancing.BalancingStatus.FAILED:
            logger.critical(
                f"Balancing: Job ID {job.job_id} (guest: {job.name}) "
                "for migration went into an error! Please check manually.")
            jobs_to_wait.remove(job)
            return True
        # RUNNING
        job.retry_counter += 1
        if job.retry_counter >= max_retries:
            logger.warning(
                f"Balancing: Job ID {job.job_id} (guest: {job.name}) for migration "
                f"is still running. Retry counter: {job.retry_counter} exceeded.")
            jobs_to_wait.remove(job)
            return True
        return False

    @staticmethod
    def _get_rebalancing_job_status(
            proxmox_api: ProxmoxApi,
            job: 'Balancing.RebalancingJob',
    ) -> 'Balancing.BalancingStatus':
        """
        Returns the current BalancingStatus of a migration job by polling the Proxmox API.

        Args:
            proxmox_api (ProxmoxApi): The Proxmox API client instance.
            job (RebalancingJob): The job to poll.

        Returns:
            BalancingStatus: RUNNING, FINISHED, or FAILED.
        """
        logger.debug("Starting: _get_rebalancing_job_status.")
        task = proxmox_api.nodes(job.current_node).tasks(job.job_id).status().get()
        job_id = job.job_id

        # Fetch actual migration job status if this got spawned by a HA job
        if task["type"] == "hamigrate":
            logger.debug(
                f"Balancing: Job ID {job.job_id} (guest: {job.name}) "
                "is a HA migration job. Fetching underlying migration job...")
            time.sleep(1)
            vm_id = job.id
            qm_migrate_jobs = proxmox_api.nodes(job.current_node).tasks.get(
                typefilter="qmigrate", vmid=vm_id, start=0, source="active", limit=1)

            if len(qm_migrate_jobs) > 0:
                task = qm_migrate_jobs[0]
                job_id = task["upid"]
                logger.debug(f"Overwriting job polling for: ID {job_id} (guest: {job.name}) by {task}")
        else:
            logger.debug(
                f"Balancing: Job ID {job_id} (guest: {job.name}) is a standard migration job. "
                "Proceeding with status check.")

        # Note: unsaved jobs are delivered in uppercase from the Proxmox API
        task_status = task.get("status", "").lower()
        if task_status == "running":
            logger.debug(f"Balancing: Job ID {job_id} (guest: {job.name}) for migration is still running...")
            return Balancing.BalancingStatus.RUNNING

        if task_status == "stopped":
            if task.get("exitstatus", "") == "OK":
                logger.debug(f"Balancing: Job ID {job_id} (guest: {job.name}) was successfully.")
                logger.debug("Finished: _get_rebalancing_job_status.")
                return Balancing.BalancingStatus.FINISHED
            else:
                logger.critical(
                    f"Balancing: Job ID {job_id} (guest: {job.name}) went into an error! "
                    "Please check manually.")
                logger.debug("Finished: _get_rebalancing_job_status.")
                return Balancing.BalancingStatus.FAILED

        raise AssertionError(
            f"Balancing: Unexpected status for Job ID {job_id} (guest: {job.name}): "
            f"{task.get('status', '')}. Please check manually.")

    @staticmethod
    def get_parallel_job_limit(proxlb_data_meta_balancing: ProxLbData.Meta.Balancing) -> int:
        """
        Returns the maximum number of parallel migration jobs from the balancing config.

        Args:
            proxlb_data_meta_balancing (ProxLbData.Meta.Balancing): The balancing sub-config.

        Returns:
            int: The parallel job limit (always >= 1).
        """
        if not proxlb_data_meta_balancing.parallel:
            logger.debug("Balancing: Parallel balancing is disabled. Running sequentially.")
            return 1

        limit = proxlb_data_meta_balancing.parallel_jobs
        if limit < 1:
            logger.warning(
                "Balancing: Invalid parallel_jobs value. Parallel job limit must be at least 1. "
                "Defaulting to 1.")
            return 1

        logger.debug(f"Balancing: Parallel balancing is enabled. Running with {limit} parallel jobs.")
        return limit
