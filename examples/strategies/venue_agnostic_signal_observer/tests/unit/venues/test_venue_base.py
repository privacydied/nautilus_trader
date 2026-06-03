from __future__ import annotations

from examples.strategies.venue_agnostic_signal_observer.venues.base import (
    PublicVenueAdapter,
    VenueCapability,
    VenueSymbol,
)


def test_venue_capability_defaults_public_only() -> None:
    capability = VenueCapability(name="trades")
    assert capability.public_only is True


class FakeVenueAdapter:
    venue_id = "fake-venue"
    capabilities = (VenueCapability(name="quotes"),)

    def resolve_symbol(self, symbol: str) -> VenueSymbol:
        asset, quote = symbol.split("/")
        return VenueSymbol(
            venue=self.venue_id,
            raw_symbol=symbol,
            canonical_symbol=f"{asset}-{quote}",
            asset=asset,
            quote=quote,
        )


def test_fake_public_venue_adapter_resolves_symbol() -> None:
    adapter: PublicVenueAdapter = FakeVenueAdapter()
    resolved = adapter.resolve_symbol("BTC/USD")
    assert resolved.venue == "fake-venue"
    assert resolved.raw_symbol == "BTC/USD"
    assert resolved.canonical_symbol == "BTC-USD"
    assert resolved.asset == "BTC"
    assert resolved.quote == "USD"
