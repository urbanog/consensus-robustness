"""Property-based tests: Hypothesis generates fault schedules; the honest protocols must be safe,
and live whenever the schedule satisfies the model's assumptions."""

from hypothesis import given, note

from consensus_robustness import run
from consensus_robustness.faults import scenarios
from consensus_robustness.scenario import Scenario


def _check(sc: Scenario) -> None:
    r = run(sc)
    note("\n".join(sc.describe()))
    assert r.safe, run(sc, record_trace=True).counterexample()
    if sc.liveness_expected():
        assert r.liveness_ok, f"no decision by t={sc.deadline()}\n" + "\n".join(sc.describe())


@given(scenarios("raft", 3))
def test_raft3_safe_and_live(sc: Scenario) -> None:
    _check(sc)


@given(scenarios("raft", 5))
def test_raft5_safe_and_live(sc: Scenario) -> None:
    _check(sc)


@given(scenarios("bft", 4))
def test_bft4_safe_and_live(sc: Scenario) -> None:
    _check(sc)


@given(scenarios("bft", 4, behaviours=("equivocate",)))
def test_bft4_equivocation_safe_and_live(sc: Scenario) -> None:
    _check(sc)


@given(scenarios("bft", 7, behaviours=("equivocate", "silent")))
def test_bft7_safe_and_live(sc: Scenario) -> None:
    _check(sc)


@given(scenarios("bft", 4, byzantine=2, behaviours=("silent",)))
def test_bft_beyond_f_silent_is_still_safe(sc: Scenario) -> None:
    # With 2 > f = 1 silent nodes liveness is lost, but safety must survive (no liveness assertion
    # is made because liveness_expected() is False for these schedules).
    assert run(sc).safe
