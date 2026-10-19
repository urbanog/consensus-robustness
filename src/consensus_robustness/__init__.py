"""consensus_robustness: a deterministic simulator for testing consensus protocols under faults."""

from .scenario import Byzantine, Crash, Result, Scenario, run
from .sim.network import NetworkConfig, Partition, SlowLink

__all__ = ["Byzantine", "Crash", "NetworkConfig", "Partition", "Result", "Scenario", "SlowLink", "run"]
__version__ = "0.1.0"
