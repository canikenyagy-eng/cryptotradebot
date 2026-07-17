from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from core.scoring import ScoreBreakdown
from core.signal_engine import SignalEvaluation, TradeSignal
from services.crypto_asof_replay import (
    STATE_ACCEPTED,
    AsOfMarketDataProvider,
    AsofReplayScanner,
    CryptoAsofReplayEngine,
    CryptoAsofReplaySettings,
    StaticMarketDataProvider,
    build_phase13_parity_report,
    performance_summary,
)
from services.forward_outcomes import ForwardOutcome


def frame(start: str = "2026-01-01T00:00:00Z", periods: int = 180, freq: str = "5min", base: float = 100.0) -> pd.DataFrame:
    index = pd.date_range(start=start, periods=periods, freq=freq, tz="UTC")
    values = [base + idx for idx in range(periods)]
    return pd.DataFrame(
        {
            "open": values,
            "high": [value + 1.0 for value in values],
            "low": [value - 1.0 for value in values],
            "close": values,
            "volume": [1.0] * periods,
        },
        index=index,
    )


def score_breakdown(total: int = 88) -> ScoreBreakdown:
    return ScoreBreakdown(
        htf_alignment=20,
        regime_alignment=10,
        trigger_confirmation=15,
        liquidity_displacement=10,
        premium_discount=10,
        news_filter=5,
        session_timing=5,
        fvg_alignment=4,
        order_block_alignment=4,
        mitigation_alignment=3,
        smt_alignment=2,
        shadow_bonus=0,
        total=total,
    )


def signal(symbol: str, generated_at: pd.Timestamp) -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        side="BUY",
        entry=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        entry_mode="MARKET",
        entry_source="phase13_test",
        entry_summary="test",
        management_summary="test",
        partial_take_profit=None,
        partial_take_fraction=0.0,
        break_even_r=1.0,
        trailing_enabled=False,
        trailing_start_r=1.5,
        trailing_lookback_bars=6,
        time_stop_bars=6,
        score=88,
        htf_bias="BULLISH",
        regime_label="RANGE",
        regime_direction="BULLISH",
        zone="DISCOUNT",
        trigger_direction="BULLISH",
        trigger_event="test",
        trigger_strength=10,
        structure_event="BOS",
        structure_trend="BULLISH",
        generated_at=generated_at.to_pydatetime(),
        score_breakdown=score_breakdown(),
    )


class NoopCorrelationCap:
    def filter(self, candidates):
        return candidates, []


class FakeReplaySignalEngine:
    htf_timeframe = "H1"
    ltf_timeframe = "M15"
    trigger_timeframe = "M5"
    enable_smt_confirmation = False
    regime_long_window = 80
    swing_window = 3

    def __init__(self, market_data: AsOfMarketDataProvider) -> None:
        self.market_data = market_data
        self.correlation_cap = NoopCorrelationCap()
        self.seen_max_times: list[pd.Timestamp] = []

    @staticmethod
    def _normalize_pair(pair: str) -> str:
        return pair.upper().replace("/", "")

    def _fetch_frames(self, pair: str):
        return (
            self.market_data.fetch_ohlcv(pair, "H1"),
            self.market_data.fetch_ohlcv(pair, "M15"),
            self.market_data.fetch_ohlcv(pair, "M5"),
        )

    def _resolve_smt_reference_pair(self, pair, universe):
        return None

    def evaluate_snapshot(self, pair, htf, ltf, *, trigger_frame=None, **kwargs):
        trigger = trigger_frame if trigger_frame is not None else ltf
        latest = pd.Timestamp(trigger.index[-1]).tz_convert("UTC")
        assert self.market_data.as_of is not None
        self.seen_max_times.append(latest)
        if latest > self.market_data.as_of:
            raise AssertionError("scanner saw future candle")
        trade_signal = signal(pair, latest)
        return SignalEvaluation(
            accepted=True,
            signal=trade_signal,
            rejection_stage=None,
            rejection_reason=None,
            details={},
            score_breakdown=trade_signal.score_breakdown,
            news_assessment=None,
            regime_label="RANGE",
            score_value=88,
            threshold_used=80,
            recommended_threshold=80,
        )

    def _apply_currency_exposure_cap(self, candidates):
        return candidates, []

    def _apply_portfolio_exposure_cap(self, candidates):
        return candidates, []

    def gate_signal_release(self, signal, *, commit: bool):
        return True, None


class CryptoAsofReplayTests(unittest.TestCase):
    def test_asof_provider_blocks_future_candles(self) -> None:
        source = StaticMarketDataProvider({("BTCUSDT", "M5"): frame(periods=10)}, history_limit=10)
        provider = AsOfMarketDataProvider(source, history_limit=10)
        as_of = pd.Timestamp("2026-01-01T00:20:00Z")

        provider.set_as_of(as_of)
        result = provider.fetch_ohlcv("BTCUSDT", "M5")

        self.assertEqual(result.index.max(), as_of)
        self.assertEqual(len(result), 5)
        self.assertEqual(provider.access_log[0].future_rows_blocked, 5)
        self.assertEqual(provider.access_summary()["future_leaks"], 0)

    def test_asof_provider_requires_timestamp_before_fetch(self) -> None:
        source = StaticMarketDataProvider({("BTCUSDT", "M5"): frame(periods=10)}, history_limit=10)
        provider = AsOfMarketDataProvider(source, history_limit=10)

        with self.assertRaises(ValueError):
            provider.fetch_ohlcv("BTCUSDT", "M5")

    def test_replay_scanner_never_receives_future_candles(self) -> None:
        frames = {
            ("BTCUSDT", "H1"): frame(periods=180, freq="1h"),
            ("BTCUSDT", "M15"): frame(periods=180, freq="15min"),
            ("BTCUSDT", "M5"): frame(periods=180, freq="5min"),
        }
        source = StaticMarketDataProvider(frames, history_limit=180)
        provider = AsOfMarketDataProvider(source, history_limit=180)
        engine = FakeReplaySignalEngine(provider)
        scanner = AsofReplayScanner(engine, provider)
        as_of = pd.Timestamp("2026-01-01T10:00:00Z")

        result = scanner.scan_cycle(["BTCUSDT"], as_of=as_of, cycle_id="test-cycle")

        self.assertEqual(result.signals[0].symbol, "BTCUSDT")
        self.assertEqual(result.decisions[0].state, STATE_ACCEPTED)
        self.assertTrue(engine.seen_max_times)
        self.assertLessEqual(max(engine.seen_max_times), as_of)
        self.assertGreater(sum(access.future_rows_blocked for access in result.accesses), 0)
        payload = result.decisions[0].to_dict()
        self.assertEqual(len(payload["market_data_accesses"]), 3)
        trigger_access = [row for row in payload["market_data_accesses"] if row["timeframe"] == "M5"][0]
        self.assertEqual(trigger_access["returned_last_time"], as_of.isoformat())
        self.assertEqual(trigger_access["returned_lag_seconds"], 0.0)

    def test_phase13_engine_writes_report_with_no_future_guard(self) -> None:
        with TemporaryDirectory() as tmpdir:
            frames = {
                ("BTCUSDT", "H1"): frame(periods=180, freq="1h"),
                ("BTCUSDT", "M15"): frame(periods=180, freq="15min"),
                ("BTCUSDT", "M5"): frame(periods=180, freq="5min"),
            }
            source = StaticMarketDataProvider(frames, history_limit=180)
            asof_provider = AsOfMarketDataProvider(source, history_limit=180)
            fake_engine = FakeReplaySignalEngine(asof_provider)
            settings = CryptoAsofReplaySettings(
                pairs=("BTCUSDT",),
                max_steps=2,
                history_limit=180,
                report_path=Path(tmpdir) / "report.json",
                decisions_path=Path(tmpdir) / "decisions.jsonl",
                journal_path=Path(tmpdir) / "journal.jsonl",
                outcomes_path=Path(tmpdir) / "outcomes.jsonl",
                outcome_summary_path=Path(tmpdir) / "summary.json",
                require_full_warmup=False,
            )
            replay = CryptoAsofReplayEngine(
                settings,
                signal_engine=fake_engine,  # type: ignore[arg-type]
                source_provider=source,
                htf_timeframe="H1",
                ltf_timeframe="M15",
                trigger_timeframe="M5",
            )

            report = replay.run()

            self.assertTrue(report["no_future_guard"]["passed"])
            self.assertGreater(report["no_future_guard"]["future_rows_blocked"], 0)
            self.assertEqual(report["replay"]["signals"], 2)
            self.assertTrue(Path(settings.report_path).exists())
            decisions = Path(settings.decisions_path).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(decisions), 2)
            self.assertEqual(json.loads(decisions[0])["state"], STATE_ACCEPTED)

    def test_performance_summary_calculates_drawdown_and_roi(self) -> None:
        outcomes = [
            ForwardOutcome({"status": "closed", "r_multiple": 1.0}),
            ForwardOutcome({"status": "closed", "r_multiple": -1.0}),
            ForwardOutcome({"status": "closed", "r_multiple": 2.0}),
            ForwardOutcome({"status": "open", "r_multiple": None}),
        ]

        summary = performance_summary(outcomes, risk_per_trade_pct=1.0)

        self.assertEqual(summary["closed"], 3)
        self.assertEqual(summary["avg_r"], 0.666667)
        self.assertEqual(summary["profit_factor"], 3.0)
        self.assertEqual(summary["max_drawdown_r"], 1.0)
        self.assertEqual(summary["roi_pct"], 2.0)

    def test_parity_report_flags_live_replay_mismatch(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            live_journal = root / "live.jsonl"
            replay_decisions = root / "decisions.jsonl"
            replay_journal = root / "replay.jsonl"
            diagnostics = root / "diagnostics.jsonl"
            report_path = root / "parity.json"

            live_journal.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "forward_signal_candidate",
                                "version": 1,
                                "observed_at": "2026-01-01T00:12:30+00:00",
                                "cycle_id": "live-1",
                                "journal_id": "live-journal-1",
                                "signal": {
                                    "symbol": "BTCUSDT",
                                    "side": "BUY",
                                    "generated_at": "2026-01-01T00:05:00+00:00",
                                    "fingerprint": "live-fp",
                                    "score": 82,
                                    "entry": 100.0,
                                    "stop_loss": 99.0,
                                    "take_profit": 102.0,
                                    "entry_mode": "MARKET",
                                    "entry_source": "test",
                                    "regime_label": "RANGE",
                                    "trigger_event": "BOS",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "forward_signal_delivery",
                                "journal_id": "live-journal-1",
                                "fingerprint": "live-fp",
                                "status": "sent",
                                "delivered": True,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            replay_decisions.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "phase13_replay_decision",
                                "as_of": "2026-01-01T00:05:00+00:00",
                                "symbol": "BTCUSDT",
                                "state": "rejected",
                                "stage": "scoring",
                                "reason": "score below threshold",
                                "score": 77,
                                "details": {"regime_label": "RANGE", "threshold": 80},
                                "visible_frames": {"M5": {"rows": 10, "last": "2026-01-01T00:05:00+00:00"}},
                                "market_data_accesses": [
                                    {
                                        "symbol": "BTCUSDT",
                                        "timeframe": "M5",
                                        "returned_last_time": "2026-01-01T00:05:00+00:00",
                                    }
                                ],
                            }
                        ),
                        json.dumps(
                            {
                                "type": "phase13_replay_decision",
                                "as_of": "2026-01-01T00:10:00+00:00",
                                "symbol": "BTCUSDT",
                                "state": "rejected",
                                "stage": "regime_gate",
                                "reason": "blocked regime label",
                                "details": {"regime_label": "TREND"},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            replay_journal.write_text("", encoding="utf-8")
            diagnostics.write_text(
                json.dumps(
                    {
                        "type": "market_data_fetch",
                        "observed_at": "2026-01-01T00:12:00+00:00",
                        "pair": "BTCUSDT",
                        "timeframe": "M5",
                        "served_from": "provider",
                        "ok": True,
                        "stale": False,
                        "last_candle_time": "2026-01-01T00:10:00+00:00",
                        "rows": 1200,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = build_phase13_parity_report(
                live_journal_path=live_journal,
                replay_decisions_path=replay_decisions,
                replay_journal_path=replay_journal,
                market_diagnostics_path=diagnostics,
                output_path=report_path,
                trigger_timeframe="M5",
            )

            self.assertEqual(report["status"], "mismatch")
            self.assertEqual(report["live_candidates"], 1)
            self.assertEqual(report["exact_matches"], 0)
            self.assertEqual(report["live_only"], 1)
            self.assertEqual(report["classification_counts"]["replay_rejected_at_scan_time"], 1)
            comparison = report["live_comparisons"][0]
            self.assertEqual(comparison["scan_time_replay_decision"]["stage"], "regime_gate")
            self.assertEqual(comparison["signal_time_replay_decision"]["stage"], "scoring")
            self.assertEqual(comparison["nearby_live_market_data"][0]["served_from"], "provider")
            self.assertTrue(report_path.exists())


if __name__ == "__main__":
    unittest.main()
