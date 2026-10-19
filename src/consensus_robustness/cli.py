"""Command-line interface: ``python -m consensus_robustness <command> ...``.

Commands:

* ``run``        - run one scenario and print a summary (and the trace on request).
* ``sweep``      - random seed sweep of an honest or mutant protocol.
* ``mutants``    - Hypothesis search + shrinking for every mutant; optionally write docs/findings.md.
* ``experiment`` - liveness vs loss rate and number of silent validators; CSV + plot.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .protocols.bft import BFT_MUTANTS
from .protocols.byzantine import BYZ_BEHAVIOURS
from .protocols.raft import RAFT_MUTANTS
from .scenario import PROTOCOLS, Byzantine, Crash, LeaderCrash, Scenario, run
from .sim.network import DELAY_DISTS, NetworkConfig, Partition


def _parse_crash(s: str) -> Crash:
    """NODE@AT or NODE@AT:RESTART"""
    node, rest = s.split("@")
    at, _, restart = rest.partition(":")
    return Crash(int(node), int(at), int(restart) if restart else None)


def _parse_partition(s: str) -> Partition:
    """START-END:N1,N2,..."""
    span, side = s.split(":")
    start, end = span.split("-")
    return Partition(int(start), int(end), tuple(sorted(int(x) for x in side.split(","))))


def _parse_leader_crash(s: str) -> LeaderCrash:
    """NTH+DELAY or NTH+DELAY:DOWN_FOR"""
    nth, rest = s.split("+")
    delay, _, down = rest.partition(":")
    return LeaderCrash(int(nth), int(delay), int(down) if down else None)


def _cmd_run(a: argparse.Namespace) -> int:
    byz: list[Byzantine] = []
    if a.byzantine:
        ids = [int(x) for x in a.byzantine_ids.split(",")] if a.byzantine_ids else list(range(a.n - a.byzantine, a.n))
        if len(ids) != a.byzantine:
            print("--byzantine-ids must list exactly --byzantine node ids", file=sys.stderr)
            return 2
        byz = [Byzantine(i, a.behaviour) for i in ids]
    split = tuple(int(x) for x in a.split.split(",")) if a.split else ()
    net = NetworkConfig(
        gst=a.gst,
        delta=a.delta,
        pre_gst_max_delay=a.pre_gst_delay,
        delay_dist=a.delay_dist,
        loss_rate=a.loss,
        dup_rate=a.dup,
        partitions=tuple(_parse_partition(p) for p in a.partition),
        faults_stop_at_gst=not a.persistent_faults,
    )
    sc = Scenario(
        protocol=a.protocol,
        n=a.n,
        seed=a.seed,
        network=net,
        crashes=tuple(_parse_crash(c) for c in a.crash),
        leader_crashes=tuple(_parse_leader_crash(c) for c in a.leader_crash),
        byzantine=tuple(byz),
        split=split,
        mutant=a.mutant,
        heights=a.heights,
        horizon=a.horizon,
    )
    t0 = time.perf_counter()
    r = run(sc, record_trace=a.trace or a.full_trace)
    wall = time.perf_counter() - t0
    if a.json:
        print(
            json.dumps(
                {
                    "safe": r.safe,
                    "violations": [str(v) for v in r.violations],
                    "finished": r.finished,
                    "finish_time": r.finish_time,
                    "liveness_expected": sc.liveness_expected(),
                    "liveness_ok": r.liveness_ok,
                    "trace_hash": r.trace_hash,
                    "stats": r.stats,
                },
                indent=2,
            )
        )
    else:
        for line in sc.describe():
            print(line)
        if a.trace or a.full_trace:
            print(r.counterexample(full=a.full_trace))
        print(f"safety: {'OK' if r.safe else 'VIOLATED'}" + ("" if r.safe else f" - {r.violations[0]}"))
        status = f"finished at t={r.finish_time}" if r.finished else f"NOT finished by t={r.end_time}"
        expect = (
            f"guaranteed by t={sc.deadline()}: {'OK' if r.liveness_ok else 'VIOLATED'}"
            if sc.liveness_expected()
            else "not guaranteed for this schedule"
        )
        print(f"liveness: {status}; {expect}")
        print(
            f"messages: {r.stats['sent']} sent, {r.stats['delivered']} delivered, "
            f"{r.stats['dropped_loss']} lost, {r.stats['dropped_partition']} partitioned, "
            f"{r.stats['duplicated']} duplicated, {r.stats['forgeries_rejected']} forgeries rejected"
        )
        print(f"trace hash: {r.trace_hash}  ({r.stats['events']} events, {wall:.2f}s wall)")
    return 0 if r.safe and r.liveness_ok else 1


def _cmd_sweep(a: argparse.Namespace) -> int:
    from .sweep import sweep

    t0 = time.perf_counter()
    rep = sweep(a.protocol, a.n, range(a.start, a.start + a.seeds), mutant=a.mutant, workers=a.workers)
    print(rep.summary())
    print(f"  wall time: {time.perf_counter() - t0:.1f}s")
    if a.mutant is None:
        return 0 if rep.safety_violations == 0 and rep.liveness_failures == 0 else 1
    return 0


def _cmd_mutants(a: argparse.Namespace) -> int:
    from .mutation import MUTANTS, find_counterexample, findings_markdown

    chosen = [m for m in MUTANTS if not a.only or m.name in a.only]
    findings = []
    for spec in chosen:
        t0 = time.perf_counter()
        fd = find_counterexample(spec)
        dt = time.perf_counter() - t0
        findings.append(fd)
        if fd.result is None:
            print(f"{spec.name:<22} NOT FOUND after {fd.predicate_calls} runs ({dt:.1f}s)")
        else:
            print(
                f"{spec.name:<22} {fd.result.violations[0].prop:<20} first failure after {fd.examples_until_failure} "
                f"examples, {fd.predicate_calls} runs incl. shrinking ({dt:.1f}s)"
            )
            if a.show:
                print(fd.result.counterexample(full=a.full))
                print()
    generic = None
    if a.write and any(f.spec.mutant == "commit_old_term" for f in findings):
        spec = next(f.spec for f in findings if f.spec.mutant == "commit_old_term")
        generic = find_counterexample(spec, max_examples=1000, strategy_kwargs={"allow_leader_crashes": False})
    if a.write:
        Path(a.write).write_text(findings_markdown(findings, generic) + "\n")
        print(f"wrote {a.write}")
    return 0 if all(f.result is not None for f in findings) else 1


def _cmd_experiment(a: argparse.Namespace) -> int:
    from .experiment import liveness_grid, markdown_table, plot, write_csv

    t0 = time.perf_counter()
    cells = liveness_grid(n=a.n, seeds=a.seeds, horizon=a.horizon, workers=a.workers)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(cells, out / "liveness.csv")
    plot(cells, out / "liveness.png", n=a.n, horizon=a.horizon)
    print(markdown_table(cells))
    unsafe = sum(c.unsafe for c in cells)
    print(f"safety violations in all {sum(c.runs for c in cells)} runs: {unsafe}")
    print(f"wrote {out / 'liveness.csv'} and {out / 'liveness.png'} ({time.perf_counter() - t0:.0f}s)")
    return 0 if unsafe == 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="consensus_robustness", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one scenario")
    r.add_argument("--protocol", choices=PROTOCOLS, default="bft")
    r.add_argument("--n", type=int, default=4)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--byzantine", type=int, default=0, help="number of Byzantine nodes (BFT)")
    r.add_argument("--byzantine-ids", default="", help="comma-separated ids (default: the last ones)")
    r.add_argument("--behaviour", choices=BYZ_BEHAVIOURS, default="equivocate")
    r.add_argument("--split", default="", help="honest ids in the equivocator's group A")
    r.add_argument("--mutant", choices=RAFT_MUTANTS + BFT_MUTANTS, default=None)
    r.add_argument("--heights", type=int, default=3)
    r.add_argument("--gst", type=int, default=0)
    r.add_argument("--delta", type=int, default=10)
    r.add_argument("--pre-gst-delay", type=int, default=300)
    r.add_argument("--delay-dist", choices=DELAY_DISTS, default="uniform")
    r.add_argument("--loss", type=float, default=0.0)
    r.add_argument("--dup", type=float, default=0.0)
    r.add_argument("--persistent-faults", action="store_true", help="loss/dup/partitions continue after GST")
    r.add_argument("--partition", action="append", default=[], metavar="START-END:IDS")
    r.add_argument("--crash", action="append", default=[], metavar="NODE@AT[:RESTART]")
    r.add_argument("--leader-crash", action="append", default=[], metavar="NTH+DELAY[:DOWN]")
    r.add_argument("--horizon", type=int, default=20_000)
    r.add_argument("--trace", action="store_true", help="print protocol-level trace")
    r.add_argument("--full-trace", action="store_true", help="print every event")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=_cmd_run)

    s = sub.add_parser("sweep", help="random seed sweep")
    s.add_argument("--protocol", choices=PROTOCOLS, default="bft")
    s.add_argument("--n", type=int, default=4)
    s.add_argument("--seeds", type=int, default=1000)
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--mutant", choices=RAFT_MUTANTS + BFT_MUTANTS, default=None)
    s.add_argument("--workers", type=int, default=1)
    s.set_defaults(func=_cmd_sweep)

    m = sub.add_parser("mutants", help="find and shrink counterexamples for the mutants")
    m.add_argument("--only", nargs="*", default=[])
    m.add_argument("--show", action="store_true", help="print the shrunk counterexamples")
    m.add_argument("--full", action="store_true", help="with --show: every event")
    m.add_argument("--write", default="", help="write a findings markdown file")
    m.set_defaults(func=_cmd_mutants)

    e = sub.add_parser("experiment", help="liveness vs message loss and silent validators")
    e.add_argument("--n", type=int, default=7)
    e.add_argument("--seeds", type=int, default=100)
    e.add_argument("--horizon", type=int, default=5_000)
    e.add_argument("--workers", type=int, default=1)
    e.add_argument("--out", default="docs")
    e.set_defaults(func=_cmd_experiment)

    a = p.parse_args(argv)
    return int(a.func(a))
