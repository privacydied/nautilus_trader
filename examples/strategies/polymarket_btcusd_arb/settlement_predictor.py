from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

from .models import BinanceReferenceState, SettlementPredictionResult

MIN_SAMPLES_FOR_PREDICTION = 5
SIGNAL_OPPOSITION_SCALE = 0.78
SIGNAL_OPPOSITION_STRONG_SCALE = 0.50
SIGNAL_OPPOSITION_STRONG_PROB_SCALE = 0.75
SIGNAL_OPPOSITION_PROB_SCALE = 0.88
FLIP_CONFIRM_MARGIN = 0.10
FLIP_MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class SettlementPredictorConfig:
    """Pure-Python mirror of arb-bot settlement predictor defaults used by Phase 1.

    Units match arb-bot Rust: timestamps in milliseconds for the predictor internals,
    prices in quote currency, CVD/liquidation notionals in USD, and rates/basis in bps.
    """

    min_history_ms: int = 0
    momentum_30s_scale_bps: float = 12.0
    momentum_3m_scale_bps: float = 30.0
    cvd_min_volume_btc: float = 5.0
    cvd_strong_tfi_threshold: float = 0.15
    cvd_30s_scale_usd: float = 25_000.0
    cvd_3m_scale_usd: float = 120_000.0
    funding_neutral_band_bps: float = 3.0
    funding_strong_bps: float = 8.0
    funding_scale_bps: float = 1.0
    perp_basis_neutral_band_bps: float = 10.0
    basis_scale_bps: float = 25.0
    flush_threshold_usd: float = 1_000_000.0
    flush_cooling_usd: float = 150_000.0
    flip_hysteresis_ms: int = 45_000
    no_trade_band_low: float = 0.45
    no_trade_band_high: float = 0.55


@dataclass(frozen=True)
class SettlementView:
    spot_mid: float
    cvd_30s: float = 0.0
    cvd_3m: float = 0.0
    cvd_total_notional_30s: float = 0.0
    cvd_total_notional_3m: float = 0.0
    liquidation_sell_notional_30s: float = 0.0
    liquidation_sell_notional_3m: float = 0.0
    liquidation_buy_notional_30s: float = 0.0
    liquidation_buy_notional_3m: float = 0.0
    funding_rate_bps: float | None = None
    perp_spot_basis_bps: float | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "SettlementView":
        return cls(
            spot_mid=float(raw.get("spot_mid", 0.0)),
            cvd_30s=float(raw.get("cvd_30s", 0.0)),
            cvd_3m=float(raw.get("cvd_3m", 0.0)),
            cvd_total_notional_30s=float(raw.get("cvd_total_notional_30s", 0.0)),
            cvd_total_notional_3m=float(raw.get("cvd_total_notional_3m", 0.0)),
            liquidation_sell_notional_30s=float(raw.get("liquidation_sell_notional_30s", 0.0)),
            liquidation_sell_notional_3m=float(raw.get("liquidation_sell_notional_3m", 0.0)),
            liquidation_buy_notional_30s=float(raw.get("liquidation_buy_notional_30s", 0.0)),
            liquidation_buy_notional_3m=float(raw.get("liquidation_buy_notional_3m", 0.0)),
            funding_rate_bps=_finite_optional(raw.get("funding_rate_bps")),
            perp_spot_basis_bps=_finite_optional(raw.get("perp_spot_basis_bps")),
        )


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _squash(value: float) -> float:
    return math.tanh(value)


def _direction_from_probability(probability_yes: float) -> str:
    return "UP" if probability_yes >= 0.5 else "DOWN"


class SettlementPredictor:
    """Stateful pure-Python port of arb-bot `SettlementPredictor::predict`.

    The Rust predictor keeps 180 seconds of spot history and returns no prediction until
    at least five samples are observed. This class intentionally mirrors that stateful
    contract for parity fixtures and Phase 1 deterministic replay.
    """

    def __init__(self, config: SettlementPredictorConfig | None = None) -> None:
        self.config = config or SettlementPredictorConfig()
        self.spot_history: deque[tuple[int, float]] = deque()
        self.last_direction: str | None = None
        self.last_confidence = 0.0
        self.last_prediction_ms: int | None = None
        self.last_prediction: SettlementPredictionResult | None = None

    def reset_for_rollover(self, now_ms: int) -> None:
        self.spot_history.clear()
        self.last_direction = None
        self.last_confidence = 0.0
        self.last_prediction_ms = now_ms
        self.last_prediction = None

    def predict(self, view: SettlementView | dict[str, Any], now_ms: int) -> SettlementPredictionResult | None:
        if isinstance(view, dict):
            view = SettlementView.from_mapping(view)
        if view.spot_mid <= 0.0 or not math.isfinite(view.spot_mid):
            return None
        self._observe_spot(now_ms, view.spot_mid)
        if len(self.spot_history) < MIN_SAMPLES_FOR_PREDICTION:
            return None
        min_history_ms = max(0, self.config.min_history_ms)
        if min_history_ms > 0 and self.spot_history:
            history_span = now_ms - self.spot_history[0][0]
            if history_span < min_history_ms:
                return None

        momentum_30s = self._spot_momentum_bps(now_ms, 30_000)
        momentum_3m = self._spot_momentum_bps(now_ms, 180_000)
        if momentum_30s is None and momentum_3m is None:
            return None
        m30 = momentum_30s or 0.0
        m3m = momentum_3m or 0.0
        signals: list[str] = []
        score = 0.0

        scale_30s = max(self.config.momentum_30s_scale_bps, 1e-9)
        scale_3m = max(self.config.momentum_3m_scale_bps, 1e-9)
        score += 0.35 * _squash(m30 / scale_30s)
        score += 0.20 * _squash(m3m / scale_3m)
        signals.append(f"momentum_30s={m30:.2f}bps")
        signals.append(f"momentum_3m={m3m:.2f}bps")

        volume_btc_30s = view.cvd_total_notional_30s / max(view.spot_mid, 1e-9) if view.cvd_total_notional_30s > 0.0 else 0.0
        tfi_30s = view.cvd_30s / view.cvd_total_notional_30s if view.cvd_total_notional_30s > 0.0 else 0.0
        tfi_3m = view.cvd_3m / view.cvd_total_notional_3m if view.cvd_total_notional_3m > 0.0 else 0.0
        abs_tfi_30s = abs(tfi_30s)
        cvd_strength = "absent"
        if volume_btc_30s >= self.config.cvd_min_volume_btc and view.cvd_total_notional_30s > 0.0:
            score += 0.25 * _squash(view.cvd_30s / max(self.config.cvd_30s_scale_usd, 1.0))
            score += 0.10 * _squash(view.cvd_3m / max(self.config.cvd_3m_scale_usd, 1.0))
            cvd_windows_agree = (view.cvd_30s > 0.0 and view.cvd_3m > 0.0) or (view.cvd_30s < 0.0 and view.cvd_3m < 0.0)
            if abs_tfi_30s >= self.config.cvd_strong_tfi_threshold and cvd_windows_agree:
                cvd_strength = "strong"
            elif abs_tfi_30s >= self.config.cvd_strong_tfi_threshold * 0.5:
                cvd_strength = "moderate"
            else:
                cvd_strength = "weak"
            signals.append(f"cvd:tfi30={tfi_30s:.3f},tfi3m={tfi_3m:.3f},vol30={volume_btc_30s:.2f}btc")
        else:
            signals.append(f"cvd_absent:vol30={volume_btc_30s:.2f}btc")

        funding_bias = "NEUTRAL"
        if view.funding_rate_bps is not None:
            funding_bps = view.funding_rate_bps
            if abs(funding_bps) <= self.config.funding_neutral_band_bps:
                funding_bias = "NEUTRAL"
            elif funding_bps >= self.config.funding_strong_bps:
                funding_bias = "DOWN"
                score -= 0.08 * _squash(funding_bps / max(self.config.funding_scale_bps, 1.0))
            elif funding_bps <= -self.config.funding_strong_bps:
                funding_bias = "UP"
                score -= 0.08 * _squash(funding_bps / max(self.config.funding_scale_bps, 1.0))
            signals.append(f"funding={funding_bps:.2f}bps:{funding_bias}")

        if view.perp_spot_basis_bps is not None:
            basis_bps = view.perp_spot_basis_bps
            if abs(basis_bps) > self.config.perp_basis_neutral_band_bps:
                score += 0.07 * _squash(basis_bps / max(self.config.basis_scale_bps, 1.0))
            signals.append(f"basis={basis_bps:.2f}bps")

        threshold = self.config.flush_threshold_usd
        cooling = self.config.flush_cooling_usd
        long_flush = view.liquidation_sell_notional_3m >= threshold and view.liquidation_sell_notional_30s <= cooling
        short_flush = view.liquidation_buy_notional_3m >= threshold and view.liquidation_buy_notional_30s <= cooling
        flush_cooling_detected = long_flush or short_flush
        if long_flush:
            score += 0.18
            signals.append("liq_long_flush_cooling")
        if short_flush:
            score -= 0.18
            signals.append("liq_short_flush_cooling")
        if not long_flush and not short_flush and threshold > 0.0:
            continuation_threshold = 0.25 * threshold
            if view.liquidation_sell_notional_30s >= continuation_threshold:
                score -= 0.10
                signals.append("liq_sell_continuation")
            elif view.liquidation_buy_notional_30s >= continuation_threshold:
                score += 0.10
                signals.append("liq_buy_continuation")

        score = min(max(score, -2.5), 2.5)
        probability_yes = 1.0 / (1.0 + math.exp(-2.4 * score))
        if not math.isfinite(probability_yes):
            probability_yes = 0.5
        base_confidence = abs(probability_yes - 0.5) * 2.0
        direction = _direction_from_probability(probability_yes)
        confidence_scale = 1.0
        probability_scale = 1.0
        direction_stable = True

        significant_m30 = abs(m30) >= 0.5 * scale_30s
        significant_m3m = abs(m3m) >= 0.5 * scale_3m
        if significant_m30 and significant_m3m and (m30 * m3m < 0.0):
            confidence_scale *= 0.72
            probability_scale *= 0.80
            direction_stable = False
            signals.append("momentum_divergence")

        directional_votes: list[float] = []
        if significant_m30:
            directional_votes.append(1.0 if m30 > 0.0 else -1.0)
        if significant_m3m:
            directional_votes.append(0.8 if m3m > 0.0 else -0.8)
        cvd_net_direction: float | None = None
        if cvd_strength != "absent" and (view.cvd_total_notional_30s > 0.0 or view.cvd_total_notional_3m > 0.0):
            cvd_net = view.cvd_30s + 0.5 * view.cvd_3m
            if abs(cvd_net) > 1e-9:
                cvd_net_direction = 1.0 if cvd_net > 0.0 else -1.0
                directional_votes.append(0.7 if cvd_net > 0.0 else -0.7)
        if long_flush:
            directional_votes.append(0.9)
        elif short_flush:
            directional_votes.append(-0.9)

        direction_vote = 1.0 if direction == "UP" else -1.0
        aligned_vote = sum(abs(v) for v in directional_votes if v * direction_vote > 0.0)
        opposing_vote = sum(abs(v) for v in directional_votes if v * direction_vote < 0.0)
        total_vote = aligned_vote + opposing_vote
        if total_vote > 0.0 and opposing_vote > 0.0:
            opposition_ratio = opposing_vote / total_vote
            if opposing_vote > aligned_vote:
                confidence_scale *= SIGNAL_OPPOSITION_STRONG_SCALE
                probability_scale *= SIGNAL_OPPOSITION_STRONG_PROB_SCALE
                direction_stable = False
                signals.append("signal_opposition_strong")
            elif opposition_ratio >= 0.35:
                confidence_scale *= SIGNAL_OPPOSITION_SCALE
                probability_scale *= SIGNAL_OPPOSITION_PROB_SCALE
                direction_stable = False
                signals.append("signal_opposition")

        if self.last_direction is not None and self.last_prediction_ms is not None:
            delta_ms = now_ms - self.last_prediction_ms
            if direction != self.last_direction and delta_ms <= max(self.config.flip_hysteresis_ms, 0):
                required_conf = max(FLIP_MIN_CONFIDENCE, self.last_confidence + FLIP_CONFIRM_MARGIN)
                if base_confidence < required_conf:
                    confidence_scale *= 0.58
                    probability_scale *= 0.82
                    direction_stable = False
                    signals.append("flip_hysteresis")
                else:
                    signals.append("flip_confirmed")

        probability_yes = 0.5 + ((probability_yes - 0.5) * probability_scale)
        if not math.isfinite(probability_yes):
            probability_yes = 0.5
        probability_yes = min(max(probability_yes, 0.0), 1.0)
        confidence = min(max(abs(probability_yes - 0.5) * 2.0 * confidence_scale, 0.0), 1.0)
        direction = _direction_from_probability(probability_yes)

        dynamic_band_expand = 0.0
        if not direction_stable:
            dynamic_band_expand += 0.03
        if total_vote > 0.0 and opposing_vote > aligned_vote:
            dynamic_band_expand += 0.02
        band_low = max(self.config.no_trade_band_low - dynamic_band_expand, 0.0)
        band_high = min(self.config.no_trade_band_high + dynamic_band_expand, 1.0)
        in_no_trade_band = band_low <= probability_yes <= band_high
        if dynamic_band_expand > 0.0:
            signals.append(f"dynamic_no_trade_band=[{band_low:.3f},{band_high:.3f}]")

        liq_contradicts = (direction == "DOWN" and long_flush) or (direction == "UP" and short_flush)
        cvd_agrees = (cvd_net_direction is not None) and ((cvd_net_direction > 0.0 and direction == "UP") or (cvd_net_direction < 0.0 and direction == "DOWN"))

        prediction = SettlementPredictionResult(
            probability_yes=probability_yes,
            confidence=confidence,
            direction=direction,
            score=score,
            in_no_trade_band=in_no_trade_band,
            contributing_signals=tuple(signals),
        )
        # Attach Rust-parity diagnostic fields without changing the public dataclass contract.
        object.__setattr__(prediction, "cvd_strength", cvd_strength)
        object.__setattr__(prediction, "cvd_agrees", cvd_agrees)
        object.__setattr__(prediction, "funding_bias", funding_bias)
        object.__setattr__(prediction, "direction_stable", direction_stable)
        object.__setattr__(prediction, "liq_contradicts", liq_contradicts)
        object.__setattr__(prediction, "flush_cooling_detected", flush_cooling_detected)

        self.last_direction = direction
        self.last_confidence = confidence
        self.last_prediction_ms = now_ms
        self.last_prediction = prediction
        return prediction

    def _observe_spot(self, now_ms: int, spot_mid: float) -> None:
        self.spot_history.append((now_ms, spot_mid))
        cutoff = now_ms - 180_000
        while self.spot_history and self.spot_history[0][0] < cutoff:
            self.spot_history.popleft()

    def _spot_momentum_bps(self, now_ms: int, lookback_ms: int) -> float | None:
        if len(self.spot_history) < 2:
            return None
        current = self.spot_history[-1][1]
        if current <= 0.0:
            return None
        target_ts = now_ms - max(lookback_ms, 1)
        baseline: float | None = None
        baseline_ts: int | None = None
        for ts, price in reversed(self.spot_history):
            if ts <= target_ts:
                baseline = price
                baseline_ts = ts
                break
        if baseline is None and self.spot_history:
            baseline_ts, baseline = self.spot_history[0]
        if baseline is None or baseline <= 0.0 or baseline_ts is None:
            return None
        raw_bps = ((current - baseline) / baseline) * 10_000.0
        coverage = min(max((now_ms - baseline_ts) / max(lookback_ms, 1), 0.0), 1.0)
        return raw_bps * coverage


def predict_settlement(state: BinanceReferenceState | None, strike: float | None, expiry_ns: int | None, now_ns: int | None = None) -> SettlementPredictionResult:
    """Compatibility helper for simple one-shot settlement direction tests.

    The full arb-bot parity path uses `SettlementPredictor`; this helper fails closed
    for missing strike/expiry and maps spot-vs-strike to a bounded probability without
    retaining history.
    """
    if state is None or strike is None or expiry_ns is None or strike <= 0 or state.price <= 0:
        raise ValueError("missing strike/expiry/state blocks settlement prediction")
    score = max(-2.5, min(2.5, ((state.price - strike) / strike * 10_000.0) / 100.0))
    probability_yes = 1.0 / (1.0 + math.exp(-2.4 * score))
    confidence = max(0.0, min(1.0, abs(probability_yes - 0.5) * 2.0))
    direction = "UP" if probability_yes > 0.55 else "DOWN" if probability_yes < 0.45 else "NEUTRAL"
    return SettlementPredictionResult(probability_yes, confidence, direction, score)
