"""Network model: delays, loss, duplication, reordering, partitions, partial synchrony.

Timing model (Dwork, Lynch & Stockmeyer 1988, "eventual delivery" variant):

* There is a global stabilisation time ``GST`` and a delay bound ``delta`` (Δ).
* A message sent at time ``t >= GST`` that is not addressed to a crashed node is
  delivered at some time in ``[t + min_delay, t + Δ]``.
* A message sent at time ``t < GST`` may be lost, duplicated or blocked by a
  partition. If it is not lost, it is delivered after an *adversarial* delay that
  may be much larger than Δ, but no later than ``GST + Δ``.
* If ``faults_stop_at_gst`` is ``False`` (used by the statistical experiments)
  loss, duplication and partitions continue forever; delays are still ≤ Δ after
  GST. Liveness is then a statistical question, not a guarantee.

Reordering is not a separate knob: every copy of every message draws an
independent delay, so two messages on the same link are reordered whenever the
later one draws a shorter delay.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

DELAY_DISTS = ("uniform", "exponential", "bimodal")


@dataclass(frozen=True, slots=True)
class Partition:
    """Messages between ``side`` and its complement are dropped during ``[start, end)``."""

    start: int
    end: int
    side: tuple[int, ...]

    def separates(self, a: int, b: int, t: int) -> bool:
        return self.start <= t < self.end and ((a in self.side) != (b in self.side))

    def __str__(self) -> str:
        return f"partition {set(self.side)} | rest during [{self.start}, {self.end})"


@dataclass(frozen=True, slots=True)
class SlowLink:
    """Adversarial pre-GST delay for every message on the directed link ``src -> dst``."""

    src: int
    dst: int
    delay: int

    def __str__(self) -> str:
        return f"slow link n{self.src}->n{self.dst} delay {self.delay} before GST"


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    gst: int = 0
    delta: int = 10
    min_delay: int = 1
    pre_gst_max_delay: int = 300
    delay_dist: str = "uniform"
    loss_rate: float = 0.0
    dup_rate: float = 0.0
    partitions: tuple[Partition, ...] = ()
    slow_links: tuple[SlowLink, ...] = ()
    faults_stop_at_gst: bool = True

    def __post_init__(self) -> None:
        if self.delay_dist not in DELAY_DISTS:
            raise ValueError(f"unknown delay distribution {self.delay_dist!r}")
        if not 1 <= self.min_delay <= self.delta:
            raise ValueError("need 1 <= min_delay <= delta")
        if self.pre_gst_max_delay < self.min_delay:
            raise ValueError("pre_gst_max_delay must be >= min_delay")
        if not (0.0 <= self.loss_rate <= 1.0 and 0.0 <= self.dup_rate <= 1.0):
            raise ValueError("rates must lie in [0, 1]")
        if self.gst < 0:
            raise ValueError("gst must be >= 0")

    def describe(self) -> str:
        parts = [
            f"GST={self.gst}",
            f"Δ={self.delta}",
            f"pre-GST max delay={self.pre_gst_max_delay}",
            f"dist={self.delay_dist}",
            f"loss={self.loss_rate:g}",
            f"dup={self.dup_rate:g}",
        ]
        if not self.faults_stop_at_gst:
            parts.append("faults persist after GST")
        return ", ".join(parts)


class NetworkModel:
    """Decides the fate of each message. All randomness comes from ``rng``."""

    def __init__(self, cfg: NetworkConfig, rng: random.Random) -> None:
        self.cfg = cfg
        self.rng = rng
        self._slow = {(s.src, s.dst): s.delay for s in cfg.slow_links}

    def _sample(self, lo: int, hi: int) -> int:
        rng, dist = self.rng, self.cfg.delay_dist
        if hi <= lo:
            return lo
        if dist == "uniform":
            return rng.randint(lo, hi)
        if dist == "exponential":
            mean = (hi - lo) / 3.0
            return lo + min(hi - lo, int(rng.expovariate(1.0 / mean)))
        # bimodal: mostly fast, occasionally slow
        if rng.random() < 0.9:
            return rng.randint(lo, lo + (hi - lo) // 5)
        return rng.randint(lo + (hi - lo) // 2, hi)

    def _delay(self, src: int, dst: int, t: int) -> int:
        cfg = self.cfg
        if t >= cfg.gst:
            return self._sample(cfg.min_delay, cfg.delta)
        slow = self._slow.get((src, dst))
        d = slow if slow is not None else self._sample(cfg.min_delay, cfg.pre_gst_max_delay)
        # A message sent before GST is delivered no later than GST + Δ.
        return max(cfg.min_delay, min(d, cfg.gst + cfg.delta - t))

    def route(self, src: int, dst: int, t: int) -> tuple[list[int], str | None]:
        """Return the delivery times of the copies of a message (empty = dropped) and a reason."""
        cfg = self.cfg
        adversarial = t < cfg.gst or not cfg.faults_stop_at_gst
        if adversarial:
            for p in cfg.partitions:
                if p.separates(src, dst, t):
                    return [], "partition"
            if cfg.loss_rate > 0.0 and self.rng.random() < cfg.loss_rate:
                return [], "loss"
        copies = 1
        if adversarial and cfg.dup_rate > 0.0 and self.rng.random() < cfg.dup_rate:
            copies = 2
        return [t + self._delay(src, dst, t) for _ in range(copies)], None
