from __future__ import annotations

from examples.strategies.venue_agnostic_signal_observer.venues.base import (
    PublicVenueAdapter,
    VenueCapability,
    VenueSymbol,
)


class FakePublicVenueAdapter:
    venue_id = "hyperliquid-public"
    capabilities = (
        VenueCapability(name="fills"),
        VenueCapability(name="funding"),
    )

    def resolve_symbol(self, symbol: str) -> VenueSymbol:
        base, quote = symbol.split("/")
        return VenueSymbol(
            venue=self.venue_id,
            raw_symbol=symbol,
            canonical_symbol=f"{base}-{quote}",
            asset=base,
            quote=quote,
        )


def test_public_venue_adapter_contract() -> None:
    adapter: PublicVenueAdapter = FakePublicVenueAdapter()
    resolved = adapter.resolve_symbol("ETH/USD")

    assert adapter.capabilities[0].public_only is True
    assert resolved.venue == "hyperliquid-public"
    assert resolved.canonical_symbol == "ETH-USD"
