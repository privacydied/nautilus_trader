# liquidation_cascade_aftershock_v0 reconnaissance

## Scope

Study ID: `liquidation_cascade_aftershock_v0`

Required archive source: Binance Vision public archive, USD-M futures, daily liquidationSnapshot:

`https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/<SYMBOL>/<SYMBOL>-liquidationSnapshot-YYYY-MM-DD.zip`

Required symbols for reconnaissance: `SOLUSDT`, `BTCUSDT`.

## Actual URL/path shape checked

S3 listing endpoint checked:

`https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?list-type=2&prefix=data/futures/um/daily/liquidationSnapshot/<SYMBOL>/&max-keys=10`

Direct object URL shape checked:

`https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/<SYMBOL>/<SYMBOL>-liquidationSnapshot-YYYY-MM-DD.zip`

## Actual URL used for SOLUSDT recon

Attempted direct public HTTPS URLs:

- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/SOLUSDT/SOLUSDT-liquidationSnapshot-2026-04-30.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/SOLUSDT/SOLUSDT-liquidationSnapshot-2026-04-29.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/SOLUSDT/SOLUSDT-liquidationSnapshot-2026-04-28.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/SOLUSDT/SOLUSDT-liquidationSnapshot-2026-01-01.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/SOLUSDT/SOLUSDT-liquidationSnapshot-2025-12-31.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/SOLUSDT/SOLUSDT-liquidationSnapshot-2024-01-01.zip`

All returned HTTP 404.

S3 listing checked:

`https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?list-type=2&prefix=data/futures/um/daily/liquidationSnapshot/SOLUSDT/&max-keys=10`

Result: `KeyCount=0`.

## Actual URL used for BTCUSDT recon

Attempted direct public HTTPS URLs:

- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-2026-04-30.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-2026-04-29.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-2026-04-28.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-2026-01-01.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-2025-12-31.zip`
- `https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-2024-01-01.zip`

All returned HTTP 404.

S3 listing checked:

`https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?list-type=2&prefix=data/futures/um/daily/liquidationSnapshot/BTCUSDT/&max-keys=10`

Result: `KeyCount=0`.

## Parent directory listing result

Binance Vision USD-M daily top-level listing was checked with delimiter:

`https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?list-type=2&prefix=data/futures/um/daily/&delimiter=/&max-keys=1000`

Observed USD-M daily directories:

- `aggTrades/`
- `bookDepth/`
- `bookTicker/`
- `indexPriceKlines/`
- `klines/`
- `markPriceKlines/`
- `metrics/`
- `premiumIndexKlines/`
- `trades/`

No `liquidationSnapshot/` directory was present under USD-M daily.

A control listing for COIN-M daily did show `data/futures/cm/daily/liquidationSnapshot/`, including keys such as:

`data/futures/cm/daily/liquidationSnapshot/BTCUSD_PERP/BTCUSD_PERP-liquidationSnapshot-2023-06-25.zip`

This confirms the listing method is capable of finding liquidationSnapshot archives where Binance Vision publishes them. It does not satisfy the study requirement because the frozen universe is USD-M symbols (`SOLUSDT`, `BTCUSDT`, etc.), not COIN-M symbols.

## Observed schema / exact column names

No USD-M `liquidationSnapshot` CSV or ZIP object was available for `SOLUSDT` or `BTCUSDT`, so no USD-M liquidationSnapshot schema could be observed.

Observed columns: unavailable.

## Timestamp unit detected

Unavailable. No USD-M liquidationSnapshot CSV rows were reachable.

## Side field values observed

Unavailable. No USD-M liquidationSnapshot CSV rows were reachable.

## Row counts for the two recon days

The requested recent/available USD-M recon day could not be found for either symbol.

- `SOLUSDT`: 0 reachable USD-M liquidationSnapshot rows; S3 listing `KeyCount=0`.
- `BTCUSDT`: 0 reachable USD-M liquidationSnapshot rows; S3 listing `KeyCount=0`.

## Parser go/no-go decision

Go/no-go: **NO-GO**.

Reason: the required Binance Vision USD-M daily liquidationSnapshot archive path is unavailable for the required symbols. The parser schema, timestamp unit, symbol field, side field, and notional field mapping cannot be verified from the required source.

Phase 0 must not proceed to implementation or full audit using alternate sources, alternate market type, private APIs, authenticated APIs, live streams, or non-Binance-Vision data.

## Whether full Phase 0 should proceed

Full Phase 0 should **not** proceed from this branch state.

Documented blocked verdict: `ARCHIVE_UNAVAILABLE`.

Safety notes:

- Public data only was used.
- Binance Vision archive only was queried.
- No API keys, auth, private keys, orders, execution imports, live trading, paper trading, shadow executor, or bot path were used.
- No returns, costs, null tests, FDR, profitability metrics, or trade/candidate verdicts were computed.
- `REJECTED_RESEARCH.md` was not updated.
