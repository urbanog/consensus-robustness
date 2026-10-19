"""Large random seed sweeps (safety and liveness of the honest protocols)."""

from __future__ import annotations

import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from .faults import random_scenario
from .scenario import run


@dataclass
class SweepReport:
    protocol: str
    n: int
    mutant: str | None
    runs: int = 0
    safety_violations: int = 0
    liveness_expected: int = 0
    liveness_failures: int = 0
    finished: int = 0
    events: int = 0
    violation_kinds: Counter[str] = field(default_factory=Counter)
    first_bad_seeds: list[int] = field(default_factory=list)
    first_stuck_seeds: list[int] = field(default_factory=list)
    finish_after_gst: list[int] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{self.protocol} n={self.n}" + (f" mutant={self.mutant}" if self.mutant else "") + f": {self.runs} seeds",
            f"  safety violations: {self.safety_violations}"
            + (f" {dict(self.violation_kinds)} e.g. seeds {self.first_bad_seeds}" if self.safety_violations else ""),
            f"  liveness: {self.liveness_expected - self.liveness_failures}/{self.liveness_expected} "
            "runs with a liveness guarantee finished by the deadline"
            + (f"; stuck seeds {self.first_stuck_seeds}" if self.liveness_failures else ""),
            f"  finished (all runs): {self.finished}/{self.runs}; simulated events: {self.events}",
        ]
        if self.finish_after_gst:
            q = (
                statistics.quantiles(self.finish_after_gst, n=100, method="inclusive")
                if len(self.finish_after_gst) > 1
                else [0] * 99
            )
            med = statistics.median(self.finish_after_gst)
            lines.append(
                f"  time from stabilisation to completion (guaranteed runs): median {med:.0f},"
                f" p99 {q[98]:.0f}, max {max(self.finish_after_gst)}"
            )
        return "\n".join(lines)


def _one(args: tuple[str, int, int, str | None]) -> tuple[int, list[str], bool, bool, bool, int, int | None]:
    protocol, n, seed, mutant = args
    sc = random_scenario(protocol, n, seed, mutant=mutant)
    r = run(sc)
    after = None
    if sc.liveness_expected() and r.finish_time is not None:
        after = max(0, r.finish_time - sc.stabilisation_time())
    return (
        seed,
        [v.prop for v in r.violations],
        sc.liveness_expected(),
        r.liveness_ok,
        r.finished,
        r.stats["events"],
        after,
    )


def sweep(protocol: str, n: int, seeds: range, mutant: str | None = None, workers: int = 1) -> SweepReport:
    rep = SweepReport(protocol, n, mutant)
    jobs = [(protocol, n, s, mutant) for s in seeds]
    if workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            results = list(ex.map(_one, jobs, chunksize=32))
    else:
        results = [_one(j) for j in jobs]
    for seed, viols, expected, live_ok, finished, events, after in results:
        rep.runs += 1
        rep.events += events
        rep.finished += finished
        if viols:
            rep.safety_violations += 1
            rep.violation_kinds.update(viols)
            if len(rep.first_bad_seeds) < 5:
                rep.first_bad_seeds.append(seed)
        if expected:
            rep.liveness_expected += 1
            if not live_ok:
                rep.liveness_failures += 1
                if len(rep.first_stuck_seeds) < 5:
                    rep.first_stuck_seeds.append(seed)
            elif after is not None:
                rep.finish_after_gst.append(after)
    return rep
