"""Generators of fault schedules.

Two generators produce the same kind of object, a :class:`Scenario`:

Fault types: network faults (loss, duplication, reordering, partitions, slow
links, pre-GST asynchrony), timed crashes/restarts, Byzantine nodes (BFT), and
for Raft *leader-triggered* crashes (the k-th elected leader crashes shortly
after its election), which reach the deep "leader churn" states that timed
crashes almost never hit.

* :func:`random_scenario` draws a schedule from a ``random.Random`` seeded with
  an integer; it is used for the large seed sweeps.
* :func:`scenarios` is a Hypothesis strategy; Hypothesis can *shrink* what it
  draws, which is what turns a failing random schedule into a small
  counterexample.

Both respect the model's assumptions for honest runs: at most ``f`` Byzantine
nodes (BFT); crashes and restarts are arbitrary, because safety must hold no
matter how many honest nodes crash. Liveness is only demanded when the scenario
is within the fault bound and all faults stop at GST
(:meth:`Scenario.liveness_expected`).
"""

from __future__ import annotations

import random

from hypothesis import strategies as st

from .protocols.bft import fault_bound
from .protocols.byzantine import BYZ_BEHAVIOURS
from .scenario import Byzantine, Crash, LeaderCrash, Scenario
from .sim.network import DELAY_DISTS, NetworkConfig, Partition, SlowLink


def _f(protocol: str, n: int, mutant: str | None) -> int:
    return (n - 1) // 2 if protocol == "raft" else fault_bound(n, mutant)


def _with_horizon(sc: Scenario) -> Scenario:
    """Run long enough to observe the liveness deadline, plus a margin."""
    from dataclasses import replace

    return replace(sc, horizon=sc.deadline() + 500)


def random_scenario(
    protocol: str,
    n: int,
    seed: int,
    *,
    mutant: str | None = None,
    max_byzantine: int | None = None,
    behaviours: tuple[str, ...] = BYZ_BEHAVIOURS,
) -> Scenario:
    rng = random.Random(f"schedule/{protocol}/{n}/{seed}")
    f = _f(protocol, n, mutant)
    gst = rng.choice([0, rng.randrange(50, 400), rng.randrange(400, 1500)])
    partitions = []
    for _ in range(rng.randrange(3)):
        start = rng.randrange(0, max(1, gst + 200))
        side = tuple(sorted(rng.sample(range(n), rng.randrange(1, n))))
        partitions.append(Partition(start, start + rng.randrange(20, 500), side))
    slow = []
    for _ in range(rng.randrange(4)):
        a, b = rng.sample(range(n), 2)
        slow.append(SlowLink(a, b, rng.randrange(20, 600)))
    net = NetworkConfig(
        gst=gst,
        delta=rng.choice([5, 10, 20]),
        pre_gst_max_delay=rng.choice([30, 100, 300]),
        delay_dist=rng.choice(DELAY_DISTS),
        loss_rate=rng.choice([0.0, 0.0, 0.05, 0.2, 0.4]),
        dup_rate=rng.choice([0.0, 0.1, 0.3]),
        partitions=tuple(partitions),
        slow_links=tuple(slow),
    )
    byz: list[Byzantine] = []
    if protocol == "bft":
        k = rng.randrange(0, (f if max_byzantine is None else max_byzantine) + 1)
        for node in rng.sample(range(n), k):
            byz.append(Byzantine(node, rng.choice(behaviours)))
    byz_ids = {b.node for b in byz}
    honest = [i for i in range(n) if i not in byz_ids]
    crashes = []
    for node in rng.sample(honest, rng.randrange(0, len(honest) + 1)):
        at = rng.randrange(0, gst + 600)
        restart = rng.choice([None, at + rng.randrange(1, 800)])
        crashes.append(Crash(node, at, restart))
    split = tuple(sorted(rng.sample(honest, rng.randrange(len(honest) + 1))))
    leader_crashes: list[LeaderCrash] = []
    if protocol == "raft" and rng.random() < 0.3:
        for nth in range(1, rng.randrange(2, 5)):
            leader_crashes.append(LeaderCrash(nth, rng.randrange(1, 80), rng.choice([None, rng.randrange(50, 500)])))
    return _with_horizon(
        Scenario(
            protocol=protocol,
            n=n,
            seed=seed,
            network=net,
            crashes=tuple(crashes),
            leader_crashes=tuple(leader_crashes),
            byzantine=tuple(byz),
            split=split,
            mutant=mutant,
            horizon=0,
        )
    )


@st.composite
def scenarios(
    draw: st.DrawFn,
    protocol: str,
    n: int,
    *,
    mutant: str | None = None,
    byzantine: int | None = None,
    behaviours: tuple[str, ...] = BYZ_BEHAVIOURS,
    max_crashes: int | None = None,
    allow_loss: bool = True,
    allow_leader_crashes: bool = True,
    max_gst: int = 1500,
    max_partitions: int = 2,
    max_slow_links: int = 6,
    min_byzantine: int = 0,
    min_leader_crashes: int = 0,
    max_leader_crashes: int = 4,
    max_leader_crash_delay: int = 100,
    leader_down_range: tuple[int, int] = (1, 600),
    allow_permanent_leader_crash: bool = True,
    loss_rates: tuple[float, ...] = (0.0, 0.1, 0.3),
) -> Scenario:
    """Hypothesis strategy for scenarios.

    ``byzantine`` is the *maximum* number of Byzantine nodes (default ``f``).
    Every list shrinks towards empty and every number towards zero, so a
    shrunk counterexample contains only the faults it actually needs.
    """
    f = _f(protocol, n, mutant)
    node = st.integers(0, n - 1)
    gst = draw(st.integers(0, max_gst))
    partitions = draw(
        st.lists(
            st.builds(
                lambda s, d, side: Partition(s, s + d, tuple(sorted(side))),
                st.integers(0, 1500),
                st.integers(1, 800),
                st.sets(node, min_size=1, max_size=n - 1),
            ),
            max_size=max_partitions,
        )
    )
    slow = draw(
        st.lists(
            st.tuples(node, node, st.integers(1, 1000)).filter(lambda t: t[0] != t[1]).map(lambda t: SlowLink(*t)),
            max_size=max_slow_links,
            unique_by=lambda s: (s.src, s.dst),
        )
    )
    net = NetworkConfig(
        gst=gst,
        delta=10,
        pre_gst_max_delay=draw(st.sampled_from([300, 100, 30])),
        delay_dist=draw(st.sampled_from(DELAY_DISTS)),
        loss_rate=draw(st.sampled_from(loss_rates)) if allow_loss else 0.0,
        dup_rate=draw(st.sampled_from([0.0, 0.2])),
        partitions=tuple(partitions),
        slow_links=tuple(slow),
    )
    byz: tuple[Byzantine, ...] = ()
    if protocol == "bft":
        kmax = f if byzantine is None else byzantine
        ids = draw(st.lists(node, min_size=min(min_byzantine, kmax), max_size=kmax, unique=True))
        byz = tuple(Byzantine(i, draw(st.sampled_from(behaviours))) for i in sorted(ids))
    byz_ids = {b.node for b in byz}
    honest = [i for i in range(n) if i not in byz_ids]
    crash_list = draw(
        st.lists(
            st.tuples(
                st.sampled_from(honest),
                st.integers(0, 2000),
                st.one_of(st.none(), st.integers(1, 1000)),
            ),
            max_size=len(honest) if max_crashes is None else max_crashes,
            unique_by=lambda c: c[0],
        )
    )
    crashes = tuple(Crash(i, at, None if d is None else at + d) for i, at, d in crash_list)
    split = tuple(sorted(draw(st.sets(st.sampled_from(honest)))))
    leader_crashes: tuple[LeaderCrash, ...] = ()
    if protocol == "raft" and allow_leader_crashes:
        lc = draw(
            st.lists(
                st.tuples(
                    st.integers(1, max(6, max_leader_crashes + 2)),
                    st.integers(0, max_leader_crash_delay),
                    st.integers(*leader_down_range)
                    if not allow_permanent_leader_crash
                    else st.one_of(st.none(), st.integers(*leader_down_range)),
                ),
                min_size=min_leader_crashes,
                max_size=max_leader_crashes,
                unique_by=lambda t: t[0],
            )
        )
        leader_crashes = tuple(LeaderCrash(*t) for t in sorted(lc))
    seed = draw(st.integers(0, 10_000))
    return _with_horizon(
        Scenario(
            protocol=protocol,
            n=n,
            seed=seed,
            network=net,
            crashes=crashes,
            leader_crashes=leader_crashes,
            byzantine=byz,
            split=split,
            mutant=mutant,
            horizon=0,
        )
    )
