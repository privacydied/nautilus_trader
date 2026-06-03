from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from .runner import LeverageMode


__all__ = (
    "StudyConfig",
    "TargetedBackwardLookupCheckpoint",
    "TargetedBackwardLookupSummary",
)


@dataclass
class StudyConfig:
    study_id: str = field(default="hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0")
    run_id: str = field(default="")
    out_root: str = field(default="")
    data_root: str | None = None
    preferred_symbol: str = "SOL"
    fallback_symbols: tuple[str, ...] = ("DOGE", "LINK", "AVAX", "ADA")
    start_date: str = ""
    end_date: str = ""
    dry_run: bool = False
    plan_only: bool = False
    include_remote_plan: bool = False
    allow_s3_archive_read: bool = False
    requester_pays: bool = False
    cache_only: bool = True
    max_download_bytes: int = 100_000_000  # 100 MB hard cap
    schema_sample_limit: int = 10_000
    max_hours: int = 6
    min_oi_coverage_fraction: float = 0.40
    burn_in_days: int = 0
    leverage_mode: str = "exact_required"
    bound_diagnostic: bool = False
    leverage_source_plan_only: bool = False
    wall2_margin_mode_killtest: bool = False
    wall2_update_leverage_source_probe: bool = False
    wall2_targeted_holder_leverage_lookup: bool = False
    target_symbol: str = "SOL"
    target_top_n: int = 30
    replica_cmds_selection_mode: str = "chronology_strict_newest_prior"
    resume_targeted_backscan: bool = False
    memory_audit: bool = False
    progress_every_objects: int = 1

    def effective_leverage_mode(self) -> LeverageMode:
        if self.bound_diagnostic:
            return LeverageMode.MAX_BOUND_DIAGNOSTIC
        return LeverageMode.EXACT_REQUIRED


@dataclass
class TargetedBackwardLookupCheckpoint:
    run_id: str = ""
    selection_mode: str = "chronology_strict_newest_prior"
    target_symbol: str = "SOL"
    target_top_n: int = 30
    cap: int = 0
    compressed_bytes_downloaded: int = 0
    objects_planned_total: int = 0
    objects_processed_count: int = 0
    objects_processed_keys: list[str] = field(default_factory=list)
    last_completed_object_key: str = ""
    last_completed_object_timestamp: str = ""
    target_pairs_total: int = 0
    target_pairs_resolved: int = 0
    target_pairs_unresolved: int = 0
    target_notional_resolved: Decimal = field(default_factory=lambda: Decimal(0))
    target_notional_unresolved: Decimal = field(default_factory=lambda: Decimal(0))
    resolved_pair_states: list[dict[str, Any]] = field(default_factory=list)
    unresolved_pair_keys: list[str] = field(default_factory=list)
    stop_rule_if_any: str = ""
    safe_to_resume: bool = True


@dataclass
class TargetedBackwardLookupSummary:
    target_symbol: str = "SOL"
    target_asset_id: str = ""
    target_top_n: int = 30
    selected_target_pairs: int = 0
    selected_target_notional: Decimal = field(default_factory=lambda: Decimal(0))
    selected_target_notional_fraction: float = 0.0
    selected_target_notional_fraction_of_SOL: float = 0.0
    selected_target_notional_fraction_of_total_open: float = 0.0
    selected_unique_addresses: int = 0
    objects_considered: int = 0
    objects_selected: int = 0
    objects_downloaded: int = 0
    compressed_bytes_downloaded: int = 0
    cap: int = 0
    remaining_cap: int = 0
    cap_exhausted: bool = False
    coverage_start_reached: bool = False
    stop_rule: str = ""
    actions_decoded_total: int = 0
    updateLeverage_count_total: int = 0
    target_updateLeverage_matches_total: int = 0
    target_pairs_resolved: int = 0
    target_pairs_unresolved: int = 0
    target_notional_resolved: Decimal = field(default_factory=lambda: Decimal(0))
    target_notional_unresolved: Decimal = field(default_factory=lambda: Decimal(0))
    target_notional_resolved_fraction: float = 0.0
    coverage_start_reached_for_unresolved_pairs: bool = False
    source_or_decoder_blocked: bool = False
    # Listing audit fields (mirrors scan plan for cross-reference)
    objects_listed_total: int = 0
    objects_skipped_over_cap: int = 0
    objects_skipped_missing_size: int = 0
    objects_skipped_non_data: int = 0
    smallest_listed_object_size: int = 0
    largest_listed_object_size: int = 0
    # Chronology-strict selection audit (Wall 2)
    selection_mode: str = "chronology_strict_newest_prior"
    newest_downloaded_object_timestamp: str = ""
    oldest_downloaded_object_timestamp: str = ""
    scan_span_hours: float = 0.0
    scan_span_days: float = 0.0
    chronology_strict: bool = False
    selected_objects_are_newest_prior_sequence: bool = False
    identity_join_verdict: str = "UNVERIFIED"
    objects_planned_total: int = 0
    objects_processed_count: int = 0
    last_completed_object_key: str = ""
    last_completed_object_timestamp: str = ""
    safe_to_resume: bool = False
    rss_mb_peak_if_available: float | None = None
