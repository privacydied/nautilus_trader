#!/usr/bin/env python3
"""Write run_manifest.json and summary.json/summary.md for the reanalysis."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RUN_ID = "20260530_reanalysis"
OUT_DIR = Path(f"/mnt/nasirjones/py/nautilus_trader/reports/hip3_flx_stale_oracle_funding_bias_phase_minus2_v0/{RUN_ID}")


def git_sha():
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def git_branch():
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = git_sha()
    branch = git_branch()

    manifest = {
        "study_id": "hip3_flx_stale_oracle_funding_bias_phase_minus2_v0",
        "run_id": RUN_ID,
        "created_at_utc": now,
        "git_sha": sha,
        "git_dirty": True,
        "branch": branch,
        "safety_mode": "public_archive_observer_only",
        "mode": "corrected_frequency_reanalysis",
        "positive_control_status": "HIP3_FLX_ORACLE_FREQUENCY_SCAN_POSITIVE_CONTROL_PASSED",
        "previous_frequency_scan_conclusion": "INVALIDATED_EXTRACTION_FALSE_NEGATIVE",
        "root_cause": "multiSig payload.action perpDeploy envelope was not traversed",
        "stale_premise_status": "HIP3_FLX_STALE_PREMISE_FALSIFIED_DECODER_ARTIFACT",
        "actual_funding_measured": False,
        "funding_clock_proxy_only": True,
        "phase0_review_allowed": False,
        "paper_or_live_allowed": False,
        "registry_mutation_allowed": False,
        "registry_mutation_type": None,
        "registry_correction_status": "HIP3_FLX_REGISTRY_CORRECTION_NOT_REQUIRED",
        "files_decoded": 3,
        "records_decoded": 25000,
        "total_oracle_updates": 95152,
        "total_flx_updates": 10336,
        "flx_btc_count": 646,
        "flx_tsla_count": 646,
        "flx_nvda_count": 646,
        "flx_unique_symbols": 16,
        "flx_updates_per_hour": 16.1763,
        "conditional_alignment_ran": False,
        "conditional_alignment_reason": "Stale premise falsified; flx is actively updating. Alignment/bias probe not warranted.",
    }
    (OUT_DIR / "run_manifest.json").write_text(json.dumps(manifest, indent=2))

    # summary.json
    summary = {**manifest}
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

    # summary.md
    md = f"""# FLX Oracle Frequency Reanalysis — Corrected Decoder

**Run ID:** {RUN_ID}
**Status:** HIP3_FLX_STALE_PREMISE_FALSIFIED_DECODER_ARTIFACT
**Created:** {now}
**Git SHA:** {sha}
**Branch:** {branch}

## Safety

- Safety mode: public_archive_observer_only
- No orders, private keys, auth, live execution, paper trading, shadow executor, systemd, bot path, or registry mutation.
- Actual funding data NOT measured. Funding-clock is a proxy only.
- Phase 0 review NOT authorized.

## Positive Control

- Status: HIP3_FLX_ORACLE_FREQUENCY_SCAN_POSITIVE_CONTROL_PASSED
- Control block: /tmp/test_block.lz4 (2026-05-23)
- flx:BTC detected: 646 (3-date sample)

## Previous Result Invalidation

- Previous conclusion: INVALIDATED_EXTRACTION_FALSE_NEGATIVE
- Root cause: multiSig payload.action perpDeploy envelope was not traversed
- Prior zero flx counts were extraction false negatives, not evidence of absence

## Decode Path Inventory

| Path | Count |
|------|-------|
| direct_action_perpDeploy_setOracle | 2,159 |
| multiSig_payload_action_perpDeploy_setOracle | 2,258 |
| other_or_unknown | 25,000 |
| malformed_or_decode_failed | 0 |

The multiSig path contributed 2,258 oracle action extractions. Without this path, all flx:* keys would be dropped.

## Prior Reconstruction Decoder Audit

- Branch: feat/hip3-replica-cmds-setoracle-reconstruction-v0
- Same bug found: prior decoder also only checked sa.action.type for "perpDeploy"
- All prior flx:0 findings from that decoder are invalidated

## Corrected Frequency (3-date sample: 2026-05-23, 2026-05-24, 2026-05-25)

| DEX | TSLA | NVDA | updates/hour |
|-----|------|------|-------------|
| flx | 646 | 646 | 16.18 |
| cash | 1,121 | 1,121 | 28.07 |
| km | 461 | 461 | ~11.5 |
| xyz | 552 | 552 | ~13.8 |

## All Observed flx:* Keys (16)

BTC, COIN, COPPER, CRCL, GAS, GOLD, NVDA, OIL, PALLADIUM, PLATINUM, SILVER, TSLA, USA100, USA500, USDE, XMR

## Stale Premise Evaluation

- flx:TSLA updates/hour: 16.18 (comparable to km 11.5, xyz 13.8)
- flx:NVDA updates/hour: 16.18
- flx is NOT sparse relative to references
- Status: HIP3_FLX_STALE_PREMISE_FALSIFIED_DECODER_ARTIFACT

**The prior stale/sparse flx oracle premise was caused by extraction failure. B-slow's stale-oracle foundation is not supported by corrected oracle-frequency evidence.**

## Conditional Alignment/Bias Probe

Not run. The stale premise is falsified; flx is actively updating. The alignment/bias/funding-clock reachability probe is not warranted under the corrected frequency evidence.

## Forward-Recorder Residual Sanity Note

The prior wide flx residual, if still valid, requires a different explanation: deployer oracle methodology difference, reference basket difference, anchor mismatch, forward-recorder parsing issue, or residual calculation artifact. It is not explained by absent flx oracle updates under the corrected decoder.

## Registry Correction Review

- Status: HIP3_FLX_REGISTRY_CORRECTION_NOT_REQUIRED
- REJECTED_RESEARCH.md has no FLX-specific entry
- The FLX stale oracle doc premise (line 20: "flx oracle updates are sparse or stale") is invalidated, but this is a study design doc, not a registry entry

## Verdict

**Status:** `HIP3_FLX_STALE_PREMISE_FALSIFIED_DECODER_ARTIFACT`

The scanner demonstrably detects flx keys when present via the flx:BTC positive control. Within the controlled 3-date sample, flx:TSLA and flx:NVDA are actively updating at 16.18 updates/hour. The prior "flx absent/stale" finding was a decoder artifact caused by failure to unwrap the multiSig payload.action envelope.

This falsifies the stale-oracle B-slow premise. The FLX oracle is active, not stale.

Phase 0 review NOT authorized. Actual funding NOT measured.
"""
    (OUT_DIR / "summary.md").write_text(md)
    print(f"Artifacts written to {OUT_DIR}")


if __name__ == "__main__":
    main()
