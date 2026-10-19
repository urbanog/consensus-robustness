"""Statistical liveness experiment: BFT under persistent message loss and silent faults.

Setup: Tendermint-style BFT with ``n = 7`` (``f = 2``, quorum 5), GST = 0,
Δ = 10, uniform delays in [1, 10]. Message loss is *persistent*
(``faults_stop_at_gst=False``), so the run never becomes synchronous in the
DLS sense and liveness is a probabilistic question. ``k`` validators are
Byzantine-silent (equivalently: crashed from the start). For every
(loss rate, k) cell we run ``seeds`` independent seeds with ``heights = 3``
and a horizon of ``horizon`` time units and record

* the decision rate: fraction of runs in which every honest node decided all 3 heights;
* the time at which the last honest node decided height 2 (completed runs only).

Safety violations are also counted; they must be zero in every cell.
"""

from __future__ import annotations

import csv
import statistics
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .scenario import Byzantine, Scenario, run
from .sim.network import NetworkConfig

LOSS_RATES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7)
FAULTY = (0, 1, 2, 3)


@dataclass
class Cell:
    loss: float
    faulty: int
    runs: int
    completed: int
    unsafe: int
    times: list[int]

    @property
    def rate(self) -> float:
        return self.completed / self.runs

    @property
    def median(self) -> float | None:
        return statistics.median(self.times) if self.times else None

    def quantile(self, q: float) -> float | None:
        if not self.times:
            return None
        xs = sorted(self.times)
        return xs[min(len(xs) - 1, int(q * len(xs)))]


def _scenario(n: int, loss: float, faulty: int, seed: int, horizon: int) -> Scenario:
    return Scenario(
        protocol="bft",
        n=n,
        seed=seed,
        network=NetworkConfig(gst=0, delta=10, loss_rate=loss, faults_stop_at_gst=False),
        byzantine=tuple(Byzantine(n - 1 - i, "silent") for i in range(faulty)),
        heights=3,
        horizon=horizon,
    )


def _job(args: tuple[int, float, int, int, int]) -> tuple[float, int, bool, bool, int | None]:
    n, loss, faulty, seed, horizon = args
    r = run(_scenario(n, loss, faulty, seed, horizon), tail=0)
    return loss, faulty, r.finished, not r.safe, r.finish_time


def liveness_grid(
    n: int = 7,
    seeds: int = 200,
    horizon: int = 5_000,
    losses: tuple[float, ...] = LOSS_RATES,
    faulty: tuple[int, ...] = FAULTY,
    workers: int = 1,
) -> list[Cell]:
    jobs = [(n, loss, k, s, horizon) for loss in losses for k in faulty for s in range(seeds)]
    if workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            results = list(ex.map(_job, jobs, chunksize=16))
    else:
        results = [_job(j) for j in jobs]
    cells = {(loss, k): Cell(loss, k, 0, 0, 0, []) for loss in losses for k in faulty}
    for loss, k, finished, unsafe, t in results:
        c = cells[(loss, k)]
        c.runs += 1
        c.unsafe += unsafe
        if finished and t is not None:
            c.completed += 1
            c.times.append(t)
    return [cells[(loss, k)] for loss in losses for k in faulty]


def write_csv(cells: list[Cell], path: Path) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "loss_rate",
                "silent_faulty",
                "runs",
                "completed",
                "decision_rate",
                "median_time",
                "p90_time",
                "safety_violations",
            ]
        )
        for c in cells:
            w.writerow([c.loss, c.faulty, c.runs, c.completed, f"{c.rate:.3f}", c.median, c.quantile(0.9), c.unsafe])


def plot(cells: list[Cell], path: Path, n: int = 7, horizon: int = 5_000) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Categorical slots 1-4 of the reference palette (light mode), fixed order by entity.
    colours = {0: "#2a78d6", 1: "#eb6834", 2: "#1baf7a", 3: "#6250d6"}
    markers = {0: "o", 1: "s", 2: "^", 3: "D"}
    ink, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"
    plt.rcParams.update(
        {"font.size": 10, "axes.edgecolor": muted, "axes.labelcolor": ink, "xtick.color": muted, "ytick.color": muted}
    )
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2), constrained_layout=True)
    fig.patch.set_facecolor("#fcfcfb")
    ks = sorted({c.faulty for c in cells})
    for k in ks:
        row = sorted((c for c in cells if c.faulty == k), key=lambda c: c.loss)
        xs = [c.loss for c in row]
        label = f"{k} silent" + (" (> f: no quorum possible)" if k > (n - 1) // 3 else "")
        axes[0].plot(xs, [c.rate for c in row], color=colours[k], marker=markers[k], lw=2, ms=6, label=label)
        pts = [(c.loss, c.median) for c in row if c.median is not None and c.completed >= max(5, c.runs // 10)]
        if pts:
            axes[1].plot(
                [p[0] for p in pts], [p[1] for p in pts], color=colours[k], marker=markers[k], lw=2, ms=6, label=label
            )
    axes[0].set_ylabel("decision rate (all 3 heights decided)")
    axes[0].set_ylim(-0.03, 1.03)
    axes[1].set_ylabel("median completion time (simulated units)")
    axes[1].set_yscale("log")
    for ax in axes:
        ax.set_facecolor("#fcfcfb")
        ax.set_xlabel("message loss rate (persistent)")
        ax.grid(True, color=grid, lw=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_title(f"Liveness of BFT, n = {n}, f = {(n - 1) // 3}", color=ink, loc="left")
    axes[1].set_title("Time to decide 3 heights (cells with >= 10% completion)", color=ink, loc="left")
    axes[0].legend(frameon=False, title="Byzantine-silent validators", fontsize=9, title_fontsize=9)
    seeds = cells[0].runs
    fig.text(
        0.01,
        -0.04,
        f"{seeds} seeds per point; horizon {horizon}; Δ = 10, GST = 0, loss never stops.",
        color=muted,
        fontsize=8.5,
    )
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def markdown_table(cells: list[Cell]) -> str:
    ks = sorted({c.faulty for c in cells})
    losses = sorted({c.loss for c in cells})
    by = {(c.loss, c.faulty): c for c in cells}
    head = "| loss | " + " | ".join(f"{k} silent: rate / median t" for k in ks) + " |"
    sep = "|---|" + "---|" * len(ks)
    rows = [head, sep]
    for loss in losses:
        cols = []
        for k in ks:
            c = by[(loss, k)]
            med = "-" if c.median is None else f"{c.median:.0f}"
            cols.append(f"{c.rate:.2f} / {med}")
        rows.append(f"| {loss:.1f} | " + " | ".join(cols) + " |")
    return "\n".join(rows)
