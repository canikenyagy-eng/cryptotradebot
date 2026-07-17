from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping

from services.crypto_realtime_guard import (
    STATE_BLOCKED,
    STATE_GUARD_READY,
    CryptoRealtimeGuardEngine,
    CryptoRealtimeGuardSettings,
    MarketSnapshot,
    RealtimeSnapshotStore,
    write_crypto_realtime_guard_outputs,
)


def phase11_check(*, symbol: str = "BTCUSDT", side: str = "buy", entry_price: float = 100000.0) -> dict[str, object]:
    return {
        "source_request_id": "phase10-BTCUSDT-buy-source",
        "source_client_order_id": "phase9-BTCUSDT-buy-source",
        "source_intent_id": "intent-1",
        "symbol": symbol,
        "exchange_symbol": "BTC/USDT" if symbol == "BTCUSDT" else symbol,
        "side": side,
        "order_type": "market",
        "entry_price": entry_price,
        "reference_price": 100000.0,
        "entry_price_deviation_bps": 0.0,
        "spread_bps": 1.0,
        "state": "market_safe",
        "block_reasons": [],
        "order_submission_allowed": False,
        "live_execution_allowed": False,
    }


def phase11_report(*checks: dict[str, object], ready: bool = True) -> dict[str, object]:
    return {
        "type": "crypto_market_safety_report",
        "phase": "phase11_realtime_market_safety",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "decision": {
            "action": "REALTIME_MARKET_SAFETY_READY" if ready else "BLOCK_EXECUTION_MARKET_UNSAFE",
            "readiness": "market_safe" if ready else "blocked",
            "market_data_safe": ready,
            "order_submission_allowed": False,
            "live_execution_allowed": False,
        },
        "summary": {
            "requests_checked": len(checks),
            "market_safe": len(checks) if ready else 0,
            "blocked": 0 if ready else len(checks),
            "order_submission_allowed": 0,
            "live_execution_allowed": 0,
        },
        "checks": list(checks),
        "execution_market_data_safe": ready,
        "order_submission_allowed": False,
        "live_execution_allowed": False,
    }


def write_diagnostics(
    path: Path,
    *,
    served_from: str = "provider",
    stale: bool = False,
    ok: bool = True,
) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "market_data_fetch",
                "version": 1,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "data_source": "ccxt",
                "pair": "BTCUSDT",
                "timeframe": "M5",
                "served_from": served_from,
                "ok": ok,
                "stale": stale,
                "candle_age_seconds": 3,
                "last_candle_time": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )


class FakeRealtimeProvider:
    def __init__(
        self,
        *,
        bid: float = 99995.0,
        ask: float = 100005.0,
        last: float = 100000.0,
        server_time_offset_ms: int = 0,
        fail_ticker: bool = False,
    ) -> None:
        self.bid = bid
        self.ask = ask
        self.last = last
        self.server_time_offset_ms = server_time_offset_ms
        self.fail_ticker = fail_ticker

    def fetch_ticker(self, symbol: str) -> Mapping[str, object]:
        if self.fail_ticker:
            raise ConnectionError("ticker unavailable")
        return {
            "symbol": symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "timestamp": int(time.time() * 1000),
        }

    def fetch_order_book(self, symbol: str, limit: int) -> Mapping[str, object]:
        return {
            "symbol": symbol,
            "bids": [[self.bid, 1.0]],
            "asks": [[self.ask, 1.0]],
        }

    def fetch_exchange_time_ms(self) -> int | None:
        return int(time.time() * 1000) + self.server_time_offset_ms

    def close(self) -> None:
        return None


def fresh_store(*, bid: float = 99995.0, ask: float = 100005.0, last: float = 100000.0) -> RealtimeSnapshotStore:
    store = RealtimeSnapshotStore()
    now = datetime.now(timezone.utc).isoformat()
    store.upsert(
        MarketSnapshot(
            symbol="BTCUSDT",
            exchange_symbol="BTC/USDT",
            bid=bid,
            ask=ask,
            last=last,
            bid_size=1.0,
            ask_size=1.0,
            book_ticker_observed_at=now,
            last_observed_at=now,
            observed_at=now,
            source="websocket_book_ticker+mini_ticker",
        )
    )
    return store


def stale_store() -> RealtimeSnapshotStore:
    store = RealtimeSnapshotStore()
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    store.upsert(
        MarketSnapshot(
            symbol="BTCUSDT",
            exchange_symbol="BTC/USDT",
            bid=99995.0,
            ask=100005.0,
            last=100000.0,
            book_ticker_observed_at=old,
            last_observed_at=old,
            observed_at=old,
            source="websocket_book_ticker+mini_ticker",
        )
    )
    return store


class CryptoRealtimeGuardTests(unittest.TestCase):
    def build_engine(
        self,
        tmpdir: str,
        *,
        store: RealtimeSnapshotStore | None = None,
        provider: FakeRealtimeProvider | None = None,
        allow_rest_fallback: bool = True,
        enable_order_book_refresh: bool = True,
        served_from: str = "provider",
        stale_diagnostics: bool = False,
    ) -> CryptoRealtimeGuardEngine:
        diagnostics = Path(tmpdir) / "market_data.jsonl"
        write_diagnostics(diagnostics, served_from=served_from, stale=stale_diagnostics)
        settings = CryptoRealtimeGuardSettings(
            market_data_diagnostics_path=diagnostics,
            snapshot_path=Path(tmpdir) / "snapshots.json",
            max_market_diagnostics_age_seconds=60 * 60 * 24,
            allow_rest_fallback=allow_rest_fallback,
            enable_order_book_refresh=enable_order_book_refresh,
        )
        return CryptoRealtimeGuardEngine(settings, store=store or fresh_store(), provider=provider or FakeRealtimeProvider())

    def test_final_guard_passes_with_fresh_snapshot_and_clean_diagnostics(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir)

            report = engine.build_report(phase11_report(phase11_check()))

        self.assertEqual(report["decision"]["action"], "FINAL_PRE_ORDER_GUARD_READY")
        self.assertTrue(report["paper_testnet_readiness_allowed"])
        self.assertFalse(report["order_submission_allowed"])
        self.assertFalse(report["live_execution_allowed"])
        check = report["checks"][0]
        self.assertEqual(check["state"], STATE_GUARD_READY)
        self.assertEqual(check["block_reasons"], [])

    def test_missing_snapshot_blocks_when_rest_fallback_disabled(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(
                tmpdir,
                store=RealtimeSnapshotStore(),
                allow_rest_fallback=False,
                enable_order_book_refresh=False,
            )

            report = engine.build_report(phase11_report(phase11_check()))

        self.assertEqual(report["decision"]["action"], "BLOCK_FINAL_PRE_ORDER_GUARD")
        self.assertEqual(report["checks"][0]["state"], STATE_BLOCKED)
        self.assertIn("snapshot_missing", report["checks"][0]["block_reasons"])

    def test_stale_snapshot_uses_rest_fallback_when_enabled(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir, store=stale_store(), provider=FakeRealtimeProvider())

            report = engine.build_report(phase11_report(phase11_check()))

        self.assertEqual(report["decision"]["action"], "FINAL_PRE_ORDER_GUARD_READY")
        self.assertEqual(report["checks"][0]["snapshot"]["source"], "rest_ticker_fallback")

    def test_stale_snapshot_blocks_when_rest_fallback_fails(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir, store=stale_store(), provider=FakeRealtimeProvider(fail_ticker=True))

            report = engine.build_report(phase11_report(phase11_check()))

        self.assertEqual(report["decision"]["action"], "BLOCK_FINAL_PRE_ORDER_GUARD")
        reasons = report["checks"][0]["block_reasons"]
        self.assertIn("snapshot_too_old", reasons)
        self.assertIn("rest_ticker_fallback_failed", reasons)

    def test_wide_spread_blocks_final_guard(self) -> None:
        with TemporaryDirectory() as tmpdir:
            provider = FakeRealtimeProvider(bid=99000.0, ask=101000.0)
            engine = self.build_engine(tmpdir, store=fresh_store(bid=99000.0, ask=101000.0), provider=provider)

            report = engine.build_report(phase11_report(phase11_check()))

        self.assertEqual(report["decision"]["action"], "BLOCK_FINAL_PRE_ORDER_GUARD")
        self.assertIn("spread_above_max", report["checks"][0]["block_reasons"])

    def test_exchange_time_drift_blocks_final_guard(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir, provider=FakeRealtimeProvider(server_time_offset_ms=10_000))

            report = engine.build_report(phase11_report(phase11_check()))

        self.assertEqual(report["decision"]["action"], "BLOCK_FINAL_PRE_ORDER_GUARD")
        self.assertIn("exchange_time_drift_above_max", report["checks"][0]["block_reasons"])

    def test_entry_price_deviation_blocks_final_guard(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir)

            report = engine.build_report(phase11_report(phase11_check(entry_price=110000.0)))

        self.assertEqual(report["decision"]["action"], "BLOCK_FINAL_PRE_ORDER_GUARD")
        self.assertIn("entry_price_deviation_above_max", report["checks"][0]["block_reasons"])

    def test_stale_cache_diagnostics_block_final_guard(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir, served_from="stale_cache_after_provider_failure")

            report = engine.build_report(phase11_report(phase11_check()))

        self.assertEqual(report["decision"]["action"], "BLOCK_FINAL_PRE_ORDER_GUARD")
        self.assertIn("stale_cache_used_for_signal_data", report["checks"][0]["block_reasons"])

    def test_websocket_payloads_update_snapshot_store(self) -> None:
        store = RealtimeSnapshotStore()
        observed = datetime.now(timezone.utc)

        store.update_from_websocket_payload(
            {"stream": "btcusdt@bookTicker", "data": {"u": 1, "s": "BTCUSDT", "b": "99995", "B": "1.5", "a": "100005", "A": "2.5"}},
            observed_at=observed,
        )
        store.update_from_websocket_payload(
            {"stream": "btcusdt@miniTicker", "data": {"e": "24hrMiniTicker", "E": int(time.time() * 1000), "s": "BTCUSDT", "c": "100000"}},
            observed_at=observed,
        )

        snapshot = store.get("BTCUSDT")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.bid, 99995.0)
        self.assertEqual(snapshot.ask, 100005.0)
        self.assertEqual(snapshot.last, 100000.0)

    def test_outputs_write_report_and_checks_csv(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir)
            settings = CryptoRealtimeGuardSettings(
                report_path=Path(tmpdir) / "phase12.json",
                checks_csv_path=Path(tmpdir) / "checks.csv",
                snapshot_path=Path(tmpdir) / "snapshots.json",
                market_data_diagnostics_path=Path(tmpdir) / "market_data.jsonl",
            )
            report = engine.build_report(phase11_report(phase11_check()))

            write_crypto_realtime_guard_outputs(report, settings)

            self.assertTrue(Path(settings.report_path).exists())
            self.assertTrue(Path(settings.checks_csv_path).exists())
            payload = json.loads(Path(settings.report_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["phase"], "phase12_realtime_final_guard")
            self.assertIn("paper_testnet_readiness_allowed", Path(settings.checks_csv_path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
