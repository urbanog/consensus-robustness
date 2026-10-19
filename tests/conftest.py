"""Hypothesis profiles.

* ``dev`` (default): quick local feedback.
* ``ci``: derandomised (reproducible in CI), more examples.
* ``thorough``: for overnight runs, e.g. ``HYPOTHESIS_PROFILE=thorough pytest``.
"""

import os

from hypothesis import HealthCheck, settings

_common = dict(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])

settings.register_profile("dev", max_examples=40, **_common)
settings.register_profile("ci", max_examples=80, derandomize=True, database=None, print_blob=True, **_common)
settings.register_profile("thorough", max_examples=2000, **_common)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
