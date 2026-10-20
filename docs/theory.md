# The mathematics behind the tests

This note states the results the test harness relies on and gives short proofs. Notation: `n` nodes,
at most `f` of them faulty; a *quorum* is a set of `Q` nodes from which a node has received matching
messages. Message senders are authenticated (modelled by the simulator's signature check), so a
Byzantine node can lie about its own messages but cannot impersonate an honest node.

## 1. Quorum intersection

**Lemma 1 (counting).** Two subsets `A, B` of an `n`-element set satisfy `|A ∩ B| ≥ |A| + |B| − n`.

*Proof.* `|A ∪ B| ≤ n` and `|A ∪ B| = |A| + |B| − |A ∩ B|`. ∎

**Corollary 2 (BFT quorums).** Let `n = 3f + 1` and `Q = 2f + 1`. Any two quorums intersect in at
least `2(2f + 1) − (3f + 1) = f + 1` nodes. Since at most `f` nodes are Byzantine, **every two
quorums share at least one honest node**.

More generally, for any `n ≥ 1` the implementation uses `f = ⌊(n − 1)/3⌋` and `Q = ⌊2n/3⌋ + 1`.
Then `2Q − n ≥ f + 1` (check the three residues: `n = 3f+1, 3f+2, 3f+3` give `Q = 2f+1, 2f+2, 2f+3`
and `2Q − n = f+1, f+2, f+3`), and `Q ≤ n − f`, so a quorum can be formed from honest nodes alone.
These are the two requirements: **safety needs `2Q − n ≥ f + 1`, liveness needs `Q ≤ n − f`.**
Together they force `2(n − f) − n ≥ f + 1`, i.e. `n ≥ 3f + 1`, for this style of protocol.
Theorem 5 below shows that the bound holds for *every* protocol.

**Corollary 3 (crash quorums, Raft).** With `n = 2f + 1` and majorities `Q = f + 1`,
`2Q − n = 1`: any two majorities share a node. Crashed nodes do not lie, so one common node suffices.

Two mutants show that the bounds are tight for the implemented protocol (see `findings.md`):
`quorum_2f` uses `Q = 2f` with `n = 3f + 1` (`2Q − n = f − 1`); for `n = 4` two quorums of size 2 can
be disjoint. `n_eq_3f` uses `n = 3f`, `Q = n − f = 2f`, giving an intersection of `f` nodes, which may
all be Byzantine.

## 2. Why intersection gives agreement in the Tendermint-style protocol

Within one height, honest node `p` *locks* `v` in round `r` when it receives the proposal for `v` and
`Q` prevotes for `v` in round `r` while in the prevote step; it then precommits `v`. A *polka* for
`(r, w)` is a set of `Q` prevotes for `w` in round `r` (from distinct senders).

**Theorem 4 (agreement).** With `n ≥ 3f + 1`, `Q = ⌊2n/3⌋ + 1` and at most `f` Byzantine nodes, no
two honest nodes decide different values at the same height, in every execution (no timing
assumption is used).

*Proof.* Suppose an honest node decides `v` in round `r`. Then it received `Q` precommits for `v` in
round `r`, of which at least `Q − f ≥ f + 1` are from honest nodes; call this set `L`. Every
`p ∈ L` locked `v` in round `r`, so a polka for `(r, v)` exists.

*Claim:* for every round `r' ≥ r` there is no polka for `(r', w)` with `w ≠ v`, and every `p ∈ L`
has `lockedValue = v` and `lockedRound ≥ r` from round `r` on.

Induction on `r'`. For `r' = r`: a polka for `(r, w)` and the polka for `(r, v)` share an honest
node (Corollary 2), which would have prevoted twice in round `r` — honest nodes prevote at most once
per round. For `r' > r`, assume the claim for all rounds in `[r, r')`. An honest `p ∈ L` prevotes `w`
in round `r'` only through one of two rules:

* line 22 (proposal with `validRound = −1`): requires `lockedRound = −1` or `lockedValue = w`;
  neither holds;
* line 28 (proposal with `validRound = vr`, `vr < r'`, and a polka for `(vr, w)`): requires
  `lockedRound ≤ vr` or `lockedValue = w`. As `lockedRound ≥ r` this needs `r ≤ vr < r'` and a
  polka for `(vr, w)`, which does not exist by the induction hypothesis.

So the `≥ f + 1` nodes of `L` do not prevote `w` in round `r'`, and at most `n − (f + 1)` prevotes
for `w` exist in that round. Writing `n = 3f + k` with `k ∈ {1, 2, 3}` we have `Q = 2f + k` and
`n − f − 1 = Q − 1 < Q`, so there is no polka for `(r', w)`. Consequently any lock that a node of
`L` takes in round `r'` is on `v`, and the claim holds for `r'`.

Finally, deciding `w ≠ v` in a round `r' ≥ r` needs `Q` precommits for `w` in round `r'`, hence at
least `Q − f ≥ 1` honest precommit for `w`, hence (an honest node precommits a value only after
seeing a polka for it) a polka for `(r', w)` — impossible by the claim. If instead the decision on
`w` came in a round `r' < r`, apply the same argument to that earlier decision. ∎

The induction is exactly what the `no_lock` mutant breaks: without locks, a node that precommitted
`v` in round 0 may prevote a new value in round 1 and the claim fails (see `findings.md`).

## 3. Why `n ≥ 3f + 1` is necessary (Dwork–Lynch–Stockmeyer 1988)

The model of [DLS88] is *partial synchrony*: there are a bound `Δ` and a global stabilisation time
`GST`, both unknown to the protocol; messages sent after `GST` arrive within `Δ`; before `GST`
messages are delayed arbitrarily (in one variant they may also be lost). Consensus requires

* **agreement**: no two honest nodes decide differently;
* **validity** (unanimity): if all honest nodes start with the same input `v`, the decision is `v`;
* **termination**: every honest node eventually decides.

**Theorem 5 [DLS88].** In the partially synchronous model, consensus with `f ≥ 1` Byzantine faults
is solvable if and only if `n ≥ 3f + 1`. The lower bound holds even if messages are signed.

*Proof of necessity (partition argument).* Suppose a protocol solves consensus with `n ≤ 3f`.
Split the nodes into three disjoint groups `P, R, S`, each of size at most `f`, with `P` and `R`
non-empty (possible whenever `2 ≤ n ≤ 3f`). Consider three executions.

1. `E₀`: the nodes of `R` are faulty and silent; `P` and `S` are honest with input 0; `GST = 0`.
   Only `|R| ≤ f` nodes are faulty, so by termination and validity every node of `P` decides 0, by
   some time `T₀`.
2. `E₁`: the nodes of `P` are faulty and silent; `R` and `S` are honest with input 1; `GST = 0`.
   Every node of `R` decides 1, by some time `T₁`.
3. `E₂`: the nodes of `S` are Byzantine; `P` is honest with input 0 and `R` is honest with input 1;
   `GST > max(T₀, T₁)`. The adversary delays every message between `P` and `R` until after `GST`,
   delivers messages between `P` and `S` with the same delays as in `E₀`, and messages between `R`
   and `S` with the same delays as in `E₁` (before `GST` it may choose any delays). The Byzantine group `S` runs two copies of the honest protocol: towards `P` it behaves
   exactly as `S` behaved in `E₀` (input 0), towards `R` exactly as in `E₁` (input 1). Signatures do
   not help: `S` signs both personalities with its own keys, and `P` and `R` cannot compare notes
   before `GST`.

Up to time `T₀`, the nodes of `P` receive in `E₂` exactly the messages they received in `E₀`: the
same messages from `S`, and nothing from `R` (silent in `E₀`, delayed in `E₂`). The protocol is
deterministic, so `P` decides 0 in `E₂`. Symmetrically, `R` decides 1 in `E₂`. Both groups are
honest, so agreement fails. ∎

| execution | faulty | honest inputs | `P` sees | `R` sees |
|---|---|---|---|---|
| `E₀` | `R` (silent) | `P, S`: 0 | `S` with input 0; nothing from `R` | — |
| `E₁` | `P` (silent) | `R, S`: 1 | — | `S` with input 1; nothing from `P` |
| `E₂` | `S` (Byzantine) | `P`: 0, `R`: 1 | identical to `E₀` up to `T₀` | identical to `E₁` up to `T₁` |

The proof needs `|R| ≤ f` (so `E₀` is admissible), `|P| ≤ f` (for `E₁`) and `|S| ≤ f` (for `E₂`),
which is possible exactly when `n ≤ 3f`. The theorem needs a non-trivial validity condition such as
unanimity: a protocol that ignores its inputs and always decides a fixed value satisfies agreement
and termination trivially. (The harness itself checks *external validity*, `valid(v)` for every
decided value [Cachin et al. 2001], which suits a blockchain-style protocol where values come from
proposers. The impossibility theorem is stated for unanimity.)

In this repository the `n_eq_3f` mutant shows the dichotomy concretely: with `Q = n − f = 2f` the
protocol stays live with `f` silent nodes but loses agreement under equivocation; with
`Q = 2f + 1 > n − f` it would keep agreement but stall as soon as `f` nodes are silent.

## 4. FLP impossibility and how GST sidesteps it

**Theorem 6 (Fischer–Lynch–Paterson 1985).** No *deterministic* protocol solves consensus in an
asynchronous message-passing system — reliable channels with finite but unbounded delays, no
clocks — if even **one** process may crash. Precisely: let a deterministic protocol for `n ≥ 2`
processes with binary inputs satisfy

* **agreement**: no reachable configuration contains two different decision values, and
* **non-triviality**: both 0 and 1 are the decision value of some reachable configuration (a much
  weaker requirement than unanimity validity).

Then some *admissible* run — at most one process crashes, every non-faulty process takes infinitely
many steps, and every message sent to a non-faulty process is eventually delivered — is not a
deciding run: **no process ever decides**. So the protocol can guarantee agreement and validity, but
not termination. The theorem is strong in three ways: termination is weakened to "some process
decides", validity to non-triviality, and the run constructed in the proof has no crash at all (the
mere *possibility* of one crash is what forces indecision).

*Idea of the proof.* Call a configuration *bivalent* if both decisions are still reachable from it.
(i) Some initial configuration is bivalent (change inputs one process at a time; two adjacent
univalent configurations of different valence would be distinguished only by one process, which
might crash). (ii) From any bivalent configuration and any pending message `m`, some schedule that
delivers `m` last leads to another bivalent configuration (a case analysis on which process takes
the step). Iterating (ii), delivering every message eventually, yields an admissible infinite run in
which no process ever decides. ∎

**How partial synchrony sidesteps FLP.** [DLS88] keeps the asynchronous adversary for *safety* —
agreement and validity must hold in every run, whatever the delays — but demands *termination* only
in runs where `GST` eventually occurs. The non-terminating run constructed by FLP needs to delay some
message for an arbitrarily long time; after `GST` every message arrives within `Δ`, so that run is
not admissible. Protocols exploit this with **timeouts that grow with the round number**
(`timeout(r) = base + r · increment` here): from some round on, the timeout exceeds the actual
message delay, and a round with an honest proposer succeeds. Nothing contradicts FLP: in runs where
`GST` never comes, these protocols may indeed run forever, and the test
`test_raft_long_pre_gst_delays_prevent_elections_until_gst` shows the effect in miniature — while
delays exceed the election timeout, Raft cannot elect a leader; once delays are bounded, it does.

Other ways around FLP: randomisation (Ben-Or 1983 — termination with probability 1) and failure
detectors (Chandra–Toueg 1996).

## 5. Raft's safety argument (Ongaro–Ousterhout 2014, §5.4)

* **Election safety** (at most one leader per term): a node votes at most once per term (persisted
  `votedFor`), and two majorities intersect (Corollary 3). Mutants `stale_votes` and `small_quorum`
  break the "once per term" and the "intersect" halves respectively.
* **Log matching**: a leader creates at most one entry per index in its term and never rewrites its
  own log; a follower accepts entries only if its entry at `prevLogIndex` has term `prevLogTerm`;
  by induction, equal `(index, term)` implies equal prefixes.
* **Leader completeness**: if an entry is committed in term `T`, every leader of a term `U > T`
  stores it. The proof is spelled out below. Mutant `no_log_check` removes exactly the up-to-date
  check it relies on.
* **Commit rule (§5.4.2, Figure 8)**: a leader may only count replicas for entries *of its current
  term*; earlier entries are committed indirectly. Counting replicas of an old-term entry is unsafe:
  that entry can be on a majority and still be overwritten by a later leader whose last entry has a
  newer term. Mutant `commit_old_term` exhibits this.
* **State-machine safety** follows: if a node applies entry `e` at index `i`, every later leader
  holds `e` at `i`, so no other entry is ever committed at `i`.

**Theorem 7 (leader completeness, [OO14] §5.4.3).** If the leader of term `T` commits an entry `e`
at index `i`, then the leader of every term `U > T` has `e` at index `i` in its log.

An entry is identified by its `(index, term)`; "has `e`" means having an entry with that index and
term. By the commit rule, the leader of term `T` commits `e` directly only if `e` has term `T` and
the leader has learned that a majority of nodes store `e` (each acknowledged an `AppendEntries` of
term `T` containing it). Entries committed indirectly precede such an `e` in the leader's log and,
by log matching, are covered by the same statement. Leaders never delete or overwrite entries in
their own logs, and a follower deletes an entry only when it conflicts with one sent by a leader
(same index, different term; Figure 2, rule 3).

*Proof.* Suppose the claim fails and let `U > T` be the **smallest** term whose leader `L_U` does not
have `e`. Since `L_U` never removes entries, it already lacked `e` when it was elected.

1. *A common node.* A majority stored `e` in term `T`, and `L_U` was elected by the votes of a
   majority in term `U`. Two majorities intersect (Corollary 3), so some node `v` did both. Its vote
   was cast before `L_U` became leader, hence before any `AppendEntries` of term `U` or higher
   reached `v` (one of term above `U` would have raised `v`'s term past `U` and prevented the vote).
2. *`v` stored `e` before voting.* If `v` had voted in term `U` first, its `currentTerm` would have
   been at least `U > T`, and it would have rejected the `AppendEntries` of term `T` that carried
   `e`. So `v` stored `e` first.
3. *`v` still had `e` when it voted.* After storing `e`, `v` rejects messages of terms below `T`.
   Its log can therefore change only through `AppendEntries` from the leaders of terms `t` with
   `T ≤ t < U`. Each of them has `e` at `i`: the leader of `T` by hypothesis, the others by the
   minimality of `U`. By log matching, each such leader's log agrees with `v`'s log at every index
   `≤ i` (both contain `e` at `i`), so none of their entries conflicts with `v`'s entries at indices
   `≤ i`, and `v` never deletes `e`.
4. *The vote condition.* `v` granted its vote, so `L_U`'s log was at least as up-to-date as `v`'s:
   either `lastTerm(L_U) > lastTerm(v)`, or the last terms are equal and `L_U`'s log is at least as
   long.
5. *Equal last terms.* Let the common last term be `t'`, and let `v`'s last entry be at index `j`,
   with `L_U`'s last entry at index `k ≥ j`. Both entries were created by the unique leader `L_{t'}`
   of term `t'` (election safety), which never deletes its own entries, so `L_{t'}` has both. By log
   matching at `(k, t')`, `L_U`'s log agrees with `L_{t'}`'s up to index `k ≥ j`, so `L_U` has
   `(j, t')`. By log matching at `(j, t')`, `L_U`'s log then contains all of `v`'s log, including
   `e`. Contradiction.
6. *Larger last term.* Let `t' = lastTerm(L_U) > lastTerm(v)`. Since `v` has `e`, which has term `T`,
   and terms never decrease along a log, `lastTerm(v) ≥ T`, so `t' > T`. Also `t' < U`, because
   at its election `L_U` holds no entries of its own term `U`. The last entry of `L_U` was created by
   the leader of term `t'`, which has `e` at `i` by the minimality of `U`. In that leader's log `e`
   comes before the entry of term `t' > T`, so by log matching `L_U`'s log agrees with it up to and
   including index `i`, and `L_U` has `e`. Contradiction. ∎

## References

* [FLP85] M. J. Fischer, N. A. Lynch, M. S. Paterson. *Impossibility of distributed consensus with
  one faulty process.* Journal of the ACM 32(2):374–382, 1985.
* [DLS88] C. Dwork, N. Lynch, L. Stockmeyer. *Consensus in the presence of partial synchrony.*
  Journal of the ACM 35(2):288–323, 1988.
* [CL99] M. Castro, B. Liskov. *Practical Byzantine fault tolerance.* OSDI 1999.
* [OO14] D. Ongaro, J. Ousterhout. *In search of an understandable consensus algorithm.* USENIX ATC 2014.
* [BKM18] E. Buchman, J. Kwon, Z. Milosevic. *The latest gossip on BFT consensus.* arXiv:1807.04938, 2018.
* M. Ben-Or. *Another advantage of free choice: completely asynchronous agreement protocols.* PODC 1983.
* T. D. Chandra, S. Toueg. *Unreliable failure detectors for reliable distributed systems.* JACM 43(2), 1996.
* C. Cachin, K. Kursawe, F. Petzold, V. Shoup. *Secure and efficient asynchronous broadcast protocols.* CRYPTO 2001.
