import pytest
from pydantic import ValidationError

from proxlb.utils.config_parser import Config


def test_solver_method_defaults_to_none() -> None:
    assert Config.Solver().method is None


@pytest.mark.parametrize("method", [m.value for m in Config.Solver.Method])
def test_solver_method_accepts_solver_methods(method: str) -> None:
    cfg = Config.Solver.model_validate({"method": method})
    assert str(cfg.method) == method


def test_solver_method_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Config.Solver.model_validate({"method": "bogus"})


def test_solver_method_independent_of_balancing_method() -> None:
    cfg = Config.model_validate({
        "proxmox_api": {"hosts": ["pve"], "user": "root@pam"},
        "balancing": {"method": "memory"},
        "solver": {"method": "global_smart"},
    })
    assert cfg.balancing.method == Config.Balancing.Resource.Memory
    assert cfg.solver.method == Config.Solver.Method.GlobalSmart


def test_balancing_method_still_rejects_solver_only_methods() -> None:
    with pytest.raises(ValidationError):
        Config.Balancing.model_validate({"method": "global_smart"})
