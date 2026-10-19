"""Online property checkers.

Checkers are simulator observers: protocol nodes report protocol-level events
through ``Node.emit`` and the checkers verify invariants the moment an event
happens (and, for Raft's log-matching property, after every simulation step).
The first violation stops the simulation, so the recorded trace ends exactly at
the bad event.

Only *honest* nodes report events; a crashed-and-restarted node is honest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..protocols.bft import valid
from ..sim.core import Simulator


@dataclass(frozen=True, slots=True)
class Violation:
    prop: str
    time: int
    message: str

    def __str__(self) -> str:
        return f"[{self.prop}] at t={self.time}: {self.message}"


class Checker:
    def __init__(self) -> None:
        self.violations: list[Violation] = []

    def fail(self, sim: Simulator, prop: str, message: str) -> None:
        v = Violation(prop, sim.now, message)
        self.violations.append(v)
        sim.note(None, f"VIOLATION {v}")
        sim.stop(str(v))

    def on_event(self, sim: Simulator, node: int, kind: str, data: dict[str, Any]) -> None:
        pass

    def after_step(self, sim: Simulator) -> None:
        pass


class BftChecker(Checker):
    """Agreement, validity and integrity for every height."""

    def __init__(self) -> None:
        super().__init__()
        self.decided: dict[int, dict[int, Any]] = {}  # height -> node -> value
        self.decide_time: dict[int, dict[int, int]] = {}

    def on_event(self, sim: Simulator, node: int, kind: str, data: dict[str, Any]) -> None:
        if kind != "decide":
            return
        h, v = data["height"], data["value"]
        per_h = self.decided.setdefault(h, {})
        if node in per_h:
            self.fail(sim, "integrity", f"n{node} decided twice at height {h}")
            return
        if not valid(v):
            self.fail(sim, "validity", f"n{node} decided invalid value {v!r} at height {h}")
            return
        for other, w in per_h.items():
            if w != v:
                self.fail(
                    sim,
                    "agreement",
                    f"height {h}: n{other} decided {w!r} but n{node} decided {v!r}",
                )
                return
        per_h[node] = v
        self.decide_time.setdefault(h, {})[node] = sim.now


class RaftChecker(Checker):
    """Election safety, log matching, leader completeness, state-machine safety, validity."""

    def __init__(self, commands: tuple[str, ...]) -> None:
        super().__init__()
        self.allowed = set(commands) | {"noop"}
        self.leaders: dict[int, int] = {}  # term -> leader id
        self.leader_logs: dict[int, tuple[Any, ...]] = {}  # term -> leader's log at its election
        self.committed: dict[int, tuple[Any, int]] = {}  # index -> (entry, term of committing leader)
        self.applied: dict[int, tuple[Any, int]] = {}  # index -> (entry, first node to apply it)
        self._versions: dict[int, int] = {}

    def on_event(self, sim: Simulator, node: int, kind: str, data: dict[str, Any]) -> None:
        if kind == "leader":
            term, log = data["term"], data["log"]
            prev = self.leaders.get(term)
            if prev is not None and prev != node:
                self.fail(sim, "election-safety", f"two leaders in term {term}: n{prev} and n{node}")
                return
            self.leaders[term] = node
            self.leader_logs[term] = log
            for idx, (entry, cterm) in sorted(self.committed.items()):
                if cterm < term and not self._complete(sim, term, idx, entry, cterm):
                    return
        elif kind == "commit":
            idx, entry = data["index"], data["entry"]
            prev = self.committed.get(idx)
            if prev is not None and prev[0] != entry:
                self.fail(
                    sim,
                    "state-machine-safety",
                    f"index {idx} committed as {prev[0]} and later as {entry} (by n{node})",
                )
                return
            if prev is None:
                cterm = data["term"]
                self.committed[idx] = (entry, cterm)
                # Leader completeness also constrains leaders of later terms that were
                # elected *before* this commit happened in real time.
                for term in sorted(t for t in self.leader_logs if t > cterm):
                    if not self._complete(sim, term, idx, entry, cterm):
                        return
        elif kind == "apply":
            idx, entry = data["index"], data["entry"]
            if entry.cmd not in self.allowed:
                self.fail(sim, "validity", f"n{node} applied unknown command {entry.cmd!r}")
                return
            prev = self.applied.get(idx)
            if prev is not None and prev[0] != entry:
                self.fail(
                    sim,
                    "state-machine-safety",
                    f"n{prev[1]} applied {prev[0]} at index {idx} but n{node} applied {entry}",
                )
                return
            if prev is None:
                self.applied[idx] = (entry, node)

    def _complete(self, sim: Simulator, term: int, idx: int, entry: Any, cterm: int) -> bool:
        """Leader completeness for one (leader term, committed entry) pair."""
        log = self.leader_logs[term]
        if idx <= len(log) and log[idx - 1] == entry:
            return True
        have = log[idx - 1] if idx <= len(log) else "nothing"
        self.fail(
            sim,
            "leader-completeness",
            f"n{self.leaders[term]} became leader of term {term} without committed entry "
            f"#{idx}={entry} (committed in term {cterm}); its log has {have}",
        )
        return False

    def after_step(self, sim: Simulator) -> None:
        if sim.stopped:
            return
        nodes = sim.nodes
        changed = [nd for nd in nodes if self._versions.get(nd.id) != nd.log_version]  # type: ignore[attr-defined]
        if not changed:
            return
        for nd in changed:
            self._versions[nd.id] = nd.log_version  # type: ignore[attr-defined]
        for a in changed:
            for b in nodes:
                if b.id == a.id:
                    continue
                msg = _log_matching_error(a.log, b.log)  # type: ignore[attr-defined]
                if msg:
                    self.fail(sim, "log-matching", f"n{a.id} vs n{b.id}: {msg}")
                    return


def _log_matching_error(la: list[Any], lb: list[Any]) -> str | None:
    """If two logs share an entry with the same index and term, all preceding entries agree."""
    m = min(len(la), len(lb))
    last_same_term = 0
    for i in range(m, 0, -1):
        if la[i - 1].term == lb[i - 1].term:
            last_same_term = i
            break
    for j in range(last_same_term):
        if la[j] != lb[j]:
            return (
                f"both have term {la[last_same_term - 1].term} at index {last_same_term} "
                f"but differ at index {j + 1}: {la[j]} vs {lb[j]}"
            )
    return None
