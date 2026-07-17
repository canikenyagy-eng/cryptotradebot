from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Protocol, Sequence

import pandas as pd

from core.correlation import SignalCandidate
from core.signal_engine import SignalEngine, TradeSignal
from core.symbols import normalize_symbol
from data.market_data_base import MarketDataProvider
from services.forward_journal import ForwardSignalJournal
from services.forward_outcomes import (
    ForwardOutcome,
    ForwardOutcomeSettings,
    ForwardOutcomeTracker,
)


PHASE13 = "phase13_honest_asof_replay"
STATE_ACCEPTED = "accepted"
STATE_REJECTED = "rejected"
STATE_ERROR = "error"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _timestamp(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _timestamp_or_none(value: object | None) -> pd.Timestamp | None:
    if value is None or str(value).strip() == "":
        return None
    return _timestamp(value)


def _iso(value: object | None) -> str | None:
    if value is None:
        return None
    return _timestamp(value).isoformat()


def _optional_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def _timeframe_delta(timeframe: str) -> pd.Timedelta:
    value = str(timeframe or "M5").strip().upper()
    if value.startswith("M") and value[1:].isdigit():
        return pd.Timedelta(int(value[1:]) * 60, unit="s")
    if value.startswith("H") and value[1:].isdigit():
        return pd.Timedelta(int(value[1:]) * 3600, unit="s")
    if value.startswith("D") and value[1:].isdigit():
        return pd.Timedelta(int(value[1:]) * 86400, unit="s")
    return pd.Timedelta(300, unit="s")


def _floor_to_timeframe(value: object, timeframe: str) -> pd.Timestamp:
    return _timestamp(value).floor(_timeframe_delta(timeframe))


def _frame_window(frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty:
        return {"rows": 0, "first": None, "last": None}
    return {
        "rows": int(len(frame)),
        "first": _iso(frame.index[0]),
        "last": _iso(frame.index[-1]),
    }


def _standardize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    if normalized.empty:
        return normalized
    if normalized.index.tz is None:
        normalized.index = normalized.index.tz_localize("UTC")
    else:
        normalized.index = normalized.index.tz_convert("UTC")
    return normalized.sort_index()


def _clean_pair_list(value: Sequence[str] | str) -> tuple[str, ...]:
    raw = value.split(",") if isinstance(value, str) else value
    return tuple(sorted({normalize_symbol(item) for item in raw if str(item).strip()}))


def _read_jsonl(path: Path | str) -> list[dict[str, object]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


class OhlcvProvider(Protocol):
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
        ...

    def health_check(self) -> bool:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class AsOfAccess:
    symbol: str
    timeframe: str
    as_of: str
    requested_limit: int | None
    full_rows: int
    visible_rows: int
    returned_rows: int
    limit_truncated_rows: int
    future_rows_blocked: int
    full_first_time: str | None
    full_last_time: str | None
    returned_first_time: str | None
    returned_last_time: str | None
    returned_lag_seconds: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of": self.as_of,
            "requested_limit": self.requested_limit,
            "full_rows": self.full_rows,
            "visible_rows": self.visible_rows,
            "returned_rows": self.returned_rows,
            "limit_truncated_rows": self.limit_truncated_rows,
            "future_rows_blocked": self.future_rows_blocked,
            "full_first_time": self.full_first_time,
            "full_last_time": self.full_last_time,
            "returned_first_time": self.returned_first_time,
            "returned_last_time": self.returned_last_time,
            "returned_lag_seconds": self.returned_lag_seconds,
        }


class StaticMarketDataProvider(MarketDataProvider):
    """In-memory OHLCV provider used for replay frames and unit tests."""

    def __init__(self, frames: Mapping[tuple[str, str], pd.DataFrame], history_limit: int = 500) -> None:
        super().__init__(history_limit=history_limit)
        self.frames = {
            (normalize_symbol(symbol), str(timeframe).upper()): _standardize_frame(frame)
            for (symbol, timeframe), frame in frames.items()
        }
        self._initialized = True

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
        key = (normalize_symbol(symbol), timeframe.upper())
        if key not in self.frames:
            raise ValueError(f"No static OHLCV frame for {key[0]} {key[1]}")
        frame = self.frames[key]
        max_rows = max(1, int(limit or self.history_limit))
        return frame.tail(max_rows).copy()

    def health_check(self) -> bool:
        return True


class AsOfMarketDataProvider(MarketDataProvider):
    """Market data wrapper that exposes only candles available at the replay timestamp."""

    def __init__(self, provider: OhlcvProvider, history_limit: int = 500) -> None:
        super().__init__(history_limit=history_limit)
        self.provider = provider
        self.as_of: pd.Timestamp | None = None
        self.access_log: list[AsOfAccess] = []
        self._initialized = True

    def set_as_of(self, value: object) -> None:
        self.as_of = _timestamp(value)

    def clear_access_log(self) -> None:
        self.access_log.clear()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
        if self.as_of is None:
            raise ValueError("As-of replay timestamp is not set")
        clean_symbol = normalize_symbol(symbol)
        tf = timeframe.upper()
        request_limit = limit or self.history_limit
        full = _standardize_frame(self.provider.fetch_ohlcv(clean_symbol, tf, limit=None))
        visible = full[full.index <= self.as_of]
        result = visible.tail(max(1, int(request_limit))).copy()
        if not result.empty and result.index.max() > self.as_of:
            raise AssertionError(f"As-of provider leaked future candles for {clean_symbol} {tf}")
        returned_lag_seconds = None
        if not result.empty:
            returned_lag_seconds = round(max(0.0, (self.as_of - _timestamp(result.index[-1])).total_seconds()), 3)
        self.access_log.append(
            AsOfAccess(
                symbol=clean_symbol,
                timeframe=tf,
                as_of=self.as_of.isoformat(),
                requested_limit=int(request_limit),
                full_rows=int(len(full)),
                visible_rows=int(len(visible)),
                returned_rows=int(len(result)),
                limit_truncated_rows=max(0, int(len(visible) - len(result))),
                future_rows_blocked=int((full.index > self.as_of).sum()),
                full_first_time=None if full.empty else _iso(full.index[0]),
                full_last_time=None if full.empty else _iso(full.index[-1]),
                returned_first_time=None if result.empty else _iso(result.index[0]),
                returned_last_time=None if result.empty else _iso(result.index[-1]),
                returned_lag_seconds=returned_lag_seconds,
            )
        )
        return result

    def health_check(self) -> bool:
        return self.provider.health_check()

    def close(self) -> None:
        self.provider.close()
        super().close()

    def access_summary(self) -> dict[str, object]:
        total_blocked = sum(item.future_rows_blocked for item in self.access_log)
        leaks = [
            item
            for item in self.access_log
            if item.returned_last_time is not None and _timestamp(item.returned_last_time) > _timestamp(item.as_of)
        ]
        return {
            "fetches": len(self.access_log),
            "future_rows_blocked": total_blocked,
            "future_leaks": len(leaks),
        }


@dataclass(frozen=True)
class ReplayDecision:
    cycle_id: str
    as_of: str
    symbol: str
    state: str
    stage: str | None = None
    reason: str | None = None
    score: int | None = None
    signal: TradeSignal | None = None
    details: Mapping[str, object] | None = None
    visible_frames: Mapping[str, Mapping[str, object]] | None = None
    market_data_accesses: Sequence[Mapping[str, object]] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": "phase13_replay_decision",
            "version": 1,
            "cycle_id": self.cycle_id,
            "as_of": self.as_of,
            "symbol": self.symbol,
            "state": self.state,
            "stage": self.stage,
            "reason": self.reason,
            "score": self.score,
            "visible_frames": dict(self.visible_frames or {}),
            "market_data_accesses": list(self.market_data_accesses or []),
        }
        if self.signal is not None:
            payload["fingerprint"] = self.signal.fingerprint()
            payload["side"] = self.signal.side
            payload["generated_at"] = self.signal.generated_at.isoformat()
            payload["entry"] = self.signal.entry
            payload["stop_loss"] = self.signal.stop_loss
            payload["take_profit"] = self.signal.take_profit
            payload["entry_mode"] = self.signal.entry_mode
            payload["entry_source"] = self.signal.entry_source
            payload["regime_label"] = self.signal.regime_label
            payload["trigger_event"] = self.signal.trigger_event
        if self.details:
            payload["details"] = dict(self.details)
        return payload


@dataclass(frozen=True)
class ReplayCycleResult:
    cycle_id: str
    as_of: pd.Timestamp
    decisions: list[ReplayDecision]
    signals: list[TradeSignal]
    accesses: list[AsOfAccess]


class AsofReplayScanner:
    """Mirrors SignalEngine.scan_pairs while preserving accepted/rejected decisions."""

    def __init__(self, engine: SignalEngine, market_data: AsOfMarketDataProvider) -> None:
        self.engine = engine
        self.market_data = market_data

    def scan_cycle(self, pairs: Iterable[str], *, as_of: object, cycle_id: str) -> ReplayCycleResult:
        timestamp = _timestamp(as_of)
        self.market_data.set_as_of(timestamp)
        self.market_data.clear_access_log()
        pair_list = [self.engine._normalize_pair(item) for item in pairs]
        pair_frames: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]] = {}
        visible_by_pair: dict[str, dict[str, Mapping[str, object]]] = {}
        accesses_by_pair: dict[str, list[Mapping[str, object]]] = {}
        decisions_by_pair: dict[str, ReplayDecision] = {}

        for pair in pair_list:
            access_start = len(self.market_data.access_log)
            try:
                htf, ltf, trigger = self.engine._fetch_frames(pair)
                pair_frames[pair] = (htf, ltf, trigger)
                visible_by_pair[pair] = {
                    self.engine.htf_timeframe: _frame_window(htf),
                    self.engine.ltf_timeframe: _frame_window(ltf),
                    self.engine.trigger_timeframe: _frame_window(trigger),
                }
                accesses_by_pair[pair] = [
                    item.to_dict()
                    for item in self.market_data.access_log[access_start:]
                    if item.symbol == pair
                ]
            except Exception as exc:
                accesses_by_pair[pair] = [
                    item.to_dict()
                    for item in self.market_data.access_log[access_start:]
                    if item.symbol == pair
                ]
                decisions_by_pair[pair] = ReplayDecision(
                    cycle_id=cycle_id,
                    as_of=timestamp.isoformat(),
                    symbol=pair,
                    state=STATE_ERROR,
                    stage="data",
                    reason=str(exc),
                    visible_frames=visible_by_pair.get(pair, {}),
                    market_data_accesses=accesses_by_pair.get(pair, []),
                )

        universe = set(pair_frames.keys())
        candidates: list[SignalCandidate] = []
        for pair in pair_list:
            frames = pair_frames.get(pair)
            if frames is None:
                continue
            htf, ltf, trigger = frames
            reference_pair = self.engine._resolve_smt_reference_pair(pair, universe) if self.engine.enable_smt_confirmation else None
            reference_trigger_frame = None
            if reference_pair is not None:
                ref_frames = pair_frames.get(reference_pair)
                if ref_frames is not None:
                    reference_trigger_frame = ref_frames[2]

            evaluation = self.engine.evaluate_snapshot(
                pair,
                htf,
                ltf,
                trigger_frame=trigger,
                reference_pair=reference_pair,
                reference_trigger_frame=reference_trigger_frame,
                emit_logs=False,
            )
            if evaluation.signal is None:
                decisions_by_pair[pair] = ReplayDecision(
                    cycle_id=cycle_id,
                    as_of=timestamp.isoformat(),
                    symbol=pair,
                    state=STATE_REJECTED,
                    stage=evaluation.rejection_stage,
                    reason=evaluation.rejection_reason,
                    score=evaluation.score_value,
                    details=evaluation.details,
                    visible_frames=visible_by_pair.get(pair, {}),
                    market_data_accesses=accesses_by_pair.get(pair, []),
                )
                continue

            candidates.append(SignalCandidate(pair=pair, signal=evaluation.signal, frame=ltf))
            decisions_by_pair[pair] = ReplayDecision(
                cycle_id=cycle_id,
                as_of=timestamp.isoformat(),
                symbol=pair,
                state="candidate",
                stage="candidate",
                reason=None,
                score=evaluation.signal.score,
                signal=evaluation.signal,
                details={"threshold_used": evaluation.threshold_used, "regime_label": evaluation.regime_label},
                visible_frames=visible_by_pair.get(pair, {}),
                market_data_accesses=accesses_by_pair.get(pair, []),
            )

        kept, dropped = self.engine.correlation_cap.filter(candidates)
        dropped_pairs: set[str] = set()
        for drop in dropped:
            dropped_pairs.add(drop.pair)
            decisions_by_pair[drop.pair] = self._drop_decision(
                cycle_id=cycle_id,
                as_of=timestamp,
                pair=drop.pair,
                stage="correlation",
                reason=drop.reason,
                context={"kept_pair": drop.kept_pair, "correlation": drop.correlation},
                visible_frames=visible_by_pair.get(drop.pair, {}),
                market_data_accesses=accesses_by_pair.get(drop.pair, []),
                previous=decisions_by_pair.get(drop.pair),
            )

        kept, exposure_drops = self.engine._apply_currency_exposure_cap(kept)
        for drop in exposure_drops:
            dropped_pairs.add(drop.pair)
            decisions_by_pair[drop.pair] = self._drop_decision(
                cycle_id=cycle_id,
                as_of=timestamp,
                pair=drop.pair,
                stage=drop.stage,
                reason=drop.reason,
                context=drop.context,
                visible_frames=visible_by_pair.get(drop.pair, {}),
                market_data_accesses=accesses_by_pair.get(drop.pair, []),
                previous=decisions_by_pair.get(drop.pair),
            )

        kept, portfolio_drops = self.engine._apply_portfolio_exposure_cap(kept)
        for drop in portfolio_drops:
            dropped_pairs.add(drop.pair)
            decisions_by_pair[drop.pair] = self._drop_decision(
                cycle_id=cycle_id,
                as_of=timestamp,
                pair=drop.pair,
                stage=drop.stage,
                reason=drop.reason,
                context=drop.context,
                visible_frames=visible_by_pair.get(drop.pair, {}),
                market_data_accesses=accesses_by_pair.get(drop.pair, []),
                previous=decisions_by_pair.get(drop.pair),
            )

        released: list[TradeSignal] = []
        for candidate in kept:
            signal = candidate.signal
            allowed, drop = self.engine.gate_signal_release(signal, commit=True)
            if not allowed and drop is not None:
                dropped_pairs.add(drop.pair)
                decisions_by_pair[drop.pair] = self._drop_decision(
                    cycle_id=cycle_id,
                    as_of=timestamp,
                    pair=drop.pair,
                    stage=drop.stage,
                    reason=drop.reason,
                    context=drop.context,
                    visible_frames=visible_by_pair.get(drop.pair, {}),
                    market_data_accesses=accesses_by_pair.get(drop.pair, []),
                    previous=decisions_by_pair.get(drop.pair),
                )
                continue
            released.append(signal)

        trade_gate = getattr(self.engine, "_trade_gate", None)
        if trade_gate is not None:
            filtered: list[TradeSignal] = []
            for signal in released:
                result = trade_gate.check_trade(
                    pair=signal.symbol,
                    side=signal.side,
                    regime_output=None,
                    universe=universe,
                    current_score=signal.score,
                )
                if not result.allowed:
                    dropped_pairs.add(signal.symbol)
                    decisions_by_pair[signal.symbol] = ReplayDecision(
                        cycle_id=cycle_id,
                        as_of=timestamp.isoformat(),
                        symbol=signal.symbol,
                        state=STATE_REJECTED,
                        stage="trade_gate_v2",
                        reason=result.reason,
                        score=signal.score,
                        signal=signal,
                        details=result.details,
                        visible_frames=visible_by_pair.get(signal.symbol, {}),
                        market_data_accesses=accesses_by_pair.get(signal.symbol, []),
                    )
                    continue
                filtered.append(signal)
            released = filtered

        for signal in released:
            if signal.symbol in dropped_pairs:
                continue
            decisions_by_pair[signal.symbol] = ReplayDecision(
                cycle_id=cycle_id,
                as_of=timestamp.isoformat(),
                symbol=signal.symbol,
                state=STATE_ACCEPTED,
                stage="released",
                score=signal.score,
                signal=signal,
                visible_frames=visible_by_pair.get(signal.symbol, {}),
                market_data_accesses=accesses_by_pair.get(signal.symbol, []),
            )

        return ReplayCycleResult(
            cycle_id=cycle_id,
            as_of=timestamp,
            decisions=[decisions_by_pair[pair] for pair in pair_list if pair in decisions_by_pair],
            signals=released,
            accesses=list(self.market_data.access_log),
        )

    @staticmethod
    def _drop_decision(
        *,
        cycle_id: str,
        as_of: pd.Timestamp,
        pair: str,
        stage: str,
        reason: str,
        context: Mapping[str, object],
        visible_frames: Mapping[str, Mapping[str, object]],
        market_data_accesses: Sequence[Mapping[str, object]],
        previous: ReplayDecision | None,
    ) -> ReplayDecision:
        return ReplayDecision(
            cycle_id=cycle_id,
            as_of=as_of.isoformat(),
            symbol=pair,
            state=STATE_REJECTED,
            stage=stage,
            reason=reason,
            score=previous.score if previous else None,
            signal=previous.signal if previous else None,
            details=context,
            visible_frames=visible_frames,
            market_data_accesses=market_data_accesses,
        )


@dataclass(frozen=True)
class CryptoAsofReplaySettings:
    pairs: tuple[str, ...] | str = ("BTCUSDT", "ETHUSDT")
    start: str | None = None
    end: str | None = None
    max_steps: int = 96
    history_limit: int = 1200
    report_path: Path | str = Path("reports/crypto_phase13_asof_replay_report.json")
    decisions_path: Path | str = Path("logs/crypto_phase13_asof_replay_decisions.jsonl")
    journal_path: Path | str = Path("logs/crypto_phase13_asof_replay_journal.jsonl")
    outcomes_path: Path | str = Path("logs/crypto_phase13_asof_replay_outcomes.jsonl")
    outcome_summary_path: Path | str = Path("reports/crypto_phase13_asof_replay_outcome_summary.json")
    outcome_timeframe: str = "M15"
    risk_per_trade_pct: float = 1.0
    require_full_warmup: bool = True
    step_source: str = "trigger"
    live_journal_path: Path | str | None = None
    parity_report_path: Path | str | None = Path("reports/crypto_phase13_parity_report.json")
    market_diagnostics_path: Path | str | None = Path("logs/crypto_forward_market_data.jsonl")

    def normalized(self) -> "CryptoAsofReplaySettings":
        step_source = str(self.step_source or "trigger").strip().lower()
        if step_source not in {"trigger", "live_journal", "trigger_plus_live_journal"}:
            step_source = "trigger"
        live_journal_path = _optional_path(self.live_journal_path)
        return CryptoAsofReplaySettings(
            pairs=_clean_pair_list(self.pairs),
            start=self.start,
            end=self.end,
            max_steps=max(1, int(self.max_steps)),
            history_limit=max(150, int(self.history_limit)),
            report_path=Path(self.report_path),
            decisions_path=Path(self.decisions_path),
            journal_path=Path(self.journal_path),
            outcomes_path=Path(self.outcomes_path),
            outcome_summary_path=Path(self.outcome_summary_path),
            outcome_timeframe=str(self.outcome_timeframe or "M15").upper(),
            risk_per_trade_pct=max(0.0, float(self.risk_per_trade_pct)),
            require_full_warmup=bool(self.require_full_warmup),
            step_source=step_source,
            live_journal_path=live_journal_path,
            parity_report_path=_optional_path(self.parity_report_path) if live_journal_path is not None else None,
            market_diagnostics_path=_optional_path(self.market_diagnostics_path) if live_journal_path is not None else None,
        )


class CryptoAsofReplayEngine:
    def __init__(
        self,
        settings: CryptoAsofReplaySettings,
        *,
        signal_engine: SignalEngine,
        source_provider: OhlcvProvider,
        htf_timeframe: str,
        ltf_timeframe: str,
        trigger_timeframe: str,
    ) -> None:
        self.settings = settings.normalized()
        self.source_provider = source_provider
        self.asof_provider = AsOfMarketDataProvider(source_provider, history_limit=self.settings.history_limit)
        signal_engine.market_data = self.asof_provider
        # Historical replay uses simulated time; wall-clock freshness would mark all historical candles stale.
        signal_engine.enable_market_data_freshness_gate = False
        self.signal_engine = signal_engine
        self.scanner = AsofReplayScanner(signal_engine, self.asof_provider)
        self.htf_timeframe = htf_timeframe.upper()
        self.ltf_timeframe = ltf_timeframe.upper()
        self.trigger_timeframe = trigger_timeframe.upper()

    def run(self) -> dict[str, object]:
        frames = self._load_frames()
        self.asof_provider.provider = StaticMarketDataProvider(frames, history_limit=self.settings.history_limit)
        steps = self._build_steps(frames)
        self._reset_outputs()

        cycle_results: list[ReplayCycleResult] = []
        journal_count = 0
        for index, step in enumerate(steps, start=1):
            cycle_id = self._cycle_id(step, index)
            result = self.scanner.scan_cycle(self.settings.pairs, as_of=step, cycle_id=cycle_id)
            cycle_results.append(result)
            self._append_decisions(result.decisions)
            for signal in result.signals:
                self._append_journal_signal(cycle_id=cycle_id, signal=signal, as_of=step)
                journal_count += 1

        outcomes = self._evaluate_outcomes(frames)
        outcome_tracker = ForwardOutcomeTracker(
            ForwardOutcomeSettings(
                journal_path=self.settings.journal_path,
                output_path=self.settings.outcomes_path,
                timeframe=self.settings.outcome_timeframe,
                history_limit=self.settings.history_limit,
                sent_only=False,
                skip_terminal_existing=False,
            )
        )
        outcome_summary = outcome_tracker.summarize(outcomes)
        performance = performance_summary(outcomes, risk_per_trade_pct=self.settings.risk_per_trade_pct)
        Path(self.settings.outcome_summary_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.settings.outcome_summary_path).write_text(
            json.dumps(outcome_summary, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        parity_summary = self._write_parity_report()

        report = self._report(
            frames=frames,
            steps=steps,
            cycle_results=cycle_results,
            journal_count=journal_count,
            outcome_summary=outcome_summary,
            performance=performance,
            parity_summary=parity_summary,
        )
        write_json(report, self.settings.report_path)
        return report

    def _load_frames(self) -> dict[tuple[str, str], pd.DataFrame]:
        frames: dict[tuple[str, str], pd.DataFrame] = {}
        timeframes = sorted({self.htf_timeframe, self.ltf_timeframe, self.trigger_timeframe, self.settings.outcome_timeframe})
        for pair in self.settings.pairs:
            for timeframe in timeframes:
                frame = self.source_provider.fetch_ohlcv(pair, timeframe, limit=self.settings.history_limit)
                frames[(pair, timeframe)] = _standardize_frame(frame)
        return frames

    def _build_steps(self, frames: Mapping[tuple[str, str], pd.DataFrame]) -> list[pd.Timestamp]:
        start = _timestamp_or_none(self.settings.start)
        end = _timestamp_or_none(self.settings.end)
        trigger_times: set[pd.Timestamp] = set()
        if self.settings.step_source in {"trigger", "trigger_plus_live_journal"}:
            for pair in self.settings.pairs:
                frame = frames.get((pair, self.trigger_timeframe), pd.DataFrame())
                for timestamp in frame.index:
                    point = _timestamp(timestamp)
                    if start is not None and point < start:
                        continue
                    if end is not None and point > end:
                        continue
                    trigger_times.add(point)
        if self.settings.step_source in {"live_journal", "trigger_plus_live_journal"}:
            trigger_times.update(self._live_journal_steps(start=start, end=end))
        steps = sorted(trigger_times)
        if self.settings.require_full_warmup:
            steps = [step for step in steps if self._has_warmup(frames, step)]
        return steps[: self.settings.max_steps]

    def _live_journal_steps(self, *, start: pd.Timestamp | None, end: pd.Timestamp | None) -> set[pd.Timestamp]:
        if self.settings.live_journal_path is None:
            return set()
        steps: set[pd.Timestamp] = set()
        for row in _read_jsonl(self.settings.live_journal_path):
            if row.get("type") != "forward_signal_candidate":
                continue
            observed_at = row.get("observed_at")
            if observed_at is None:
                continue
            point = _floor_to_timeframe(observed_at, self.trigger_timeframe)
            if start is not None and point < start:
                continue
            if end is not None and point > end:
                continue
            steps.add(point)
        return steps

    def _has_warmup(self, frames: Mapping[tuple[str, str], pd.DataFrame], step: pd.Timestamp) -> bool:
        required = {
            self.htf_timeframe: max(120, self.signal_engine.regime_long_window),
            self.ltf_timeframe: max(80, self.signal_engine.swing_window * 2 + 3),
            self.trigger_timeframe: max(40, self.signal_engine.swing_window * 6),
        }
        for pair in self.settings.pairs:
            for timeframe, needed in required.items():
                frame = frames.get((pair, timeframe), pd.DataFrame())
                if len(frame[frame.index <= step]) < needed:
                    return False
        return True

    def _reset_outputs(self) -> None:
        for path in (
            self.settings.decisions_path,
            self.settings.journal_path,
            self.settings.outcomes_path,
            self.settings.outcome_summary_path,
            self.settings.report_path,
            self.settings.parity_report_path,
        ):
            if path is None:
                continue
            file_path = Path(path)
            if file_path.exists():
                file_path.unlink()

    def _append_decisions(self, decisions: Iterable[ReplayDecision]) -> None:
        path = Path(self.settings.decisions_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for decision in decisions:
                fh.write(json.dumps(decision.to_dict(), sort_keys=True, default=str) + "\n")

    def _append_journal_signal(self, *, cycle_id: str, signal: TradeSignal, as_of: pd.Timestamp) -> None:
        journal_id = build_replay_journal_id(cycle_id=cycle_id, signal=signal)
        signal_payload = ForwardSignalJournal._signal_payload(signal)
        candidate = {
            "type": "forward_signal_candidate",
            "version": 1,
            "observed_at": as_of.isoformat(),
            "cycle_id": cycle_id,
            "journal_id": journal_id,
            "status": "candidate",
            "source": PHASE13,
            "signal": signal_payload,
            "score_breakdown": signal.score_breakdown.contribution_dict(),
            "score_total": int(signal.score_breakdown.total),
        }
        delivery = {
            "type": "forward_signal_delivery",
            "version": 1,
            "observed_at": as_of.isoformat(),
            "cycle_id": cycle_id,
            "journal_id": journal_id,
            "fingerprint": signal.fingerprint(),
            "symbol": signal.symbol,
            "side": signal.side,
            "status": "would_send",
            "delivered": True,
            "latency_seconds": 0.0,
            "source": PHASE13,
        }
        path = Path(self.settings.journal_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(candidate, sort_keys=True, default=str) + "\n")
            fh.write(json.dumps(delivery, sort_keys=True, default=str) + "\n")

    def _evaluate_outcomes(self, frames: Mapping[tuple[str, str], pd.DataFrame]) -> list[ForwardOutcome]:
        provider = StaticMarketDataProvider(frames, history_limit=self.settings.history_limit)
        market = _MarketDataAdapter(provider)
        tracker = ForwardOutcomeTracker(
            ForwardOutcomeSettings(
                journal_path=self.settings.journal_path,
                output_path=self.settings.outcomes_path,
                timeframe=self.settings.outcome_timeframe,
                history_limit=self.settings.history_limit,
                sent_only=False,
                skip_terminal_existing=False,
            )
        )
        outcomes = tracker.run(market)
        path = Path(self.settings.outcomes_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for outcome in outcomes:
                fh.write(json.dumps(outcome.payload, sort_keys=True, default=str) + "\n")
        return outcomes

    def _write_parity_report(self) -> dict[str, object] | None:
        if self.settings.live_journal_path is None or self.settings.parity_report_path is None:
            return None
        if not Path(self.settings.live_journal_path).exists():
            return {
                "enabled": False,
                "reason": f"live journal not found: {self.settings.live_journal_path}",
            }
        parity = build_phase13_parity_report(
            live_journal_path=self.settings.live_journal_path,
            replay_decisions_path=self.settings.decisions_path,
            replay_journal_path=self.settings.journal_path,
            market_diagnostics_path=self.settings.market_diagnostics_path,
            output_path=self.settings.parity_report_path,
            trigger_timeframe=self.trigger_timeframe,
        )
        return {
            "enabled": True,
            "report_path": str(self.settings.parity_report_path),
            "status": parity.get("status"),
            "exact_matches": parity.get("exact_matches"),
            "live_only": parity.get("live_only"),
            "replay_only": parity.get("replay_only"),
        }

    def _report(
        self,
        *,
        frames: Mapping[tuple[str, str], pd.DataFrame],
        steps: Sequence[pd.Timestamp],
        cycle_results: Sequence[ReplayCycleResult],
        journal_count: int,
        outcome_summary: Mapping[str, object],
        performance: Mapping[str, object],
        parity_summary: Mapping[str, object] | None,
    ) -> dict[str, object]:
        decisions = [decision for result in cycle_results for decision in result.decisions]
        state_counts = Counter(decision.state for decision in decisions)
        stage_counts = Counter(str(decision.stage or "none") for decision in decisions)
        access_rows = [access for result in cycle_results for access in result.accesses]
        future_leaks = [
            access
            for access in access_rows
            if access.returned_last_time is not None and _timestamp(access.returned_last_time) > _timestamp(access.as_of)
        ]
        return {
            "type": "crypto_asof_replay_report",
            "version": 1,
            "phase": PHASE13,
            "generated_at": utc_now(),
            "settings": {
                "pairs": list(self.settings.pairs),
                "start": self.settings.start,
                "end": self.settings.end,
                "max_steps": self.settings.max_steps,
                "history_limit": self.settings.history_limit,
                "htf_timeframe": self.htf_timeframe,
                "ltf_timeframe": self.ltf_timeframe,
                "trigger_timeframe": self.trigger_timeframe,
                "outcome_timeframe": self.settings.outcome_timeframe,
                "risk_per_trade_pct": self.settings.risk_per_trade_pct,
                "require_full_warmup": self.settings.require_full_warmup,
                "step_source": self.settings.step_source,
                "live_journal_path": None
                if self.settings.live_journal_path is None
                else str(self.settings.live_journal_path),
                "market_diagnostics_path": None
                if self.settings.market_diagnostics_path is None
                else str(self.settings.market_diagnostics_path),
                "replay_adjustments": [
                    "SignalEngine wall-clock candle freshness gate is disabled; AsOfMarketDataProvider enforces simulated-time freshness and no-future access.",
                ],
            },
            "paths": {
                "report": str(self.settings.report_path),
                "decisions": str(self.settings.decisions_path),
                "journal": str(self.settings.journal_path),
                "outcomes": str(self.settings.outcomes_path),
                "outcome_summary": str(self.settings.outcome_summary_path),
                "parity_report": None
                if self.settings.parity_report_path is None
                else str(self.settings.parity_report_path),
            },
            "data_windows": {
                f"{pair}_{timeframe}": _frame_window(frame)
                for (pair, timeframe), frame in sorted(frames.items())
            },
            "replay": {
                "steps": len(steps),
                "first_step": None if not steps else steps[0].isoformat(),
                "last_step": None if not steps else steps[-1].isoformat(),
                "decisions": len(decisions),
                "signals": journal_count,
                "state_counts": dict(state_counts),
                "stage_counts": dict(stage_counts),
            },
            "no_future_guard": {
                "fetches": len(access_rows),
                "future_rows_blocked": sum(access.future_rows_blocked for access in access_rows),
                "future_leaks": len(future_leaks),
                "passed": len(future_leaks) == 0,
            },
            "outcome_summary": dict(outcome_summary),
            "performance": dict(performance),
            "parity": None if parity_summary is None else dict(parity_summary),
            "order_submission_allowed": False,
            "live_execution_allowed": False,
            "safety_note": "Phase 13 is historical replay validation only. It does not create order intents or send orders.",
        }

    @staticmethod
    def _cycle_id(step: pd.Timestamp, index: int) -> str:
        return f"phase13-{step.strftime('%Y%m%dT%H%M%S')}-{index:06d}"


def _candidate_records_from_journal(path: Path | str, *, trigger_timeframe: str) -> list[dict[str, object]]:
    rows = _read_jsonl(path)
    deliveries_by_journal: dict[str, dict[str, object]] = {}
    deliveries_by_fingerprint: dict[str, dict[str, object]] = {}
    for row in rows:
        if row.get("type") != "forward_signal_delivery":
            continue
        journal_id = str(row.get("journal_id") or "")
        fingerprint = str(row.get("fingerprint") or "")
        if journal_id:
            deliveries_by_journal[journal_id] = row
        if fingerprint:
            deliveries_by_fingerprint[fingerprint] = row

    records: list[dict[str, object]] = []
    for row in rows:
        if row.get("type") != "forward_signal_candidate":
            continue
        signal = row.get("signal") if isinstance(row.get("signal"), dict) else {}
        symbol = normalize_symbol(str(signal.get("symbol") or row.get("symbol") or ""))
        side = str(signal.get("side") or row.get("side") or "").upper()
        generated_at = _iso(signal.get("generated_at") or row.get("observed_at"))
        observed_at = _iso(row.get("observed_at"))
        scan_time = _iso(_floor_to_timeframe(observed_at, trigger_timeframe)) if observed_at is not None else None
        fingerprint = str(signal.get("fingerprint") or row.get("fingerprint") or "")
        delivery = deliveries_by_journal.get(str(row.get("journal_id") or "")) or deliveries_by_fingerprint.get(fingerprint) or {}
        records.append(
            {
                "key": f"{generated_at}|{symbol}|{side}",
                "observed_at": observed_at,
                "scan_time": scan_time,
                "generated_at": generated_at,
                "cycle_id": row.get("cycle_id"),
                "journal_id": row.get("journal_id"),
                "symbol": symbol,
                "side": side,
                "score": signal.get("score") or row.get("score_total"),
                "entry": signal.get("entry"),
                "stop_loss": signal.get("stop_loss"),
                "take_profit": signal.get("take_profit"),
                "planned_rr": signal.get("planned_rr"),
                "fingerprint": fingerprint,
                "entry_mode": signal.get("entry_mode"),
                "entry_source": signal.get("entry_source"),
                "regime_label": signal.get("regime_label"),
                "regime_direction": signal.get("regime_direction"),
                "trigger_event": signal.get("trigger_event"),
                "delivery_status": delivery.get("status"),
                "delivered": delivery.get("delivered"),
                "source": row.get("source"),
            }
        )
    return records


def _decisions_by_step_symbol(path: Path | str) -> dict[tuple[str, str], dict[str, object]]:
    decisions: dict[tuple[str, str], dict[str, object]] = {}
    for row in _read_jsonl(path):
        if row.get("type") != "phase13_replay_decision":
            continue
        as_of = _iso(row.get("as_of"))
        symbol = normalize_symbol(str(row.get("symbol") or ""))
        if as_of and symbol:
            decisions[(as_of, symbol)] = row
    return decisions


def _decision_summary(decision: Mapping[str, object] | None) -> dict[str, object] | None:
    if decision is None:
        return None
    details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
    key_details = {
        key: details.get(key)
        for key in (
            "regime_label",
            "regime_direction",
            "zone",
            "htf",
            "liquidity",
            "pd",
            "trigger",
            "session",
            "threshold",
            "reason",
        )
        if key in details
    }
    return {
        "as_of": decision.get("as_of"),
        "symbol": decision.get("symbol"),
        "state": decision.get("state"),
        "stage": decision.get("stage"),
        "reason": decision.get("reason"),
        "score": decision.get("score"),
        "side": decision.get("side"),
        "fingerprint": decision.get("fingerprint"),
        "generated_at": decision.get("generated_at"),
        "entry": decision.get("entry"),
        "entry_mode": decision.get("entry_mode"),
        "entry_source": decision.get("entry_source"),
        "regime_label": decision.get("regime_label") or key_details.get("regime_label"),
        "trigger_event": decision.get("trigger_event"),
        "key_details": key_details,
        "visible_frames": decision.get("visible_frames") if isinstance(decision.get("visible_frames"), dict) else {},
        "market_data_accesses": decision.get("market_data_accesses")
        if isinstance(decision.get("market_data_accesses"), list)
        else [],
    }


def _nearby_market_diagnostics(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate: Mapping[str, object],
    timeframes: Sequence[str],
    tolerance_minutes: int = 20,
) -> list[dict[str, object]]:
    observed_at = candidate.get("observed_at")
    symbol = normalize_symbol(str(candidate.get("symbol") or ""))
    if observed_at is None or not symbol:
        return []
    observed = _timestamp(observed_at)
    tolerance = pd.Timedelta(max(1, int(tolerance_minutes)) * 60, unit="s")
    matches: list[dict[str, object]] = []
    for timeframe in timeframes:
        tf = timeframe.upper()
        nearest: tuple[pd.Timedelta, Mapping[str, object]] | None = None
        for row in rows:
            if row.get("type") != "market_data_fetch":
                continue
            if normalize_symbol(str(row.get("pair") or "")) != symbol:
                continue
            if str(row.get("timeframe") or "").upper() != tf:
                continue
            row_observed_at = row.get("observed_at")
            if row_observed_at is None:
                continue
            delta = abs(_timestamp(row_observed_at) - observed)
            if delta > tolerance:
                continue
            if nearest is None or delta < nearest[0]:
                nearest = (delta, row)
        if nearest is None:
            continue
        _, row = nearest
        matches.append(
            {
                "timeframe": tf,
                "observed_at": row.get("observed_at"),
                "seconds_from_candidate": round(float(nearest[0].total_seconds()), 3),
                "served_from": row.get("served_from"),
                "ok": row.get("ok"),
                "stale": row.get("stale"),
                "last_candle_time": row.get("last_candle_time"),
                "candle_age_seconds": row.get("candle_age_seconds"),
                "rows": row.get("rows"),
                "cache_enabled": row.get("cache_enabled"),
                "cache_mode": row.get("cache_mode"),
                "latency_seconds": row.get("latency_seconds"),
                "error": row.get("error"),
            }
        )
    return matches


def _classification(
    *,
    exact_replay: Mapping[str, object] | None,
    signal_time_decision: Mapping[str, object] | None,
    scan_time_decision: Mapping[str, object] | None,
) -> str:
    if exact_replay is not None:
        return "exact_match"
    if scan_time_decision is not None:
        if scan_time_decision.get("state") == STATE_ACCEPTED:
            return "scan_time_accepted_different_signal"
        return "replay_rejected_at_scan_time"
    if signal_time_decision is not None:
        if signal_time_decision.get("state") == STATE_ACCEPTED:
            return "signal_time_accepted_different_signal"
        return "replay_rejected_at_signal_time"
    return "missing_replay_decision"


def build_phase13_parity_report(
    *,
    live_journal_path: Path | str,
    replay_decisions_path: Path | str,
    replay_journal_path: Path | str,
    output_path: Path | str,
    trigger_timeframe: str = "M5",
    market_diagnostics_path: Path | str | None = None,
) -> dict[str, object]:
    """Compare live Phase 4 candidates with Phase 13 replay decisions."""

    live_candidates = _candidate_records_from_journal(live_journal_path, trigger_timeframe=trigger_timeframe)
    replay_candidates = _candidate_records_from_journal(replay_journal_path, trigger_timeframe=trigger_timeframe)
    decisions = _decisions_by_step_symbol(replay_decisions_path)
    diagnostics = _read_jsonl(market_diagnostics_path) if market_diagnostics_path is not None else []
    timeframes = ("H1", "M15", trigger_timeframe.upper())

    replay_by_key = {str(row["key"]): row for row in replay_candidates}
    live_keys = {str(row["key"]) for row in live_candidates}
    replay_keys = {str(row["key"]) for row in replay_candidates}

    comparisons: list[dict[str, object]] = []
    classification_counts: Counter[str] = Counter()
    scan_stage_counts: Counter[str] = Counter()
    scan_reason_counts: Counter[str] = Counter()
    generated_stage_counts: Counter[str] = Counter()
    generated_reason_counts: Counter[str] = Counter()
    attached_diagnostics: list[dict[str, object]] = []

    for live in live_candidates:
        symbol = str(live.get("symbol") or "")
        exact = replay_by_key.get(str(live.get("key")))
        generated_at = live.get("generated_at")
        scan_time = live.get("scan_time")
        signal_time_decision = decisions.get((str(generated_at), symbol)) if generated_at is not None else None
        scan_time_decision = decisions.get((str(scan_time), symbol)) if scan_time is not None else None
        classification = _classification(
            exact_replay=exact,
            signal_time_decision=signal_time_decision,
            scan_time_decision=scan_time_decision,
        )
        classification_counts[classification] += 1
        if scan_time_decision is not None:
            scan_stage_counts[str(scan_time_decision.get("stage") or "none")] += 1
            scan_reason_counts[str(scan_time_decision.get("reason") or "none")] += 1
        if signal_time_decision is not None:
            generated_stage_counts[str(signal_time_decision.get("stage") or "none")] += 1
            generated_reason_counts[str(signal_time_decision.get("reason") or "none")] += 1

        live_diagnostics = _nearby_market_diagnostics(
            diagnostics,
            candidate=live,
            timeframes=timeframes,
        )
        attached_diagnostics.extend(live_diagnostics)
        comparisons.append(
            {
                "classification": classification,
                "live": live,
                "exact_replay_signal": exact,
                "signal_time_replay_decision": _decision_summary(signal_time_decision),
                "scan_time_replay_decision": _decision_summary(scan_time_decision),
                "nearby_live_market_data": live_diagnostics,
            }
        )

    replay_only = [row for row in replay_candidates if str(row.get("key")) not in live_keys]
    live_only = [row for row in live_candidates if str(row.get("key")) not in replay_keys]
    exact_matches = len(live_keys & replay_keys)
    diagnostic_served_from_counts = Counter(str(row.get("served_from") or "unknown") for row in attached_diagnostics)
    diagnostic_stale_counts = Counter(
        str(row.get("timeframe") or "unknown") for row in attached_diagnostics if row.get("stale") is True
    )

    report = {
        "type": "crypto_phase13_parity_report",
        "version": 1,
        "generated_at": utc_now(),
        "status": "matched" if not live_only and not replay_only else "mismatch",
        "paths": {
            "live_journal": str(live_journal_path),
            "replay_decisions": str(replay_decisions_path),
            "replay_journal": str(replay_journal_path),
            "market_diagnostics": None if market_diagnostics_path is None else str(market_diagnostics_path),
            "report": str(output_path),
        },
        "trigger_timeframe": trigger_timeframe.upper(),
        "live_candidates": len(live_candidates),
        "replay_candidates": len(replay_candidates),
        "exact_matches": exact_matches,
        "live_only": len(live_only),
        "replay_only": len(replay_only),
        "classification_counts": dict(classification_counts),
        "scan_time_rejection_stage_counts": dict(scan_stage_counts),
        "scan_time_rejection_reason_counts": dict(scan_reason_counts),
        "signal_time_rejection_stage_counts": dict(generated_stage_counts),
        "signal_time_rejection_reason_counts": dict(generated_reason_counts),
        "market_diagnostics_summary": {
            "attached_rows": len(attached_diagnostics),
            "served_from_counts": dict(diagnostic_served_from_counts),
            "stale_counts_by_timeframe": dict(diagnostic_stale_counts),
        },
        "live_comparisons": comparisons,
        "live_only_signals": live_only,
        "replay_only_signals": replay_only,
        "notes": [
            "Exact match key is generated_at + symbol + side.",
            "Scan-time comparison floors live observed_at to the replay trigger timeframe.",
            "Nearby market-data diagnostics are matched by symbol/timeframe and nearest observed_at within 20 minutes.",
        ],
    }
    write_json(report, output_path)
    return report


class _MarketDataAdapter:
    def __init__(self, provider: StaticMarketDataProvider) -> None:
        self.provider = provider

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int | None = None, end_time: object | None = None) -> pd.DataFrame:
        frame = self.provider.fetch_ohlcv(symbol, timeframe, limit=None)
        cutoff = _timestamp_or_none(end_time)
        if cutoff is not None:
            frame = frame[frame.index <= cutoff]
        return frame.tail(max(1, int(limit or self.provider.history_limit))).copy()


def build_replay_journal_id(*, cycle_id: str, signal: TradeSignal) -> str:
    raw = f"{cycle_id}|{signal.symbol}|{signal.fingerprint()}|{signal.generated_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def performance_summary(outcomes: Iterable[ForwardOutcome], *, risk_per_trade_pct: float) -> dict[str, object]:
    payloads = [outcome.payload for outcome in outcomes]
    closed = [row for row in payloads if row.get("status") == "closed" and row.get("r_multiple") is not None]
    r_values = [_as_float(row.get("r_multiple")) for row in closed]
    wins = [value for value in r_values if value > 0]
    losses = [value for value in r_values if value < 0]
    gross_loss = abs(sum(losses))
    profit_factor = (sum(wins) / gross_loss) if gross_loss > 0 else (float("inf") if wins else 0.0)
    equity_r: list[float] = []
    running = 0.0
    peak = 0.0
    max_drawdown_r = 0.0
    for value in r_values:
        running += value
        equity_r.append(round(running, 6))
        peak = max(peak, running)
        max_drawdown_r = max(max_drawdown_r, peak - running)
    cumulative_r = sum(r_values)
    roi_pct = cumulative_r * max(0.0, float(risk_per_trade_pct))
    return {
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 6) if closed else 0.0,
        "avg_r": round(cumulative_r / len(r_values), 6) if r_values else 0.0,
        "profit_factor": round(profit_factor, 6) if profit_factor != float("inf") else "inf",
        "cumulative_r": round(cumulative_r, 6),
        "max_drawdown_r": round(max_drawdown_r, 6),
        "roi_pct": round(roi_pct, 6),
        "risk_per_trade_pct": risk_per_trade_pct,
        "equity_curve_r": equity_r,
    }


def write_json(payload: Mapping[str, object], path: Path | str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
