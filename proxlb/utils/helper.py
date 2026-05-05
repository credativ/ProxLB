"""
The Helper class provides some basic helper functions to not mess up the code in other
classes.
"""

__author__ = "Florian Paul Azim Hoberg <gyptazy>"
__copyright__ = "Copyright (C) 2025 Florian Paul Azim Hoberg (@gyptazy)"
__license__ = "GPL-3.0"


import json
import uuid
import re
import socket
import sys
import time
from datetime import datetime, timedelta, time as datetime_time
from proxlb.utils import version
from proxlb.utils.config_parser import Config
from proxlb.utils.logger import SystemdLogger
from proxlb.utils.proxlb_data import ProxLbData
from typing import Dict, Tuple, Optional
from types import FrameType

BalancingResource = Config.Balancing.Resource

logger = SystemdLogger()


class Helper:
    """
    The Helper class provides some basic helper functions to not mess up the code in other
    classes.

    Methods:
        __init__():
            Initializes the general Helper class.

        get_uuid_string() -> str:
            Generates a random uuid and returns it as a string.

        log_node_metrics(proxlb_data: Dict[str, Any], init: bool = True) -> None:
            Logs the memory, CPU, and disk usage metrics of nodes in the provided proxlb_data dictionary.

        get_version(print_version: bool = False) -> None:
            Returns the current version of ProxLB and optionally prints it to stdout.

        get_daemon_mode(proxlb_config: Config) -> None:
            Checks if the daemon mode is active and handles the scheduling accordingly.
    """
    proxlb_reload = False
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    def __init__(self) -> None:
        """
        Initializes the general Helper clas.
        """

    @staticmethod
    def get_uuid_string() -> str:
        """
        Generates a random uuid and returns it as a string.

        Args:
            None

        Returns:
            Str: Returns a random uuid as a string.
        """
        logger.debug("Starting: get_uuid_string.")
        generated_uuid = uuid.uuid4()
        logger.debug("Finished: get_uuid_string.")
        return str(generated_uuid)

    @staticmethod
    def log_node_metrics(proxlb_data: ProxLbData, init: bool = True) -> None:
        """
        Logs the memory, CPU, and disk usage metrics of nodes in the provided proxlb_data dictionary.

        This method processes the usage metrics of nodes and logs them. It also updates the
        'statistics' field in the 'meta' section of the proxlb_data dictionary with the
        memory, CPU, and disk usage metrics before and after a certain operation.

            proxlb_data (Dict[str, Any]): A dictionary containing node metrics and metadata.
            init (bool): A flag indicating whether to initialize the 'before' statistics
                        (True) or update the 'after' statistics (False). Default is True.
        """
        logger.debug("Starting: log_node_metrics.")
        nodes_usage_memory = " | ".join([f"{key}: {value.memory.used_percent:.2f}%" for key, value in proxlb_data.nodes.items()])
        nodes_assigned_memory = " | ".join([f"{key}: {value.memory.assigned_percent:.2f}%" for key, value in proxlb_data.nodes.items()])
        nodes_usage_cpu = "  | ".join([f"{key}: {value.cpu.used_percent:.2f}%" for key, value in proxlb_data.nodes.items()])
        nodes_usage_disk = " | ".join([f"{key}: {value.disk.used_percent:.2f}%" for key, value in proxlb_data.nodes.items()])

        if init:
            proxlb_data.meta.statistics = {"before": {BalancingResource.Memory: nodes_usage_memory, BalancingResource.Cpu: nodes_usage_cpu, BalancingResource.Disk: nodes_usage_disk}, "after": {BalancingResource.Memory: "", BalancingResource.Cpu: "", BalancingResource.Disk: ""}}
        elif proxlb_data.meta.statistics:
            proxlb_data.meta.statistics["after"] = {BalancingResource.Memory: nodes_usage_memory, BalancingResource.Cpu: nodes_usage_cpu, BalancingResource.Disk: nodes_usage_disk}
        else:
            proxlb_data.meta.statistics = {"after": {BalancingResource.Memory: nodes_usage_memory, BalancingResource.Cpu: nodes_usage_cpu, BalancingResource.Disk: nodes_usage_disk}}

        logger.debug(f"Nodes usage memory: {nodes_usage_memory}")
        logger.debug(f"Nodes usage memory assigned: {nodes_assigned_memory}")
        logger.debug(f"Nodes usage cpu:    {nodes_usage_cpu}")
        logger.debug(f"Nodes usage disk:   {nodes_usage_disk}")
        logger.debug("Finished: log_node_metrics.")

    @staticmethod
    def get_version(print_version: bool = False) -> None:
        """
        Returns the current version of ProxLB and optionally prints it to stdout.

        Parameters:
            print_version (bool): If True, prints the version information to stdout and exits the program.

        Returns:
            None
        """
        if print_version:
            print(f"{version.__app_name__} version: {version.__version__}\n(C) 2025 by {version.__author__}\n{version.__url__}")
            sys.exit(0)

    @staticmethod
    def get_daemon_mode(proxlb_config: Config) -> None:
        """
        Checks if the daemon mode is active and handles the scheduling accordingly.

        Parameters:
            proxlb_config (Dict[str, Any]): A dictionary containing the ProxLB configuration.

        Returns:
            None
        """
        logger.debug("Starting: get_daemon_mode.")
        if proxlb_config.service.daemon:

            logger.info(f"Daemon mode active: Next run in: {proxlb_config.service.schedule}.")
            time.sleep(proxlb_config.service.schedule.seconds)

        else:
            logger.debug("Successfully executed ProxLB. Daemon mode not active - stopping.")
            print("Daemon mode not active - stopping.")
            sys.exit(0)

        logger.debug("Finished: get_daemon_mode.")

    @staticmethod
    def apply_maintenance_nodes_schedule(proxlb_config: Config, now: Optional[datetime] = None) -> None:
        """
        Adds nodes with active maintenance schedules to the runtime maintenance list.

        The configured static maintenance list is kept as the source of truth. This
        allows scheduled entries to be removed automatically after their window ends.
        """
        logger.debug("Starting: apply_maintenance_nodes_schedule.")
        now = now or datetime.now()
        schedule_config = proxlb_config.proxmox_cluster.maintenance_nodes_schedule
        maintenance_nodes = proxlb_config.proxmox_cluster.static_maintenance_nodes()

        if not schedule_config.schedules:
            proxlb_config.proxmox_cluster.maintenance_nodes = maintenance_nodes
            logger.debug("No maintenance_nodes_schedule configured.")
            logger.debug("Finished: apply_maintenance_nodes_schedule.")
            return

        scheduled_nodes: list[str] = []
        for node_name, schedules in schedule_config.schedules.items():
            for schedule in schedules:
                if Helper.is_maintenance_schedule_active(
                    schedule,
                    schedule_config.duration,
                    schedule_config.pre_migration,
                    now,
                ):
                    scheduled_nodes.append(node_name)
                    logger.info(f"Node: {node_name} has been set to maintenance mode (by ProxLB schedule).")
                    break

        proxlb_config.proxmox_cluster.maintenance_nodes = list(dict.fromkeys(maintenance_nodes + scheduled_nodes))
        logger.debug(f"Runtime maintenance nodes: {proxlb_config.proxmox_cluster.maintenance_nodes}")
        logger.debug("Finished: apply_maintenance_nodes_schedule.")

    @staticmethod
    def is_maintenance_schedule_active(schedule: str, duration_hours: int, pre_migration_minutes: int, now: datetime) -> bool:
        """
        Returns True when now is inside a weekly maintenance schedule window.
        """
        parsed_schedule = Helper.parse_maintenance_schedule(schedule)
        if not parsed_schedule:
            return False

        weekday, schedule_time = parsed_schedule
        weekday_delta = weekday - now.weekday()
        schedule_date = (now + timedelta(days=weekday_delta)).date()

        for week_offset in (-1, 0, 1):
            schedule_start = datetime.combine(
                schedule_date + timedelta(days=week_offset * 7),
                schedule_time,
            )
            window_start = schedule_start - timedelta(minutes=pre_migration_minutes)
            window_end = schedule_start + timedelta(hours=duration_hours)

            if window_start <= now < window_end:
                return True

        return False

    @staticmethod
    def parse_maintenance_schedule(schedule: str) -> Optional[tuple[int, datetime_time]]:
        """
        Parses weekly maintenance schedules in the format 'Monday, 8:00'.
        """
        try:
            weekday_name, time_config = [part.strip() for part in schedule.split(",", 1)]
            hour_config, minute_config = time_config.split(":", 1)
            weekday = Helper.weekdays[weekday_name.lower()]
            schedule_time = datetime_time(hour=int(hour_config), minute=int(minute_config))
        except (KeyError, TypeError, ValueError):
            logger.warning(f"Ignoring invalid maintenance schedule '{schedule}'. Expected format: Monday, 8:00")
            return None

        return weekday, schedule_time

    @staticmethod
    def get_service_delay(proxlb_config: Config) -> None:
        """
        Checks if a start up delay for the service is defined and waits to proceed until
        the time is up.

        Parameters:
            proxlb_config (Dict[str, Any]): A dictionary containing the ProxLB configuration.

        Returns:
            None
        """
        logger.debug("Starting: get_service_delay.")
        if proxlb_config.service.delay.enable:
            logger.info(f"Service delay active: First run in: {proxlb_config.service.delay}.")
            time.sleep(proxlb_config.service.delay.seconds)

        else:
            logger.debug("Service delay not active. Proceeding without delay.")

        logger.debug("Finished: get_service_delay.")

    @staticmethod
    def print_json(proxlb_data: ProxLbData, print_json: bool = False) -> None:
        """
        Prints the calculated balancing matrix as a JSON output to stdout.

        Parameters:
            proxlb_config (Dict[str, Any]): A dictionary containing the ProxLB configuration.

        Returns:
            None
        """
        logger.debug("Starting: print_json.")
        if print_json:
            # Create a filtered list by stripping the 'meta' key from the proxlb_config dictionary
            # to make sure that no credentials are leaked.
            data = proxlb_data.model_dump()
            del data["meta"]
            print(json.dumps(data, indent=4))

        logger.debug("Finished: print_json.")

    @staticmethod
    def handler_sighup(signum: int, frame: Optional[FrameType]) -> None:
        """
        Signal handler for SIGHUP.

        This method is triggered when the process receives a SIGHUP signal.
        It sets the `proxlb_reload` class variable to True to indicate that
        configuration should be reloaded in the main loop.

        Args:
            signum (int): The signal number (expected to be signal.SIGHUP).
            frame (frame object): Current stack frame (unused but required by signal handler signature).
        """
        logger.debug("Starting: handle_sighup.")
        logger.debug("Got SIGHUP signal. Reloading...")
        Helper.proxlb_reload = True
        logger.debug("Finished: handle_sighup.")

    @staticmethod
    def handler_sigint(signum: int, frame: Optional[FrameType]) -> None:
        """
        Signal handler for SIGINT. (triggered by CTRL+C).

        Args:
            signum (int): The signal number (e.g., SIGINT).
            frame (FrameType): The current stack frame when the signal was received.

        Returns:
            None
        """
        exit_message = "ProxLB has been successfully terminated by user."
        logger.debug(exit_message)
        print(f"\n {exit_message}")
        sys.exit(0)

    @staticmethod
    def get_host_port_from_string(host_object: str) -> Tuple[str, int]:
        """
        Parses a string containing a host (IPv4, IPv6, or hostname) and an optional port, and returns a tuple of (host, port).

        Supported formats:
        - Hostname or IPv4 without port: "example.com" or "192.168.0.1"
        - Hostname or IPv4 with port: "example.com:8006" or "192.168.0.1:8006"
        - IPv6 in brackets with optional port: "[fc00::1]" or "[fc00::1]:8006"
        - IPv6 without brackets, port is assumed after last colon: "fc00::1:8006"

        If no port is specified, port 8006 is used as the default.

        Args:
            host_object (str): A string representing a host with or without a port.

        Returns:
            tuple: A tuple (host: str, port: int)
        """
        logger.debug("Starting: get_host_port_from_string.")

        # IPv6 (with or without port, written in brackets)
        match = re.match(r'^\[(.+)\](?::(\d+))?$', host_object)
        if match:
            host = match.group(1)
            port = int(match.group(2)) if match.group(2) else 8006
            return host, port

        # Count colons to identify IPv6 addresses without brackets
        colon_count = host_object.count(':')

        # IPv4 or hostname without port
        if colon_count == 0:
            return host_object, 8006

        # IPv4 or hostname with port
        elif colon_count == 1:
            parts = host_object.split(':')
            return parts[0], int(parts[1])

        # IPv6 (with or without port, assume last colon is port)
        else:
            parts = host_object.rsplit(':', 1)
            try:
                port = int(parts[1])
                return parts[0], port
            except ValueError:
                return host_object, 8006

    @staticmethod
    def validate_node_presence(node: str, nodes: Dict[str, ProxLbData.Node]) -> bool:
        """
        Validates whether a given node exists in the provided cluster nodes dictionary.

        Args:
            node (str): The name of the node to validate.
            nodes (Dict[str, Any]): A dictionary containing cluster information.
                                    Must include a "nodes" key mapping to a dict of available nodes.

        Returns:
            bool: True if the node exists in the cluster, False otherwise.
        """
        logger.debug("Starting: validate_node_presence.")

        if node in nodes.keys():
            logger.info(f"Node {node} found in cluster. Applying pinning.")
            logger.debug("Finished: validate_node_presence.")
            return True
        else:
            logger.warning(f"Node {node} not found in cluster. Not applying pinning!")
            logger.debug("Finished: validate_node_presence.")
            return False

    @staticmethod
    def tcp_connect_test(addr_family: int, host: str, port: int, timeout: int) -> tuple[bool, Optional[int]]:
        """
        Attempt a TCP connection to the specified host and port to test the reachability.

        Args:
            addr_family (int): Address family for the socket (e.g., socket.AF_INET for IPv4, socket.AF_INET6 for IPv6).
            host (str): The hostname or IP address to connect to.
            port (int): The port number to connect to.
            timeout (int): Connection timeout in seconds.

        Returns:
            tuple[bool, int | None]: A tuple containing:
                - bool: True if the connection was successful, False otherwise.
                - int | None: None if the connection was successful, otherwise the errno code indicating the reason for failure.
        """
        test_socket = socket.socket(addr_family, socket.SOCK_STREAM)
        test_socket.settimeout(timeout)

        try:
            rc = test_socket.connect_ex((host, port))
            return (rc == 0, rc if rc != 0 else None)
        finally:
            test_socket.close()
