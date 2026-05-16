# Polymarket Complement Arb Shadow Report

- verdict: NEEDS_MORE_DATA
- reasons: MIN_DETECTED_OPPORTUNITIES_NOT_MET, MIN_SAME_CONDITION_VALID_OPPORTUNITIES_NOT_MET, MIN_NON_DUST_OPPORTUNITIES_NOT_MET, MIN_PESSIMISTIC_PAIRED_FILLS_NOT_MET
- paired_gain: 0
- unwind_loss: 0
- net_shadow_harvest: 0
- expected_edge_per_detected_opportunity: 0.0
- key_metric: paired_fill_realized_net_edge_per_share

## Raw rates
{
  "non_dust_rate": {
    "denominator": 0,
    "numerator": 0,
    "rate": null
  },
  "one_leg_fill_rate": {
    "denominator": 0,
    "numerator": 0,
    "rate": null
  },
  "paired_fill_rate": {
    "denominator": 0,
    "numerator": 0,
    "rate": null
  },
  "pessimistic_paired_fill_rate": {
    "denominator": 0,
    "numerator": 0,
    "rate": null
  },
  "same_condition_valid_rate": {
    "denominator": 0,
    "numerator": 0,
    "rate": null
  }
}

## Trade Evidence

- status: TRADE_EVIDENCE_READY
- fetch attempts: 27
- fetch successes: 27
- fetch empty responses: 0
- fetch errors: 0
- rows fetched (total across all tokens): 178
- rows joined to detected opportunities: 0
- opportunities missing trade data: 0

**Interpretation**: Trade data was fetched and successfully joined to detected
opportunity tokens. Trade evidence path is operational.

Theoretical edge is not treated as tradeable edge. Only pessimistic paired fills with positive realized shadow net edge can support CANDIDATE_FOR_LONGER_OBSERVATION.