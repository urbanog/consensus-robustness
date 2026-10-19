"""Deterministic discrete-event simulation kernel.

Every source of randomness is a ``random.Random`` seeded from the scenario seed
(network: ``"{seed}/network"``, node ``i``: ``"{seed}/node/{i}"``). String seeds
are hashed with SHA-512 by CPython, so they do not depend on ``PYTHONHASHSEED``.
The event queue is a heap ordered by ``(time, insertion counter)``, so ties are
broken FIFO. Consequently a run is a pure function of the scenario, which is
checked by hashing every processed event into a SHA-256 "trace hash".
"""

from __future__ import annotations

import hashlib
import heapq
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .network import NetworkConfig, NetworkModel


@dataclass(frozen=True, slots=True)
class TraceEvent:
    time: int
    kind: str
    node: int | None
    detail: str

    def format(self) -> str:
        who = "" if self.node is None else f"n{self.node}"
        return f"t={self.time:>5}  {who:<4} {self.detail}"


class Observer(Protocol):
    def on_event(self, sim: Simulator, node: int, kind: str, data: dict[str, Any]) -> None: ...

    def after_step(self, sim: Simulator) -> None: ...


class Node:
    """A message-driven state machine. Subclasses override the ``on_*`` hooks."""

    def __init__(self, node_id: int, n: int) -> None:
        self.id = node_id
        self.n = n
        self.sim: Simulator
        self.rng: random.Random
        self._timer_gen: dict[str, int] = {}

    # --- wiring -----------------------------------------------------------------
    def attach(self, sim: Simulator, rng: random.Random) -> None:
        self.sim = sim
        self.rng = rng

    @property
    def peers(self) -> list[int]:
        return [p for p in range(self.n) if p != self.id]

    # --- actions available to protocol code --------------------------------------
    def send(self, dst: int, msg: Any) -> None:
        self.sim.send(self.id, dst, msg)

    def broadcast(self, msg: Any, include_self: bool = True) -> None:
        for dst in range(self.n):
            if include_self or dst != self.id:
                self.send(dst, msg)

    def set_timer(self, delay: int, name: str, data: Any = None) -> None:
        """(Re)arm the timer ``name``; re-arming cancels the previous instance."""
        gen = self._timer_gen.get(name, 0) + 1
        self._timer_gen[name] = gen
        self.sim.schedule_timer(self.id, delay, name, gen, data)

    def emit(self, kind: str, **data: Any) -> None:
        """Report a protocol-level event to the property checkers."""
        self.sim.emit(self.id, kind, data)

    def note(self, text: str) -> None:
        self.sim.note(self.id, text)

    # --- hooks --------------------------------------------------------------------
    def on_start(self) -> None:  # pragma: no cover - default
        pass

    def on_message(self, src: int, msg: Any) -> None:  # pragma: no cover - default
        pass

    def on_timer(self, name: str, data: Any) -> None:  # pragma: no cover - default
        pass

    def on_crash(self) -> None:
        pass

    def on_restart(self) -> None:
        pass


class Simulator:
    def __init__(self, seed: int, network: NetworkConfig, *, record_trace: bool = False) -> None:
        self.seed = seed
        self.now = 0
        self.network = NetworkModel(network, random.Random(f"{seed}/network"))
        self.nodes: list[Node] = []
        self.up: list[bool] = []
        self._epoch: list[int] = []
        self._booted: list[bool] = []  # has on_start run? (a node may crash before booting)
        self._queue: list[tuple[int, int, str, Any]] = []
        self._seq = 0
        self._hash = hashlib.sha256()
        self.record_trace = record_trace
        self.trace: list[TraceEvent] = []
        self.observers: list[Observer] = []
        self.stopped = False
        self.stop_reason: str | None = None
        self._authored: set[Any] = set()
        self._started = False
        self.stats: dict[str, int] = {
            "events": 0,
            "sent": 0,
            "delivered": 0,
            "dropped_loss": 0,
            "dropped_partition": 0,
            "dropped_crashed": 0,
            "duplicated": 0,
            "forgeries_rejected": 0,
        }

    # --- setup ----------------------------------------------------------------------
    def add_node(self, node: Node) -> None:
        assert node.id == len(self.nodes), "nodes must be added in id order"
        node.attach(self, random.Random(f"{self.seed}/node/{node.id}"))
        self.nodes.append(node)
        self.up.append(True)
        self._epoch.append(0)
        self._booted.append(False)

    def add_observer(self, obs: Observer) -> None:
        self.observers.append(obs)

    def schedule_crash(self, node: int, at: int) -> None:
        self._push(at, "crash", node)

    def schedule_restart(self, node: int, at: int) -> None:
        self._push(at, "restart", node)

    # --- internals ----------------------------------------------------------------
    def _push(self, time: int, kind: str, payload: Any) -> None:
        heapq.heappush(self._queue, (time, self._seq, kind, payload))
        self._seq += 1

    def _log(self, kind: str, node: int | None, detail: str) -> None:
        self._hash.update(f"{self.now}|{kind}|{node}|{detail}\n".encode())
        if self.record_trace:
            self.trace.append(TraceEvent(self.now, kind, node, detail))

    @property
    def trace_hash(self) -> str:
        return self._hash.hexdigest()

    # --- services used by nodes --------------------------------------------------
    def schedule_timer(self, node: int, delay: int, name: str, gen: int, data: Any) -> None:
        self._push(self.now + max(0, delay), "timer", (node, name, gen, self._epoch[node], data))

    def send(self, src: int, dst: int, msg: Any) -> None:
        if not self.up[src]:
            return
        author = getattr(msg, "sender", src)
        try:
            hash(msg)
            hashable = True
        except TypeError:
            hashable = False
        if author != src:
            # Signature model: a node may relay a message authored by someone else
            # only if that author really sent it. Forgeries are rejected here.
            if not hashable or msg not in self._authored:
                self.stats["forgeries_rejected"] += 1
                self._log("forgery", src, f"forged {msg!r} rejected")
                return
        elif hashable:
            self._authored.add(msg)
        self.stats["sent"] += 1
        self._log("send", src, f"-> n{dst} {msg!r}")
        if src == dst:
            self._push(self.now, "deliver", (src, dst, msg))
            return
        times, reason = self.network.route(src, dst, self.now)
        if not times:
            self.stats[f"dropped_{reason}"] += 1
            self._log("drop", src, f"-> n{dst} dropped ({reason}) {msg!r}")
            return
        if len(times) > 1:
            self.stats["duplicated"] += 1
        for t in times:
            self._push(t, "deliver", (src, dst, msg))

    def emit(self, node: int, kind: str, data: dict[str, Any]) -> None:
        for obs in self.observers:
            obs.on_event(self, node, kind, data)

    def note(self, node: int | None, text: str) -> None:
        self._log("note", node, text)

    def stop(self, reason: str) -> None:
        self.stopped = True
        self.stop_reason = reason

    # --- main loop ------------------------------------------------------------------
    def run(self, until: int, stop_when: Callable[[], bool] | None = None) -> None:
        if not self._started:
            self._started = True
            for node in self.nodes:
                self._push(0, "start", node.id)
        while self._queue and not self.stopped:
            if self._queue[0][0] > until:
                break
            t, _, kind, payload = heapq.heappop(self._queue)
            self.now = t
            self.stats["events"] += 1
            self._dispatch(kind, payload)
            for obs in self.observers:
                obs.after_step(self)
            if stop_when is not None and stop_when():
                break

    def _dispatch(self, kind: str, payload: Any) -> None:
        if kind == "deliver":
            src, dst, msg = payload
            if not self.up[dst]:
                self.stats["dropped_crashed"] += 1
                self._log("drop", dst, f"<- n{src} dropped (crashed) {msg!r}")
                return
            self.stats["delivered"] += 1
            self._log("deliver", dst, f"<- n{src} {msg!r}")
            self.nodes[dst].on_message(src, msg)
        elif kind == "timer":
            node, name, gen, epoch, data = payload
            if self.up[node] and epoch == self._epoch[node] and self.nodes[node]._timer_gen.get(name) == gen:
                self._log("timer", node, name)
                self.nodes[node].on_timer(name, data)
        elif kind == "crash":
            node = payload
            if self.up[node]:
                self.up[node] = False
                self._epoch[node] += 1
                self._log("crash", node, "CRASH")
                self.nodes[node].on_crash()
        elif kind == "restart":
            node = payload
            if not self.up[node]:
                self.up[node] = True
                if self._booted[node]:
                    self._log("restart", node, "RESTART (persistent state kept, volatile state lost)")
                    self.nodes[node].on_restart()
                else:  # crashed before it ever booted: this is its first start
                    self._booted[node] = True
                    self._log("restart", node, "RESTART (first boot)")
                    self.nodes[node].on_start()
        elif kind == "start":
            node = payload
            if self.up[node]:
                self._booted[node] = True
                self.nodes[node].on_start()
        else:  # pragma: no cover
            raise ValueError(kind)
