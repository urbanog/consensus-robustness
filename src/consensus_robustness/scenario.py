"""Scenarios: a complete, serialisable description of one experiment, and its runner.

A :class:`Scenario` fixes the protocol, the number of nodes, the seed, the
network model and the fault schedule. :func:`run` executes it and returns a
:class:`Result` with the safety violations found, liveness information, the
trace hash and (optionally) the full trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .checking.properties import BftChecker, Checker, RaftChecker, Violation
from .protocols.bft import BftNode, BftParams, fault_bound
from .protocols.byzantine import BYZ_CLASSES
from .protocols.raft import RaftNode, RaftParams
from .sim.core import Simulator, TraceEvent
from .sim.network import NetworkConfig

PROTOCOLS = ("raft", "bft")


@dataclass(frozen=True, slots=True)
class Crash:
    node: int
    at: int
    restart_at: int | None = None

    def __str__(self) -> str:
        tail = "never restarts" if self.restart_at is None else f"restarts at t={self.restart_at}"
        return f"n{self.node} crashes at t={self.at}, {tail}"


@dataclass(frozen=True, slots=True)
class LeaderCrash:
    """Event-triggered fault (Raft): the ``nth`` leader elected (1-based, counting
    every election win) crashes ``delay`` time units after winning, and restarts
    ``down_for`` time units later (``None``: never)."""

    nth: int
    delay: int
    down_for: int | None = None

    def __str__(self) -> str:
        tail = "never restarts" if self.down_for is None else f"restarts {self.down_for} later"
        return f"leader #{self.nth} crashes {self.delay} after its election, {tail}"


class LeaderCrashNemesis:
    """Simulator observer that executes :class:`LeaderCrash` faults."""

    def __init__(self, faults: tuple[LeaderCrash, ...]) -> None:
        self.by_nth: dict[int, list[LeaderCrash]] = {}
        for fc in faults:
            self.by_nth.setdefault(fc.nth, []).append(fc)
        self.elections = 0

    def on_event(self, sim: Simulator, node: int, kind: str, data: dict[str, Any]) -> None:
        if kind != "leader":
            return
        self.elections += 1
        for fc in self.by_nth.get(self.elections, ()):
            sim.note(None, f"nemesis: leader #{self.elections} is n{node}, crash scheduled at t={sim.now + fc.delay}")
            sim.schedule_crash(node, sim.now + fc.delay)
            if fc.down_for is not None:
                sim.schedule_restart(node, sim.now + fc.delay + max(1, fc.down_for))

    def after_step(self, sim: Simulator) -> None:
        pass


@dataclass(frozen=True, slots=True)
class Byzantine:
    node: int
    behaviour: str

    def __str__(self) -> str:
        return f"n{self.node} is Byzantine ({self.behaviour})"


@dataclass(frozen=True, slots=True)
class Scenario:
    protocol: str = "bft"
    n: int = 4
    seed: int = 0
    network: NetworkConfig = field(default_factory=NetworkConfig)
    crashes: tuple[Crash, ...] = ()
    leader_crashes: tuple[LeaderCrash, ...] = ()
    byzantine: tuple[Byzantine, ...] = ()
    split: tuple[int, ...] = ()  # honest nodes that an equivocator puts in "group A"
    mutant: str | None = None
    heights: int = 3  # BFT: number of consensus instances
    commands: tuple[str, ...] = ("c1", "c2", "c3")  # Raft: client commands
    horizon: int = 20_000  # simulated time at which a run is cut off
    liveness_bound: int = 2_000  # all decisions required by stabilisation_time() + liveness_bound

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise ValueError(f"unknown protocol {self.protocol!r}")
        if self.n < 1:
            raise ValueError("n must be positive")
        byz = {b.node for b in self.byzantine}
        if self.protocol == "raft" and byz:
            raise ValueError("Raft is crash-fault tolerant only; Byzantine nodes are not supported")
        if self.protocol != "raft" and self.leader_crashes:
            raise ValueError("leader-triggered crashes are implemented for Raft only")
        for nd in byz | {c.node for c in self.crashes}:
            if not 0 <= nd < self.n:
                raise ValueError(f"node id {nd} out of range")
        for b in self.byzantine:
            if b.behaviour not in BYZ_CLASSES:
                raise ValueError(f"unknown Byzantine behaviour {b.behaviour!r}")

    # --- fault accounting -------------------------------------------------------
    @property
    def f(self) -> int:
        """Number of faults the configuration is designed to tolerate."""
        if self.protocol == "raft":
            return (self.n - 1) // 2
        return fault_bound(self.n, self.mutant)

    @property
    def byzantine_ids(self) -> frozenset[int]:
        return frozenset(b.node for b in self.byzantine)

    def stabilisation_time(self) -> int:
        """max(GST, time of the last crash/restart): from here on the system is stable."""
        times = [c.at for c in self.crashes] + [c.restart_at for c in self.crashes if c.restart_at is not None]
        return max([self.network.gst, *times])

    def liveness_expected(self) -> bool:
        """Liveness is demanded only if network faults stop at GST and at most f nodes are faulty.

        A node counts as faulty if it is Byzantine or is down at the stabilisation time.
        Event-triggered crashes have no a-priori end, so they switch the liveness check off.
        """
        if not self.network.faults_stop_at_gst or self.leader_crashes:
            return False
        t = self.stabilisation_time()
        down = {c.node for c in self.crashes if c.at <= t and (c.restart_at is None or c.restart_at > t)}
        return len(self.byzantine_ids | down) <= self.f

    def deadline(self) -> int:
        return self.stabilisation_time() + self.liveness_bound

    def describe(self) -> list[str]:
        lines = [
            f"protocol={self.protocol} n={self.n} f={self.f} seed={self.seed}"
            + (f" mutant={self.mutant}" if self.mutant else ""),
            f"network: {self.network.describe()}",
        ]
        lines += [f"fault: {b}" for b in self.byzantine]
        if self.split and any(b.behaviour == "equivocate" for b in self.byzantine):
            lines.append(f"equivocation split: group A = {sorted(self.split)}, group B = the other honest nodes")
        lines += [f"fault: {c}" for c in self.crashes]
        lines += [f"fault: {c}" for c in self.leader_crashes]
        lines += [f"fault: {p}" for p in self.network.partitions]
        lines += [f"fault: {s}" for s in self.network.slow_links]
        return lines

    def with_seed(self, seed: int) -> Scenario:
        return replace(self, seed=seed)


@dataclass
class Result:
    scenario: Scenario
    violations: list[Violation]
    finished: bool
    finish_time: int | None
    end_time: int
    trace_hash: str
    stats: dict[str, int]
    trace: list[TraceEvent]
    honest: list[int]

    @property
    def safe(self) -> bool:
        return not self.violations

    @property
    def liveness_ok(self) -> bool:
        """True iff all live honest nodes finished by the deadline (when liveness is expected)."""
        if not self.scenario.liveness_expected():
            return True
        return self.finished and self.finish_time is not None and self.finish_time <= self.scenario.deadline()

    def counterexample(self, full: bool = False) -> str:
        """A readable counterexample: the fault schedule, the protocol-level events, the violation."""
        out = ["Scenario:"] + [f"  {x}" for x in self.scenario.describe()]
        out.append("Trace (protocol-level events):" if not full else "Trace (all events):")
        for ev in self.trace:
            if full or ev.kind in ("note", "crash", "restart"):
                out.append("  " + ev.format())
        if self.violations:
            out.append(f"Result: {self.violations[0]}")
        return "\n".join(out)


def build(sc: Scenario, record_trace: bool = False) -> tuple[Simulator, Checker, list[int]]:
    sim = Simulator(sc.seed, sc.network, record_trace=record_trace)
    byz = {b.node: b.behaviour for b in sc.byzantine}
    checker: Checker
    if sc.protocol == "raft":
        rp = RaftParams(commands=sc.commands, mutant=sc.mutant)
        for i in range(sc.n):
            sim.add_node(RaftNode(i, sc.n, rp))
        checker = RaftChecker(sc.commands)
    else:
        bp = BftParams(heights=sc.heights, mutant=sc.mutant)
        coalition = tuple(sorted(i for i, b in byz.items() if b == "equivocate"))
        for i in range(sc.n):
            if i in byz:
                sim.add_node(BYZ_CLASSES[byz[i]](i, sc.n, bp, sc.split, coalition))
            else:
                sim.add_node(BftNode(i, sc.n, bp))
        checker = BftChecker()
    sim.add_observer(checker)
    if sc.leader_crashes:
        sim.add_observer(LeaderCrashNemesis(sc.leader_crashes))
    for c in sc.crashes:
        sim.schedule_crash(c.node, c.at)
        if c.restart_at is not None:
            sim.schedule_restart(c.node, max(c.restart_at, c.at + 1))
    honest = [i for i in range(sc.n) if i not in byz]
    return sim, checker, honest


def run(sc: Scenario, record_trace: bool = False, tail: int = 500) -> Result:
    """Run a scenario.

    The run ends at ``sc.horizon`` or ``tail`` time units after all live honest
    nodes have finished (whichever is earlier); the tail lets the safety checkers
    observe behaviour after completion (e.g. later Raft elections).
    """
    sim, checker, honest = build(sc, record_trace)
    never_back = {c.node for c in sc.crashes if c.restart_at is None}
    pending_restart = {c.node: max(c.restart_at, c.at + 1) for c in sc.crashes if c.restart_at is not None}
    last_fault = max([c.at for c in sc.crashes] + list(pending_restart.values()) + [0])
    finish_time: list[int | None] = [None]

    def all_done() -> bool:
        if finish_time[0] is not None:
            return sim.now >= finish_time[0] + tail
        if sc.leader_crashes and not all(sim.up[i] for i in honest if i not in never_back):
            return False
        if sim.now < last_fault:
            return False
        live = [i for i in honest if i not in never_back]
        if live and all(sim.up[i] and sim.nodes[i].done() for i in live):  # type: ignore[attr-defined]
            finish_time[0] = sim.now
        return False

    sim.run(sc.horizon, stop_when=all_done)
    return Result(
        scenario=sc,
        violations=list(checker.violations),
        finished=finish_time[0] is not None,
        finish_time=finish_time[0],
        end_time=sim.now,
        trace_hash=sim.trace_hash,
        stats=dict(sim.stats),
        trace=sim.trace,
        honest=honest,
    )


def scenario_to_dict(sc: Scenario) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(sc)
