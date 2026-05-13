"""Replay CLI for Phase 2 captured data."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description="Replay Phase 2 captured observer data offline")
    p.add_argument("--capture-dir",type=str,required=True,help="Path to capture directory")
    args=p.parse_args()
    from .live_replay import replay_capture
    from .config import PolymarketArbConfig
    config=PolymarketArbConfig()
    result=replay_capture(Path(args.capture_dir),config)
    print(json.dumps(result,indent=2,default=str))
    if not result.get("deterministic",False):
        print("FAIL: replay is not deterministic",file=sys.stderr); return 1
    return 0

if __name__=="__main__":
    sys.exit(main())