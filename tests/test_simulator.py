"""Tests of the simulation kernel and the network model."""

import random

import pytest

from consensus_robustness import Byzantine, Crash, NetworkConfig, Partition, Scenario, SlowLink, run
from consensus_robustness.faults import random_scenario
from consensus_robustness.sim.core import Node, Simulator
from consensus_robustness.sim.network import NetworkModel


class Echo(Node):
    """Node 0 sends `count` numbered pings to node 1; node 1 records arrival order."""

    def __init__(self, node_id: int, n: int, count: int = 0) -> None:
        super().__init__(node_id, n)
        self.count = count
        self.got: list[tuple[int, int]] = []

    def on_start(self) -> None:
        for i in range(self.count):
            self.send(1, ("ping", i))

    def on_message(self, src: int, msg: object) -> None:
        self.got.append((self.sim.now, msg[1]))  # type: ignore[index]


def _echo(net: NetworkConfig, count: int = 200, seed: int = 1) -> Echo:
    sim = Simulator(seed, net)
    sim.add_node(Echo(0, 2, count))
    receiver = Echo(1, 2)
    sim.add_node(receiver)
    sim.run(10_000)
    return receiver


@pytest.mark.parametrize("protocol,n", [("raft", 3), ("raft", 5), ("bft", 4), ("bft", 7)])
def test_same_seed_same_trace_hash(protocol: str, n: int) -> None:
    for seed in range(10):
        sc = random_scenario(protocol, n, seed)
        assert run(sc).trace_hash == run(sc).trace_hash


def test_trace_hash_independent_of_global_random_state() -> None:
    sc = random_scenario("bft", 4, 3)
    h1 = run(sc).trace_hash
    random.seed(12345)
    random.random()
    assert run(sc).trace_hash == h1


def test_different_seeds_differ() -> None:
    hashes = {run(Scenario(protocol="bft", n=4, seed=s, network=NetworkConfig(gst=300))).trace_hash for s in range(20)}
    assert len(hashes) == 20


def test_post_gst_delays_bounded_by_delta() -> None:
    net = NetworkConfig(gst=0, delta=7, min_delay=2)
    got = _echo(net)
    assert len(got.got) == 200
    assert all(2 <= t <= 7 for t, _ in got.got)


def test_pre_gst_messages_arrive_by_gst_plus_delta() -> None:
    net = NetworkConfig(gst=500, delta=10, pre_gst_max_delay=5000)
    got = _echo(net)
    assert len(got.got) == 200
    assert max(t for t, _ in got.got) <= 510


def test_reordering_happens() -> None:
    order = [i for _, i in _echo(NetworkConfig(gst=0, delta=50)).got]
    assert order != sorted(order)


def test_loss_rate_is_respected_and_stops_at_gst() -> None:
    rng = random.Random(0)
    m = NetworkModel(NetworkConfig(gst=100, loss_rate=0.3), rng)
    pre = sum(not m.route(0, 1, 0)[0] for _ in range(10_000))
    post = sum(not m.route(0, 1, 100)[0] for _ in range(10_000))
    assert 2700 < pre < 3300
    assert post == 0


def test_persistent_loss_continues_after_gst() -> None:
    m = NetworkModel(NetworkConfig(gst=0, loss_rate=0.3, faults_stop_at_gst=False), random.Random(0))
    lost = sum(not m.route(0, 1, 50)[0] for _ in range(10_000))
    assert 2700 < lost < 3300


def test_duplication() -> None:
    got = _echo(NetworkConfig(gst=10_000, dup_rate=1.0, pre_gst_max_delay=20))
    assert len(got.got) == 400


def test_partition_drops_and_heals() -> None:
    p = Partition(0, 100, (0,))
    m = NetworkModel(NetworkConfig(gst=1000, partitions=(p,)), random.Random(0))
    assert m.route(0, 1, 50) == ([], "partition")
    assert m.route(1, 0, 99) == ([], "partition")
    assert m.route(0, 1, 100)[0]  # healed
    assert m.route(1, 2, 50)[0]  # same side


def test_slow_link() -> None:
    m = NetworkModel(NetworkConfig(gst=1000, slow_links=(SlowLink(0, 1, 400),)), random.Random(0))
    assert m.route(0, 1, 0)[0] == [400]
    assert m.route(0, 1, 800)[0] == [1010]  # capped at GST + Δ


def test_crashed_node_gets_nothing_and_loses_timers() -> None:
    sc = Scenario(protocol="raft", n=3, seed=0, crashes=(Crash(0, 0),), horizon=3000)
    r = run(sc, record_trace=True)
    assert not any(ev.node == 0 and ev.kind in ("deliver", "timer") for ev in r.trace if ev.time > 0)
    assert r.finished  # the other two still make progress


def test_forged_messages_are_rejected() -> None:
    sc = Scenario(protocol="bft", n=4, seed=5, byzantine=(Byzantine(3, "garbage"),), horizon=3000)
    r = run(sc)
    assert r.stats["forgeries_rejected"] > 0
    assert r.safe and r.finished


def test_invalid_scenarios_rejected() -> None:
    with pytest.raises(ValueError):
        Scenario(protocol="raft", n=3, byzantine=(Byzantine(0, "silent"),))
    with pytest.raises(ValueError):
        Scenario(protocol="bft", n=4, byzantine=(Byzantine(9, "silent"),))
    with pytest.raises(ValueError):
        NetworkConfig(min_delay=20, delta=10)
