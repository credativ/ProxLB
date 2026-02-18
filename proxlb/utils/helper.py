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
import utils.version
from utils.logger import SystemdLogger
from typing import Dict, Any
from types import FrameType

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

        get_daemon_mode(proxlb_config: Dict[str, Any]) -> None:
            Checks if the daemon mode is active and handles the scheduling accordingly.
    """
    proxlb_reload = False

    def __init__(self):
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
    def log_node_metrics(proxlb_data: Dict[str, Any], init: bool = True) -> None:
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
        nodes_usage_memory = " | ".join([f"{key}: {value['memory_used_percent']:.2f}%" for key, value in proxlb_data["nodes"].items()])
        nodes_assigned_memory = " | ".join([f"{key}: {value['memory_assigned_percent']:.2f}%" for key, value in proxlb_data["nodes"].items()])
        nodes_usage_cpu = "  | ".join([f"{key}: {value['cpu_used_percent']:.2f}%" for key, value in proxlb_data["nodes"].items()])
        nodes_usage_disk = " | ".join([f"{key}: {value['disk_used_percent']:.2f}%" for key, value in proxlb_data["nodes"].items()])

        if init:
            proxlb_data["meta"]["statistics"] = {"before": {"memory": nodes_usage_memory, "cpu": nodes_usage_cpu, "disk": nodes_usage_disk}, "after": {"memory": "", "cpu": "", "disk": ""}}
        else:
            proxlb_data["meta"]["statistics"]["after"] = {"memory": nodes_usage_memory, "cpu": nodes_usage_cpu, "disk": nodes_usage_disk}

        label = "Before" if init else "After"
        logger.info(f"[{label}] Node memory usage:    {nodes_usage_memory}")
        logger.info(f"[{label}] Node memory assigned: {nodes_assigned_memory}")
        logger.info(f"[{label}] Node CPU usage:       {nodes_usage_cpu}")
        logger.info(f"[{label}] Node disk usage:      {nodes_usage_disk}")
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
            print(f"{utils.version.__app_name__} version: {utils.version.__version__}\n(C) 2025 by {utils.version.__author__}\n{utils.version.__url__}")
            sys.exit(0)

    @staticmethod
    def get_daemon_mode(proxlb_config: Dict[str, Any]) -> None:
        """
        Checks if the daemon mode is active and handles the scheduling accordingly.

        Parameters:
            proxlb_config (Dict[str, Any]): A dictionary containing the ProxLB configuration.

        Returns:
            None
        """
        logger.debug("Starting: get_daemon_mode.")
        if proxlb_config.get("service", {}).get("daemon", True):

            # Validate schedule format which changed in v1.1.1
            if type(proxlb_config["service"].get("schedule", None)) != dict:
                logger.error("Invalid format for schedule. Please use 'hours' or 'minutes'.")
                sys.exit(1)

            # Convert hours to seconds
            if proxlb_config["service"]["schedule"].get("format", "hours") == "hours":
                sleep_seconds = proxlb_config.get("service", {}).get("schedule", {}).get("interval", 12) * 3600
            # Convert minutes to seconds
            elif proxlb_config["service"]["schedule"].get("format", "hours") == "minutes":
                sleep_seconds = proxlb_config.get("service", {}).get("schedule", {}).get("interval", 720) * 60
            else:
                logger.error("Invalid format for schedule. Please use 'hours' or 'minutes'.")
                sys.exit(1)

            logger.info(f"Daemon mode active: Next run in: {proxlb_config.get('service', {}).get('schedule', {}).get('interval', 12)} {proxlb_config['service']['schedule'].get('format', 'hours')}.")
            time.sleep(sleep_seconds)

        else:
            logger.debug("Successfully executed ProxLB. Daemon mode not active - stopping.")
            print("Daemon mode not active - stopping.")
            sys.exit(0)

        logger.debug("Finished: get_daemon_mode.")

    @staticmethod
    def get_service_delay(proxlb_config: Dict[str, Any]) -> None:
        """
        Checks if a start up delay for the service is defined and waits to proceed until
        the time is up.

        Parameters:
            proxlb_config (Dict[str, Any]): A dictionary containing the ProxLB configuration.

        Returns:
            None
        """
        logger.debug("Starting: get_service_delay.")
        if proxlb_config.get("service", {}).get("delay", {}).get("enable", False):

            # Convert hours to seconds
            if proxlb_config["service"]["delay"].get("format", "hours") == "hours":
                sleep_seconds = proxlb_config.get("service", {}).get("delay", {}).get("time", 1) * 3600
            # Convert minutes to seconds
            elif proxlb_config["service"]["delay"].get("format", "hours") == "minutes":
                sleep_seconds = proxlb_config.get("service", {}).get("delay", {}).get("time", 60) * 60
            else:
                logger.error("Invalid format for service delay. Please use 'hours' or 'minutes'.")
                sys.exit(1)

            logger.info(f"Service delay active: First run in: {proxlb_config.get('service', {}).get('delay', {}).get('time', 1)} {proxlb_config['service']['delay'].get('format', 'hours')}.")
            time.sleep(sleep_seconds)

        else:
            logger.debug("Service delay not active. Proceeding without delay.")

        logger.debug("Finished: get_service_delay.")

    @staticmethod
    def log_cluster_summary(proxlb_data: Dict[str, Any]) -> None:
        """
        Logs a one-time summary of the cluster state after data collection:
        node count, maintenance nodes, guest count, ignored guests, and
        which guests are pinned to specific nodes.

        Parameters:
            proxlb_data (Dict[str, Any]): The assembled ProxLB data dict.

        Returns:
            None
        """
        logger.debug("Starting: log_cluster_summary.")
        nodes = proxlb_data.get("nodes", {})
        guests = proxlb_data.get("guests", {})

        maintenance_nodes = sorted(n for n, d in nodes.items() if d.get("maintenance"))
        ignored_guests = sorted(g for g, d in guests.items() if d.get("ignore"))
        pinned_guests = {
            g: d["node_relationships"]
            for g, d in guests.items()
            if d.get("node_relationships")
        }

        node_str = f"{len(nodes)} node(s)"
        if maintenance_nodes:
            node_str += f", {len(maintenance_nodes)} in maintenance: {', '.join(maintenance_nodes)}"

        guest_str = f"{len(guests)} guest(s)"
        if ignored_guests:
            guest_str += f", {len(ignored_guests)} ignored: {', '.join(ignored_guests)}"

        logger.info(f"Cluster: {node_str}, {guest_str}.")

        if pinned_guests:
            pin_parts = []
            for g, node_list in sorted(pinned_guests.items()):
                pin_parts.append(f"{g}->{'/'.join(node_list)}")
            logger.info(f"Pinned guests ({len(pinned_guests)}): {', '.join(pin_parts)}.")
        else:
            logger.debug("No guests are pinned to specific nodes.")

        logger.debug("Finished: log_cluster_summary.")

    @staticmethod
    def print_explain(proxlb_data: Dict[str, Any]) -> None:
        """
        Prints a human-readable, semi-graphical explanation of the balancing
        decisions ProxLB would make (similar to PostgreSQL's EXPLAIN).

        Parameters:
            proxlb_data (Dict[str, Any]): The fully computed ProxLB data dict,
                including 'meta.balancing.explain_before' (node snapshot before
                relocation) and the post-calculation 'nodes' state.

        Returns:
            None
        """
        logger.debug("Starting: print_explain.")

        method = proxlb_data["meta"]["balancing"].get("method", "memory")
        mode = proxlb_data["meta"]["balancing"].get("mode", "used")
        balanciness = proxlb_data["meta"]["balancing"].get("balanciness", 10)
        bar_width = 30

        def make_bar(pct: float) -> str:
            filled = max(0, min(bar_width, int(round(pct / 100.0 * bar_width))))
            return "#" * filled + "." * (bar_width - filled)

        def get_metric(node_data: dict) -> tuple:
            """Return (percent, display_label) for the active balancing metric."""
            if mode == "psi":
                pct = node_data.get(f"{method}_pressure_full_spikes_percent", 0.0)
                return pct, f"{pct:.2f}% spk"
            pct = node_data.get(f"{method}_{mode}_percent", 0.0)
            if method == "memory":
                key = "memory_assigned" if mode == "assigned" else "memory_used"
                used_gb = node_data.get(key, 0) / (1024 ** 3)
                total_gb = node_data.get("memory_total", 1) / (1024 ** 3)
                return pct, f"{used_gb:.1f}/{total_gb:.1f} GB"
            if method == "cpu":
                key = "cpu_assigned" if mode == "assigned" else "cpu_used"
                used = node_data.get(key, 0)
                total = node_data.get("cpu_total", 1)
                return pct, f"{used:.1f}/{total:.0f} cores"
            # disk
            key = "disk_assigned" if mode == "assigned" else "disk_used"
            used_gb = node_data.get(key, 0) / (1024 ** 3)
            total_gb = node_data.get("disk_total", 1) / (1024 ** 3)
            return pct, f"{used_gb:.1f}/{total_gb:.1f} GB"

        bar_header = "0%" + " " * (bar_width - 7) + "100%"

        def print_node_table(title: str, node_data: dict) -> None:
            print(f"\n  {title}")
            print(f"  {'-' * 74}")
            print(f"  {'Node':<18} {'Load%':>6}  {'Resource':>16}  Bar ({bar_header})")
            print(f"  {'-' * 18} {'-' * 6}  {'-' * 16}  {'-' * bar_width}")
            for name in sorted(node_data.keys()):
                n = node_data[name]
                pct, res_label = get_metric(n)
                bar = make_bar(pct)
                maint = "  [MAINTENANCE]" if n.get("maintenance") else ""
                print(f"  {name:<18} {pct:>5.1f}%  {res_label:>16}  {bar}{maint}")

        # Header
        print()
        print("  +-----------------------------------------------------------------+")
        print("  |      ProxLB Explain - Cluster Balancing Decision Report        |")
        print("  +-----------------------------------------------------------------+")
        print()
        print(f"  Balancing metric : {method} ({mode})")
        print(f"  Balanciness      : {balanciness}%  (max allowed spread between nodes)")
        larger_first = proxlb_data.get("meta", {}).get("balancing", {}).get("balance_larger_guests_first", False)
        print(f"  Guest sort order : {'larger guests first' if larger_first else 'smaller guests first'}")
        print("  Mode             : explain (no migrations will be executed)")

        # Before state
        before = proxlb_data["meta"]["balancing"].get("explain_before", {})
        if before:
            print_node_table("CLUSTER STATE  (before)", before)
            pcts = [get_metric(n)[0] for n in before.values()]
            if pcts:
                spread = max(pcts) - min(pcts)
                high_node = max(before.items(), key=lambda x: get_metric(x[1])[0])
                low_node = min(before.items(), key=lambda x: get_metric(x[1])[0])
                high_pct = get_metric(high_node[1])[0]
                low_pct = get_metric(low_node[1])[0]
                verdict = "BALANCING REQUIRED" if spread > balanciness else "OK - no balancing needed"
                print(f"\n  Spread : {spread:.1f}%  "
                      f"(most loaded: {high_node[0]} {high_pct:.1f}%,  "
                      f"least loaded: {low_node[0]} {low_pct:.1f}%)")
                print(f"  Verdict: {verdict}  (threshold: {balanciness}%)")

        # Planned migrations
        migrations = sorted(
            [
                (name, guest)
                for name, guest in proxlb_data["guests"].items()
                if (
                    guest.get("node_current") != guest.get("node_target")
                    and not guest.get("ignore")
                    and guest.get("node_target") is not None
                )
            ],
            key=lambda x: x[1].get("memory_used", 0),
            reverse=True,
        )

        print("\n\n  PLANNED MIGRATIONS")
        print(f"  {'-' * 76}")

        if not migrations:
            print("  No migrations planned - cluster is already balanced.")
        else:
            col_g = 24
            type_labels = {"vm": "VM", "ct": "CT (LXC)"}
            print(f"  {'#':<3}  {'Guest':<{col_g}}  {'Type':<8}  {'RAM Used':>8}  Migration")
            print(f"  {'---'}  {'-' * col_g}  {'--------'}  {'--------'}  {'-' * 30}")
            for i, (name, guest) in enumerate(migrations, 1):
                mem_gb = guest.get("memory_used", 0) / (1024 ** 3)
                gtype = type_labels.get(guest.get("type", "vm"), guest.get("type", "vm").upper())
                src = guest["node_current"]
                dst = guest["node_target"]
                # Collect reason flags
                pins = guest.get("node_relationships", []) or []
                ha_rules_list = guest.get("ha_rules", []) or []
                notes = []
                if ha_rules_list:
                    rule_ids = [str(r.get("rule", "?")) for r in ha_rules_list]
                    notes.append(f"affinity-rule:{','.join(rule_ids)}")
                if pins:
                    notes.append(f"pinned-to:{','.join(pins)}")
                note_str = f"  [{', '.join(notes)}]" if notes else ""
                trunc_name = name[:col_g]
                print(f"  {i:<3}  {trunc_name:<{col_g}}  {gtype:<8}  {mem_gb:>6.2f} GB  "
                      f"{src} --> {dst}{note_str}")

        print(f"\n  Total: {len(migrations)} guest(s) planned for migration")

        # Projected state
        after = proxlb_data["nodes"]
        print_node_table("CLUSTER STATE  (projected, after planned migrations)", after)

        pcts_after = [get_metric(n)[0] for n in after.values()]
        if pcts_after:
            spread_after = max(pcts_after) - min(pcts_after)
            if spread_after <= balanciness:
                post_verdict = "Within threshold - cluster will be balanced"
            else:
                post_verdict = f"Still {spread_after:.1f}% spread - further runs may improve balance"
            print(f"\n  Projected spread : {spread_after:.1f}%  (threshold: {balanciness}%)")
            print(f"  Post-migration   : {post_verdict}")
        print()

        logger.debug("Finished: print_explain.")

    @staticmethod
    def print_json(proxlb_config: Dict[str, Any], print_json: bool = False) -> None:
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
            filtered_data = {k: v for k, v in proxlb_config.items() if k != "meta"}
            print(json.dumps(filtered_data, indent=4))

        logger.debug("Finished: print_json.")

    @staticmethod
    def handler_sighup(signum: int, frame: FrameType) -> None:
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
    def handler_sigint(signum: int, frame: FrameType) -> None:
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
    def get_host_port_from_string(host_object):
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
            host, port = host_object.split(':')
            return host, int(port)

        # IPv6 (with or without port, assume last colon is port)
        else:
            parts = host_object.rsplit(':', 1)
            try:
                port = int(parts[1])
                return parts[0], port
            except ValueError:
                return host_object, 8006

    @staticmethod
    def validate_node_presence(node: str, nodes: Dict[str, Any]) -> bool:
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

        if node in nodes["nodes"].keys():
            logger.debug(f"Node {node} found in cluster.")
            logger.debug("Finished: validate_node_presence.")
            return True
        else:
            logger.warning(f"Node {node} not found in cluster. Pinning will not be applied!")
            logger.debug("Finished: validate_node_presence.")
            return False

    @staticmethod
    def tcp_connect_test(addr_family: int, host: str, port: int, timeout: int) -> tuple[bool, int | None]:
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
