from __future__ import annotations

import argparse

from examples.strategies.venue_agnostic_signal_observer.runners.registry import list_runners


def main() -> int:
    parser = argparse.ArgumentParser(description="List scaffold hypothesis runners")
    parser.add_argument("--list", action="store_true", help="List registered runners")
    args = parser.parse_args()

    if args.list:
        runners = list_runners()
        if not runners:
            print("no registered runners")
            return 0
        for registered in runners:
            print(f"{registered.name}\t{registered.study_id}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
