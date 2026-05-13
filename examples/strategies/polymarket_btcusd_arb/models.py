from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
Verdict=Literal["NEEDS_MORE_DATA","REJECTED","CANDIDATE_FOR_LONGER_OBSERVATION"]
@dataclass(frozen=True)
class BinanceReferenceState:
    symbol:str; price:float; ts_event_ns:int; ts_recv_ns:int|None=None; best_bid:float|None=None; best_ask:float|None=None; last_trade:float|None=None; spread_bps:float|None=None; source:str="trade_proxy"
@dataclass(frozen=True)
class PolymarketQuoteState:
    market_slug:str; token_id:str|None; outcome:str; quoted_probability:float; ts_event_ns:int; ts_recv_ns:int|None=None; best_bid:float|None=None; best_ask:float|None=None; last_trade:float|None=None; spread_bps:float|None=None; depth:float|None=None
@dataclass(frozen=True)
class PolymarketContractMetadata:
    market_slug:str; strike:float; expiry_ns:int; condition_id:str|None=None; yes_token_id:str|None=None; no_token_id:str|None=None; yes_outcome:str="YES"; no_outcome:str="NO"; has_no_token:bool=False; sanitized_info:bool=True; resolution_metadata:dict[str,Any]=field(default_factory=dict)
@dataclass(frozen=True)
class PolymarketMarketSnapshot: metadata:PolymarketContractMetadata; quotes:tuple[PolymarketQuoteState,...]
@dataclass(frozen=True)
class FairProbabilityResult: fair_probability:float; sigma_used:float; time_to_expiry_years:float; settlement_basis_bps:float|None=None; settlement_staleness_ms:int|None=None; expiry_configured:bool=True
@dataclass(frozen=True)
class SettlementPredictionResult: probability_yes:float; confidence:float; direction:Literal["UP","DOWN","NEUTRAL","UNKNOWN"]; score:float=0.0; in_no_trade_band:bool=False; contributing_signals:tuple[str,...]=()
@dataclass(frozen=True)
class DivergenceSignal:
    market_slug:str; side:Literal["YES"]; threshold_bps:float; lookback_ns:int; tte_bucket:str; fair_probability:float; quoted_probability:float; raw_divergence_bps:float; maker_fee_bps:float; spread_bps:float; latency_buffer_bps:float; settlement_buffer_bps:float; stale_buffer_bps:float; net_divergence_bps:float; ts_event_ns:int; expiry_ns:int; direction:Literal["LONG_YES","NO_SIGNAL"]="LONG_YES"; rejection_reason:str|None=None
@dataclass(frozen=True)
class ForwardOutcome:
    signal:DivergenceSignal; horizon_ns:int; forward_prob_at_horizon:float|None; probability_move_bps:float|None; direction_adjusted_move_bps:float|None; maker_fee_adjusted_edge_bps:float|None; spread_adjusted_edge_bps:float|None; settlement_payoff:float|None; favorable:bool|None; adverse_selected:bool|None; rejection_reason:str|None=None
@dataclass(frozen=True)
class CandidateGroupResult:
    lookback_ns:int; threshold_bps:float; tte_bucket:str; verdict:Verdict; reason:str; candidate_count:int; mean_edge_bps:float|None=None; median_edge_bps:float|None=None; win_rate:float|None=None; baseline_mean_edge_bps:float|None=None; beats_baseline_bps:float|None=None; gates:dict[str,bool]=field(default_factory=dict)
@dataclass(frozen=True)
class BaselineResult: ts_event_ns:int; tte_bucket:str; horizon_ns:int; maker_fee_adjusted_edge_bps:float|None
@dataclass(frozen=True)
class ReportPaths:
    run_dir:Path; summary_json:Path; candidates_jsonl:Path; rejections_jsonl:Path; baseline_jsonl:Path; candidate_groups_csv:Path; report_md:Path; safety_check_json:Path; parity_check_json:Path; cache_metadata_json:Path
@dataclass(frozen=True)
class CacheMetadata:
    source:str; symbol_or_slug:str; start_ns:int; end_ns:int; row_count:int; created_at:str; data_hash:str; sanitize_info:bool; loader_version_or_module:str; path:str|None=None
@dataclass(frozen=True)
class BacktestRunResult: run_id:str; branch:str; market_slug:str; summary:dict[str,Any]; report_paths:ReportPaths
