from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping

from services.crypto_market_safety import (
    STATE_BLOCKED,
    STATE_MARKET_SAFE,
    CryptoMarketSafetyEngine,
    CryptoMarketSafetySettings,
    write_crypto_market_safety_outputs,
)


def phase10_request(*, symbol: str = "BTCUSDT", side: str = "BUY", entry_price: float = 100000.0) -> dict[str, object]:
    return {
        "source_client_order_id": "phase9-BTCUSDT-buy-source",
        "source_intent_id": "intent-1",
        "symbol": symbol,
        "exchange_symbol": "BTC/USDT" if symbol == "BTCUSDT" else symbol,
        "side": side,
        "order_type": "market",
        "quantity": 0.01,
        "entry_price": entry_price,
        "notional_quote": 1000.0,
        "state": "request_previewed",
        "block_reasons": [],
        "request": {
            "request_id": "phase10-BTCUSDT-buy-source",
            "exchange_id": "binance",
            "default_type": "spot",
            "sandbox_required": True,
            "method": "create_order",
            "symbol": "BTC/USDT",
            "type": "market",
            "side": side.lower(),
            "amount": 0.01,
            "price": None,
            "params": {"test": True, "sandbox": True},
        },
        "order_submission_attempted": False,
        "live_order_sent": False,
    }


def phase10_report(*requests: dict[str, object], ready: bool = True) -> dict[str, object]:
    return {
        "type": "crypto_testnet_order_preview_report",
        "phase": "phase10_testnet_order_preview",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "decision": {
            "action": "TESTNET_ORDER_REQUEST_PREVIEW_READY" if ready else "COLLECT_PHASE9_SANDBOX_ORDERS",
            "readiness": "preview_ready" if ready else "collecting",
            "testnet_request_preview_ready": ready,
            "live_execution_allowed": False,
        },
        "summary": {
            "source_orders_selected": len(requests),
            "request_previews": len(requests),
            "blocked": 0,
            "live_order_sent": 0,
        },
        "requests": list(requests),
        "order_submission_attempted": False,
        "testnet_order_submission_enabled": False,
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
                "observed_at": "2026-01-01T00:00:00+00:00",
                "data_source": "ccxt",
                "pair": "BTCUSDT",
                "timeframe": "M5",
                "served_from": served_from,
                "ok": ok,
                "stale": stale,
                "candle_age_seconds": 3,
                "last_candle_time": "2026-01-01T00:00:00+00:00",
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
        ticker_age_seconds: float = 1.0,
        server_time_offset_ms: int = 0,
    ) -> None:
        self.bid = bid
        self.ask = ask
        self.last = last
        self.ticker_age_seconds = ticker_age_seconds
        self.server_time_offset_ms = server_time_offset_ms

    def fetch_ticker(self, symbol: str) -> Mapping[str, object]:
        return {
            "symbol": symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "timestamp": int((time.time() - self.ticker_age_seconds) * 1000),
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


class CryptoMarketSafetyTests(unittest.TestCase):
    def build_engine(
        self,
        tmpdir: str,
        *,
        provider: FakeRealtimeProvider | None = None,
        served_from: str = "provider",
        stale: bool = False,
    ) -> CryptoMarketSafetyEngine:
        diagnostics = Path(tmpdir) / "market_data.jsonl"
        write_diagnostics(diagnostics, served_from=served_from, stale=stale)
        settings = CryptoMarketSafetySettings(
            market_data_diagnostics_path=diagnostics,
            max_market_diagnostics_age_seconds=60 * 60 * 24 * 365,
        )
        return CryptoMarketSafetyEngine(settings, provider=provider or FakeRealtimeProvider())

    def test_realtime_market_safety_passes_with_fresh_ticker_tight_spread_and_clean_diagnostics(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir)

            report = engine.build_report(phase10_report(phase10_request()))

        self.assertEqual(report["decision"]["action"], "REALTIME_MARKET_SAFETY_READY")
        self.assertFalse(report["order_submission_allowed"])
        self.assertFalse(report["live_execution_allowed"])
        self.assertEqual(report["summary"]["market_safe"], 1)
        check = report["checks"][0]
        self.assertEqual(check["state"], STATE_MARKET_SAFE)
        self.assertEqual(check["block_reasons"], [])

    def test_stale_ticker_blocks_market_safety(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir, provider=FakeRealtimeProvider(ticker_age_seconds=120))

            report = engine.build_report(phase10_report(phase10_request()))

        self.assertEqual(report["decision"]["action"], "BLOCK_EXECUTION_MARKET_UNSAFE")
        self.assertEqual(report["checks"][0]["state"], STATE_BLOCKED)
        self.assertIn("ticker_too_old", report["checks"][0]["block_reasons"])

    def test_wide_spread_blocks_market_safety(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir, provider=FakeRealtimeProvider(bid=99000.0, ask=101000.0))

            report = engine.build_report(phase10_report(phase10_request()))

        self.assertEqual(report["decision"]["action"], "BLOCK_EXECUTION_MARKET_UNSAFE")
        self.assertIn("spread_above_max", report["checks"][0]["block_reasons"])

    def test_exchange_time_drift_blocks_market_safety(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir, provider=FakeRealtimeProvider(server_time_offset_ms=10_000))

            report = engine.build_report(phase10_report(phase10_request()))

        self.assertEqual(report["decision"]["action"], "BLOCK_EXECUTION_MARKET_UNSAFE")
        self.assertIn("exchange_time_drift_above_max", report["checks"][0]["block_reasons"])

    def test_stale_cache_diagnostics_block_market_safety(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir, served_from="stale_cache_after_provider_failure")

            report = engine.build_report(phase10_report(phase10_request()))

        self.assertEqual(report["decision"]["action"], "BLOCK_EXECUTION_MARKET_UNSAFE")
        self.assertIn("stale_cache_used_for_signal_data", report["checks"][0]["block_reasons"])

    def test_outputs_write_report_and_checks_csv(self) -> None:
        with TemporaryDirectory() as tmpdir:
            engine = self.build_engine(tmpdir)
            settings = CryptoMarketSafetySettings(
                report_path=Path(tmpdir) / "phase11.json",
                checks_csv_path=Path(tmpdir) / "checks.csv",
                market_data_diagnostics_path=Path(tmpdir) / "market_data.jsonl",
            )
            report = engine.build_report(phase10_report(phase10_request()))

            write_crypto_market_safety_outputs(report, settings)

            self.assertTrue(Path(settings.report_path).exists())
            self.assertTrue(Path(settings.checks_csv_path).exists())
            payload = json.loads(Path(settings.report_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["phase"], "phase11_realtime_market_safety")
            self.assertIn("source_request_id", Path(settings.checks_csv_path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
