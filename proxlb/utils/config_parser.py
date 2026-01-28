"""
The ConfigParser class handles the parsing of configuration file
from a given YAML file from any location.
"""

__author__ = "Florian Paul Azim Hoberg <gyptazy>"
__copyright__ = "Copyright (C) 2025 Florian Paul Azim Hoberg (@gyptazy)"
__license__ = "GPL-3.0"


import os
import sys
try:
    import yaml
    PYYAML_PRESENT = True
except ImportError:
    PYYAML_PRESENT = False
from typing import Any, Dict, Literal, Optional, TypeAlias
from pydantic import BaseModel, Field, ValidationError
from pathlib import Path
from utils.logger import SystemdLogger


if not PYYAML_PRESENT:
    print("Error: The required library 'pyyaml' is not installed.")
    sys.exit(1)


logger = SystemdLogger()

ResourceType: TypeAlias = Literal["cpu", "disk", "memory"]

class Config(BaseModel):

    class ProxmoxAPI(BaseModel):
        hosts: list[str]
        password: str = Field(alias="pass")
        retries: int = 1
        ssl_verification: bool = True
        timeout: int = 1
        token_id: Optional[str] = None
        token_secret: Optional[str] = None
        username: str = Field(alias="user")
        wait_time: int = 1

    class ProxmoxCluster(BaseModel):
        ignore_nodes: list[str] = Field(default_factory=list)
        maintenance_nodes: list[str] = Field(default_factory=list)
        overprovisioning: bool = False

    class Balancing(BaseModel):
        class Psi(BaseModel):
            class Pressure(BaseModel):
                full: float = Field(alias="pressure_full")
                some: float = Field(alias="pressure_full")
                spikes: float = Field(alias="pressure_full")
            guests: dict[ResourceType, Pressure]
            nodes: dict[ResourceType, Pressure]

        class Pool(BaseModel):
            pin: Optional[list[str]] = None
            strict: bool = True
            type: Optional[Literal["affinity", "anti-affinity"]] = None

        balance_larger_guests_first: bool = False
        balance_types: list[Literal['ct', 'vm']] = Field(default_factory=list)
        balanciness: int = 10
        cpu_threshold: Optional[int] = None
        enable: bool = False
        enforce_affinity: bool = False
        enforce_pinning: bool = False
        live: bool = True
        max_job_validation: int = 1800
        memory_threshold: Optional[int] = None
        method: Literal["cpu", "memory"] = "memory"
        mode: Literal["assigned", "psi", "used"] = "used"
        node_resource_reserve: Optional[dict[str, dict[ResourceType, int]]] = None
        parallel: bool = False
        pools: Optional[dict[str, Pool]] = None
        psi: Optional[Psi] = None
        with_conntrack_state: bool = True
        with_local_disks: bool = True

        def threshold(self, method: Literal["cpu", "memory"]) -> Optional[int]:
            return self.cpu_threshold if method == "cpu" else self.memory_threshold

    class Service(BaseModel):
        class Delay(BaseModel):
            enable: bool = False
            format: Literal["hours", "minutes"] = "hours"
            time: int = 1
            @property
            def seconds(self) -> int:
                return self.time * 3600 if self.format == "hours" else self.time * 60
            def __str__(self) -> str:
                return f"{self.time} {self.format}"

        class Schedule(BaseModel):
            format: Literal["hours", "minutes"] = "hours"
            interval: int = 12
            @property
            def seconds(self) -> int:
                return self.interval * 3600 if self.format == "hours" else self.interval * 60
            def __str__(self) -> str:
                return f"{self.interval} {self.format}"

        daemon: bool = True
        delay: Delay = Field(default_factory=Delay)
        log_level: Literal["CRITICAL", "DEBUG", "ERROR", "INFO", "WARNING"] = "INFO"
        schedule: Schedule = Field(default_factory=Schedule)

    proxmox_api: ProxmoxAPI
    proxmox_cluster: ProxmoxCluster
    balancing: Balancing
    service: Service


class ConfigParser:
    """
    The ConfigParser class handles the parsing of a configuration file.

    Methods:
    __init__(config_path: str)

    test_config_path(config_path: Path) -> None
        Checks if the configuration file is present at the given config path.

    get_config() -> Dict[str, Any]
        Parses and returns the configuration data from the YAML file.
    """
    def __init__(self, config_path: Path):
        """
        Initializes the configuration file parser and validates the config file.
        """
        logger.debug("Starting: ConfigParser.")
        self.config_path = self.test_config_path(config_path)
        logger.debug("Finished: ConfigParser.")

    def test_config_path(self, config_path: Path) -> Path:
        """
        Checks if configuration file is present at given config path.
        """
        logger.debug("Starting: test_config_path.")

        if os.path.exists(config_path):
            logger.debug(f"The file {config_path} exists.")
        else:
            print(f"The config file {config_path} does not exist.")
            logger.critical(f"The config file {config_path} does not exist.")
            sys.exit(1)

        logger.debug("Finished: test_config_path.")
        return config_path

    def get_config(self) -> Config:
        """
        Parses and returns CLI arguments.
        """
        logger.debug("Starting: get_config.")
        logger.info(f"Using config path: {self.config_path}")

        try:
            with self.config_path.open(encoding="utf-8") as config_file:
                return Config(**yaml.load(config_file, Loader=yaml.FullLoader))
        except yaml.YAMLError as exception_error:
            msg = f"Error loading YAML file: {exception_error}"
            print(msg)
            logger.critical(msg)
            sys.exit(1)
        except (TypeError, ValidationError) as exception_error:
            msg = f"Error parsing {self.config_path}: {exception_error}"
            print(msg)
            logger.critical(msg)
            sys.exit(1)
