"""Mutation testing: does the harness catch deliberately broken protocols?

For each mutant we ask Hypothesis to *find* a scenario that violates a safety
property, then let it shrink the scenario. ``derandomize=True`` makes the search
itself deterministic, so the counterexamples recorded in ``docs/findings.md``
are reproducible bit for bit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    max_examples: int = 400


MUTANTS: tuple[MutantSpec, ...] = (
    MutantSpec(
        "bft-quorum-2f",
        "bft",
        4,
        "quorum_2f",
        "BFT, n = 4, f = 1, quorum 2f = 2 instead of 2f + 1 = 3",
        "Two quorums of size 2 among 4 nodes can be disjoint, so two halves of a partition can "
        "each gather a full quorum of precommits for different values. No Byzantine node is needed.",
        {"byzantine": 1, "behaviours": ("equivocate",)},
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
        {"byzantine": 2, "behaviours": ("equivocate",)},
        max_examples=1500,
    ),
    MutantSpec(
        "bft-n-eq-3f",
        "bft",
        3,
        "n_eq_3f",
        "BFT with n = 3f = 3 nodes, f = 1, quorum n - f = 2",
        "With n = 3f the largest quorum that tolerates f silent nodes is n - f = 2f; two such "
        "quorums meet in only n - 2f = f nodes, all of which may be Byzantine.",
        {"byzantine": 1, "behaviours": ("equivocate",)},
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
        {"byzantine": 1, "behaviours": ("equivocate",), "allow_loss": False},
        max_examples=1500,
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
        "This needs repeated leader changes, so the search uses leader-triggered crashes "
        "and a short pre-GST period.",
        {"max_crashes": 0, "max_gst": 200},
        max_examples=1500,
    ),
)


def search_settings(max_examples: int) -> settings:
    return settings(
        max_examples=max_examples,
        database=None,
        derandomize=True,
        deadline=None,
        phases=(Phase.generate, Phase.shrink),
        suppress_health_check=list(HealthCheck),
    )


@dataclass
class Finding:
    spec: MutantSpec
    result: Result | None
    examples_until_failure: int | None
    predicate_calls: int
    first_failing: Scenario | None = None


def find_counterexample(
    spec: MutantSpec, max_examples: int | None = None, strategy_kwargs: dict[str, Any] | None = None
) -> Finding:
    """Search for a safety violation of the mutant and return the *shrunk* failing run."""
    kwargs = spec.strategy_kwargs if strategy_kwargs is None else strategy_kwargs
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
        sc: Scenario = find(strategy, violates, settings=search_settings(max_examples or spec.max_examples))
    except NoSuchExample:
        return Finding(spec, None, None, calls)
    return Finding(spec, run(sc, record_trace=True), first_fail[0][0], calls, first_fail[0][1])


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


def findings_markdown(findings: list[Finding], generic_commit_old_term: Finding | None = None) -> str:
    """Render ``docs/findings.md``."""
    out = [
        "# Mutation-testing findings",
        "",
        "Generated by `python -m consensus_robustness mutants --write docs/findings.md`.",
        "Each mutant is a deliberately broken variant of an honest protocol. Hypothesis searches the",
        "space of fault schedules (`consensus_robustness.faults.scenarios`) for a run that violates a",
        "safety property and then *shrinks* it: lists of faults shrink towards empty, numbers towards",
        "zero. The search is derandomised, so these traces are reproducible.",
        "",
        "The traces show protocol-level events only (`--full` in the CLI shows every message).",
        "",
        "## Summary",
        "",
        "| mutant | property violated | examples until first failure | runs incl. shrinking "
        "| faults: first failure -> shrunk | trace events: first failure -> shrunk |",
        "|---|---|---|---|---|---|",
    ]
    for fd in findings:
        if fd.result is None:
            out.append(f"| `{fd.spec.name}` | not found | - | {fd.predicate_calls} | - | - |")
            continue
        v = fd.result.violations[0]
        assert fd.first_failing is not None
        first_events = run(fd.first_failing).stats["events"]
        out.append(
            f"| `{fd.spec.name}` | {v.prop} | {fd.examples_until_failure} | {fd.predicate_calls} "
            f"| {fault_count(fd.first_failing)} -> {fault_count(fd.result.scenario)} "
            f"| {first_events} -> {fd.result.stats['events']} |"
        )
    out += [
        "",
        "Columns: *faults* counts fault ingredients (crashes, leader crashes, Byzantine nodes,",
        "partitions, slow links, loss > 0, duplication > 0). Hypothesis minimises its internal choice",
        "sequence (shortlex order), not these metrics, so a shrunk example is usually but not always",
        "smaller in both (e.g. `raft-small-quorum` trades a 1500-unit pre-GST period for a 129-unit one",
        "with 30% loss).",
        "",
        "**Hypothesis vs. uniform random sweeps.** In the 2000-seed random sweeps",
        "(`docs/sweep_results.md`) the generic random generator caught five of the seven mutant",
        "kinds but **not** `bft-no-lock` and **not** `raft-commit-old-term` (0 violations each).",
        "Hypothesis found `bft-no-lock` after 218 examples; a plausible reason is that its generator",
        "over-samples boundary values (GST = 0, empty sets, short partitions), which favour round changes.",
        "",
    ]
    if generic_commit_old_term is not None:
        g = generic_commit_old_term
        status = "found it" if g.result is not None else f"did **not** find a violation in {g.predicate_calls} examples"
        out += [
            "**`raft-commit-old-term` (the Figure 8 bug).** Hypothesis with the generic Raft fault",
            f"vocabulary (timed crashes, partitions, loss, pre-GST periods up to 1500) {status}.",
            "The bug needs a specific *sequence of leader changes*, each new leader replicating an",
            "old-term entry only partway. The targeted search below adds leader-triggered crashes",
            "(`LeaderCrash`: the k-th elected leader crashes shortly after its election) and restricts",
            "GST to at most 200 so that elections succeed quickly. Lesson: coverage of deep protocol",
            "states depends on the fault vocabulary, not only on the number of random runs.",
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
