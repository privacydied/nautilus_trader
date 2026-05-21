"""Report writing for the signal observer."""
import csv
import json
from pathlib import Path
from typing import List

from .models import ForwardReturnResult
from .models import SignalEvaluationSummary


def write_outputs(
    signals_dicts: List[dict],
    results: List[ForwardReturnResult],
    summary: SignalEvaluationSummary,
    output_dir: Path,
):
    """Write all report files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. signal_events.jsonl
    _write_jsonl(signals_dicts, output_dir / "signal_events.jsonl")

    # 2. forward_returns.jsonl
    _write_jsonl([r.to_dict() for r in results], output_dir / "forward_returns.jsonl")

    # 3. summary.json
    _write_json(summary.to_dict(), output_dir / "summary.json")

    # 4. summary.csv (horizon summary table)
    _write_horizon_csv(summary.results_by_horizon, output_dir / "summary.csv")

    # Optional: by_signal_type.csv
    _write_signal_type_csv(summary.results_by_signal_type, output_dir / "by_signal_type.csv")

    # Optional: rejections.json
    rejections = [r.to_dict() for r in results if not r.valid]
    _write_json(rejections, output_dir / "rejections.json")


def _write_jsonl(rows: List[dict], path: Path):
    with open(path, "w") as fh:
        fh.writelines(json.dumps(row) + "\n" for row in rows)


def _write_json(data, path: Path):
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)


def _write_horizon_csv(rows: List[dict], path: Path):
    if not rows:
        path.touch()
        return
    keys = rows[0].keys()
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_signal_type_csv(rows: List[dict], path: Path):
    if not rows:
        path.touch()
        return
    keys = rows[0].keys()
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
