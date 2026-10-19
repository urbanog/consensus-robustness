"""Mutation testing: does the harness catch deliberately broken protocols?

For each mutant we ask Hypothesis to *find* a scenario that violates a safety
property, then let it shrink the scenario.

Robustness of the search. How many examples Hypothesis needs depends on its
internal random stream, which changes between Hypothesis versions. A single
derandomised search with a fixed budget is therefore luck-dependent. The search
here runs ``find`` with a fixed sequence of seeds (``random=Random(seed)``) and a
per-seed budget, stopping at the first seed that finds a violation; budgets and
strategies are chosen from measured success rates (``measure_search``) so that
failure of the whole search is very unlikely for any Hypothesis version. The
shrunk counterexamples themselves are stored as data in
``tests/test_mutant_regressions.py`` and replayed without Hypothesis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Any

from hypothesis import HealthCheck, Phase, find, settings
from hypothesis.errors import NoSuchExample

from .faults import scenarios
from .scenario import Result, Scenario, run


@dataclass(frozen=True)
class MutantSpec:
    name: str
    protocol: str
    n: int
    mutant: str
    description: str
    why: str
    strategy_kwargs: dict[str, Any] = field(default_factory=dict)
    budget: int = 300  # examples per seed
    seeds: int = 4  # number of seeds tried before giving up
    baseline_kwargs: dict[str, Any] | None = None  # strategy before targeting, for comparison


_BFT_EQ = {"byzantine": 1, "behaviours": ("equivocate",)}
_LEADER_CHURN = {
    "max_crashes": 0,
    "max_gst": 0,
    "max_partitions": 0,
    "max_slow_links": 0,
    "loss_rates": (0.0,),
    "min_leader_crashes": 3,
    "max_leader_crashes": 6,
    "max_leader_crash_delay": 60,
    "leader_down_range": (150, 400),
    "allow_permanent_leader_crash": False,
}

MUTANTS: tuple[MutantSpec, ...] = (
    MutantSpec(
        "bft-quorum-2f",
        "bft",
        4,
        "quorum_2f",
        "BFT, n = 4, f = 1, quorum 2f = 2 instead of 2f + 1 = 3",
        "Two quorums of size 2 among 4 nodes can be disjoint, so two halves of a partition can "
        "each gather a full quorum of precommits for different values. No Byzantine node is even "
        "needed: the earlier counterexample in tests/test_mutant_regressions.py uses only two partitions.",
        _BFT_EQ,
    ),
    MutantSpec(
        "bft-quorum-2f-n7",
        "bft",
        7,
        "quorum_2f",
        "BFT, n = 7, f = 2, quorum 2f = 4 instead of 2f + 1 = 5",
        "Two quorums of size 4 among 7 nodes meet in at least 1 node, which may be Byzantine. "
        "Two equivocating Byzantine nodes vote for value a towards one honest group and for b "
        "towards the other; 2 Byzantine + 2 honest = 4 votes on each side.",
        {"byzantine": 2, "min_byzantine": 2, "behaviours": ("equivocate",)},
        budget=500,
        baseline_kwargs={"byzantine": 2, "behaviours": ("equivocate",)},
    ),
    MutantSpec(
        "bft-n-eq-3f",
        "bft",
        3,
        "n_eq_3f",
        "BFT with n = 3f = 3 nodes, f = 1, quorum n - f = 2",
        "With n = 3f the largest quorum that tolerates f silent nodes is n - f = 2f; two such "
        "quorums meet in only n - 2f = f nodes, all of which may be Byzantine.",
        _BFT_EQ,
        budget=500,
    ),
    MutantSpec(
        "bft-no-lock",
        "bft",
        4,
        "no_lock",
        "BFT whose honest nodes ignore their lock when prevoting",
        "Quorum intersection alone only prevents two decisions in the same round. Across rounds, "
        "safety relies on locks: an honest node that precommitted v in round r must not prevote "
        "another value later unless it sees a newer polka (2f+1 prevotes) for it.",
        {**_BFT_EQ, "min_byzantine": 1, "allow_loss": False, "max_gst": 300, "max_slow_links": 2},
        budget=800,
        seeds=12,
        baseline_kwargs={**_BFT_EQ, "allow_loss": False},
    ),
    MutantSpec(
        "raft-no-log-check",
        "raft",
        3,
        "no_log_check",
        "Raft voters grant votes without the up-to-date-log check (§5.4.1)",
        "Any majority of voters contains a node holding every committed entry, but without the "
        "check that node does not refuse its vote, so a candidate missing a committed entry wins.",
    ),
    MutantSpec(
        "raft-stale-votes",
        "raft",
        3,
        "stale_votes",
        "Raft candidates count vote replies from earlier terms",
        "A vote is a promise for one term only; counting a vote cast in term t-1 towards term t lets "
        "two candidates of term t both reach a majority.",
        {"allow_leader_crashes": False},
        budget=1500,
    ),
    MutantSpec(
        "raft-small-quorum",
        "raft",
        4,
        "small_quorum",
        "Raft, n = 4, majority computed as n // 2 = 2 instead of n // 2 + 1 = 3",
        "Two sets of 2 out of 4 nodes may be disjoint, so two candidates win the same term.",
    ),
    MutantSpec(
        "raft-commit-old-term",
        "raft",
        3,
        "commit_old_term",
        "Raft leaders commit entries of earlier terms by counting replicas (Figure 8), no no-op entry",
        "An entry from an older term stored on a majority can still be overwritten by a leader "
        "elected with a log whose last term is newer (Raft paper, §5.4.2 and Figure 8). "
        "This needs a sequence of leader changes, so the search uses a *leader-churn* fault "
        "family: 3-6 leader-triggered crashes (the k-th elected leader crashes 0-60 time units "
        "after its election and restarts 150-400 later), no other faults.",
        _LEADER_CHURN,
        budget=1000,
        seeds=6,
        baseline_kwargs={"max_crashes": 0, "max_gst": 200},
    ),
)


def search_settings(max_examples: int, shrink: bool = True) -> settings:
    return settings(
        max_examples=max_examples,
        database=None,
        derandomize=False,  # the explicit Random(seed) passed to find() fixes the stream
        deadline=None,
        phases=(Phase.generate, Phase.shrink) if shrink else (Phase.generate,),
        suppress_health_check=list(HealthCheck),
    )


@dataclass
class Finding:
    spec: MutantSpec
    result: Result | None
    examples_until_failure: int | None  # within the successful seed
    predicate_calls: int  # all runs, all seeds, including shrinking
    first_failing: Scenario | None = None
    seed: int | None = None


def _search_one(
    spec: MutantSpec, kwargs: dict[str, Any], seed: int, budget: int, shrink: bool
) -> tuple[Scenario | None, int, int | None, Scenario | None]:
    strategy = scenarios(spec.protocol, spec.n, mutant=spec.mutant, **kwargs)
    calls = 0
    first_fail: list[tuple[int, Scenario]] = []

    def violates(s: Scenario) -> bool:
        nonlocal calls
        calls += 1
        bad = not run(s).safe
        if bad and not first_fail:
            first_fail.append((calls, s))
        return bad

    try:
        sc: Scenario = find(strategy, violates, settings=search_settings(budget, shrink), random=Random(seed))
    except NoSuchExample:
        return None, calls, None, None
    return sc, calls, first_fail[0][0], first_fail[0][1]


def find_counterexample(
    spec: MutantSpec,
    budget: int | None = None,
    strategy_kwargs: dict[str, Any] | None = None,
    seeds: int | None = None,
) -> Finding:
    """Try seeds 0, 1, ... in turn; return the *shrunk* failing run of the first seed that finds one."""
    kwargs = spec.strategy_kwargs if strategy_kwargs is None else strategy_kwargs
    total = 0
    for seed in range(spec.seeds if seeds is None else seeds):
        sc, calls, first_n, first_sc = _search_one(spec, kwargs, seed, budget or spec.budget, shrink=True)
        total += calls
        if sc is not None:
            return Finding(spec, run(sc, record_trace=True), first_n, total, first_sc, seed)
    return Finding(spec, None, None, total)


def measure_search(
    spec: MutantSpec, seeds: int, budget: int, strategy_kwargs: dict[str, Any] | None = None, workers: int = 1
) -> list[int | None]:
    """Examples until the first violation for each of ``seeds`` independent searches (None: not found)."""
    kwargs = spec.strategy_kwargs if strategy_kwargs is None else strategy_kwargs
    jobs = [(spec, kwargs, s, budget) for s in range(seeds)]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(workers) as ex:
            return list(ex.map(_measure_job, jobs))
    return [_measure_job(j) for j in jobs]


def _measure_job(job: tuple[MutantSpec, dict[str, Any], int, int]) -> int | None:
    spec, kwargs, seed, budget = job
    return _search_one(spec, kwargs, seed, budget, shrink=False)[2]


def fault_count(sc: Scenario) -> int:
    """Size of a fault schedule: number of fault ingredients, used to report shrinking."""
    net = sc.network
    return (
        len(sc.crashes)
        + len(sc.leader_crashes)
        + len(sc.byzantine)
        + len(net.partitions)
        + len(net.slow_links)
        + (net.loss_rate > 0)
        + (net.dup_rate > 0)
    )


def _fmt_measure(xs: list[int | None]) -> str:
    found = sorted(x for x in xs if x is not None)
    miss = len(xs) - len(found)
    med = found[len(found) // 2] if found else None
    return (
        f"{len(found)}/{len(xs)} seeds"
        + (f", median {med}, max {found[-1]}" if found else "")
        + (f", {miss} not found" if miss else "")
    )


def findings_markdown(
    findings: list[Finding],
    measurements: dict[str, tuple[int, list[int | None]]] | None = None,
    hypothesis_version: str = "",
) -> str:
    """Render ``docs/findings.md``."""
    out = [
        "# Mutation-testing findings",
        "",
        "Generated by `python -m consensus_robustness mutants --measure 10 --baseline 4000 --write docs/findings.md`"
        + (f" with Hypothesis {hypothesis_version}." if hypothesis_version else "."),
        "Each mutant is a deliberately broken variant of an honest protocol. Hypothesis searches the",
        "space of fault schedules (`consensus_robustness.faults.scenarios`) for a run that violates a",
        "safety property and then *shrinks* it: lists of faults shrink towards empty, numbers towards",
        "zero. The search tries seeds 0, 1, 2, ... (`find(..., random=Random(seed))`) with a fixed",
        "budget per seed and stops at the first seed that finds a violation.",
        "",
        "**Version dependence.** The number of examples until the first failure, and the exact shrunk",
        "schedule, depend on Hypothesis's internal random stream, which changes between Hypothesis",
        "versions. The numbers below are therefore illustrative. What is version-independent: the shrunk",
        "counterexamples are stored as data in `tests/test_mutant_regressions.py`, and replaying them with",
        "the deterministic simulator must violate the stated property (and the honest protocol must be",
        "safe on the same schedule).",
        "",
        "The traces show protocol-level events only (`--full` in the CLI shows every message).",
        "",
        "## Summary",
        "",
        "| mutant | property violated | search seed | examples until first failure | runs incl. shrinking "
        "| faults: first failure -> shrunk | trace events: first failure -> shrunk |",
        "|---|---|---|---|---|---|---|",
    ]
    for fd in findings:
        if fd.result is None:
            out.append(f"| `{fd.spec.name}` | not found | - | - | {fd.predicate_calls} | - | - |")
            continue
        v = fd.result.violations[0]
        assert fd.first_failing is not None
        first_events = run(fd.first_failing).stats["events"]
        out.append(
            f"| `{fd.spec.name}` | {v.prop} | {fd.seed} | {fd.examples_until_failure} | {fd.predicate_calls} "
            f"| {fault_count(fd.first_failing)} -> {fault_count(fd.result.scenario)} "
            f"| {first_events} -> {fd.result.stats['events']} |"
        )
    out += [
        "",
        "Columns: *faults* counts fault ingredients (crashes, leader crashes, Byzantine nodes,",
        "partitions, slow links, loss > 0, duplication > 0). Hypothesis minimises its internal choice",
        "sequence (shortlex order), not these metrics, so a shrunk example is usually but not always",
        "smaller in both.",
        "",
    ]
    if measurements:
        out += [
            "## How reliable is the search?",
            "",
            "Examples until the first violation in independent searches (seeds 0..k-1, no shrinking).",
            "The per-seed budgets and the number of seeds tried by the test-suite are chosen from these",
            "measurements so that the whole search fails only with negligible probability.",
            "",
            "| mutant | per-seed budget / seeds tried | targeted strategy | baseline strategy |",
            "|---|---|---|---|",
        ]
        for fd in findings:
            name = fd.spec.name
            if name not in measurements:
                continue
            budget, xs = measurements[name]
            base = measurements.get(name + ":baseline")
            base_txt = f"{_fmt_measure(base[1])} (budget {base[0]})" if base else "same as targeted"
            out.append(
                f"| `{name}` | {fd.spec.budget} / {fd.spec.seeds} | {_fmt_measure(xs)} (budget {budget}) | {base_txt} |"
            )
        out += [
            "",
            "History: the first version of this harness used one derandomised search per mutant. With",
            "Hypothesis 6.170.0 it happened to find the Figure 8 bug after 1172 examples, but measuring",
            "with independent seeds showed that the same strategy finds it in at most 1 of 10 seeds within",
            "4000 examples (0/10 in a first measurement, 1/10 in the table above), and that",
            "`raft-stale-votes` and `bft-no-lock` can need more than their",
            "budgets of 400 and 1500. With Hypothesis 6.170.2 the `raft-stale-votes` and",
            "`raft-commit-old-term` searches failed in CI. The fix:",
            "targeted strategies (the leader-churn family for Figure 8; at least one equivocator and a",
            "short pre-GST period for `bft-no-lock`; exactly two equivocators for n = 7), several seeds",
            "with measured budgets, and version-independent replay tests of the recorded counterexamples.",
            "",
        ]
    out += [
        "**Uniform random sweeps vs. Hypothesis.** In the 2000-seed random sweeps",
        "(`docs/sweep_results.md`) the generic random generator caught five of the seven mutant",
        "kinds but **not** `bft-no-lock` and **not** `raft-commit-old-term` (0 violations each).",
        "Both need a specific *sequence* of events (a lock taken in one round and abandoned in a",
        "later one; a chain of leader changes in which each new leader replicates an old-term entry",
        "only partway), which a generic fault distribution rarely produces. The coverage of deep",
        "protocol states depends on the fault vocabulary and its distribution, not only on the",
        "number of runs.",
        "",
    ]
    for fd in findings:
        out += [
            f"## `{fd.spec.name}`",
            "",
            f"**Mutant.** {fd.spec.description}.",
            "",
            f"**Why it is unsafe.** {fd.spec.why}",
            "",
        ]
        if fd.result is None:
            out += ["No counterexample found.", ""]
            continue
        out += ["**Shrunk counterexample.**", "", "```text", fd.result.counterexample(), "```", ""]
    return "\n".join(out)
