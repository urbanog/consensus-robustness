"""A Tendermint-style Byzantine-fault-tolerant consensus protocol.

This is Algorithm 1 of Buchman, Kwon & Milosevic, "The latest gossip on BFT
consensus" (arXiv:1807.04938, 2018), for a sequence of ``heights`` instances,
with the line numbers of the paper quoted next to each rule. The precise rules
are documented in ``docs/bft.md``.

Thresholds. With ``n`` validators and ``f = (n - 1) // 3`` the quorum is
``Q = floor(2n/3) + 1`` (``= 2f + 1`` when ``n = 3f + 1``) and the round-skip
threshold is ``f + 1``.

Communication. Signatures are modelled by the simulator: a message carries its
author in ``sender`` and the simulator refuses to deliver a message whose author
did not send it, so nodes may *relay* messages but nobody can forge them. The
paper assumes a gossip layer that eventually delivers every message received by a
correct process to all correct processes; we implement the two pieces of it that
liveness needs: (1) every node periodically re-broadcasts (relays) every
well-formed message it has received for its current height, its own included,
and (2) a node that receives a message for a height it has
already decided answers the sender with the decision certificate (the proposal
plus ``Q`` matching precommits).

Persistence. All state is treated as persistent (write-ahead log), so a crashed
and restarted honest node never contradicts its earlier votes.

Mutants (deliberately broken variants):

* ``quorum_2f`` - quorum ``2f`` instead of ``2f + 1`` (with ``n = 3f + 1``).
* ``n_eq_3f``  - run with ``n = 3f`` validators and quorum ``n - f = 2f``, the
  largest quorum that still tolerates ``f`` silent validators.
* ``no_lock``  - ignore ``lockedValue``/``lockedRound`` when prevoting.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..sim.core import Node

BFT_MUTANTS = ("quorum_2f", "n_eq_3f", "no_lock")

PROPOSE, PREVOTE, PRECOMMIT = 0, 1, 2
STEP_NAMES = {PROPOSE: "propose", PREVOTE: "prevote", PRECOMMIT: "precommit"}

_VALID = re.compile(r"[a-z][a-z0-9.\-]*")


def valid(v: Any) -> bool:
    """The application-level validity predicate ``valid(v)`` of the paper."""
    return isinstance(v, str) and _VALID.fullmatch(v) is not None


def _fmt(v: Any) -> str:
    return "nil" if v is None else repr(v)


@dataclass(frozen=True, slots=True)
class Proposal:
    sender: int
    height: int
    round: int
    value: Any
    valid_round: int

    def __repr__(self) -> str:
        return f"PROPOSAL(h={self.height}, r={self.round}, {_fmt(self.value)}, vr={self.valid_round}) by n{self.sender}"


@dataclass(frozen=True, slots=True)
class Prevote:
    sender: int
    height: int
    round: int
    value: Any  # None means nil

    def __repr__(self) -> str:
        return f"PREVOTE(h={self.height}, r={self.round}, {_fmt(self.value)}) by n{self.sender}"


@dataclass(frozen=True, slots=True)
class Precommit:
    sender: int
    height: int
    round: int
    value: Any  # None means nil

    def __repr__(self) -> str:
        return f"PRECOMMIT(h={self.height}, r={self.round}, {_fmt(self.value)}) by n{self.sender}"


@dataclass(frozen=True, slots=True)
class BftParams:
    heights: int = 3
    timeout_propose: int = 30
    timeout_prevote: int = 30
    timeout_precommit: int = 30
    timeout_increment: int = 15
    retransmit: int = 60
    mutant: str | None = None


@dataclass
class HeightLog:
    """All well-formed messages received for one height."""

    proposals: dict[int, dict[tuple[Any, int], Proposal]] = field(default_factory=lambda: defaultdict(dict))
    prevotes: dict[int, dict[Any, set[int]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(set)))
    prevote_any: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))
    precommits: dict[int, dict[Any, set[int]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(set)))
    precommit_any: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))
    precommit_msgs: dict[tuple[int, Any], list[Precommit]] = field(default_factory=lambda: defaultdict(list))
    round_senders: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))
    msgs: dict[Any, None] = field(default_factory=dict)  # insertion-ordered set, relayed by gossip


def quorum_size(n: int, mutant: str | None) -> int:
    f = (n - 1) // 3
    if mutant == "quorum_2f":
        return 2 * f
    if mutant == "n_eq_3f":
        return n - n // 3
    return (2 * n) // 3 + 1


def fault_bound(n: int, mutant: str | None) -> int:
    return n // 3 if mutant == "n_eq_3f" else (n - 1) // 3


class BftNode(Node):
    def __init__(self, node_id: int, n: int, params: BftParams) -> None:
        super().__init__(node_id, n)
        if params.mutant is not None and params.mutant not in BFT_MUTANTS:
            raise ValueError(f"unknown BFT mutant {params.mutant!r}")
        self.p = params
        self.f = fault_bound(n, params.mutant)
        self.Q = quorum_size(n, params.mutant)
        self.height = 0
        self.round = 0
        self.step = PROPOSE
        self.finished = False
        self.decisions: dict[int, Any] = {}
        self.certificates: dict[int, tuple[Any, ...]] = {}
        self.logs: dict[int, HeightLog] = defaultdict(HeightLog)
        self._catchup_sent: dict[tuple[int, int], int] = {}
        self._reset_height_state()

    def _reset_height_state(self) -> None:
        self.locked_value: Any = None
        self.locked_round = -1
        self.valid_value: Any = None
        self.valid_round = -1
        self._fired: set[tuple[str, int]] = set()

    # --- basic helpers ------------------------------------------------------------
    def proposer(self, h: int, r: int) -> int:
        return (h + r) % self.n

    def get_value(self) -> str:
        return f"v{self.height}.{self.id}"

    def _timeout(self, base: int, r: int) -> int:
        return base + r * self.p.timeout_increment

    def _broadcast(self, msg: Any) -> None:
        self.broadcast(msg, include_self=True)

    def _well_formed(self, m: Any) -> bool:
        if not isinstance(m, (Proposal, Prevote, Precommit)):
            return False
        for x in (m.sender, m.height, m.round):
            if not isinstance(x, int) or isinstance(x, bool):
                return False
        if not (0 <= m.sender < self.n and m.height >= 0 and m.round >= 0):
            return False
        try:
            hash(m.value)
        except TypeError:
            return False
        if isinstance(m, Proposal):
            vr = m.valid_round
            if not isinstance(vr, int) or isinstance(vr, bool) or not -1 <= vr < m.round:
                return False
            if m.sender != self.proposer(m.height, m.round):
                return False
        return True

    # --- lifecycle ------------------------------------------------------------------
    def on_start(self) -> None:
        self.set_timer(self.p.retransmit, "retransmit")
        self._start_round(0)
        self._process()

    def on_restart(self) -> None:
        # All protocol state is persistent; only the timers were lost. Re-arm exactly
        # the timers that were pending at the crash (a timer that had already fired
        # would have changed the step or the round).
        self.set_timer(self.p.retransmit, "retransmit")
        if self.finished:
            return
        h, r = self.height, self.round
        if self.step == PROPOSE and self.proposer(h, r) != self.id:
            self.set_timer(self._timeout(self.p.timeout_propose, r), "propose", (h, r))
        if self.step == PREVOTE and ("tpv", r) in self._fired:
            self.set_timer(self._timeout(self.p.timeout_prevote, r), "prevote", (h, r))
        if ("tpc", r) in self._fired:
            self.set_timer(self._timeout(self.p.timeout_precommit, r), "precommit", (h, r))

    # --- the algorithm --------------------------------------------------------------
    def _start_round(self, r: int) -> None:  # StartRound, lines 11-21
        self.round = r
        self.step = PROPOSE
        h = self.height
        if self.proposer(h, r) == self.id:
            v = self.valid_value if self.valid_value is not None else self.get_value()
            self.note(f"h={h} r={r}: proposer, proposes {_fmt(v)} (vr={self.valid_round})")
            self._broadcast(Proposal(self.id, h, r, v, self.valid_round))
        else:
            self.set_timer(self._timeout(self.p.timeout_propose, r), "propose", (h, r))

    def _prevote(self, v: Any) -> None:
        self.note(f"h={self.height} r={self.round}: prevotes {_fmt(v)}")
        self._broadcast(Prevote(self.id, self.height, self.round, v))
        self.step = PREVOTE

    def _precommit(self, v: Any) -> None:
        self.note(f"h={self.height} r={self.round}: precommits {_fmt(v)}")
        self._broadcast(Precommit(self.id, self.height, self.round, v))
        self.step = PRECOMMIT

    def _lock_allows(self, v: Any, vr: int) -> bool:
        if self.p.mutant == "no_lock":
            return True
        if vr == -1:  # line 23
            return self.locked_round == -1 or self.locked_value == v
        return self.locked_round <= vr or self.locked_value == v  # line 29

    def _decide(self, r: int, v: Any) -> None:  # lines 49-54
        h = self.height
        log = self.logs[h]
        self.decisions[h] = v
        prop = next(p for (val, _), p in log.proposals[r].items() if val == v)
        self.certificates[h] = (prop, *log.precommit_msgs[(r, v)])
        self.note(f"h={h}: DECIDES {_fmt(v)} (round {r})")
        self.emit("decide", height=h, value=v, round=r)
        del self.logs[h]
        self.height += 1
        self._reset_height_state()
        if self.height >= self.p.heights:
            self.finished = True
        else:
            self._start_round(0)

    def _step_once(self) -> bool:
        """Apply the first enabled 'upon' rule; return False if none is enabled."""
        h, r, Q = self.height, self.round, self.Q
        log = self.logs[h]
        # line 49: decide on any round with a proposal and Q precommits for it
        for rr, props in log.proposals.items():
            for v, _vr in props:
                if valid(v) and len(log.precommits[rr].get(v, ())) >= Q:
                    self._decide(rr, v)
                    return True
        # line 55: f+1 messages from a higher round -> skip to that round
        higher = [rr for rr, s in log.round_senders.items() if rr > r and len(s) >= self.f + 1]
        if higher:
            self.note(f"h={h}: skips from round {r} to round {max(higher)} (f+1 messages seen)")
            self._start_round(max(higher))
            return True
        if self.step == PROPOSE:
            for v, vr in log.proposals.get(r, {}):
                if vr == -1:  # line 22
                    self._prevote(v if valid(v) and self._lock_allows(v, -1) else None)
                    return True
                if len(log.prevotes.get(vr, {}).get(v, ())) >= Q:  # line 28 (0 <= vr < r by well-formedness)
                    self._prevote(v if valid(v) and self._lock_allows(v, vr) else None)
                    return True
        if self.step == PREVOTE and len(log.prevote_any.get(r, ())) >= Q and ("tpv", r) not in self._fired:
            self._fired.add(("tpv", r))  # line 34
            self.set_timer(self._timeout(self.p.timeout_prevote, r), "prevote", (h, r))
            return True
        if self.step >= PREVOTE and ("lock", r) not in self._fired:  # line 36
            for v, _vr in log.proposals.get(r, {}):
                if valid(v) and len(log.prevotes.get(r, {}).get(v, ())) >= Q:
                    self._fired.add(("lock", r))
                    if self.step == PREVOTE:
                        self.locked_value, self.locked_round = v, r
                        self.note(f"h={h} r={r}: LOCKS on {_fmt(v)}")
                        self._precommit(v)
                    self.valid_value, self.valid_round = v, r
                    return True
        if self.step == PREVOTE and len(log.prevotes.get(r, {}).get(None, ())) >= Q:  # line 44
            self._precommit(None)
            return True
        if len(log.precommit_any.get(r, ())) >= Q and ("tpc", r) not in self._fired:  # line 47
            self._fired.add(("tpc", r))
            self.set_timer(self._timeout(self.p.timeout_precommit, r), "precommit", (h, r))
            return True
        return False

    def _process(self) -> None:
        while not self.finished and self._step_once():
            pass

    # --- message and timer handling ----------------------------------------------
    def _store(self, m: Any) -> None:
        log = self.logs[m.height]
        if m in log.msgs:
            return
        log.msgs[m] = None
        log.round_senders[m.round].add(m.sender)
        if isinstance(m, Proposal):
            log.proposals[m.round].setdefault((m.value, m.valid_round), m)
        elif isinstance(m, Prevote):
            log.prevotes[m.round][m.value].add(m.sender)
            log.prevote_any[m.round].add(m.sender)
        else:
            if m.sender not in log.precommits[m.round][m.value]:
                log.precommit_msgs[(m.round, m.value)].append(m)
            log.precommits[m.round][m.value].add(m.sender)
            log.precommit_any[m.round].add(m.sender)

    def _catch_up(self, src: int, h: int) -> None:
        cert = self.certificates.get(h)
        last = self._catchup_sent.get((src, h))
        if cert is None or src == self.id or (last is not None and self.sim.now - last < self.p.retransmit):
            return
        self._catchup_sent[(src, h)] = self.sim.now
        for m in cert:
            self.send(src, m)

    def on_message(self, src: int, msg: Any) -> None:
        if not self._well_formed(msg):
            return
        if msg.height < self.height:
            if msg.sender == src:
                self._catch_up(src, msg.height)
            return
        if msg.height >= self.p.heights:
            return
        self._store(msg)
        if msg.height == self.height:
            self._process()

    def on_timer(self, name: str, data: Any) -> None:
        if name == "retransmit":
            if not self.finished:
                for m in list(self.logs[self.height].msgs):
                    self.broadcast(m, include_self=False)
            self.set_timer(self.p.retransmit, "retransmit")
            return
        if self.finished or data != (self.height, self.round):
            return
        if name == "propose" and self.step == PROPOSE:  # line 57
            self._prevote(None)
        elif name == "prevote" and self.step == PREVOTE:  # line 61
            self._precommit(None)
        elif name == "precommit":  # line 65
            self._start_round(self.round + 1)
        self._process()

    def done(self) -> bool:
        return self.finished
