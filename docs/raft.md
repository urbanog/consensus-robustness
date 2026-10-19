# The Raft implementation

Implementation: `src/consensus_robustness/protocols/raft.py`. It follows Figure 2 of Ongaro &
Ousterhout, *In Search of an Understandable Consensus Algorithm* (USENIX ATC 2014). Leader election
and log replication are implemented. Membership changes, log compaction and client sessions are not.

## Model

* `n = 2f + 1` servers (any `n ≥ 1` is accepted); majority `⌊n/2⌋ + 1`. Crash faults only: nodes
  crash and restart, and messages may be lost, duplicated, reordered or delayed.
* **Persistent state** (survives a crash): `currentTerm`, `votedFor`, `log[]`.
  **Volatile state** (lost on a crash): role, `commitIndex`, `lastApplied`, `nextIndex[]`,
  `matchIndex[]`, received votes. A restarted node re-applies its log from index 1 once it learns
  the commit index.
* Log entries are `(term, cmd)`. The client workload is a fixed tuple of commands (`c1, c2, c3`).
  At each heartbeat, with probability 0.5, the leader appends the first command not yet in its log.
  This is equivalent to a client that retries until it succeeds.
* Timing: heartbeat 20, election timeout drawn uniformly from `[100, 200)`, Δ = 10 (simulated units).

## Rules (Figure 2)

**All servers.** If an RPC request or response carries `term T > currentTerm`: set
`currentTerm = T`, `votedFor = null`, and become a follower.

**Followers.** Answer RPCs. If the election timeout elapses without an AppendEntries from the
current leader and without granting a vote, become a candidate.

**Candidates.** Increment `currentTerm`, vote for yourself, reset the election timer, and send
`RequestVote(term, candidateId, lastLogIndex, lastLogTerm)` to all. A candidate counts a granted
vote only if the reply's term equals `currentTerm`. On votes from a majority it becomes leader. On
an AppendEntries of the current term it becomes a follower. If the timer elapses, it starts a new
election.

**RequestVote receiver.** Grant the vote iff `term = currentTerm`, `votedFor ∈ {null, candidate}`,
and the candidate's log is *at least as up-to-date* as the receiver's:
`lastLogTerm > myLastTerm`, or `lastLogTerm = myLastTerm` and `lastLogIndex ≥ myLastIndex`.
Granting a vote resets the election timer.

**Leaders.** On election, append a no-op entry of the current term (§8 of the paper; it lets entries
of earlier terms commit promptly). Every heartbeat, send each follower
`AppendEntries(term, leaderId, prevLogIndex = nextIndex − 1, prevLogTerm, entries[nextIndex..], leaderCommit)`,
at most 8 entries per message. On success, set `matchIndex = max(matchIndex, reply.matchIndex)` and
`nextIndex = max(nextIndex, matchIndex + 1)`. On failure, decrease `nextIndex`, using the follower's
log length as a hint, and retry. Commit rule: if there is an `N > commitIndex` such that a majority
has `matchIndex ≥ N` **and `log[N].term = currentTerm`**, set `commitIndex = N`.

**AppendEntries receiver.** Reply false if `term < currentTerm`, or if the log has no entry at
`prevLogIndex` with term `prevLogTerm`. Otherwise, for each new entry: if an existing entry
conflicts (same index, different term), delete it and everything after it; append the entries not
already present. Never truncate on a matching entry, since a delayed duplicate AppendEntries could
otherwise erase newer entries. Set `commitIndex = min(leaderCommit, prevLogIndex + len(entries))`
if that is larger than the current value. Reply with `matchIndex = prevLogIndex + len(entries)`.

**Applying.** While `lastApplied < commitIndex`, apply `log[++lastApplied]`.

## Properties checked online (`checking/properties.py`)

| property | statement | how it is checked |
|---|---|---|
| Election safety | at most one leader per term | map `term → leader` on every election |
| Log matching | equal `(index, term)` in two logs ⇒ equal prefixes up to that index | after every simulation step in which some log changed, compare that log with all others |
| Leader completeness | an entry committed in term `t` is in the log of every leader of a term `> t` | checked at every election against all committed entries, and at every commit against leaders of later terms that were already elected |
| State-machine safety | no two nodes apply different entries at the same index | map `index → entry` on every apply and commit |
| Validity | only client commands or no-ops are applied | on every apply |
| Liveness | with network faults ending at GST and at most `f` nodes down at `T* = max(GST, last crash/restart)`, all live nodes apply all commands by `T* + 2000` | after the run |

## Fault types specific to Raft

Besides timed crashes, the generator can schedule **leader-triggered crashes**: `LeaderCrash(k,
delay, down_for)` crashes the `k`-th node to win an election `delay` units after it wins.
Uncommitted entries from several terms then pile up on different nodes. The Figure 8 bug
(`commit_old_term` mutant) needs this kind of state, and timed crashes almost never reach it (see
`findings.md`).
