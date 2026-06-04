"""CLI entrypoint for single-precommitment paper promotion."""

from __future__ import annotations

import argparse
from ._prog import set_legacy_prog
import sys
from pathlib import Path

from ...paper.auto_promotion import evaluate_promotion
from ...paper.gate_verifier import GateResult


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a precommitment for paper strategy promotion"
    )
    set_legacy_prog(parser)
    parser.add_argument(
        "--precommitment-hash",
        type=str,
        required=True,
        help="Precommitment hash to evaluate",
    )
    parser.add_argument(
        "--precommitment-dir",
        type=str,
        default="reports/conductor/precommitments",
        help="Directory containing precommitment files",
    )
    parser.add_argument(
        "--registry-dir",
        type=str,
        default="reports/paper/registry",
        help="Paper strategy registry directory",
    )
    parser.add_argument(
        "--paper-events-ledger",
        type=str,
        default="reports/paper/paper_events.jsonl",
        help="Paper events ledger path",
    )
    parser.add_argument(
        "--evidence-ledger",
        type=str,
        default="reports/evidence_ledger.jsonl",
        help="Evidence ledger path (from conductor locked runs)",
    )
    parser.add_argument(
        "--artifacts-base-dir",
        type=str,
        default="reports",
        help="Base directory for locked-run artifacts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Evaluate gates and report decision without writing registry or ledger",
    )
    args = parser.parse_args()

    # In dry-run mode, we still run evaluate_promotion which may write ledger events.
    # Dry-run for paper promotion evaluates and reports without saving.
    # For simplicity, dry-run will skip the full evaluation pathway and just
    # run gate checks.
    from ...paper.gate_verifier import (
        FrozenGateDecision,
        check_promotion_frozen,
        verify_promotion_gates,
    )

    hash_val = args.precommitment_hash
    precommitment_dir = Path(args.precommitment_dir)
    artifacts_base = Path(args.artifacts_base_dir)

    frozen = check_promotion_frozen(hash_val, precommitment_dir)
    print(f"Frozen gate: {frozen.status()}")
    print(f"  allowed={frozen.allowed}")

    if args.dry_run:
        # Also show gate results
        # Read precommitment to extract target group_id for gate checks
        from ...conductor.atomic_io import read_json

        try:
            pc_payload = read_json(
                precommitment_dir / f"{hash_val}.json"
            )
        except Exception:
            pc_payload = {}

        gate_results = verify_promotion_gates(
            precommitment_hash=hash_val,
            ledger_path=Path(args.evidence_ledger),
            artifacts_dir=artifacts_base,
            group_id=pc_payload.get("group_id"),
        )
        for g in gate_results:
            print(f"  Gate '{g.gate_id}': passed={g.passed} detail={g.detail}")

        if frozen.allowed and all(g.passed for g in gate_results):
            print("Dry-run result: PROMOTION_READY (no write performed)")
        else:
            print("Dry-run result: NOT_READY (see gate details above)")
    else:
        decision = evaluate_promotion(
            precommitment_hash=hash_val,
            precommitment_dir=precommitment_dir,
            registry_dir=Path(args.registry_dir),
            paper_events_ledger_path=Path(args.paper_events_ledger),
            artifacts_base_dir=artifacts_base,
            evidence_ledger_path=Path(args.evidence_ledger),
        )
        print(f"Promotion decision: allowed={decision.allowed}")
        print(f"  reason={decision.reason}")
        if decision.strategy_spec:
            print(f"  strategy_id={decision.strategy_spec.strategy_id}")
        if decision.event_hash:
            print(f"  event_hash={decision.event_hash}")


if __name__ == "__main__":
    main()
