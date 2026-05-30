# FLX Oracle Frequency Reanalysis — Closure

**Run ID:** 20260530_reanalysis
**Status:** HIP3_FLX_STALE_PREMISE_FALSIFIED_DECODER_ARTIFACT
**B-slow closure:** CLOSED_UPSTREAM / PREMISE_FALSIFIED_DECODER_ARTIFACT
**Created:** 2026-05-30
**Git SHA:** 2a56da6e99f4ff0677bdaa9a779d1a5db3e46b04
**Branch:** feat/hip3-flx-stale-oracle-funding-bias-phase-minus2-v0

## Safety

- Safety mode: public_archive_observer_only
- No orders, private keys, auth, live execution, paper trading, shadow executor, systemd, bot path, or registry mutation.
- Actual funding data NOT measured.
- Phase 0 review NOT authorized.

## Conclusion

**The FLX stale-oracle premise is falsified.** Under the corrected decoder, FLX equity oracles update actively. The B-slow stale-oracle funding-distortion hypothesis is closed upstream because its core premise is unsupported.

This is a premise falsification / data-plane correction, not a strategy rejection.

## Positive Control

- Status: HIP3_FLX_ORACLE_FREQUENCY_SCAN_POSITIVE_CONTROL_PASSED
- Control block: /tmp/test_block.lz4 (2026-05-23)
- flx:BTC detected: 646 (3-date sample)

## Previous Result Invalidation

- Previous conclusion: INVALIDATED_EXTRACTION_FALSE_NEGATIVE
- Root cause: multiSig payload.action perpDeploy envelope was not traversed
- Prior zero flx counts were extraction false negatives, not evidence of absence

## Corrected Frequency (3-date sample)

| DEX | TSLA | NVDA | updates/hour |
|-----|------|------|-------------|
| flx | 646 | 646 | 16.18 |
| cash | 1,121 | 1,121 | 28.07 |
| km | 461 | 461 | ~11.5 |
| xyz | 552 | 552 | ~13.8 |

## All Observed flx:* Keys (16)

BTC, COIN, COPPER, CRCL, GAS, GOLD, NVDA, OIL, PALLADIUM, PLATINUM, SILVER, TSLA, USA100, USA500, USDE, XMR

## Stale Premise Evaluation

flx:TSLA and flx:NVDA update at 16.18 updates/hour — comparable to km (11.5) and xyz (13.8). FLX is not sparse relative to references.

Status: HIP3_FLX_STALE_PREMISE_FALSIFIED_DECODER_ARTIFACT

## Alignment/Bias/Funding-Clock

- alignment_probe_ran: false
- alignment_probe_skip_reason: stale premise falsified by corrected oracle-frequency evidence
- funding_clock_proxy_evaluated: false
- actual_funding_measured: false

## B-slow Closure

- Status: CLOSED_UPSTREAM / PREMISE_FALSIFIED_DECODER_ARTIFACT
- Not a strategy rejection — premise falsified by corrected decoder
- Tag: hip3-flx-stale-oracle-bslow-closed-upstream-decoder-artifact
- Appended to REJECTED_RESEARCH.md as closure note

## Forward-Recorder Residual

The prior wide flx forward-recorder residual is not explained by absent/sparse oracle updates under the corrected decoder. Possible explanations include deployer oracle methodology differences, reference basket differences, anchor mismatch, forward-recorder parsing issues, or residual calculation artifact. This task does not investigate that residual further.

## Registry Correction

- Status: HIP3_FLX_REGISTRY_CORRECTION_NOT_REQUIRED
- No cross-DEX exclusion rationale citing stale FLX was found
- flx:TSLA/flx:NVDA are included in forward-recorder and sonarx docs
- B-slow closure note appended to REJECTED_RESEARCH.md

## Decoder Lesson

Any future replica_cmds oracle reconstruction must traverse multiSig.payload.action before checking for perpDeploy.setOracle.oraclePxs. A direct action.perpDeploy-only traversal can silently produce false zero counts for builder DEX oracle updates.

## What is NOT Authorized

- No Phase 0 review
- No paper trading
- No live trading
- No bot path changes
- No registry promotion
- No edge or profitability claim
- No actual funding measurement
