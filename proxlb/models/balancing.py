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
from utils.logger import SystemdLogger
from pydantic import BaseModel
from enum import Enum
from typing import Dict, Any

logger = SystemdLogger()


class Balancing:
    """
    The balancing class is responsible for processing workloads on Proxmox clusters.
    The previously generated data (hold in proxlb_data) will processed and guests and
    other supported types will be moved across Proxmox clusters based on the defined
    values by an operator.

    Methods:
    __init__(self, proxmox_api: any, proxlb_data: Dict[str, Any]):
        Initializes the Balancing class with the provided ProxLB data and initiates the rebalancing
        process for guests.

    exec_rebalancing_vm(self, proxmox_api: any, proxlb_data: Dict[str, Any], guest_name: str) -> None:
        Executes the rebalancing of a virtual machine (VM) to a new node within the cluster. Logs the migration
        process and handles exceptions.

    exec_rebalancing_ct(self, proxmox_api: any, proxlb_data: Dict[str, Any], guest_name: str) -> None:
        Executes the rebalancing of a container (CT) to a new node within the cluster. Logs the migration
        process and handles exceptions.

    get_rebalancing_job_status(self, proxmox_api: any, job: Dict[str, Any]) -> bool:
        Monitors the status of a rebalancing job on a Proxmox node until it completes or a timeout
        is reached. Returns True if the job completed successfully, False otherwise.
    """

    class BalancingStatus(Enum):
        """
        a helper class to passthe current status of an rebalancing operation to the main thread when
        running in parallel mode. This is required to update the queue.
        """
        RUNNING = "running"
        FINISHED = "finished"
        FAILED = "failed"

    class RebalancingJob(BaseModel):
        """
        Type definition for rebalancing job information.
        """
        name: str
        id: int
        current_node: str
        job_id: str
        retry_counter: int = 0

        def __getitem__(self, item: str) -> str | int:
            return getattr(self, item)

    @staticmethod
    def balance(proxmox_api: any, proxlb_data: Dict[str, Any]) -> bool:
        """
        Initializes the Balancing class with the provided ProxLB data.

        Args:
            proxmox_api (object): The Proxmox API client instance used to interact with the Proxmox cluster.
            proxlb_data (dict): A dictionary containing data related to the ProxLB load balancing configuration.
        """

        # Validate if balancing should be performed in parallel or sequentially.
        # If parallel balancing is enabled, set the number of parallel jobs.
        if not proxlb_data["meta"]["balancing"].get("parallel", False):
            parallel_job_limit = 1
            logger.debug("Balancing: Parallel balancing is disabled. Running sequentially.")
        else:
            parallel_job_limit = proxlb_data["meta"]["balancing"].get("parallel_jobs", 5)
            logger.debug(f"Balancing: Parallel balancing is enabled. Running with {parallel_job_limit} parallel jobs.")

        jobs_to_wait: list[Balancing.RebalancingJob] = []
        max_retries = proxlb_data["meta"]["balancing"].get("max_job_validation", 1800)
        item_iterator = iter(proxlb_data["guests"].items())
        migration_done = False
        error_occurred = False

        logger.debug("Starting: Balancing loop for guests.")
        while True:
            # get the next element to process from the guests dict.
            element = next(item_iterator, None)
            if not element:
                logger.debug("Finished: no more guests to process.")
                migration_done = True
            else:
                guest_name, guest_meta = element
                job_id = Balancing._exec_rebalancing(proxmox_api, proxlb_data, guest_name)

                if job_id is not None:
                    jobs_to_wait.append(Balancing.RebalancingJob(
                        name=guest_name,
                        id=guest_meta['id'],
                        current_node=guest_meta['node_current'],
                        job_id=job_id
                    ))

            # Wait for at least one job in the current chunk to complete
            while len(jobs_to_wait) >= parallel_job_limit or (migration_done and len(jobs_to_wait) > 0):
                # work on a copy of the list to avoid issues when removing items while iterating
                for job in list(jobs_to_wait):
                    if Balancing._handle_job_status(proxmox_api, job, jobs_to_wait, max_retries):
                        error_occurred = True

                if len(jobs_to_wait) >= parallel_job_limit or (migration_done and len(jobs_to_wait) > 0):
                    time.sleep(5)  # Sleep for a short period before checking the job statuses again

            if migration_done and len(jobs_to_wait) == 0:
                logger.debug("Finished: Balancing loop for guests. All guests processed and migrations processed.")
                break

            # continue with the next guest if we are still below the parallel job limit and there are still guests
            # to process

        if error_occurred:
            logger.warning("Balancing: Some migrations did not complete successfully. "
                           + "Please check the logs and Proxmox cluster manually.")
            logger.debug("Finished: get_rebalancing_job_status.")
            return False

        logger.info("Finished: Balancing loop for guests. All guests processed and migrations completed.")
        return True

    @staticmethod
    def _exec_rebalancing(proxmox_api: Any, proxlb_data: Dict[str, Any], guest_name: str) -> str | None:
        """
        Executes the rebalancing of a guest to a new node within the cluster based on the guest
        type. This function initiates the migration of a specified guest to a target node as
        part of the load balancing process. It logs the migration process and handles any exceptions
        that may occur during the migration.It returns the job_id the Proxmox VE API returned
        when starting the migration, which can be used to monitor the migration status or
        None if the migration could not be started.
        Args:
            proxmox_api (object): The Proxmox API client instance used to interact with the Proxmox cluster.
            proxlb_data (dict): A dictionary containing data related to the ProxLB load balancing configuration.
            guest_name (str): The name of the guest to be migrated.
        Returns:
            str | None: The job ID of the migration task if successful, None otherwise.
        """
        logger.debug("Starting: exec_rebalancing.")
        guest_meta = proxlb_data["guests"][guest_name]
        job_id = None

        logger.debug(f"Balancing: Processing guest {guest_name} for potential rebalancing.")

        # Check if the guest's target is not the same as the current node
        if guest_meta["node_current"] != guest_meta["node_target"]:
            # Check if the guest is not ignored and perform the balancing
            # operation based on the guest type
            if not guest_meta["ignore"]:
                # VM Balancing
                if guest_meta["type"] == "vm":
                    if 'vm' in proxlb_data["meta"]["balancing"].get("balance_types", []):
                        logger.debug(f"Balancing: Balancing for guest {guest_name} of type VM started.")
                        job_id = Balancing._exec_rebalancing_vm(proxmox_api, proxlb_data, guest_name)
                    else:
                        logger.debug(
                            f"Balancing: Balancing for guest {guest_name} will not be performed. "
                            f"Guest is of type VM which is not included in allowed balancing types.")
                # CT Balancing
                elif guest_meta["type"] == "ct":
                    if 'ct' in proxlb_data["meta"]["balancing"].get("balance_types", []):
                        logger.debug(f"Balancing: Balancing for guest {guest_name} of type CT started.")
                        job_id = Balancing._exec_rebalancing_ct(proxmox_api, proxlb_data, guest_name)
                    else:
                        logger.debug(
                            f"Balancing: Balancing for guest {guest_name} will not be performed. "
                            + "Guest is of type CT which is not included in allowed balancing types.")
                # Just in case we get a new type of guest in the future
                else:
                    logger.critical(f"Balancing: Got unexpected guest type: {guest_meta['type']}. "
                                    + f"Cannot proceed guest: {guest_meta['name']}.")
            else:
                logger.debug(f"Balancing: Guest {guest_name} is ignored and will not be rebalanced.")
        else:
            logger.debug(f"Balancing: Guest {guest_name} is already on the target node "
                         + f"{guest_meta['node_target']} and will not be rebalanced.")

        logger.debug("Finished: exec_rebalancing.")
        return job_id

    @staticmethod
    def _exec_rebalancing_vm(proxmox_api: any, proxlb_data: Dict[str, Any], guest_name: str) -> str | None:
        """
        Executes the rebalancing of a virtual machine (VM) to a new node within the cluster.
        This function initiates the migration of a specified VM to a target node as part of the
        load balancing process. It logs the migration process and handles any exceptions that
        may occur during the migration.
        Args:
            proxmox_api (object): The Proxmox API client instance used to interact with the Proxmox cluster.
            proxlb_data (dict): A dictionary containing data related to the ProxLB load balancing configuration.
            guest_name (str): The name of the guest VM to be migrated.
        Raises:
            proxmox_api.core.ResourceException: If an error occurs during the migration process.
        Returns:
            str | None: The job ID of the migration task if successful, None otherwise.
        """
        logger.debug("Starting: exec_rebalancing_vm.")
        guest_id = proxlb_data["guests"][guest_name]["id"]
        guest_node_current = proxlb_data["guests"][guest_name]["node_current"]
        guest_node_target = proxlb_data["guests"][guest_name]["node_target"]
        job_id = None

        online_migration = 1 if proxlb_data["meta"]["balancing"].get("live", True) else 0
        with_local_disks = 1 if proxlb_data["meta"]["balancing"].get("with_local_disks", True) else 0

        migration_options = {
            'target': guest_node_target,
            'online': online_migration,
            'with-local-disks': with_local_disks,
        }

        # Conntrack state aware migrations are not supported in older
        # PVE versions, so we should not add it by default.
        if proxlb_data["meta"]["balancing"].get("with_conntrack_state", True):
            migration_options['with-conntrack-state'] = 1

        try:
            logger.info(f"Balancing: Starting to migrate VM guest {guest_name} "
                        + f"from {guest_node_current} to {guest_node_target}.")
            job_id = proxmox_api.nodes(guest_node_current).qemu(guest_id).migrate().post(**migration_options)
        except proxmoxer.core.ResourceException as proxmox_api_error:
            logger.critical(f"Balancing: Failed to migrate guest {guest_name} of type VM due to some Proxmox errors. "
                            + "Please check if resource is locked or similar.")
            logger.debug(f"Balancing: Failed to migrate guest {guest_name} of type VM due to "
                         + f"some Proxmox errors: {proxmox_api_error}")

        logger.debug("Finished: exec_rebalancing_vm.")
        return job_id

    @staticmethod
    def _exec_rebalancing_ct(proxmox_api: any, proxlb_data: Dict[str, Any], guest_name: str) -> str | None:
        """
        Executes the rebalancing of a container (CT) to a new node within the cluster.
        This function initiates the migration of a specified CT to a target node as part of the
        load balancing process. It logs the migration process and handles any exceptions that
        may occur during the migration.
        Args:
            proxmox_api (object): The Proxmox API client instance used to interact with the Proxmox cluster.
            proxlb_data (dict): A dictionary containing data related to the ProxLB load balancing configuration.
            guest_name (str): The name of the guest CT to be migrated.
        Raises:
            proxmox_api.core.ResourceException: If an error occurs during the migration process.
        Returns:
            str | None: The job ID of the migration task if successful, None otherwise.
        """
        logger.debug("Starting: exec_rebalancing_ct.")
        guest_id = proxlb_data["guests"][guest_name]["id"]
        guest_node_current = proxlb_data["guests"][guest_name]["node_current"]
        guest_node_target = proxlb_data["guests"][guest_name]["node_target"]
        job_id = None

        try:
            logger.info(f"Balancing: Starting to migrate CT guest {guest_name} from {guest_node_current} "
                        + f"to {guest_node_target}.")
            job_id = proxmox_api.nodes(guest_node_current).lxc(guest_id).migrate().post(
                target=guest_node_target, restart=1
            )
        except proxmoxer.core.ResourceException as proxmox_api_error:
            logger.critical(f"Balancing: Failed to migrate guest {guest_name} of type CT due to some Proxmox errors. "
                            + "Please check if resource is locked or similar.")
            logger.debug(f"Balancing: Failed to migrate guest {guest_name} of type CT due to some Proxmox errors:"
                         + f" {proxmox_api_error}")

        logger.debug("Finished: exec_rebalancing_ct.")
        return job_id

    @staticmethod
    def _handle_job_status(proxmox_api: any, job: RebalancingJob, jobs_to_wait: list, max_retries: int) -> bool:
        """
        Checks the current status of a single in-flight migration job and updates jobs_to_wait.

        Args:
            proxmox_api (object): The Proxmox API client instance.
            job (RebalancingJob): The job whose status to check.
            jobs_to_wait (list): The list of currently in-flight jobs (mutated in place).
            max_retries (int): Maximum number of status checks before the job is considered timed out.

        Returns:
            bool: True if the job entered an error state (FAILED or timed out), False otherwise.
        """
        status = Balancing._get_rebalancing_job_status(proxmox_api, job)
        if status == Balancing.BalancingStatus.FINISHED:
            jobs_to_wait.remove(job)
            return False
        if status == Balancing.BalancingStatus.FAILED:
            logger.critical(f"Balancing: Job ID {job.job_id} (guest: {job.name}) "
                            + "for migration went into an error! Please check manually.")
            jobs_to_wait.remove(job)
            return True
        # RUNNING
        job.retry_counter += 1
        if job.retry_counter >= max_retries:
            logger.warning(f"Balancing: Job ID {job.job_id} (guest: {job.name}) for migration "
                           + f"is still running. Retry counter: {job.retry_counter} exceeded.")
            jobs_to_wait.remove(job)
            return True
        return False

    @staticmethod
    def _get_rebalancing_job_status(proxmox_api: any, job: RebalancingJob) -> BalancingStatus:
        """
        Monitors the status of a rebalancing job on a Proxmox node until it completes or a timeout is reached.

        Args:
            proxmox_api (object): The Proxmox API client instance.
            job (RebalancingJob): A RebalancingJob object containing information about the
                        rebalancing job, including the guest name, current node, job ID, and retry counter.

        Returns:
            BalancingStatus: The status of the rebalancing job.
        """
        logger.debug("Starting: get_rebalancing_job_status.")
        task = proxmox_api.nodes(job.current_node).tasks(job.job_id).status().get()
        job_id = job.job_id

        # Fetch actual migration job status if this got spawned by a HA job
        if task["type"] == "hamigrate":
            logger.debug(f"Balancing: Job ID {job.job_id} (guest: {job.name}) "
                         + "is a HA migration job. Fetching underlying migration job...")
            time.sleep(1)
            vm_id = job.id
            qm_migrate_jobs = proxmox_api.nodes(job.current_node).tasks.get(
                typefilter="qmigrate", vmid=vm_id, start=0, source="active", limit=1)

            if len(qm_migrate_jobs) > 0:
                task = qm_migrate_jobs[0]
                job_id = task["upid"]
                logger.debug(f'Overwriting job polling for: ID {job_id} (guest: {job.name}) by {job}')
        else:
            logger.debug(f"Balancing: Job ID {job_id} (guest: {job.name}) is a standard migration job."
                         + "Proceeding with status check.")

        # Watch job id until it finalizes
        # Note: Unsaved jobs are delivered in uppercase from Proxmox API
        # keep it defensive and provide a default value if anything changes in the future
        task_status = task.get("status", "").lower()
        if task_status == "running":
            logger.debug(f"Balancing: Job ID {job_id} (guest: {job.name}) for migration is still running...")
            return Balancing.BalancingStatus.RUNNING

        # Validate job output for errors when finished
        if task_status == "stopped":
            if task.get("exitstatus", "") == "OK":  # exitstatus is optional
                logger.debug(f"Balancing: Job ID {job_id} (guest: {job.name}) was successfully.")
                logger.debug("Finished: get_rebalancing_job_status.")
                return Balancing.BalancingStatus.FINISHED
            else:
                logger.critical(f"Balancing: Job ID {job_id} (guest: {job.name}) went into an error! "
                                + "Please check manually.")
                logger.debug("Finished: get_rebalancing_job_status.")
                return Balancing.BalancingStatus.FAILED

        raise AssertionError(
            f"Balancing: Unexpected status for Job ID {job_id} (guest: {job.name}): "
            + f"{task.get('status', '')}. Please check manually."
        )
