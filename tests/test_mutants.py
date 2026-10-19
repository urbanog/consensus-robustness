"""Mutation testing: the harness must catch every deliberately broken protocol variant,
and shrinking must produce a small counterexample."""

import pytest

from consensus_robustness.mutation import MUTANTS, MutantSpec, fault_count, find_counterexample
from consensus_robustness.scenario import run


@pytest.mark.parametrize("spec", MUTANTS, ids=[m.name for m in MUTANTS])
def test_mutant_is_caught_and_shrunk(spec: MutantSpec) -> None:
    fd = find_counterexample(spec)
    assert fd.result is not None, f"harness failed to catch {spec.name}"
    assert not fd.result.safe
    assert fd.first_failing is not None
    # Hypothesis shrinks in its own order on the underlying choice sequence, so the number of
    # fault ingredients is not guaranteed to be monotone (e.g. GST 1500 -> 129 may need one slow
    # link); we only require the result to be small.
    assert fault_count(fd.result.scenario) <= 6
    # The shrunk counterexample is deterministic: re-running it reproduces the violation.
    again = run(fd.result.scenario)
    assert [str(v) for v in again.violations] == [str(v) for v in fd.result.violations]
    text = fd.result.counterexample()
    assert "VIOLATION" in text and "Scenario:" in text
