from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_asset_ctxs_fragment_merge import (
    FragmentMergeError,
    merge_asset_ctxs_fragments,
)


def _write_manifest(fragment: Path, dates: list[str], *, caveats: list[str] | None = None, unusable: dict[str, str] | None = None) -> None:
    fragment.mkdir(parents=True, exist_ok=True)
    (fragment / "manifest.json").write_text(
        json.dumps(
            {
                "date_list": dates,
                "requested_symbols": ["BTC", "ETH"],
                "coverage_caveats": caveats or [],
                "unusable_symbol_notes": unusable or {},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_rows(fragment: Path, symbol: str, rows: list[dict[str, object]]) -> None:
    with (fragment / f"{symbol}.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_exact_duplicate_rows_are_deduped_and_timestamp_sorted(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    out = tmp_path / "merged"
    _write_manifest(a, ["2026-01-01"])
    _write_manifest(b, ["2026-01-02"])
    duplicate = {"ts_event": "2026-01-01T00:00:00Z", "symbol": "BTC", "price": 100.0, "open_interest": 10.0}
    _write_rows(a, "BTC", [{"ts_event": "2026-01-01T00:01:00Z", "symbol": "BTC", "price": 101.0, "open_interest": 11.0}, duplicate])
    _write_rows(b, "BTC", [duplicate, {"ts_event": "2026-01-02T00:00:00Z", "symbol": "BTC", "price": 102.0, "open_interest": 12.0}])

    result = merge_asset_ctxs_fragments([a, b], out)

    assert result.output_dir == str(out)
    btc = _rows(out / "BTC.jsonl")
    assert [row["ts_event"] for row in btc] == ["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "2026-01-02T00:00:00Z"]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["duplicate_rows_deduped"] == 1
    assert manifest["per_symbol_row_counts"] == {"BTC": 3}


def test_conflicting_duplicate_rows_hard_fail(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_manifest(a, ["2026-01-01"])
    _write_manifest(b, ["2026-01-02"])
    _write_rows(a, "BTC", [{"ts_event": "2026-01-01T00:00:00Z", "symbol": "BTC", "price": 100.0, "open_interest": 10.0}])
    _write_rows(b, "BTC", [{"ts_event": "2026-01-01T00:00:00Z", "symbol": "BTC", "price": 101.0, "open_interest": 10.0}])

    with pytest.raises(FragmentMergeError, match="DUPLICATE_TIMESTAMP_CONFLICT"):
        merge_asset_ctxs_fragments([a, b], tmp_path / "merged")


def test_manifest_rebuilt_from_actual_rows_not_fragment_manifest_counts(tmp_path: Path) -> None:
    a = tmp_path / "a"
    out = tmp_path / "merged"
    _write_manifest(a, ["2026-01-01", "2026-01-02"])
    _write_rows(a, "ETH", [{"ts_event": "2026-01-02T00:00:00Z", "symbol": "ETH", "price": 200.0, "open_interest": 20.0}])

    merge_asset_ctxs_fragments([a], out)

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["date_list"] == ["2026-01-02"]
    assert manifest["first_date"] == "2026-01-02"
    assert manifest["last_date"] == "2026-01-02"
    assert manifest["first_last_timestamp"]["ETH"] == {
        "first_timestamp": "2026-01-02T00:00:00Z",
        "last_timestamp": "2026-01-02T00:00:00Z",
    }


def test_unusable_symbol_notes_and_source_truncation_caveat_are_preserved(tmp_path: Path) -> None:
    a = tmp_path / "2024_q3"
    b = tmp_path / "2025_q4"
    out = tmp_path / "merged"
    _write_manifest(a, ["2024-09-30"], caveats=["2024_q3 final day truncated at 2024-09-30T19:56:00Z"])
    _write_manifest(b, ["2025-12-31"], unusable={"MKR": "SYMBOL_INACTIVE_ZERO_OI_PLACEHOLDER"})
    _write_rows(a, "BTC", [{"ts_event": "2024-09-30T19:56:00Z", "symbol": "BTC", "price": 100.0, "open_interest": 10.0}])
    _write_rows(b, "MKR", [{"ts_event": "2025-12-31T23:59:00Z", "symbol": "MKR", "price": 1831.5, "open_interest": 0.0}])

    merge_asset_ctxs_fragments([a, b], out)

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_fragments"] == [str(a), str(b)]
    assert manifest["coverage_caveats"] == ["2024_q3 final day truncated at 2024-09-30T19:56:00Z"]
    assert manifest["unusable_symbol_notes"] == {"MKR": "SYMBOL_INACTIVE_ZERO_OI_PLACEHOLDER"}
