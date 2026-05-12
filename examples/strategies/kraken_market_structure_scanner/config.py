"""V6: Market Structure Scanner — Configuration."""
from dataclasses import dataclass
from typing import Optional, List

from dataclasses import dataclass, field

@dataclass(frozen=True)
class FeeConfig:
    """Default taker fees in bps (1 bp = 0.01%)."""
    kraken: float = 40.0
    coinbase: float = 40.0
    binance: float = 10.0


@dataclass
class ScannerConfig:
    symbols: List[str] = field(default_factory=lambda: ["BTC/USD", "ETH/USD"])
    venues: List[str] = field(default_factory=lambda: ["kraken", "coinbase"])
    poll_interval_seconds: float = 2.0
    min_net_edge_bps: float = 0.0   # log all; filter at read time
    latency_buffer_bps: float = 10.0
    max_runtime_seconds: Optional[float] = None
    output_dir: str = "reports/v6_market_structure"
