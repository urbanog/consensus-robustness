"""Deterministic scenario tests of Raft and BFT (honest versions)."""

import pytest

from consensus_robustness import Byzantine, Crash, NetworkConfig, Partition, Scenario, run
from consensus_robustness.checking.properties import _log_matching_error
from consensus_robustness.protocols.bft import quorum_size
from consensus_robustness.protocols.raft import Entry
from consensus_robustness.scenario import LeaderCrash


@pytest.mark.parametrize("n", [1, 3, 4, 5, 7])
def test_raft_fault_free(n: int) -> None:
    r = run(Scenario(protocol="raft", n=n, seed=1))
    assert r.safe and r.finished and r.liveness_ok


@pytest.mark.parametrize("n", [1, 4, 7, 10])
def test_bft_fault_free(n: int) -> None:
    r = run(Scenario(protocol="bft", n=n, seed=1))
    assert r.safe and r.finished and r.liveness_ok


def test_quorum_sizes() -> None:
    assert [quorum_size(n, None) for n in (1, 4, 7, 10)] == [1, 3, 5, 7]  # 2f + 1 when n = 3f + 1
    assert quorum_size(4, "quorum_2f") == 2
    assert quorum_size(3, "n_eq_3f") == 2


def test_raft_majority_side_of_partition_elects_leader_then_heals() -> None:
    # Pre-GST delays are small here, so the partition is the only adversarial fault.
    net = NetworkConfig(gst=800, pre_gst_max_delay=10, partitions=(Partition(0, 800, (0, 1)),))
    r = run(Scenario(protocol="raft", n=5, seed=2, network=net), record_trace=True)
    assert r.safe and r.finished
    leaders = [ev.node for ev in r.trace if ev.kind == "note" and ev.time < 800 and "LEADER" in ev.detail]
    assert leaders and set(leaders) <= {2, 3, 4}  # only the majority side can elect a leader


def test_raft_long_pre_gst_delays_prevent_elections_until_gst() -> None:
    # Adversarial delays (up to 300) exceed the election timeout (100-200): no leader before GST.
    # This is the FLP phenomenon in miniature; after GST a leader is elected promptly.
    net = NetworkConfig(gst=800, pre_gst_max_delay=300, partitions=(Partition(0, 800, (0, 1)),))
    r = run(Scenario(protocol="raft", n=5, seed=2, network=net), record_trace=True)
    leaders = [ev.time for ev in r.trace if ev.kind == "note" and "LEADER" in ev.detail]
    assert r.safe and r.liveness_ok and leaders and min(leaders) > 800


def test_raft_leader_churn_is_safe() -> None:
    lc = tuple(LeaderCrash(k, 10, 200) for k in range(1, 6))
    for seed in range(30):
        r = run(Scenario(protocol="raft", n=3, seed=seed, leader_crashes=lc, horizon=8000))
        assert r.safe


def test_raft_restart_keeps_persistent_state() -> None:
    r = run(Scenario(protocol="raft", n=3, seed=4, crashes=(Crash(0, 300, 500), Crash(1, 320, 700))))
    assert r.safe and r.finished and r.liveness_ok


def test_bft_tolerates_f_silent() -> None:
    sc = Scenario(protocol="bft", n=7, seed=3, byzantine=(Byzantine(5, "silent"), Byzantine(6, "silent")))
    r = run(sc)
    assert r.safe and r.finished and r.liveness_ok


def test_bft_stalls_but_stays_safe_with_more_than_f_silent() -> None:
    sc = Scenario(protocol="bft", n=4, seed=3, byzantine=(Byzantine(2, "silent"), Byzantine(3, "silent")), horizon=3000)
    r = run(sc)
    assert r.safe and not r.finished
    assert not sc.liveness_expected()


@pytest.mark.parametrize("split", [(), (0,), (0, 1), (1, 2)])
def test_bft_equivocator_cannot_break_agreement(split: tuple[int, ...]) -> None:
    for seed in range(15):
        sc = Scenario(
            protocol="bft",
            n=4,
            seed=seed,
            byzantine=(Byzantine(3, "equivocate"),),
            split=split,
            network=NetworkConfig(gst=400),
        )
        r = run(sc)
        assert r.safe and r.liveness_ok


def test_bft_two_equivocators_n7() -> None:
    for seed in range(10):
        sc = Scenario(
            protocol="bft",
            n=7,
            seed=seed,
            byzantine=(Byzantine(0, "equivocate"), Byzantine(1, "equivocate")),
            split=(2, 3),
            network=NetworkConfig(gst=300),
        )
        r = run(sc)
        assert r.safe and r.liveness_ok


def test_bft_garbage_is_harmless() -> None:
    for seed in range(10):
        r = run(Scenario(protocol="bft", n=4, seed=seed, byzantine=(Byzantine(0, "garbage"),)))
        assert r.safe and r.liveness_ok


def test_bft_restarted_node_catches_up() -> None:
    sc = Scenario(protocol="bft", n=4, seed=8, crashes=(Crash(2, 5, 900),))
    r = run(sc)
    assert r.safe and r.finished and r.liveness_ok


def test_log_matching_helper() -> None:
    a = [Entry(1, "x"), Entry(2, "y")]
    assert _log_matching_error(a, [Entry(1, "x"), Entry(2, "y"), Entry(3, "z")]) is None
    assert _log_matching_error(a, [Entry(1, "x"), Entry(3, "q")]) is None
    assert _log_matching_error(a, [Entry(1, "w"), Entry(2, "y")]) is not None


@pytest.mark.parametrize("protocol,n", [("bft", 4), ("raft", 3)])
def test_node_crashed_before_boot_joins_after_others_finished(protocol: str, n: int) -> None:
    # Regression test (found by the 5000-seed sweep, BFT seed 898): a node that crashes at t=0
    # never ran on_start; when it restarts after everyone else has finished it must still boot,
    # announce itself and catch up.
    sc = Scenario(protocol=protocol, n=n, seed=898, crashes=(Crash(0, 0, 1500),))
    r = run(sc)
    assert r.safe and r.finished and r.liveness_ok
