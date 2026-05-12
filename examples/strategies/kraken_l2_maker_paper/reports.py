"""V7: Report writing for L2 maker paper simulator."""
import json
from pathlib import Path
from typing import Dict


def write_event(event: Dict, fh) -> None:
    """Append one event to JSONL. Flushes after every write."""
    fh.write(json.dumps(event) + "\n")
    fh.flush()


def write_summary(summary: Dict, output_dir: Path) -> Path:
    """Write summary.json."""
    out = output_dir / "summary.json"
    with open(out, "w") as f:
        json.dump(summary, f, indent=2)
    return out
