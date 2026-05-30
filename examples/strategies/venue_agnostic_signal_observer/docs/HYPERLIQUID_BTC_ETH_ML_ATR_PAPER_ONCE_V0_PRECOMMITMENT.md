# HYPERLIQUID BTC/ETH ML+ATR Paper v0 — Precommitment (Task A)

> **Project context:** This study builds on the audited v0 scaffold at
> `feat/hyperliquid-btc-eth-ml-atr-v0` (SHA b343176c04).
> See also [HYPERLIQUID_BTC_ETH_ML_ATR_V0_PRECOMMITMENT.md](HYPERLIQUID_BTC_ETH_ML_ATR_V0_PRECOMMITMENT.md)
> for the upstream research precommitment.

---

## Purpose

- Run a local simulated-paper ledger for the frozen Hyperliquid BTC/ETH ML+ATR v0 model.
- Validate that sequential local-file-fed updates, state persistence, restart behavior, funding accounting, and ATR stop/trailing execution remain deterministic and non-leaking.
- Validate that sequential paper behavior matches v0 batch backtest behavior on the same synthetic input.
- **This is not shadow logging and not exchange paper trading.**

---

## Terminology

| Term | Meaning |
|------|---------|
| Shadow logging | Signal-only logging, no simulated positions |
| Simulated-paper diagnostic | Local-file-fed simulated positions, ledger, equity, and state |
| Exchange paper trading | Broker/exchange paper account. **This task does not do that.** |

---

## Preconditions for Non-Test Runs

- A real v0 run has been executed against actual Hyperliquid archive data.
- That v0 run produced status: `ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE`
- The produced `summary.json` and `model_bundle.json` are the only permitted bundles for non-fixture paper runs.
- `--allow-nonpassing-bundle-for-test-fixtures` is for synthetic fixtures only.

---

## What This Layer Is For

- Validate that the v0 pipeline behaves identically in a sequential/streaming-like local-file-fed setting as it did in batch backtest.
- Catch hidden state bugs, feature drift, funding off-by-one errors, and batch/sequential divergence.
- **The batch-vs-sequential equivalence test is the core contract.**

---

## Scope (Task A Only)

- `--once`, bundle export, state, events, trades, equity, restart/resume, equivalence test.
- **No `--loop`.** No systemd. No signal handling. No polling. No long-running process.
- Symbols: BTC, ETH only. Timeframe: 1h only. Data source: local files only.
- No LINK. No new features. No model retraining. No threshold tuning. No exit-rule changes.

---

## Frozen Execution Semantics

- Feature generation must match v0 exactly.
- Prediction uses frozen scaler/model/calibrator exactly.
- Entry: long if `calibrated_p_up >= long_threshold`, short if `<= short_threshold`.
- Entry timing: signal at completed bar close t, simulated entry at open_{t+1}.
- ATR frozen from signal bar close t.
- Stop/trailing: same rules as v0 research backtest.
- Funding: positive funding means longs pay shorts. Half-open interval `[entry, exit)`.
- Costs: frozen bundle/config values unless CLI-overridden.

---

## Invalidation Conditions

- Bundle source status not eligible
- Bundle hash mismatch
- Feature mismatch vs bundle feature_names
- Missing required bars/funding
- Naive/non-UTC timestamps
- Timestamp gap above configured limit
- Duplicate paper event hash
- Broken paper event hash chain
- Restart creates duplicate trade/event
- Paper runner changes model, thresholds, features, or exit config
- Any test weakened to pass a failing paper run
- Simulated paper trade economics diverge from v0 batch backtest by more than epsilon

---

## Promotion Path

- Successful paper simulation produces `PAPER_SIM_V0_DIAGNOSTIC_COMPLETE`.
- **Does NOT unlock live, exchange-paper, bot, or order-routing.**
- Any later loop runner, Nautilus engine integration, systemd service, or exchange-connected paper account requires its own precommitment.

---

## Dependency Note

- The equivalence test is a contract between v0 batch logic and paper sequential logic.
- If v0 batch backtest output changes, paper equivalence tests must be re-run against a fresh bundle.
