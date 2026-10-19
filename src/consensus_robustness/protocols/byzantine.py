"""Byzantine behaviours for the BFT protocol.

A Byzantine node is an arbitrary program. We implement three representative
strategies; they share the honest nodes' message types, so the only thing they
cannot do is forge another node's signature (the simulator rejects forgeries).

* ``silent``     - never sends anything (indistinguishable from a crash).
* ``equivocate`` - sends conflicting proposals and votes to different honest
  peers; all equivocators act as one coordinated coalition (see
  :class:`EquivocatorNode`).
* ``garbage``    - periodically sends random messages to random peers:
  well-formed votes for random heights, rounds and values, malformed fields
  (negative rounds, bad ``valid_round``, invalid or unhashable values), forged
  sender ids, and non-protocol junk.
"""

from __future__ import annotations

from typing import Any

from ..sim.core import Node
from .bft import BftParams, Precommit, Prevote, Proposal

BYZ_BEHAVIOURS = ("silent", "equivocate", "garbage")


class SilentNode(Node):
    def __init__(
        self, node_id: int, n: int, params: BftParams, split: tuple[int, ...] = (), coalition: tuple[int, ...] = ()
    ) -> None:
        super().__init__(node_id, n)


class EquivocatorNode(Node):
    """Coordinated equivocation by the coalition of all ``equivocate`` nodes.

    The honest nodes are divided into group A (``split``) and group B (the rest).
    For a round whose proposer is in the coalition, the coalition uses two fresh
    values ``a`` and ``b`` (the same names at every member, so no communication
    is needed): the proposer proposes ``a`` to A and ``b`` to B, and every member
    prevotes and precommits ``a`` towards A and ``b`` towards B. For a round with
    an honest proposer whose proposal carries value ``v``, every member prevotes
    and precommits ``v`` towards A and ``nil`` towards B. All of this happens
    immediately, ignoring locks and timeouts, and is periodically re-sent.
    """

    def __init__(
        self, node_id: int, n: int, params: BftParams, split: tuple[int, ...] = (), coalition: tuple[int, ...] = ()
    ) -> None:
        super().__init__(node_id, n)
        self.p = params
        self.coalition = set(coalition) | {node_id}
        honest = [i for i in range(n) if i not in self.coalition]
        self.group_a = [i for i in honest if i in set(split)]
        self.group_b = [i for i in honest if i not in set(split)]
        self.handled: set[tuple[int, int]] = set()
        self.outbox: list[tuple[int, Any]] = []

    def proposer(self, h: int, r: int) -> int:
        return (h + r) % self.n

    def _send_kept(self, dst: int, msg: Any) -> None:
        self.outbox.append((dst, msg))
        self.send(dst, msg)

    def _vote(self, h: int, r: int, v: Any, targets: list[int]) -> None:
        for t in targets:
            self._send_kept(t, Prevote(self.id, h, r, v))
            self._send_kept(t, Precommit(self.id, h, r, v))

    def _coalition_round(self, h: int, r: int) -> None:
        """Rounds led by a coalition member: propose/vote a to group A and b to group B."""
        if (h, r) in self.handled or h >= self.p.heights:
            return
        self.handled.add((h, r))
        lead = self.proposer(h, r)
        for tag, group in (("a", self.group_a), ("b", self.group_b)):
            if not group:
                continue
            v = f"x{h}.{r}.{tag}"
            if lead == self.id:
                self.note(f"h={h} r={r}: EQUIVOCATES, proposes {v!r} to {group}")
                for t in group:
                    self._send_kept(t, Proposal(self.id, h, r, v, -1))
            self._vote(h, r, v, group)

    def _honest_round(self, h: int, r: int, v: Any) -> None:
        if (h, r) in self.handled:
            return
        self.handled.add((h, r))
        self.note(f"h={h} r={r}: votes {v!r} towards {self.group_a} and nil towards {self.group_b}")
        self._vote(h, r, v, self.group_a)
        self._vote(h, r, None, self.group_b)

    def _see_round(self, h: int, r: int) -> None:
        for rr in (r, r + 1):
            if self.proposer(h, rr) in self.coalition:
                self._coalition_round(h, rr)

    def on_start(self) -> None:
        self.set_timer(self.p.retransmit, "retransmit")
        self._see_round(0, 0)

    def on_message(self, src: int, msg: Any) -> None:
        if not isinstance(msg, (Proposal, Prevote, Precommit)):
            return
        h, r = msg.height, msg.round
        if type(h) is not int or type(r) is not int or not (0 <= h < self.p.heights and 0 <= r < 1000):
            return
        self._see_round(h, r)
        if isinstance(msg, Proposal) and msg.sender == self.proposer(h, r) and msg.sender not in self.coalition:
            try:
                hash(msg.value)
            except TypeError:
                return
            self._honest_round(h, r, msg.value)

    def on_timer(self, name: str, data: Any) -> None:
        for dst, m in self.outbox:
            self.send(dst, m)
        self.set_timer(self.p.retransmit, "retransmit")


class GarbageNode(Node):
    PERIOD = 7

    def __init__(
        self, node_id: int, n: int, params: BftParams, split: tuple[int, ...] = (), coalition: tuple[int, ...] = ()
    ) -> None:
        super().__init__(node_id, n)
        self.p = params

    def on_start(self) -> None:
        self.set_timer(self.PERIOD, "spam")

    def _random_value(self) -> Any:
        rng = self.rng
        choices: list[Any] = [
            None,
            f"g{rng.randrange(100)}",
            f"v{rng.randrange(self.p.heights)}.{rng.randrange(self.n)}",
            "BAD VALUE!",
            7,
            ("tuple", 1),
            float("nan"),
            [1, 2, 3],  # unhashable
        ]
        return rng.choice(choices)

    def _random_message(self) -> Any:
        rng = self.rng
        kind = rng.randrange(5)
        sender = self.id if rng.random() < 0.8 else rng.randrange(self.n)  # sometimes forged
        h = rng.randrange(-1, self.p.heights + 1)
        r = rng.randrange(-1, 6)
        v = self._random_value()
        if kind == 0:
            return Proposal(sender, h, r, v, rng.randrange(-3, 6))
        if kind == 1:
            return Prevote(sender, h, r, v)
        if kind == 2:
            return Precommit(sender, h, r, v)
        if kind == 3:
            return rng.choice(["hello", 42, b"\x00\xff", ("PREVOTE", h, r), {"evil": True}])
        return Prevote("n0", h, r, v)  # type: ignore[arg-type]  # ill-typed field

    def on_timer(self, name: str, data: Any) -> None:
        for _ in range(self.rng.randrange(1, 4)):
            self.send(self.rng.randrange(self.n), self._random_message())
        self.set_timer(self.PERIOD, "spam")


BYZ_CLASSES: dict[str, type[Node]] = {
    "silent": SilentNode,
    "equivocate": EquivocatorNode,
    "garbage": GarbageNode,
}
