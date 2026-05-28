# HIP-3 SonarX L2 Summary Coverage Probe (V0)

This probe inspects the public **SonarX** requester‑pays S3 bucket for
HIP‑3 L2 summary snapshots covering the DEX {dex}:{coin} markets listed in the
specification.

## What the probe does
1. **Bucket access check** – verifies that the bucket `sonarx-hyperliquid-public`
   can be listed with requester‑pays enabled.
2. **Market discovery** – for each market it tries the documented path
   `market_data/hip3/{dex}:{coin}/l2-summary-snapshots/` and falls back to
   URL‑encoded, underscore and hyphen variants.
3. **Partition inventory** – lists available height partitions for the market.
4. **Sample download** – grabs a bounded number of `.json.gz` files from a
   few partitions, respecting a byte‑budget.
5. **Schema validation** – checks that the JSON payload contains the expected
   fields, price ordering, bid/ask counts and basic numeric sanity.\n6. **Coverage decision** – reports whether the discovered data is sufficient for a
   downstream historical L2 depth/scatter scout (Phase ‑1).

All artifacts are written under the user‑specified `--out-root` directory and
include rich metadata (git SHA, branch, safety mode, licenses, etc.).

## Usage
```bash
# Dry‑run – validates configuration only
uv run -m examples.strategies.venue_agnostic_signal_observer.run_hip3_sonarx_l2_summary_coverage_probe_v0 \
  --out-root reports/hip3_sonarx_l2_summary_coverage_probe_v0 \
  --markets xyz:TSLA,flx:TSLA,km:TSLA,cash:TSLA,xyz:AAPL,km:AAPL,xyz:MSFT,cash:MSWT,xyz:NVDA,flx:NVDA,km:NVDA,cash:NVDA \
  --dry-run

# Real run (requires internet access to SonarX bucket)
uv run -m examples.strategies.venue_agnostic_signal_observer.run_hip3_sonarx_l2_summary_coverage_probe_v0 \
  --out-root reports/hip3_sonarx_l2_summary_coverage_probe_v0 \
  --markets xyz:TSLA,flx:TSLA,km:TSLA,cash:TSLA,xyz:AAPL,km:AAPL,xyz:MSFT,cash:MSFT,xyz:NVDA,flx:NVDA,km:NVDA,cash:NVDA
```

## Artifacts produced
* `run_manifest.json` – bucket access status.
* `sonarx_market_coverage.json` – per‑market partition and sample discovery.
* `gate_decisions.json` – final coverage decision.
* `summary.json` / `summary.md` – concise report with metadata.
* (optional) `dry_run_preview.json` for dry‑run.

The probe never touches live‑trading code, private keys, or any registry files.
