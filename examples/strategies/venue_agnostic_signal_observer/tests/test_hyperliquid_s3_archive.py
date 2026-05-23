from __future__ import annotations

import json
from datetime import date

import pytest

from examples.strategies.venue_agnostic_signal_observer import run_hyperliquid_s3_archive
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive import (
    S3ProbeUnavailable,
)
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive import (
    estimate_s3_cost,
)
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive import (
    fetch_s3_archive,
)


def test_s3_fetcher_refuses_bulk_download_without_confirm_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive._aws_probe_size", lambda key: 1024)
    with pytest.raises(RuntimeError, match="S3_CONFIRM_TOKEN_REQUIRED"):
        fetch_s3_archive(["BTC"], date(2026, 1, 1), date(2026, 1, 1), tmp_path, 25.0, "")


def test_s3_fetcher_refuses_if_estimate_exceeds_budget(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive._aws_probe_size", lambda key: 1024**4)
    estimate = estimate_s3_cost(["BTC"], date(2026, 1, 1), date(2026, 1, 1))
    with pytest.raises(RuntimeError, match="S3_BUDGET_EXCEEDED"):
        fetch_s3_archive(["BTC"], date(2026, 1, 1), date(2026, 1, 1), tmp_path, 0.01, estimate.confirm_token)


def test_s3_estimate_is_real_probe_one_file_extrapolation() -> None:
    calls: list[str] = []
    def probe(key: str) -> int:
        calls.append(key)
        return 100
    estimate = estimate_s3_cost(["BTC", "ETH"], date(2026, 1, 1), date(2026, 1, 2), probe_size=probe)
    assert len(calls) == 2
    assert estimate.estimated_bytes == 2 * 2 * 24 * 100
    assert estimate.confirm_token.startswith("I_HAVE_BUDGET_")


def test_s3_estimate_reports_missing_aws_cli_without_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_cli(cmd, **kwargs):
        raise FileNotFoundError("aws")

    monkeypatch.setattr("examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive.subprocess.run", missing_cli)
    with pytest.raises(S3ProbeUnavailable, match="AWS_CLI_MISSING"):
        estimate_s3_cost(["BTC"], date(2026, 1, 1), date(2026, 1, 1))


def test_s3_runner_confirm_token_disables_estimate_only_and_calls_fetch(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def fake_estimate(coins, start, end, *, dates=None):
        return estimate_s3_cost(coins, start, end, probe_size=lambda key: 1024, dates=dates)

    def fake_fetch(coins, start, end, out_dir, max_usd_budget, confirm_token, *, dates=None):
        calls.append(
            {
                "coins": coins,
                "start": start,
                "end": end,
                "out_dir": out_dir,
                "max_usd_budget": max_usd_budget,
                "confirm_token": confirm_token,
            }
        )
        return {"status": "FETCH_CALLED"}

    monkeypatch.setattr(run_hyperliquid_s3_archive, "estimate_s3_cost", fake_estimate)
    monkeypatch.setattr(run_hyperliquid_s3_archive, "fetch_s3_archive", fake_fetch)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_hyperliquid_s3_archive",
            "--coins",
            "BTC,LINK",
            "--start-date",
            "2025-11-01",
            "--end-date",
            "2025-11-30",
            "--out",
            str(tmp_path),
            "--confirm-s3-spend",
            "I_HAVE_BUDGET_0.0",
        ],
    )

    run_hyperliquid_s3_archive.main()

    assert len(calls) == 1
    assert calls[0]["coins"] == ["BTC", "LINK"]
    assert calls[0]["out_dir"] == tmp_path
    assert calls[0]["confirm_token"] == "I_HAVE_BUDGET_0.0"
    assert json.loads(capsys.readouterr().out)["status"] == "FETCH_CALLED"


def test_s3_runner_explicit_estimate_only_still_prints_estimate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path) -> None:
    def fake_estimate(coins, start, end, *, dates=None):
        return estimate_s3_cost(coins, start, end, probe_size=lambda key: 1024, dates=dates)

    def fail_fetch(*args, **kwargs):
        raise AssertionError("fetch_s3_archive should not be called in explicit estimate-only mode")

    monkeypatch.setattr(run_hyperliquid_s3_archive, "estimate_s3_cost", fake_estimate)
    monkeypatch.setattr(run_hyperliquid_s3_archive, "fetch_s3_archive", fail_fetch)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_hyperliquid_s3_archive",
            "--estimate-only",
            "--coins",
            "BTC,LINK",
            "--start-date",
            "2025-11-01",
            "--end-date",
            "2025-11-30",
            "--out",
            str(tmp_path),
            "--confirm-s3-spend",
            "I_HAVE_BUDGET_0.0",
        ],
    )

    run_hyperliquid_s3_archive.main()

    output = json.loads(capsys.readouterr().out)
    assert output["coins"] == ["BTC", "LINK"]
    assert output["confirm_token"].startswith("I_HAVE_BUDGET_")



def _sample_l2_record(ms: int = 1_761_955_208_909) -> dict:
    return {
        "time": "2025-11-01T00:00:10.621787072",
        "ver_num": 1,
        "raw": {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": ms,
                "levels": [
                    [{"px": "109592.0", "sz": "17.48542", "n": 72}],
                    [{"px": "109593.0", "sz": "0.00011", "n": 1}],
                ],
            },
        },
    }


def _write_lz4_jsonl(path, rows: list[dict]) -> None:
    import subprocess
    jsonl = path.with_suffix(".json")
    jsonl.write_text("".join(json.dumps(row) + "\n" for row in rows))
    subprocess.run(["/usr/bin/lz4", "-f", str(jsonl), str(path)], check=True, capture_output=True)  # noqa: S603


def test_fetch_s3_archive_converts_synthetic_lz4_fixture_to_live_schema(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fixture = tmp_path / "fixture.lz4"
    _write_lz4_jsonl(fixture, [_sample_l2_record(), _sample_l2_record(1_761_955_209_458)])

    def fake_download(key: str, dest) -> int:
        dest.write_bytes(fixture.read_bytes())
        return dest.stat().st_size

    monkeypatch.setattr("examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive._aws_probe_size", lambda key: fixture.stat().st_size)
    monkeypatch.setattr("examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive._aws_download", fake_download)
    estimate = estimate_s3_cost(["BTC"], date(2025, 11, 1), date(2025, 11, 1), probe_size=lambda key: fixture.stat().st_size)

    result = fetch_s3_archive(["BTC"], date(2025, 11, 1), date(2025, 11, 1), tmp_path / "out", 25.0, estimate.confirm_token, hours=[0])

    import pyarrow.parquet as pq
    out = tmp_path / "out" / "BTC" / "l2book" / "2025-11-01" / "00.parquet"
    table = pq.read_table(out)
    df = table.to_pandas()
    assert result.total_rows == 2
    assert list(df["ts_event"]) == [1_761_955_208_909_000_000, 1_761_955_209_458_000_000]
    assert df.loc[0, "coin"] == "BTC"
    assert df.loc[0, "seq"] == 1_761_955_208_909
    assert df.loc[0, "bid_px_0"] == 109592.0
    assert df.loc[0, "bid_sz_0"] == 17.48542
    assert df.loc[0, "bid_n_0"] == 72
    assert df.loc[0, "ask_px_0"] == 109593.0
    assert df.loc[0, "ask_sz_0"] == 0.00011
    assert df.loc[0, "ask_n_0"] == 1
    assert "bid_px_19" in df.columns
    assert result.manifest_path.endswith("fetch_manifest.json")


def test_fetch_s3_archive_skips_existing_valid_parquet(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    from examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive import (
        expected_l2book_columns,
    )

    out = tmp_path / "out" / "BTC" / "l2book" / "2025-11-01" / "00.parquet"
    out.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pandas(pd.DataFrame([{col: ("BTC" if col == "coin" else 1) for col in expected_l2book_columns()}]), preserve_index=False), out)

    def fail_download(*args, **kwargs):
        raise AssertionError("valid existing parquet should be skipped")

    monkeypatch.setattr("examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive._aws_probe_size", lambda key: 1024)
    monkeypatch.setattr("examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive._aws_download", fail_download)
    estimate = estimate_s3_cost(["BTC"], date(2025, 11, 1), date(2025, 11, 1), probe_size=lambda key: 1024)
    result = fetch_s3_archive(["BTC"], date(2025, 11, 1), date(2025, 11, 1), tmp_path / "out", 25.0, estimate.confirm_token, hours=[0])
    assert result.files_written == []
    assert result.total_rows == 1


def test_fetch_s3_archive_cost_guard_triggers_mid_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fixture = tmp_path / "fixture.lz4"
    _write_lz4_jsonl(fixture, [_sample_l2_record()])

    def fake_download(key: str, dest) -> int:
        dest.write_bytes(fixture.read_bytes())
        return 1024**4

    monkeypatch.setattr("examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive._aws_probe_size", lambda key: 1)
    monkeypatch.setattr("examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive._aws_download", fake_download)
    estimate = estimate_s3_cost(["BTC"], date(2025, 11, 1), date(2025, 11, 1), probe_size=lambda key: 1)
    with pytest.raises(RuntimeError, match="COST_GUARD_TRIGGERED"):
        fetch_s3_archive(["BTC"], date(2025, 11, 1), date(2025, 11, 1), tmp_path / "out", 25.0, estimate.confirm_token, hours=[0])


def test_s3_runner_date_list_estimate_matches_equivalent_range(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path) -> None:
    dates = tmp_path / "dates.txt"
    dates.write_text("2025-11-01\n2025-11-02\n")

    def fake_estimate(coins, start, end, *, dates=None):
        return estimate_s3_cost(coins, start, end, probe_size=lambda key: 100)

    monkeypatch.setattr(run_hyperliquid_s3_archive, "estimate_s3_cost", fake_estimate)
    monkeypatch.setattr("sys.argv", ["run_hyperliquid_s3_archive", "--coins", "BTC", "--date-list", str(dates), "--estimate-only"])
    run_hyperliquid_s3_archive.main()
    date_list_estimate = json.loads(capsys.readouterr().out)

    monkeypatch.setattr("sys.argv", ["run_hyperliquid_s3_archive", "--coins", "BTC", "--start-date", "2025-11-01", "--end-date", "2025-11-02", "--estimate-only"])
    run_hyperliquid_s3_archive.main()
    range_estimate = json.loads(capsys.readouterr().out)

    assert date_list_estimate["estimated_bytes"] == range_estimate["estimated_bytes"]
