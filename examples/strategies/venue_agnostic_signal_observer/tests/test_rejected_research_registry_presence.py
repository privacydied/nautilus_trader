from pathlib import Path


def test_rejected_research_registry_presence() -> None:
    registry_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "REJECTED_RESEARCH.md"
    )

    assert registry_path.exists(), f"missing registry: {registry_path}"
    text = registry_path.read_text(encoding="utf-8")
    assert text.strip(), "registry is empty"
    assert len(text.splitlines()) >= 300, "registry unexpectedly shrank"

    required_strings = [
        "REJECTED",
        "NEEDS_MORE_DATA",
        "locked",
        "funding_dispersion_carry_v1",
    ]
    lower_text = text.lower()
    for required in required_strings:
        haystack = lower_text if required == "locked" else text
        needle = required if required != "locked" else required.lower()
        assert needle in haystack, f"registry missing required marker: {required}"
