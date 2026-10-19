"""Mutation testing: the harness must catch every deliberately broken protocol variant,
and shrinking must produce a small counterexample.

The search tries several fixed seeds with measured per-seed budgets (see ``mutation.py`` and
``docs/findings.md``), so it does not depend on one lucky Hypothesis stream. The recorded
counterexamples are additionally replayed, without Hypothesis, in ``test_mutant_regressions.py``.
"""

import pytest

from consensus_robustness.mutation import MUTANTS, MutantSpec, fault_count, find_counterexample
from consensus_robustness.scenario import run


@pytest.mark.parametrize("spec", MUTANTS, ids=[m.name for m in MUTANTS])
def test_mutant_is_caught_and_shrunk(spec: MutantSpec) -> None:
    fd = find_counterexample(spec)
    assert fd.result is not None, f"harness failed to catch {spec.name} ({fd.predicate_calls} runs)"
    assert not fd.result.safe
    assert fd.first_failing is not None
    # Hypothesis shrinks in its own order on the underlying choice sequence, so the number of
    # fault ingredients is not guaranteed to be monotone; we only require the result to be small.
    assert fault_count(fd.result.scenario) <= 8
    # The shrunk counterexample is deterministic: re-running it reproduces the violation.
    again = run(fd.result.scenario)
    assert [str(v) for v in again.violations] == [str(v) for v in fd.result.violations]
    text = fd.result.counterexample()
    assert "VIOLATION" in text and "Scenario:" in text
