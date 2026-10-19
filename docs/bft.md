# The BFT protocol (Tendermint-style)

Implementation: `src/consensus_robustness/protocols/bft.py`. It follows Algorithm 1 of
Buchman, Kwon & Milosevic, *The latest gossip on BFT consensus* (arXiv:1807.04938, 2018). The paper's
line numbers appear as comments in the code. The protocol belongs to the PBFT family (Castro–Liskov
1999): a proposer and two voting phases with quorums of `2f + 1`. Tendermint's locking rules let a
round change happen without PBFT's separate view-change sub-protocol.

## Model

* `n` validators, ids `0..n−1`; `f = ⌊(n − 1)/3⌋`; quorum `Q = ⌊2n/3⌋ + 1` (that is, `2f + 1` when
  `n = 3f + 1`).
* Authenticated messages: every message carries `sender`. The simulator delivers a relayed message
  only if its author really sent it (a model of digital signatures). Byzantine nodes may send any
  message under their own name, including conflicting ones (equivocation).
* Proposer of height `h`, round `r`: `proposer(h, r) = (h + r) mod n`.
* Values are strings; `valid(v)` holds iff `v` matches `[a-z][a-z0-9.-]*`. Honest proposers propose
  `v{h}.{id}`. Byzantine nodes may propose invalid or unhashable junk, which is never decided.
* The run consists of `heights` consecutive instances (default 3). Node state is persistent (a
  write-ahead log), so a node that crashes and restarts keeps its votes and locks. Only its timers
  are lost, and on restart it re-arms the ones that were pending.

## Messages

| message | fields | meaning |
|---|---|---|
| `PROPOSAL` | `h, r, v, vr` | proposer's value for round `r`; `vr` is its `validRound` (`−1` if none) |
| `PREVOTE` | `h, r, v or nil` | first vote |
| `PRECOMMIT` | `h, r, v or nil` | second vote |

A message is *well-formed* if its fields have the right types, `0 ≤ sender < n`, `h, r ≥ 0`, the value
is hashable, and, for proposals, `−1 ≤ vr < r` and `sender = proposer(h, r)`. Anything else is
dropped. For each `(type, h, r, sender)` the node counts at most one vote per value. Votes are counted
by distinct senders, so an equivocator's two prevotes in one round can count for two different
values, but never twice for the same value.

## State per node (reset at every new height, except `h_p`)

`h_p` (height), `round_p`, `step_p ∈ {propose, prevote, precommit}`, `lockedValue`, `lockedRound`
(−1), `validValue`, `validRound` (−1).

## Rules ("upon" clauses, applied until none is enabled)

**StartRound(r)** (lines 11–21). Set `round_p = r` and `step_p = propose`. If `p` is the proposer,
broadcast `PROPOSAL(h_p, r, validValue if set else getValue(), validRound)`. Otherwise schedule
`OnTimeoutPropose(h_p, r)` after `timeoutPropose(r)`.

1. **Line 22.** On `PROPOSAL(h_p, round_p, v, −1)` from the proposer while `step = propose`:
   prevote `v` if `valid(v)` and (`lockedRound = −1` or `lockedValue = v`), else prevote nil. Then
   `step = prevote`.
2. **Line 28.** On `PROPOSAL(h_p, round_p, v, vr)` with `0 ≤ vr < round_p` and `Q` `PREVOTE(h_p, vr, v)`
   while `step = propose`: prevote `v` if `valid(v)` and (`lockedRound ≤ vr` or `lockedValue = v`),
   else nil. Then `step = prevote`.
3. **Line 34.** On `Q` prevotes (any values) for `(h_p, round_p)` while `step = prevote`, the first
   time: schedule `OnTimeoutPrevote`.
4. **Line 36.** On the proposal for `v` with `valid(v)` and `Q` `PREVOTE(h_p, round_p, v)` while
   `step ≥ prevote`, the first time in this round: if `step = prevote`, set `lockedValue = v`,
   `lockedRound = round_p`, precommit `v`, and set `step = precommit`. In both cases set
   `validValue = v` and `validRound = round_p`.
5. **Line 44.** On `Q` `PREVOTE(h_p, round_p, nil)` while `step = prevote`: precommit nil, and set
   `step = precommit`.
6. **Line 47.** On `Q` precommits (any values) for `(h_p, round_p)`, the first time: schedule
   `OnTimeoutPrecommit`.
7. **Line 49.** On a proposal for `v` in **any** round `r` of height `h_p` together with `Q`
   `PRECOMMIT(h_p, r, v)` and `valid(v)`: **decide `v`**, increment `h_p`, reset the per-height
   state, and call `StartRound(0)`.
8. **Line 55.** On messages of `f + 1` distinct senders for `(h_p, r)` with `r > round_p`: call
   `StartRound(r)` (jump to the highest such round).

Timeouts (lines 57–66). `OnTimeoutPropose(h, r)`: if still in `(h, r, propose)`, prevote nil.
`OnTimeoutPrevote(h, r)`: if still in `(h, r, prevote)`, precommit nil.
`OnTimeoutPrecommit(h, r)`: if still at `(h, r)`, call `StartRound(r + 1)`. Each timeout is
`base + r · increment`, with base 30 and increment 15 simulated units. Because the timeout grows with
`r`, it eventually exceeds any fixed `Δ`. This is what makes the protocol live after GST.

## Gossip (what the paper assumes, and what is implemented)

The paper assumes a gossip layer: a message received by one correct process is eventually received
by every correct process. Liveness, not safety, depends on it. A typical failure without it: a
Byzantine node sends its prevote to one honest node only; that node locks, and the others never see
the polka that would let them follow its proposal later. The implementation provides:

* **Relay.** Every `retransmit` = 60 units, a node re-broadcasts every well-formed message it has
  stored for its current height, its own and other nodes' (signatures make relaying safe). This also
  repairs message loss.
* **Catch-up.** When a node receives a message for a height it has already decided, it sends the
  sender that height's *decision certificate* (the proposal and `Q` matching precommits), at most
  once per retransmit period. The lagging node then decides through rule 7.

## Byzantine behaviours (`protocols/byzantine.py`)

* `silent`: sends nothing.
* `equivocate`: all equivocators form one coalition that splits the honest nodes into group A
  (`Scenario.split`) and group B. In rounds led by a coalition member, the coalition proposes value
  `a` to A and `b` to B, and prevotes and precommits `a` towards A and `b` towards B. In rounds led
  by an honest proposer with value `v`, it votes `v` towards A and nil towards B. It ignores locks
  and timeouts and re-sends periodically.
* `garbage`: random messages to random peers: votes for random heights, rounds and values, malformed
  fields, invalid and unhashable values, forged sender ids (rejected by the signature check), and
  non-protocol junk.

## Properties checked (`checking/properties.py`)

* **Agreement**: no two honest nodes decide different values at the same height.
* **Validity**: every honest decision satisfies `valid(v)` (external validity).
* **Integrity**: an honest node decides each height at most once.
* **Liveness** (checked after the run): if network faults stop at GST and at most `f` nodes are
  Byzantine or down at the stabilisation time `T* = max(GST, last crash/restart)`, then every live
  honest node decides all heights by `T* + 2000`.

Theorem 4 in `theory.md` proves agreement for this rule set.
