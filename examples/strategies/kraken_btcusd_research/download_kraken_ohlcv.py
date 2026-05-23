#!/usr/bin/env python3
"""
Download historical OHLCV data from Kraken public API.

Usage:
    python download_kraken_ohlcv.py --pair BTC/USD --interval 1 --since 2024-01-01 --out data/kraken/BTCUSD_1m.csv

Kraken API:
    https://docs.kraken.com/rest/
"""

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Dict, List, Optional

import requests

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Kraken API constants
KRAKEN_API_URL = "https://api.kraken.com/0/public/OHLC"
MAX_RESULTS_PER_REQUEST = 1000  # Kraken's limit
REQUEST_DELAY = 2  # Seconds between requests to respect rate limits


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download historical OHLCV data from Kraken"
    )
    parser.add_argument(
        "--pair",
        type=str,
        required=True,
        help="Currency pair (e.g., BTC/USD, ETH/USD)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=1,
        choices=[1, 5, 15, 30, 60, 240, 1440, 10080],
        help="Time interval in minutes (default: 1)",
    )
    parser.add_argument(
        "--since",
        type=str,
        help="Start date/time (ISO format, e.g., 2024-01-01T00:00:00)",
    )
    parser.add_argument(
        "--until",
        type=str,
        help="End date/time (ISO format)",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Output CSV file path",
    )
    return parser.parse_args()


def kraken_pair_to_api_pair(pair: str) -> str:
    """
    Convert user-friendly pair format to Kraken API format.
    
    Args:
        pair: Currency pair like "BTC/USD"
    
    Returns:
        Kraken API pair string like "XBTUSD"
    """
    pair = pair.upper()
    if "/" not in pair:
        raise ValueError(f"Invalid pair format: {pair}. Use 'BTC/USD' format.")
    
    base, quote = pair.split("/")
    
    # Kraken uses XBT for Bitcoin, others use standard codes
    if base == "BTC":
        base = "XBT"
    
    # Map common quote currencies
    quote_map = {
        "USD": "USD",
        "US Dollar": "USD",
        "USDT": "USDT",
        "USDTERC20": "USDT",
        "EUR": "EUR",
        "EURUSD": "EUR",
        "GBP": "GBP",
        "JPY": "JPY",
    }
    quote = quote_map.get(quote, quote)
    
    return f"{base}{quote}"


def unix_timestamp_to_datetime(ts: float) -> datetime:
    """Convert UNIX timestamp to datetime object."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def datetime_to_unix_timestamp(dt: datetime) -> float:
    """Convert datetime object to UNIX timestamp."""
    return dt.replace(tzinfo=timezone.utc).timestamp()


def fetch_ohlc_batch(pair: str, interval: int, since: Optional[float]) -> Dict:
    """
    Fetch one batch of OHLC data from Kraken API.
    
    Args:
        pair: Kraken pair string (e.g., "XBTUSD")
        interval: Time interval in minutes
        since: UNIX timestamp to start from
    
    Returns:
        JSON response from API
    """
    params = {
        "pair": pair,
        "interval": interval,
    }
    if since:
        params["since"] = since
    
    try:
        response = requests.get(
            KRAKEN_API_URL,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        
        if data["error"] and data["error"][0].startswith("E"):
            raise ValueError(f"Kraken API error: {data['error']}")
            
        return data
    except Exception as e:
        logger.error(f"Failed to fetch OHLC data: {str(e)}")
        raise


def fetch_all_ohlc_data(pair: str, interval: int, since: Optional[datetime] = None, until: Optional[datetime] = None) -> List[Dict]:
    """
    Fetch all OHLC data from Kraken API, handling pagination.
    
    Args:
        pair: Kraken pair string
        interval: Time interval
        since: Start datetime (UTC)
        until: End datetime (UTC)
    
    Returns:
        List of OHLC data points
    """
    all_data = []
    last_timestamp = None
    
    # Convert datetime to UNIX timestamps
    since_ts = datetime_to_unix_timestamp(since) if since else None
    until_ts = datetime_to_unix_timestamp(until) if until else None
    
    while True:
        # Fetch batch
        batch = fetch_ohlc_batch(pair, interval, since_ts)
        result = batch.get("result", {})
        
        # Get pair data
        pair_data = result.get(pair, [])
        if not pair_data:
            break
            
        # Process data
        for row in pair_data:
            timestamp = row[0]
            # Convert timestamp to datetime for filtering
            dt = unix_timestamp_to_datetime(timestamp)
            
            # Check if we've reached the until date
            if until_ts and timestamp > until_ts:
                continue
            
            all_data.append(
                {
                    "timestamp": dt.isoformat(),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "vwap": float(row[5]),
                    "volume": float(row[6]),
                    "count": int(row[7]),
                }
            )
            
            last_timestamp = timestamp
        
        # Check if we have enough data or reached until date
        if not pair_data or (until_ts and last_timestamp and last_timestamp >= until_ts):
            break
            
        # Update since for next batch (Kraken returns last timestamp automatically)
        since_ts = last_timestamp
        
        # Rate limiting
        if last_timestamp:
            logger.info(f"Downloaded {len(all_data)} rows so far...")
            sleep(REQUEST_DELAY)
        else:
            break
    
    return all_data


def save_to_csv(data: List[Dict], output_path: Path) -> None:
    """
    Save OHLC data to CSV file.
    
    Args:
        data: List of OHLC data dictionaries
        output_path: Path to output CSV file
    """
    if not data:
        logger.warning("No data to save")
        return
        
    # Ensure directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write CSV
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "vwap", "volume", "count"])
        writer.writeheader()
        writer.writerows(data)
    
    logger.info(f"Saved {len(data)} rows to {output_path}")


def main():
    args = parse_args()
    
    # Validate output path
    output_path = Path(args.out)
    if output_path.suffix.lower() != ".csv":
        output_path = output_path.with_suffix(".csv")
        logger.warning(f"Output file extension changed to .csv: {output_path}")
    
    try:
        # Convert pair to Kraken format
        kraken_pair = kraken_pair_to_api_pair(args.pair)
        logger.info(f"Fetching {args.pair} data from Kraken (API pair: {kraken_pair})")
        
        # Convert since/until to datetime
        since_dt = None
        until_dt = None
        if args.since:
            since_dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        if args.until:
            until_dt = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
        
        # Fetch all data
        all_data = fetch_all_ohlc_data(
            pair=kraken_pair,
            interval=args.interval,
            since=since_dt,
            until=until_dt,
        )
        
        # Save to CSV
        save_to_csv(all_data, output_path)
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()