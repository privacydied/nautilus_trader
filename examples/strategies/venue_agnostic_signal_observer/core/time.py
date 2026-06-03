from __future__ import annotations


def seconds_to_ns(seconds: float) -> int:
    return int(seconds * 1_000_000_000)


def milliseconds_to_ns(milliseconds: float) -> int:
    return int(milliseconds * 1_000_000)


def nanoseconds_to_seconds(ns: int) -> float:
    return ns / 1_000_000_000
