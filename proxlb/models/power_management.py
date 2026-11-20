"""
The PowerManagement class implements an experimental, opt-in power management
(similar to VMware DRS DPM): when the cluster load is low, an eligible node is
drained (by putting it into maintenance for ProxLB) and shut down; when the
load rises again, a node that ProxLB has shut down is woken up via the Proxmox
Wake-on-LAN API.
"""

__license__ = "GPL-3.0"


import json
import os
import socket
import time
from typing import Any, Literal, Optional
from pydantic import BaseModel
from proxlb.utils.config_parser import Config
from proxlb.utils.logger import SystemdLogger
from proxlb.utils.proxlb_data import ProxLbData
from proxlb.utils.proxmox_api import ProxmoxApi

logger = SystemdLogger()


class PowerState(BaseModel):
    """Persistent state of the power management between runs."""
    draining: dict[str, float] = {}
    powered_off: dict[str, float] = {}
    last_action: float = 0.0
    last_wake: float = 0.0


class PowerAction(BaseModel):
    action: Literal["drain", "shutdown", "wake", "cancel_drain"]
    node: str
    reason: str


class ClusterInfo(BaseModel):
    """Membership of the Proxmox cluster (all nodes, not only the online ones)."""
    nodes_total: int
    nodes_online: list[str]


class PowerManagement:
    """
    Experimental power management for Proxmox clusters.

    Safety rules:
      * Disabled by default and in dry-run mode by default (only logs what it would do).
      * Only nodes listed in `power_management.nodes` are ever shut down or woken up.
      * At least `min_online_nodes` and a quorum majority stay online.
      * Only one node is drained at a time; a `cooldown` applies between power actions.
      * A node is shut down only after all running guests have left it (it is
        drained via the regular ProxLB maintenance logic). If draining takes longer
        than `drain_timeout`, the drain is cancelled.
      * The node that runs ProxLB itself is never shut down.
      * Only nodes that ProxLB itself has shut down are woken up again.
    """

    @staticmethod
    def load_state(path: str) -> PowerState:
        try:
            with open(path, "r", encoding="utf-8") as state_file:
                return PowerState.model_validate(json.load(state_file))
        except FileNotFoundError:
            return PowerState()
        except (OSError, ValueError) as error:
            logger.warning(f"Power management: could not read state file {path}: {error}. Starting with an empty state.")
            return PowerState()

    @staticmethod
    def save_state(path: str, state: PowerState) -> None:
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as state_file:
                json.dump(state.model_dump(), state_file)
            os.replace(tmp_path, path)
        except OSError as error:
            logger.warning(f"Power management: could not write state file {path}: {error}.")

    @staticmethod
    def get_cluster_info(proxmox_api: ProxmoxApi) -> ClusterInfo:
        entries: list[dict[str, Any]] = proxmox_api.cluster.status.get()
        node_entries = [entry for entry in entries if entry.get("type") == "node"]
        return ClusterInfo(
            nodes_total=len(node_entries),
            nodes_online=[entry["name"] for entry in node_entries if entry.get("online")],
        )

    @staticmethod
    def cluster_load(nodes: dict[str, ProxLbData.Node], resource: Config.Balancing.Resource, exclude: Optional[str] = None) -> float:
        """
        Return the used percentage of a resource over all given nodes. With
        `exclude`, the capacity of that node is removed while its usage is kept
        (projection of the load after the node has been drained).
        """
        used = sum(node.metric(resource).used for node in nodes.values())
        total = sum(node.metric(resource).total for name, node in nodes.items() if name != exclude)
        if total <= 0:
            return 100.0
        return used / total * 100

    @staticmethod
    def plan(
        config: Config.PowerManagement,
        nodes: dict[str, ProxLbData.Node],
        guest_counts: dict[str, int],
        cluster: ClusterInfo,
        state: PowerState,
        now: float,
        local_node: Optional[str] = None,
    ) -> list[PowerAction]:
        """
        Decide the power actions for this run. This method has no side effects.

        Args:
            config: The power management configuration.
            nodes: The online (not ignored) nodes with their metrics.
            guest_counts: The number of running guests per node.
            cluster: The cluster membership.
            state: The persistent state of previous runs.
            now: The current time (epoch seconds).
            local_node: The node ProxLB runs on (never shut down).
        Returns:
            list[PowerAction]: The actions to perform.
        """
        actions: list[PowerAction] = []
        quorum = cluster.nodes_total // 2 + 1
        required_online = max(config.min_online_nodes, quorum)

        loads = {resource: PowerManagement.cluster_load(nodes, resource) for resource in config.resources}
        high = [f"{resource}={value:.1f}%" for resource, value in loads.items() if value >= config.high_threshold]

        # 1. High load: cancel drains and wake a node that ProxLB has shut down.
        if high:
            for node_name in state.draining:
                actions.append(PowerAction(action="cancel_drain", node=node_name, reason=f"cluster load is high ({', '.join(high)})"))
            offline_candidates = [
                node_name for node_name in state.powered_off
                if node_name in config.nodes and node_name not in cluster.nodes_online
            ]
            if offline_candidates and now - state.last_wake >= config.wake_wait:
                actions.append(PowerAction(action="wake", node=sorted(offline_candidates)[0], reason=f"cluster load is high ({', '.join(high)})"))
            return actions

        # 2. Nodes being drained: shut down when empty, cancel on timeout.
        for node_name, since in state.draining.items():
            if node_name not in cluster.nodes_online:
                continue
            if guest_counts.get(node_name, 0) == 0:
                if len(cluster.nodes_online) - 1 < required_online:
                    actions.append(PowerAction(action="cancel_drain", node=node_name, reason=f"shutting it down would leave less than {required_online} nodes online"))
                else:
                    actions.append(PowerAction(action="shutdown", node=node_name, reason="node is drained and cluster load is low"))
            elif now - since > config.drain_timeout:
                actions.append(PowerAction(action="cancel_drain", node=node_name, reason=f"still {guest_counts.get(node_name, 0)} running guest(s) after {config.drain_timeout}s"))
        if state.draining:
            return actions

        # 3. Low load: pick one node to drain.
        if now - state.last_action < config.cooldown:
            logger.debug("Power management: cooldown active, no new power action.")
            return actions
        if len(cluster.nodes_online) - 1 < required_online:
            logger.debug(f"Power management: {len(cluster.nodes_online)} node(s) online, at least {required_online} must stay online.")
            return actions

        candidates = []
        for node_name, node in nodes.items():
            if node_name not in config.nodes or node.maintenance or node_name == local_node:
                continue
            projected = {resource: PowerManagement.cluster_load(nodes, resource, exclude=node_name) for resource in config.resources}
            if all(value < config.low_threshold for value in projected.values()):
                candidates.append((node.memory.used, node_name, projected))

        if candidates:
            _, node_name, projected = min(candidates)
            details = ", ".join(f"{resource}={value:.1f}%" for resource, value in projected.items())
            actions.append(PowerAction(action="drain", node=node_name, reason=f"projected cluster load without this node is low ({details})"))
        return actions

    @staticmethod
    def run(proxmox_api: ProxmoxApi, proxlb_data: ProxLbData, proxlb_config: Config, dry_run: bool = False) -> list[PowerAction]:
        """
        Evaluate and perform the power management for this run. Nodes that are
        being drained are marked as maintenance in `proxlb_data`, so the regular
        balancing moves their guests away.
        """
        config = proxlb_config.power_management
        if not config.enable:
            return []

        dry_run = dry_run or config.dry_run
        prefix = "Power management (dry-run)" if dry_run else "Power management"
        if not config.nodes:
            logger.warning("Power management is enabled, but no eligible nodes are defined in power_management.nodes.")
            return []

        state = PowerManagement.load_state(config.state_file)
        cluster = PowerManagement.get_cluster_info(proxmox_api)
        guest_counts: dict[str, int] = {}
        for guest in proxlb_data.guests.values():
            guest_counts[guest.node_current] = guest_counts.get(guest.node_current, 0) + 1

        now = time.time()
        actions = PowerManagement.plan(config, proxlb_data.nodes, guest_counts, cluster, state, now, socket.gethostname())

        shut_down: list[str] = []
        for action in actions:
            logger.info(f"{prefix}: {action.action} node {action.node}: {action.reason}.")
            if dry_run:
                continue
            try:
                if action.action == "drain":
                    state.draining[action.node] = now
                    state.last_action = now
                elif action.action == "cancel_drain":
                    state.draining.pop(action.node, None)
                elif action.action == "shutdown":
                    proxmox_api.nodes(action.node).status.post(command="shutdown")
                    state.draining.pop(action.node, None)
                    state.powered_off[action.node] = now
                    state.last_action = now
                    shut_down.append(action.node)
                elif action.action == "wake":
                    # The API call is handled by the node we are connected to,
                    # which sends the magic packet to the configured MAC address.
                    proxmox_api.nodes(action.node).wakeonlan.post()
                    state.powered_off.pop(action.node, None)
                    state.last_wake = now
                    state.last_action = now
            except Exception as error:  # pylint: disable=broad-except
                logger.error(f"{prefix}: {action.action} of node {action.node} failed: {error}")

        # Forget nodes that came back online by other means (after a grace
        # period, as a node is still reported online while it shuts down).
        for node_name, since in list(state.powered_off.items()):
            if node_name in cluster.nodes_online and now - since > config.wake_wait:
                state.powered_off.pop(node_name)

        if not dry_run:
            PowerManagement.save_state(config.state_file, state)

        # A node that is shutting down is no longer a balancing target.
        for node_name in shut_down:
            proxlb_data.nodes.pop(node_name, None)

        # Let the regular maintenance logic evacuate nodes being drained.
        for node_name in state.draining:
            if node_name in proxlb_data.nodes:
                proxlb_data.nodes[node_name].maintenance = True
                logger.info(f"{prefix}: node {node_name} is being drained for standby (set to maintenance).")

        return actions
