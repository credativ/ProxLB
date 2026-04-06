"""
Unit tests for Balancing.get_parallel_job_limit().

These tests verify that the static method correctly parses the balancing
configuration and clamps invalid values, in particular the edge case where
parallel mode is enabled but parallel_jobs is set to a value below 1 (which
would otherwise cause an infinite loop in the streaming queue).
"""

__author__ = "Peter Dreuw <archandha>"
__copyright__ = "Copyright (C) 2026 Peter Dreuw (@archandha) for credativ GmbH"
__license__ = "GPL-3.0"


from models.balancing import Balancing


def test_returns_1_when_parallel_key_is_absent() -> None:
    """Missing parallel key must default to sequential mode (limit=1)."""
    assert Balancing.get_parallel_job_limit({}) == 1


def test_returns_1_when_parallel_is_false() -> None:
    """Explicitly disabled parallel mode must return limit=1."""
    assert Balancing.get_parallel_job_limit({"parallel": False}) == 1


def test_returns_default_5_when_parallel_jobs_key_is_absent() -> None:
    """parallel=True without parallel_jobs key must return the built-in default of 5."""
    assert Balancing.get_parallel_job_limit({"parallel": True}) == 5


def test_returns_configured_value_when_parallel_jobs_is_valid() -> None:
    """A valid parallel_jobs value must be returned unchanged."""
    assert Balancing.get_parallel_job_limit({"parallel": True, "parallel_jobs": 3}) == 3


def test_returns_1_when_parallel_jobs_is_exactly_1() -> None:
    """parallel_jobs=1 is the minimum valid value and must be accepted as-is."""
    assert Balancing.get_parallel_job_limit({"parallel": True, "parallel_jobs": 1}) == 1


def test_clamps_to_1_when_parallel_jobs_is_zero() -> None:
    """parallel_jobs=0 is invalid; the method must clamp it to 1 to prevent an infinite loop."""
    assert Balancing.get_parallel_job_limit({"parallel": True, "parallel_jobs": 0}) == 1


def test_clamps_to_1_when_parallel_jobs_is_negative() -> None:
    """Negative parallel_jobs is invalid; the method must clamp it to 1."""
    assert Balancing.get_parallel_job_limit({"parallel": True, "parallel_jobs": -5}) == 1
