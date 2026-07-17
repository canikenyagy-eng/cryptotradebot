from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.crypto_testnet_order_preview import (
    STATE_BLOCKED,
    STATE_REQUEST_PREVIEWED,
    CryptoTestnetOrderPreviewEngine,
    CryptoTestnetOrderPreviewSettings,
    build_ccxt_order_request,
    write_crypto_testnet_order_preview_outputs,
)


def phase9_order(*, symbol: str = "BTCUSDT", side: str = "BUY", order_type: str = "market") -> dict[str, object]:
    return {
        "intent_id": "intent-1",
        "journal_id": "journal-1",
        "fingerprint": "fp-1",
        "client_order_id": "phase9-BTCUSDT-buy-source",
        "symbol": symbol,
        "exchange_symbol": "BTC/USDT" if symbol == "BTCUSDT" else symbol,
        "side": side,
        "order_type": order_type,
        "state": "simulated_filled",
        "reason": "dry_run_simulated_fill",
        "accepted": True,
        "filled": True,
        "dry_run_only": True,
        "live_order_sent": False,
        "quantity": 0.01,
        "entry_price": 100000.0,
        "notional_quote": 1000.0,
    }


def phase9_report(*orders: dict[str, object], ready: bool = True, live_sent: int = 0) -> dict[str, object]:
    return {
        "type": "crypto_sandbox_execution_report",
        "phase": "phase9_sandbox_execution_architecture",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "decision": {
            "action": "SANDBOX_EXECUTION_READY" if ready else "COLLECT_DRY_RUN_INTENTS",
            "readiness": "sandbox_ready" if ready else "collecting",
            "sandbox_execution_ready": ready,
            "live_execution_allowed": False,
        },
        "summary": {
            "selected_for_execution": len(orders),
            "accepted": len(orders),
            "filled": len(orders),
            "live_order_sent": live_sent,
        },
        "reconciliation": {
            "status": "reconciled",
            "expected_orders": len(orders),
            "recorded_orders": len(orders),
            "accepted": len(orders),
            "filled": len(orders),
            "live_order_sent": live_sent,
        },
        "orders": list(orders),
        "live_execution_allowed": False,
    }


class CryptoTestnetOrderPreviewTests(unittest.TestCase):
    def test_builds_ccxt_style_request_preview_without_submission(self) -> None:
        report = CryptoTestnetOrderPreviewEngine(CryptoTestnetOrderPreviewSettings()).build_report(
            phase9_report(phase9_order())
        )

        self.assertEqual(report["decision"]["action"], "TESTNET_ORDER_REQUEST_PREVIEW_READY")
        self.assertFalse(report["live_execution_allowed"])
        self.assertFalse(report["order_submission_attempted"])
        self.assertEqual(report["summary"]["request_previews"], 1)
        self.assertEqual(report["summary"]["live_order_sent"], 0)
        preview = report["requests"][0]
        self.assertEqual(preview["state"], STATE_REQUEST_PREVIEWED)
        self.assertFalse(preview["order_submission_attempted"])
        request = preview["request"]
        self.assertEqual(request["method"], "create_order")
        self.assertEqual(request["symbol"], "BTC/USDT")
        self.assertEqual(request["type"], "market")
        self.assertEqual(request["side"], "buy")
        self.assertIsNone(request["price"])
        self.assertTrue(request["params"]["test"])
        self.assertTrue(request["params"]["sandbox"])

    def test_limit_request_uses_entry_price(self) -> None:
        request = build_ccxt_order_request(
            phase9_order(order_type="limit"),
            CryptoTestnetOrderPreviewSettings(),
        )

        self.assertEqual(request["type"], "limit")
        self.assertEqual(request["price"], 100000.0)

    def test_blocks_when_phase9_is_not_ready_by_default(self) -> None:
        report = CryptoTestnetOrderPreviewEngine(CryptoTestnetOrderPreviewSettings()).build_report(
            phase9_report(phase9_order(), ready=False)
        )

        self.assertEqual(report["decision"]["action"], "BLOCK_PHASE9_SOURCE")
        self.assertIn("phase9_sandbox_execution_not_ready", report["source_report"]["block_reasons"])
        self.assertEqual(report["summary"]["request_previews"], 0)

    def test_blocks_phase9_source_with_live_order_sent(self) -> None:
        source = phase9_report(phase9_order(), live_sent=1)
        source["reconciliation"]["status"] = "safety_violation"

        report = CryptoTestnetOrderPreviewEngine(CryptoTestnetOrderPreviewSettings()).build_report(source)

        self.assertEqual(report["decision"]["action"], "BLOCK_PHASE9_SOURCE")
        self.assertIn("phase9_report_has_live_order_sent", report["source_report"]["block_reasons"])
        self.assertEqual(report["summary"]["live_order_sent"], 0)

    def test_spot_long_only_blocks_sell_preview(self) -> None:
        report = CryptoTestnetOrderPreviewEngine(
            CryptoTestnetOrderPreviewSettings(require_phase9_ready=True)
        ).build_report(phase9_report(phase9_order(side="SELL")))

        self.assertEqual(report["decision"]["action"], "REVIEW_BLOCKED_TESTNET_REQUESTS")
        self.assertEqual(report["requests"][0]["state"], STATE_BLOCKED)
        self.assertIn("spot_long_only_blocks_non_buy", report["requests"][0]["block_reasons"])
        self.assertFalse(report["requests"][0]["live_order_sent"])

    def test_outputs_write_report_and_requests_csv(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings = CryptoTestnetOrderPreviewSettings(
                report_path=Path(tmpdir) / "phase10.json",
                requests_csv_path=Path(tmpdir) / "requests.csv",
            )
            report = CryptoTestnetOrderPreviewEngine(settings).build_report(phase9_report(phase9_order()))

            write_crypto_testnet_order_preview_outputs(report, settings)

            self.assertTrue(Path(settings.report_path).exists())
            self.assertTrue(Path(settings.requests_csv_path).exists())
            payload = json.loads(Path(settings.report_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["phase"], "phase10_testnet_order_preview")
            self.assertIn("request_id", Path(settings.requests_csv_path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
