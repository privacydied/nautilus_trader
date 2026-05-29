# HIP-3 replica_cmds setOracle Reconstruction — Phase -1 Scout

> **Document status:** Phase -1 scout / liquidity-probe. Not a strategy. Not a precommitment.  
> **Created:** 2026-05-29  
> **Scope:** Data-plane reconnaissance only. No orders. No live. No paper. No PnL.

---

## 1. Objective

Use Hyperliquid historical node data (`replica_cmds`) to determine whether HIP-3
deployer-submitted oracle updates (`setOracle`) are publicly reconstructable.

The question is simple: can we, from an independently accessible S3 data source,
recover the sequence of deployer oracle submissions for a target set of HIP-3
perpetual markets, and if so, does that sequence correlate with (or diverge from)
the oracle prices served by the Hyperliquid public API?

This is a data-plane probe. We are not building a strategy. We are not trading.
We are asking: **does the data exist, and does it make sense?**

---

## 2. Why replica_cmds

Hyperliquid's validator network replicates on-chain commands via a file-based
replication protocol. The historical node data published to S3 contains
`replica_cmds` records — serialized command envelopes that capture deployer
actions on-chain.

If `replica_cmds` contains historical deployer `setOracle` actions, it provides
a historical oracle update series **independent of forward recorder start date**.
This is the key value proposition: we are not constrained by when we started
recording. We can reach back into the full on-chain history.

Without `replica_cmds`, we would be limited to whatever the forward recorder
captured, which introduces survivorship bias and start-date dependency.

---

## 3. Why setOracle matters

The `setOracle` command is the deployer's direct mechanism for submitting an
oracle price for a HIP-3 perpetual market. Understanding this series may help
determine whether observed oracle discrepancies — particularly on `flx` DEX
markets — are:

| Characterization | Implication |
|---|---|
| **Persistent** | The deployer consistently submits a different oracle than API `oraclePx` |
| **DEX-specific** | The discrepancy is isolated to certain DEX sub-markets (e.g. `flx:TSLA`) |
| **Stale** | The deployer submits infrequently; oracle drifts from API price |
| **Level-shifted** | A one-time offset was introduced and persists |
| **Reverting** | Discrepancies appear transient; deployer corrects over time |

Resolving this characterization is a prerequisite for any Phase 0 commitment.
Without it, we cannot distinguish signal from noise.

---

## 4. Not a strategy

This document describes a **Phase -1 data reconstruction exercise**. It is not a
trading strategy. It produces no signals. It places no orders. It does not
initialize any Nautilus Trader nodes.

Phase -1 means: we are asking "does the data exist and is it consistent?" before
we ask "can we trade on it?"

---

## 5. Does not unlock Phase 0

Successful completion of this Phase -1 probe does **not** automatically unlock
Phase 0. Phase 0 requires:

- Decoded `replica_cmds` envelopes containing recognized oracle-like commands
- A correctness gate passed against forward recorder API oracle prices
- A documented and validated protocol transform (if any) between deployer
  submission and effective API oracle
- Explicit sign-off that the data is sufficient to form a trading hypothesis

None of these are assumed here. We are probing.

---

## 6. Source

**S3 path:** `s3://hl-mainnet-node-data/replica_cmds`

- **Access model:** Requester-pays. The caller is responsible for AWS credentials
  and data transfer costs.
- **Format:** Serialized binary envelopes (protocol buffers or custom binary;
  exact encoding TBD from inspection of first records).
- **Granularity:** Per-record command envelopes. Not pre-decoded. Not indexed by
  command type.

All data retrieval must use requester-pays access. Do not assume anonymous read.

---

## 7. Envelope-first rule

**Do not search for `setOracle` inside raw chunks before decoding the
`replica_cmds` record envelope.**

The raw S3 data contains serialized binary records. Searching for the string
`setOracle` (or any command name) via byte-level grep on undecoded chunks is
forbidden. This approach will:

- Produce false positives from unrelated byte sequences
- Miss commands whose encoding differs from naive string matching
- Corrupt the probe by building on contaminated matches

The correct workflow:

1. Download a raw chunk
2. Decode the chunk into individual `replica_cmds` records using the envelope
   format
3. Inspect the decoded record for command type / action field
4. Only then determine whether the record represents a `setOracle` or
   oracle-adjacent action

Envelope-first. Always.

---

## 8. Recon-first rule

**Reconnaissance before inference. Before building any oracle series, we must:**

1. Decode at least one `replica_cmds` record envelope successfully
2. Identify at least one record whose decoded content is oracle-like (i.e.,
   contains a deployer-submitted price or oracle update action)
3. Cross-reference that record's timestamp and market against a known
   forward-recorder API oracle price at the same time

Only after step 3 completes successfully — and with documented results — may we
proceed to wider data extraction.

This rule exists to prevent building an entire pipeline on assumed data formats
that may not survive contact with actual data.

---

## 9. Mandatory correctness gate

Before any oracle series derived from `replica_cmds` is used in any downstream
analysis (even exploratory), the following correctness gate must be passed:

**Per API symbol, per DEX:**

For each target market (e.g., `flx:TSLA`), we must demonstrate that at least one
`setOracle` record from `replica_cmds`:

1. Has a timestamp that falls within a window where the forward recorder captured
   API `oracle_price` data for the same symbol
2. Contains a deployer-submitted oracle value
3. That value is compared against the API `oracle_price` at the same (or nearest)
   timestamp
4. The comparison shows either:
   - **Exact match** (within floating-point tolerance), OR
   - **A documented protocol transform** (see section 11) that explains the
     difference

If neither condition (a) nor (b) can be demonstrated, the correctness gate
**fails** for that market, and `replica_cmds`-derived oracle data for that
market must not be used.

---

## 10. Distinction: deployer-submitted oracle vs effective API oraclePx

Two distinct oracle prices exist for any HIP-3 perpetual market:

| Field | Source | Description |
|---|---|---|
| **Deployer-submitted oracle** | `setOracle` command in `replica_cmds` | The price the deployer (or deployer bot) submitted on-chain |
| **Effective API oraclePx** | Hyperliquid public API (`/info`) | The price the API actually serves to clients for margin/liquidation |

These may differ. The protocol may apply transforms between deployer submission
and effective API oracle. Assuming they are identical without verification is
forbidden.

The purpose of this probe is to characterize the relationship between these two
prices — not to assume equivalence.

---

## 11. Protocol transform allowance vs forbidden arbitrary fitted transforms

The protocol may apply known, documented transforms between deployer-submitted
oracle values and effective API `oracle_px`. The following transforms are
**allowed** as explanations for observed differences:

### Allowed transforms

| Transform | Description |
|---|---|
| **Clamp** | Oracle value is clamped to a min/max bound before serving |
| **Median** | Oracle value is compared against other oracle sources; median is used |
| **EMA** | Oracle value is smoothed via exponential moving average |
| **Weighted median** | Multiple oracle submissions are combined via weighted median |

### Forbidden transforms

- Arbitrary fitted transforms (e.g., "we fitted a polynomial to make it match")
- Post-hoc corrections applied to force convergence
- Any transform that is not documented in the Hyperliquid protocol specification
  or verifiable from on-chain logic

If a difference between deployer-submitted oracle and API `oracle_px` cannot be
explained by an allowed transform, it must be flagged as **unexplained** and
documented.

---

## 12. Target markets

Exactly these 12 markets. No others. No supersets. No "and similar."

| # | Market |
|---|---|
| 1 | `xyz:TSLA` |
| 2 | `flx:TSLA` |
| 3 | `km:TSLA` |
| 4 | `cash:TSLA` |
| 5 | `xyz:AAPL` |
| 6 | `km:AAPL` |
| 7 | `xyz:MSFT` |
| 8 | `cash:MSFT` |
| 9 | `xyz:NVDA` |
| 10 | `flx:NVDA` |
| 11 | `km:NVDA` |
| 12 | `cash:NVDA` |

Note the asymmetry: `flx:AAPL`, `flx:MSFT`, `xyz:cash`, etc. are **not** in
the target set. This is intentional. The probe targets markets where oracle
discrepancies have been observed or where `flx` DEX data is available.

---

## 13. flx priority

Of the 12 target markets, `flx` DEX markets (`flx:TSLA`, `flx:NVDA`) receive
**priority** in the probe. This is because:

- Observed oracle discrepancies on `flx` markets motivated this probe
- `flx` markets may have different oracle update behavior than `xyz` or `km`
- If `replica_cmds` oracle reconstruction is only feasible on `flx` markets, that
  is still a valid and useful finding

Priority means: decode and inspect `flx` markets first. If `flx` correctness
gates pass, extend to other markets. If `flx` gates fail, assess whether
remaining markets are worth pursuing.

---

## 14. Allowed / forbidden statuses

### Allowed statuses

| Status | Meaning |
|---|---|
| `SCOPE_NOT_STARTED` | Probe has not begun |
| `ENVELOPE_DECODED` | At least one `replica_cmds` envelope has been successfully decoded |
| `ORACLE_RECORD_FOUND` | At least one decoded record contains oracle-like content |
| `CORRECTNESS_GATE_PASSED` | Per-DEX correctness gate passed for at least one market |
| `CORRECTNESS_GATE_FAILED` | Correctness gate failed; root cause documented |
| `DATA_SUFFICIENT` | Data is sufficient for Phase 0 hypothesis (requires gate pass) |
| `DATA_INSUFFICIENT` | Data is insufficient; probe concludes |

### Forbidden statuses

| Status | Why forbidden |
|---|---|
| `STRATEGY_READY` | This is not a strategy |
| `LIVE_TRADING` | No trading of any kind |
| `PAPER_TRADING` | No paper trading of any kind |
| `PHASE_0_UNLOCKED` | Phase 0 does not auto-unlock from Phase -1 |

---

## 15. Hard safety constraints

This probe operates under the following hard constraints. Violation of any
constraint terminates the probe.

| # | Constraint |
|---|---|
| 1 | **No orders.** No buy, sell, or cancel orders may be placed on any venue. |
| 2 | **No authentication.** No API keys, no private keys, no session tokens for trading venues. |
| 3 | **No live execution.** No live order routing, no live market data subscription that implies trading readiness. |
| 4 | **No paper execution.** No paper/simulated trading. The paper/live distinction is irrelevant here — both are forbidden. |
| 5 | **No PnL tracking.** No profit-and-loss computation, no position tracking, no portfolio valuation. |
| 6 | **No strategy initialization.** No Nautilus Trader strategy node, no clock, no execution algorithm. |
| 7 | **S3 requester-pays only.** Data access via `s3://hl-mainnet-node-data/replica_cmds` with proper credentials. |

---

## 16. Explicit exclusion: SonarX residual diagnostic

This probe is **not** the SonarX residual diagnostic. The SonarX residual
diagnostic is a separate analysis that examines residual oracle price
differences after accounting for known factors. This probe is upstream of that:
it asks whether the raw deployer-submitted oracle data is even reconstructable
from `replica_cmds`.

Do not conflate the two. Do not assume that finding `setOracle` records in
`replica_cmds` resolves the SonarX residual diagnostic. They are orthogonal
questions.

---

## 17. Critical gate

> **Do not run a wide historical backfill until at least one real replica_cmds
> command envelope has been decoded, at least one real oracle-like command has
> been inspected, and the per-DEX overlap correctness gate against forward
> recorder API oracle prices has passed or a documented protocol transform has
> been validated.**

This sentence is the single most important constraint in this document. A wide
historical backfill (e.g., "download all S3 data for the last 6 months and
extract all `setOracle` records") is expensive, slow, and potentially
misleading if the underlying data format is not yet validated.

Small-scale reconnaissance first. Scale only after the correctness gate passes.

---

## Appendix: Probe workflow (ordered)

1. **S3 access check** — Verify requester-pays access to `s3://hl-mainnet-node-data/replica_cmds`
2. **Chunk download** — Download a single raw chunk (smallest available)
3. **Envelope decode** — Decode individual `replica_cmds` records from the chunk
4. **Command type inspection** — Identify the command type / action field of decoded records
5. **Oracle record identification** — Locate at least one record with oracle-like content
6. **Timestamp cross-reference** — Match the record's timestamp against forward recorder API data
7. **Correctness gate** — Compare deployer-submitted value against API `oracle_px`
8. **Transform assessment** — If values differ, assess whether an allowed transform explains it
9. **Gate result** — Pass or fail, with documented evidence
10. **Scale decision** — Only if gate passes: consider wider data extraction for the 12 target markets

---

*End of Phase -1 scout document. Status: `SCOPE_NOT_STARTED`.*
