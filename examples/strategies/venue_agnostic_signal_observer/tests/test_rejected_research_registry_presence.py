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
