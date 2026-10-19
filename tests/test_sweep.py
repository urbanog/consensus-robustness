"""A seed sweep inside the test-suite (the large sweeps are reported in the README)."""

import pytest

from consensus_robustness.sweep import sweep


@pytest.mark.parametrize("protocol,n,seeds", [("raft", 3, 150), ("raft", 5, 100), ("bft", 4, 100), ("bft", 7, 30)])
def test_honest_sweep(protocol: str, n: int, seeds: int) -> None:
    rep = sweep(protocol, n, range(10_000, 10_000 + seeds))
    assert rep.safety_violations == 0, rep.summary()
    assert rep.liveness_failures == 0, rep.summary()
