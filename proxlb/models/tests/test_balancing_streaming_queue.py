"""
Unit tests for the streaming migration queue in Balancing.balance().

These tests verify that balance() correctly implements a continuous streaming
queue: it keeps up to parallel_job_limit migrations in flight and immediately
submits the next guest when a slot becomes free, rather than waiting for the
entire current batch to finish.
"""

__author__ = "Peter Dreuw <archandha>"
__copyright__ = "Copyright (C) 2026 Peter Dreuw (@archandha) for credativ GmbH"
__license__ = "GPL-3.0"


from unittest.mock import MagicMock, patch

from models.balancing import Balancing

FINISHED = Balancing.BalancingStatus.FINISHED
RUNNING = Balancing.BalancingStatus.RUNNING
FAILED = Balancing.BalancingStatus.FAILED


def _guest(guest_id: int, node_current: str, node_target: str, ignore: bool = False) -> dict:
    return {
        "id": guest_id,
        "type": "vm",
        "node_current": node_current,
        "node_target": node_target,
        "ignore": ignore,
    }


def _proxlb_data(guests: dict, parallel: bool = True, parallel_jobs: int = 2) -> dict:
    return {
        "meta": {
            "balancing": {
                "parallel": parallel,
                "parallel_jobs": parallel_jobs,
                "balance_types": ["vm"],
                "live": True,
                "with_local_disks": True,
                "max_job_validation": 1800,
            }
        },
        "guests": guests,
    }


@patch("models.balancing.time.sleep")
@patch.object(Balancing, "_get_rebalancing_job_status")
@patch.object(Balancing, "_exec_rebalancing_vm")
def test_no_migration_when_guests_already_on_target(mock_exec_vm, mock_get_status, mock_sleep) -> None:
    """Guests already on the target node must not trigger any migration."""
    proxlb_data = _proxlb_data({
        "vm1": _guest(101, "node1", "node1"),
        "vm2": _guest(102, "node2", "node2"),
    })

    result = Balancing.balance(MagicMock(), proxlb_data)

    assert result is True
    mock_exec_vm.assert_not_called()
    mock_get_status.assert_not_called()


@patch("models.balancing.time.sleep")
@patch.object(Balancing, "_get_rebalancing_job_status")
@patch.object(Balancing, "_exec_rebalancing_vm")
def test_no_migration_when_guests_are_ignored(mock_exec_vm, mock_get_status, mock_sleep) -> None:
    """Ignored guests must not be migrated even when their target node differs."""
    proxlb_data = _proxlb_data({
        "vm1": _guest(101, "node1", "node2", ignore=True),
        "vm2": _guest(102, "node1", "node2", ignore=True),
    })

    result = Balancing.balance(MagicMock(), proxlb_data)

    assert result is True
    mock_exec_vm.assert_not_called()


@patch("models.balancing.time.sleep")
@patch.object(Balancing, "_get_rebalancing_job_status")
@patch.object(Balancing, "_exec_rebalancing_vm")
def test_sequential_mode_runs_one_migration_at_a_time(mock_exec_vm, mock_get_status, mock_sleep) -> None:
    """With parallel=False only one migration must be in flight at any point in time."""
    proxlb_data = _proxlb_data({
        "vm1": _guest(101, "node1", "node2"),
        "vm2": _guest(102, "node1", "node2"),
        "vm3": _guest(103, "node1", "node2"),
    }, parallel=False)

    in_flight = [0]
    max_concurrent = [0]

    def tracking_exec(api, data, name) -> str:
        in_flight[0] += 1
        max_concurrent[0] = max(max_concurrent[0], in_flight[0])
        return f"job-{name}"

    def tracking_status(api, job) -> Balancing.BalancingStatus:
        in_flight[0] -= 1
        return Balancing.BalancingStatus.FINISHED

    mock_exec_vm.side_effect = tracking_exec
    mock_get_status.side_effect = tracking_status

    result = Balancing.balance(MagicMock(), proxlb_data)

    assert result is True
    assert mock_exec_vm.call_count == 3
    assert max_concurrent[0] == 1, f"Expected at most 1 concurrent migration, got {max_concurrent[0]}"


@patch("models.balancing.time.sleep")
@patch.object(Balancing, "_get_rebalancing_job_status")
@patch.object(Balancing, "_exec_rebalancing_vm")
def test_parallel_streaming_submits_next_as_soon_as_slot_frees(mock_exec_vm, mock_get_status, mock_sleep) -> None:
    """
    Core streaming guarantee: with parallel_job_limit=2 and 3 guests, the 3rd
    migration must be submitted as soon as the 1st finishes — without waiting
    for the 2nd to finish too.

    Expected call_log order:
        submit vm1 → submit vm2 → finish vm1 → submit vm3 → finish vm2 → finish vm3
    """
    proxlb_data = _proxlb_data({
        "vm1": _guest(101, "node1", "node2"),
        "vm2": _guest(102, "node1", "node2"),
        "vm3": _guest(103, "node1", "node2"),
    }, parallel=True, parallel_jobs=2)

    call_log = []

    def tracking_exec(api, data, name) -> str:
        call_log.append(("submit", name))
        return f"job-{name}"

    # vm1 finishes on its first status check; vm2 needs one RUNNING round before finishing
    status_sequences = {
        "job-vm1": iter([Balancing.BalancingStatus.FINISHED]),
        "job-vm2": iter([Balancing.BalancingStatus.RUNNING, Balancing.BalancingStatus.FINISHED]),
        "job-vm3": iter([Balancing.BalancingStatus.FINISHED]),
    }

    def tracking_status(api, job) -> Balancing.BalancingStatus:
        result = next(status_sequences[job["job_id"]])
        if result == Balancing.BalancingStatus.FINISHED:
            call_log.append(("finish", job["name"]))
        return result

    mock_exec_vm.side_effect = tracking_exec
    mock_get_status.side_effect = tracking_status

    result = Balancing.balance(MagicMock(), proxlb_data)

    assert result is True
    assert mock_exec_vm.call_count == 3

    idx_submit_vm3 = call_log.index(("submit", "vm3"))
    idx_finish_vm1 = call_log.index(("finish", "vm1"))
    idx_finish_vm2 = call_log.index(("finish", "vm2"))

    assert idx_finish_vm1 < idx_submit_vm3, (
        "vm3 must be submitted after vm1 finishes (slot freed), "
        f"but call_log was: {call_log}"
    )
    assert idx_submit_vm3 < idx_finish_vm2, (
        "vm3 must be submitted before vm2 finishes (streaming, not batching), "
        f"but call_log was: {call_log}"
    )


@patch("models.balancing.time.sleep")
@patch.object(Balancing, "_get_rebalancing_job_status")
@patch.object(Balancing, "_exec_rebalancing_vm")
def test_parallel_concurrency_never_exceeds_limit(mock_exec_vm, mock_get_status, mock_sleep) -> None:
    """The number of in-flight migrations must never exceed parallel_job_limit."""
    limit = 3
    num_guests = 9
    proxlb_data = _proxlb_data(
        {f"vm{i}": _guest(100 + i, "node1", "node2") for i in range(num_guests)},
        parallel=True,
        parallel_jobs=limit,
    )

    in_flight = [0]
    max_concurrent = [0]

    def tracking_exec(api, data, name) -> str:
        in_flight[0] += 1
        max_concurrent[0] = max(max_concurrent[0], in_flight[0])
        return f"job-{name}"

    def tracking_status(api, job) -> Balancing.BalancingStatus:
        in_flight[0] -= 1
        return Balancing.BalancingStatus.FINISHED

    mock_exec_vm.side_effect = tracking_exec
    mock_get_status.side_effect = tracking_status

    result = Balancing.balance(MagicMock(), proxlb_data)

    assert result is True
    assert mock_exec_vm.call_count == num_guests
    assert max_concurrent[0] <= limit, (
        f"Concurrency limit violated: {max_concurrent[0]} migrations were in flight, limit is {limit}"
    )


@patch("models.balancing.time.sleep")
@patch.object(Balancing, "_get_rebalancing_job_status")
@patch.object(Balancing, "_exec_rebalancing_vm")
def test_failed_migration_returns_false(mock_exec_vm, mock_get_status, mock_sleep) -> None:
    """A FAILED migration status must cause balance() to return False."""
    proxlb_data = _proxlb_data({
        "vm1": _guest(101, "node1", "node2"),
    })

    mock_exec_vm.return_value = "job-vm1"
    mock_get_status.return_value = Balancing.BalancingStatus.FAILED

    result = Balancing.balance(MagicMock(), proxlb_data)

    assert result is False
