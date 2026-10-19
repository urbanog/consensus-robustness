"""Raft leader election and log replication (Ongaro & Ousterhout 2014, Figure 2).

Crash-fault tolerant, ``n = 2f + 1``. Persistent state (``current_term``,
``voted_for``, ``log``) survives a crash; volatile state (role, commit index,
leader bookkeeping) is lost. The client workload is a fixed list of commands
that every replica knows from the start; a leader appends the next command that
is not yet in its log at a random heartbeat (probability ``client_rate``), and a leader appends a no-op entry
of its own term when it is elected (§8 of the paper). Commands are applied idempotently, so
re-proposing a command after a leader change is harmless.

Mutants (deliberately broken variants used to test the test-harness):

* ``no_log_check``   - voters skip the "candidate's log is at least as
  up-to-date" check (§5.4.1).
* ``stale_votes``    - candidates count vote replies from any term.
* ``small_quorum``   - majority computed as ``n // 2`` instead of ``n // 2 + 1``.
* ``commit_old_term`` - leaders commit entries of earlier terms by counting
  replicas (the Figure 8 bug, §5.4.2). As in Figure 2 of the paper, this
  variant also omits the no-op entry at the start of a term (§8); with the
  no-op, the old entries always travel together with a current-term entry and
  the bug is masked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..sim.core import Node

RAFT_MUTANTS = ("no_log_check", "stale_votes", "small_quorum", "commit_old_term")
NOOP = "noop"


@dataclass(frozen=True, slots=True)
class Entry:
    term: int
    cmd: str


@dataclass(frozen=True, slots=True)
class RequestVote:
    term: int
    candidate: int
    last_log_index: int
    last_log_term: int


@dataclass(frozen=True, slots=True)
class VoteReply:
    term: int
    voter: int
    granted: bool


@dataclass(frozen=True, slots=True)
class AppendEntries:
    term: int
    leader: int
    prev_index: int
    prev_term: int
    entries: tuple[Entry, ...]
    leader_commit: int


@dataclass(frozen=True, slots=True)
class AppendReply:
    term: int
    follower: int
    success: bool
    match_index: int  # on success: prev_index + len(entries); on failure: follower's log length


@dataclass(frozen=True, slots=True)
class RaftParams:
    commands: tuple[str, ...] = ("c1", "c2", "c3")
    heartbeat: int = 20
    election_timeout: int = 100  # randomised in [T, 2T)
    max_batch: int = 8
    client_rate: float = 0.5  # probability that a client command arrives at the leader per heartbeat
    mutant: str | None = None


class RaftNode(Node):
    def __init__(self, node_id: int, n: int, params: RaftParams) -> None:
        super().__init__(node_id, n)
        if params.mutant is not None and params.mutant not in RAFT_MUTANTS:
            raise ValueError(f"unknown Raft mutant {params.mutant!r}")
        self.p = params
        # persistent state
        self.current_term = 0
        self.voted_for: int | None = None
        self.log: list[Entry] = []
        self.log_version = 0
        # volatile state
        self._reset_volatile()

    def _reset_volatile(self) -> None:
        self.role = "follower"
        self.commit_index = 0
        self.last_applied = 0
        self.votes: set[int] = set()
        self.next_index: dict[int, int] = {}
        self.match_index: dict[int, int] = {}
        self.applied_cmds: set[str] = set()

    # --- helpers --------------------------------------------------------------------
    @property
    def quorum(self) -> int:
        return self.n // 2 if self.p.mutant == "small_quorum" else self.n // 2 + 1

    def last_index(self) -> int:
        return len(self.log)

    def last_term(self) -> int:
        return self.log[-1].term if self.log else 0

    def term_at(self, index: int) -> int:
        return self.log[index - 1].term if index >= 1 else 0

    def _reset_election_timer(self) -> None:
        t = self.p.election_timeout
        self.set_timer(self.rng.randrange(t, 2 * t), "election")

    def _step_down(self, term: int) -> None:
        if term > self.current_term:
            self.current_term = term
            self.voted_for = None
        if self.role != "follower":
            self.note(f"steps down to follower in term {self.current_term}")
        self.role = "follower"

    # --- lifecycle --------------------------------------------------------------------
    def on_start(self) -> None:
        self._reset_election_timer()

    def on_restart(self) -> None:
        self._reset_volatile()
        self._reset_election_timer()

    def on_timer(self, name: str, data: Any) -> None:
        if name == "election":
            if self.role == "leader":
                self._reset_election_timer()  # keep the timer armed for when we step down
            else:
                self._start_election()
        elif name == "heartbeat" and self.role == "leader":
            self._leader_tick()

    # --- election -----------------------------------------------------------------------
    def _start_election(self) -> None:
        self.current_term += 1
        self.role = "candidate"
        self.voted_for = self.id
        self.votes = {self.id}
        self.note(f"starts election for term {self.current_term}")
        self._reset_election_timer()
        if len(self.votes) >= self.quorum:
            self._become_leader()
            return
        rv = RequestVote(self.current_term, self.id, self.last_index(), self.last_term())
        self.broadcast(rv, include_self=False)

    def _log_ok(self, m: RequestVote) -> bool:
        if self.p.mutant == "no_log_check":
            return True
        return m.last_log_term > self.last_term() or (
            m.last_log_term == self.last_term() and m.last_log_index >= self.last_index()
        )

    def _on_request_vote(self, m: RequestVote) -> None:
        if m.term > self.current_term:
            self._step_down(m.term)
        grant = m.term == self.current_term and self.voted_for in (None, m.candidate) and self._log_ok(m)
        if grant:
            self.voted_for = m.candidate
            self._reset_election_timer()
            self.note(f"votes for n{m.candidate} in term {m.term}")
        self.send(m.candidate, VoteReply(self.current_term, self.id, grant))

    def _on_vote_reply(self, m: VoteReply) -> None:
        if m.term > self.current_term:
            self._step_down(m.term)
            return
        same_term = m.term == self.current_term or self.p.mutant == "stale_votes"
        if self.role == "candidate" and same_term and m.granted:
            self.votes.add(m.voter)
            if len(self.votes) >= self.quorum:
                self._become_leader()

    def _become_leader(self) -> None:
        self.role = "leader"
        self.note(f"becomes LEADER of term {self.current_term} (log length {len(self.log)})")
        self.emit("leader", term=self.current_term, log=tuple(self.log))
        self.next_index = {p: self.last_index() + 1 for p in self.peers}
        self.match_index = {p: 0 for p in self.peers}
        if self.p.mutant != "commit_old_term":
            self._append(Entry(self.current_term, NOOP))
        self._leader_tick()

    # --- replication ----------------------------------------------------------------
    def _append(self, e: Entry) -> None:
        self.log.append(e)
        self.log_version += 1

    def _leader_tick(self) -> None:
        """Heartbeat: replicate the current log, then (with probability ``client_rate``)
        accept the next client command; it is shipped with the next heartbeat.
        """
        for peer in self.peers:
            self._send_append(peer)
        self._advance_commit()
        present = {e.cmd for e in self.log}
        missing = [cmd for cmd in self.p.commands if cmd not in present]
        if missing and self.rng.random() < self.p.client_rate:
            self._append(Entry(self.current_term, missing[0]))
        self.set_timer(self.p.heartbeat, "heartbeat")

    def _send_append(self, peer: int) -> None:
        ni = self.next_index[peer]
        entries = tuple(self.log[ni - 1 : ni - 1 + self.p.max_batch])
        self.send(
            peer,
            AppendEntries(self.current_term, self.id, ni - 1, self.term_at(ni - 1), entries, self.commit_index),
        )

    def _on_append(self, m: AppendEntries) -> None:
        if m.term < self.current_term:
            self.send(m.leader, AppendReply(self.current_term, self.id, False, self.last_index()))
            return
        if m.term > self.current_term or self.role != "follower":
            self._step_down(m.term)
        self._reset_election_timer()
        if m.prev_index > self.last_index() or self.term_at(m.prev_index) != m.prev_term:
            self.send(m.leader, AppendReply(self.current_term, self.id, False, self.last_index()))
            return
        for k, e in enumerate(m.entries):
            idx = m.prev_index + 1 + k
            if idx <= self.last_index():
                if self.log[idx - 1].term == e.term:
                    continue
                del self.log[idx - 1 :]  # conflicting suffix: delete it (Fig. 2, rule 3)
            self.log.append(e)
            self.log_version += 1
        match = m.prev_index + len(m.entries)
        new_commit = min(m.leader_commit, match)
        if new_commit > self.commit_index:
            self.commit_index = new_commit
            self._apply()
        self.send(m.leader, AppendReply(self.current_term, self.id, True, match))

    def _on_append_reply(self, m: AppendReply) -> None:
        if m.term > self.current_term:
            self._step_down(m.term)
            return
        if self.role != "leader" or m.term != self.current_term:
            return
        f = m.follower
        if m.success:
            self.match_index[f] = max(self.match_index[f], m.match_index)
            self.next_index[f] = max(self.next_index[f], m.match_index + 1)
            self._advance_commit()
        else:
            self.next_index[f] = max(1, min(self.next_index[f] - 1, m.match_index + 1))
            self._send_append(f)

    def _advance_commit(self) -> None:
        for idx in range(self.last_index(), self.commit_index, -1):
            if self.log[idx - 1].term != self.current_term and self.p.mutant != "commit_old_term":
                continue
            replicas = 1 + sum(1 for p in self.peers if self.match_index[p] >= idx)
            if replicas >= self.quorum:
                for j in range(self.commit_index + 1, idx + 1):
                    self.emit("commit", index=j, entry=self.log[j - 1], term=self.current_term)
                self.commit_index = idx
                self._apply()
                return

    def _apply(self) -> None:
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            e = self.log[self.last_applied - 1]
            self.applied_cmds.add(e.cmd)
            self.emit("apply", index=self.last_applied, entry=e)

    # --- dispatch -----------------------------------------------------------------------
    def on_message(self, src: int, msg: Any) -> None:
        if isinstance(msg, RequestVote):
            self._on_request_vote(msg)
        elif isinstance(msg, VoteReply):
            self._on_vote_reply(msg)
        elif isinstance(msg, AppendEntries):
            self._on_append(msg)
        elif isinstance(msg, AppendReply):
            self._on_append_reply(msg)

    def done(self) -> bool:
        return set(self.p.commands) <= self.applied_cmds
