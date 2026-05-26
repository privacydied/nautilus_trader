# conductor/policy.py — Centralized policy rules for the conductor.
#
# Any change to PROMOTION_MEAN_NET_BPS_MIN, is_promotable_group, or promotion
# semantics requires bumping PROMOTION_RULE_ID, e.g. _v0 -> _v1, because
# promotion_rule_id is included in precommitment hash material.

PROMOTION_MEAN_NET_BPS_MIN = 0.0
PROMOTION_RULE_ID = "mean_net_bps_gt_0_and_valid_count_gte_min_events_v0"

# Verdict strings that the conductor must never emit, accept, or forward.
# These are reserved for paper / shadow / live execution pipelines which do
# not exist inside the conductor.
FORBIDDEN_VERDICTS = frozenset({
    "TRADE_READY",
    "EXECUTION_READY",
    "LIVE_READY",
    "CANDIDATE_FOR_LIVE",
})


def is_promotable_group(*, mean_net_bps: float, valid_count: int, min_events: int) -> bool:
    """Return True iff the group qualifies for auto-precommitment promotion.

    Exact rule:
        mean_net_bps > 0
        AND
        valid_count >= min_events

    No win-rate filter in v0.
    No null filter in v0.
    No FDR filter in v0.
    No cost sensitivity filter in v0.
    """
    if mean_net_bps > PROMOTION_MEAN_NET_BPS_MIN and valid_count >= min_events:
        return True
    return False