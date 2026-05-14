import json
from pathlib import Path
from examples.strategies.polymarket_btcusd_arb.fair_probability import binary_call_probability
def test_probability_parity_fixtures():
 cases=json.loads((Path(__file__).parent/'fixtures/probability_cases.json').read_text())
 for c in cases:
  i=c['inputs']; got=binary_call_probability(i['spot'],i['strike'],i['time_to_expiry_years'],i['sigma'],i['risk_free_rate']); assert abs(got-c['expected']['probability'])<1e-9,c['case_id']
