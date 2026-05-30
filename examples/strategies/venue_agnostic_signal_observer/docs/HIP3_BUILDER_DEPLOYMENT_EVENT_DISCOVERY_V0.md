# HIP‑3 Builder Deployment Event Discovery v0

**Purpose**

* This is a *Phase -1* feasibility scout. It **does not** implement a strategy, pre‑commitment, PnL evaluation, or any live‑trading behavior.
* The goal is to determine whether HIP‑3 builder‑deployed perp symbols, their deployer namespaces, and associated deployment actions are discoverable via **public** Hyperliquid block data and archives.

**Scope & Safety**

* Public HTTP endpoints only – no API keys, authentication, or private endpoints.
* Public S3 archive reads only – gated by the `--allow-s3-archive-read` flag (requester‑pays acknowledged).
* No order placement, no execution clients, no bot‑path changes, no systemd service modifications.
* No mutation of `REJECTED_RESEARCH.md` or any registry/ledger.

**Six‑Gate Kill‑Chain** (must succeed in order)

1. **Symbol discovery** – locate builder‑deployed symbols in explorer block data.
2. **Archive coverage** – verify the symbols appear in the public `asset_ctxs` and L2 archives.
3. **Archive helper reconciliation** – use existing archive helpers if possible; otherwise emit `HIP3_ARCHIVE_HELPER_RECONCILIATION_REQUIRED`.
4. **Public info cross‑reference** – confirm symbols are present in the Hyperliquid public `/info` endpoint.
5. **Archive visibility** – ensure both `asset_ctxs` and L2 data are reachable for at least one symbol.
6. **Final status** – report one of the allowed diagnostic statuses.

**Download Budgets**

* Total download budget: **5 GB** (`--download-budget-bytes`).
* Explorer block budget: **1 GB** (`--explorer-block-budget-bytes`).
* Per‑symbol L2 budget: **500 MB** (enforced implicitly by the block budget).

**Outputs** (under `reports/hip3_builder_deployment_event_discovery_v0/<run_id>/`)

* `summary.json` – machine‑readable result with provenance.
* `summary.md` – human‑readable overview.
* `deployment_event_candidates.json` – list of extracted `DeploymentCandidate` objects.
* `action_type_inventory.json` – distinct `action_type` values seen.
* `builder_symbol_cross_reference.json` – map `symbol -> bool` from the public `/info` API.
* `archive_visibility.json` – `{symbol: {asset_ctxs: bool, l2: bool}}`.
* `run_manifest.json` – provenance of helpers, git SHA, args, and safety metadata.

**What this is NOT**

* Not a strategy.
* Not a pre‑commitment.
* Not a PnL evaluator.
* Does not promote any candidate or enable Phase 0 drafting.

**Running the scout**

```bash
uv run examples/strategies/venue_agnostic_signal_observer/run_hip3_builder_deployment_event_discovery_v0.py \
    --out-root reports/hip3_builder_deployment_event_discovery_v0 \
    --start-date 2025-10-13 \
    --allow-s3-archive-read \
    --allow-network-public
```

Use `--dry-run` for a no‑network preview.
