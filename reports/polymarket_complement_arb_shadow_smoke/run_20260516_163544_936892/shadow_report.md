# Polymarket Complement Arb Shadow Report

- verdict: NEEDS_MORE_DATA
- reasons: MIN_PESSIMISTIC_PAIRED_FILLS_NOT_MET
- paired_gain: 0
- unwind_loss: 0
- net_shadow_harvest: 0
- expected_edge_per_detected_opportunity: 0.0
- key_metric: paired_fill_realized_net_edge_per_share

## Raw rates
{
  "non_dust_rate": {
    "denominator": 528,
    "numerator": 399,
    "rate": 0.7556818181818182
  },
  "one_leg_fill_rate": {
    "denominator": 528,
    "numerator": 0,
    "rate": 0.0
  },
  "paired_fill_rate": {
    "denominator": 528,
    "numerator": 0,
    "rate": 0.0
  },
  "pessimistic_paired_fill_rate": {
    "denominator": 528,
    "numerator": 0,
    "rate": 0.0
  },
  "same_condition_valid_rate": {
    "denominator": 528,
    "numerator": 528,
    "rate": 1.0
  }
}

## Trade Evidence

- status: PUBLIC_TRADE_EVIDENCE_INSUFFICIENT
- fetch attempts: 22
- fetch successes: 22
- fetch empty responses: 0
- fetch errors: 0
- rows fetched (total across all tokens): 1000
- rows joined to detected opportunities: 0
- opportunities missing trade data: 176

**Interpretation**: The data-api /trades endpoint (global feed, no per-asset filtering)
did return some trade rows, but they could not be joined to any detected opportunity's
YES/NO token IDs. This means the tokens with detected opportunities do not appear in
the most recent ~1000 global trades — the evidence path works but the tokens are
not actively trading near the top of the global feed.

Theoretical edge is not treated as tradeable edge. Only pessimistic paired fills with positive realized shadow net edge can support CANDIDATE_FOR_LONGER_OBSERVATION.