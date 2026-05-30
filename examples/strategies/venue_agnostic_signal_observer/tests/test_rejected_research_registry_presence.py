from pathlib import Path


REGISTRY_MIN_LINES = 1080


def test_rejected_research_registry_presence() -> None:
    registry_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "REJECTED_RESEARCH.md"
    )

    assert registry_path.exists(), f"missing registry: {registry_path}"
    text = registry_path.read_text(encoding="utf-8")
    assert text.strip(), "registry is empty"
    assert len(text.splitlines()) >= REGISTRY_MIN_LINES, "registry unexpectedly shrank"

    required_strings = [
        "REJECTED",
        "NEEDS_MORE_DATA",
        "locked",
        "Final verdict",
        "Family 1",
        "funding",
        "falling-OI",
        "funding_dispersion_carry_v1",
        "Hyperliquid multi-asset funding carry Phase 0",
        "hyperliquid-oi-velocity-compression-breakout-phase0",
        "PHASE0C_DIRECTION_PROXY_UNSTABLE",
        "REJECTED_PHASE0_MECHANISM_FAILURE",
    ]
    lower_text = text.lower()
    for required in required_strings:
        haystack = lower_text if required == "locked" else text
        needle = required if required != "locked" else required.lower()
        assert needle in haystack, f"registry missing required marker: {required}"


def test_ml_atr_representative_v0_findings_registry() -> None:
    """Test that the ML+ATR representative v0 findings are in the registry."""
    registry_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "REJECTED_RESEARCH.md"
    )

    assert registry_path.exists(), f"missing registry: {registry_path}"
    text = registry_path.read_text(encoding="utf-8")

    # Required strings for the ML+ATR representative v0 findings
    required_strings = [
        "hyperliquid-btc-eth-ml-atr-representative-real-strategy-v0-validation-failed",
        "VALIDATION_CALIBRATION_FAILED_NOT_PROMOTED",
        "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_VALIDATION_CALIBRATION_FAILED",
        "REPRESENTATIVE_SPLIT_ROW_AUDIT_EXPLAINED",
        "validation AUC < 0.51",
        "24-bar feature warmup",
        "Not original v0",
        "Not full 2025-window diagnostic",
        "fresh precommitment",
    ]

    for required in required_strings:
        assert required in text, f"registry missing required marker: {required}"


def test_findings_doc_presence() -> None:
    """Test that the findings doc exists and contains required content."""
    findings_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "HYPERLIQUID_BTC_ETH_ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_FINDINGS.md"
    )

    assert findings_path.exists(), f"missing findings doc: {findings_path}"
    text = findings_path.read_text(encoding="utf-8")

    required_strings = [
        "VALIDATION_CALIBRATION_FAILED_NOT_PROMOTED",
        "validation AUC < 0.51",
        "no detectable directional predictive power",
        "No paper/live/shadow",
        "fresh precommitment",
    ]

    for required in required_strings:
        assert required in text, f"findings doc missing required marker: {required}"
