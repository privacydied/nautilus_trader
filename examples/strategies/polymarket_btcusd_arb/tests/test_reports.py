from examples.strategies.polymarket_btcusd_arb.reports import make_report_paths,write_reports
from examples.strategies.polymarket_btcusd_arb.models import CandidateGroupResult
def test_reports_written(tmp_path):
 p=make_report_paths(tmp_path,'r'); g=CandidateGroupResult(1,5,'b','NEEDS_MORE_DATA','no_events_met_threshold',0); write_reports(p,summary={'branch_name':'polymarket-btcusd-arb-phase1'},candidates=[],rejections=[],baseline=[],groups=[g],safety={'ok':True},parity={},cache_metadata={}); assert p.summary_json.exists() and p.safety_check_json.exists() and 'YES-token-only' in p.report_md.read_text()
