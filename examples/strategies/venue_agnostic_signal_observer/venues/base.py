from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VenueCapability:
    name: str
    public_only: bool = True


@dataclass(frozen=True)
class VenueSymbol:
    venue: str
    raw_symbol: str
    canonical_symbol: str
    asset: str
    quote: str


class PublicVenueAdapter(Protocol):
    venue_id: str
    capabilities: tuple[VenueCapability, ...]

    def resolve_symbol(self, symbol: str) -> VenueSymbol:
        ...
