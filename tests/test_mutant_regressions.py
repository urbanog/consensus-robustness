"""Version-independent regression tests for the mutation-testing findings.

Each scenario below is a shrunk counterexample found by ``python -m consensus_robustness mutants``
with Hypothesis 6.170.0, stored as explicit data: ``COUNTEREXAMPLES`` holds the ones shown in the
current ``docs/findings.md``; ``EARLIER_COUNTEREXAMPLES`` holds those of the first version of the
findings (single derandomised search), kept as additional regression cases.
Replaying it uses only the deterministic simulator, so these tests do not depend on how a
particular Hypothesis version explores or shrinks: the broken variant must violate the recorded
property, and the honest protocol must be safe on exactly the same schedule.
"""

from dataclasses import replace

import pytest

from consensus_robustness import Byzantine, NetworkConfig, Partition, Scenario, SlowLink, run
from consensus_robustness.mutation import MUTANTS
from consensus_robustness.scenario import Crash, LeaderCrash

EARLIER_COUNTEREXAMPLES: dict[str, tuple[Scenario, str]] = {
    "bft-quorum-2f": (
        Scenario(
            protocol="bft",
            n=4,
            seed=0,
            network=NetworkConfig(
                gst=361,
                pre_gst_max_delay=300,
                partitions=(Partition(0, 121, (0,)), Partition(85, 361, (0, 1))),
            ),
            mutant="quorum_2f",
            horizon=2861,
        ),
        "agreement",
    ),
    "bft-quorum-2f-n7": (
        Scenario(
            protocol="bft",
            n=7,
            seed=0,
            network=NetworkConfig(gst=0, pre_gst_max_delay=300),
            byzantine=(Byzantine(0, "equivocate"), Byzantine(1, "equivocate")),
            split=(2, 3),
            mutant="quorum_2f",
            horizon=2500,
        ),
        "agreement",
    ),
    "bft-n-eq-3f": (
        Scenario(
            protocol="bft",
            n=3,
            seed=0,
            network=NetworkConfig(gst=121, pre_gst_max_delay=300, loss_rate=0.1, dup_rate=0.2),
            byzantine=(Byzantine(1, "equivocate"),),
            split=(0,),
            mutant="n_eq_3f",
            horizon=2621,
        ),
        "agreement",
    ),
    "bft-no-lock": (
        Scenario(
            protocol="bft",
            n=4,
            seed=0,
            network=NetworkConfig(
                gst=61,
                pre_gst_max_delay=100,
                delay_dist="exponential",
                dup_rate=0.2,
                partitions=(Partition(27, 61, (0,)),),
            ),
            byzantine=(Byzantine(1, "equivocate"),),
            mutant="no_lock",
            horizon=2561,
        ),
        "agreement",
    ),
    "raft-no-log-check": (
        Scenario(
            protocol="raft",
            n=3,
            seed=0,
            network=NetworkConfig(gst=369, pre_gst_max_delay=300, dup_rate=0.2, partitions=(Partition(0, 369, (1,)),)),
            mutant="no_log_check",
            horizon=2869,
        ),
        "leader-completeness",
    ),
    "raft-stale-votes": (
        Scenario(
            protocol="raft",
            n=3,
            seed=86,
            network=NetworkConfig(gst=798, pre_gst_max_delay=30, loss_rate=0.1, slow_links=(SlowLink(2, 1, 262),)),
            crashes=(Crash(0, 355, 537),),
            mutant="stale_votes",
            horizon=3298,
        ),
        "election-safety",
    ),
    "raft-small-quorum": (
        Scenario(
            protocol="raft",
            n=4,
            seed=0,
            network=NetworkConfig(gst=129, pre_gst_max_delay=300, loss_rate=0.3),
            mutant="small_quorum",
            horizon=2629,
        ),
        "election-safety",
    ),
    "raft-commit-old-term": (
        Scenario(
            protocol="raft",
            n=3,
            seed=0,
            network=NetworkConfig(gst=127, pre_gst_max_delay=300, loss_rate=0.1, slow_links=(SlowLink(0, 2, 1),)),
            leader_crashes=(
                LeaderCrash(1, 21, 1),
                LeaderCrash(2, 81, 1),
                LeaderCrash(3, 21, 209),
                LeaderCrash(4, 21, None),
            ),
            mutant="commit_old_term",
            horizon=2627,
        ),
        "leader-completeness",
    ),
}


COUNTEREXAMPLES: dict[str, tuple[Scenario, str]] = {
    "bft-quorum-2f": (
        Scenario(
            protocol="bft",
            n=4,
            seed=0,
            network=NetworkConfig(gst=0, pre_gst_max_delay=300),
            byzantine=(Byzantine(0, "equivocate"),),
            split=(1,),
            mutant="quorum_2f",
            horizon=2500,
        ),
        "agreement",
    ),
    "bft-quorum-2f-n7": (
        Scenario(
            protocol="bft",
            n=7,
            seed=0,
            network=NetworkConfig(gst=165, pre_gst_max_delay=300, dup_rate=0.2, slow_links=(SlowLink(0, 1, 86),)),
            byzantine=(Byzantine(1, "equivocate"), Byzantine(4, "equivocate")),
            split=(0,),
            mutant="quorum_2f",
            horizon=2665,
        ),
        "agreement",
    ),
    "bft-n-eq-3f": (
        Scenario(
            protocol="bft",
            n=3,
            seed=0,
            network=NetworkConfig(gst=0, pre_gst_max_delay=300),
            byzantine=(Byzantine(0, "equivocate"),),
            split=(1,),
            mutant="n_eq_3f",
            horizon=2500,
        ),
        "agreement",
    ),
    "bft-no-lock": (
        Scenario(
            protocol="bft",
            n=4,
            seed=1,
            network=NetworkConfig(gst=51, pre_gst_max_delay=100, delay_dist="exponential"),
            byzantine=(Byzantine(1, "equivocate"),),
            split=(0,),
            mutant="no_lock",
            horizon=2551,
        ),
        "agreement",
    ),
    "raft-no-log-check": (
        Scenario(
            protocol="raft",
            n=3,
            seed=0,
            network=NetworkConfig(gst=391, pre_gst_max_delay=300, partitions=(Partition(0, 188, (0,)),)),
            mutant="no_log_check",
            horizon=2891,
        ),
        "leader-completeness",
    ),
    "raft-stale-votes": (
        Scenario(
            protocol="raft",
            n=3,
            seed=8064,
            network=NetworkConfig(
                gst=882, pre_gst_max_delay=300, loss_rate=0.3, dup_rate=0.2, slow_links=(SlowLink(2, 0, 1),)
            ),
            mutant="stale_votes",
            horizon=3382,
        ),
        "election-safety",
    ),
    "raft-small-quorum": (
        Scenario(
            protocol="raft",
            n=4,
            seed=0,
            network=NetworkConfig(gst=129, pre_gst_max_delay=300, loss_rate=0.3),
            mutant="small_quorum",
            horizon=2629,
        ),
        "election-safety",
    ),
    "raft-commit-old-term": (
        Scenario(
            protocol="raft",
            n=3,
            seed=2,
            network=NetworkConfig(gst=0, pre_gst_max_delay=300),
            leader_crashes=(LeaderCrash(1, 0, 150), LeaderCrash(2, 21, 158), LeaderCrash(3, 16, 150)),
            mutant="commit_old_term",
            horizon=2500,
        ),
        "leader-completeness",
    ),
}

ALL_CASES = [(f"{name}", sc, prop) for name, (sc, prop) in sorted(COUNTEREXAMPLES.items())] + [
    (f"{name}-earlier", sc, prop) for name, (sc, prop) in sorted(EARLIER_COUNTEREXAMPLES.items())
]


def test_every_mutant_has_a_recorded_counterexample() -> None:
    assert set(COUNTEREXAMPLES) == {m.name for m in MUTANTS}
    assert set(EARLIER_COUNTEREXAMPLES) == {m.name for m in MUTANTS}


@pytest.mark.parametrize("name,sc,prop", ALL_CASES, ids=[c[0] for c in ALL_CASES])
def test_recorded_counterexample_breaks_the_mutant(name: str, sc: Scenario, prop: str) -> None:
    r = run(sc, record_trace=True)
    assert r.violations, f"{name}: recorded schedule no longer violates safety"
    assert r.violations[0].prop == prop
    assert "VIOLATION" in r.counterexample()
    assert run(sc).trace_hash == r.trace_hash  # replay is deterministic


@pytest.mark.parametrize("name,sc,prop", ALL_CASES, ids=[c[0] for c in ALL_CASES])
def test_same_schedule_is_safe_for_the_honest_protocol(name: str, sc: Scenario, prop: str) -> None:
    r = run(replace(sc, mutant=None))
    assert r.safe, f"{name}: honest protocol violated {r.violations[0]}"
