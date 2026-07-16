from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping

from services.crypto_sandbox_execution import (
    MODE_SANDBOX_STUB,
    STATE_BLOCKED_BY_KILL_SWITCH,
    STATE_REJECTED,
    STATE_SIMULATED_FILLED,
    CryptoSandboxExecutionEngine,
    CryptoSandboxExecutionSettings,
    build_idempotency_key,
    write_crypto_sandbox_execution_outputs,
)


def ready_intent(intent_id: str = "intent-1") -> dict[str, object]:
    return {
        "intent_id": intent_id,
        "journal_id": "journal-1",
        "fingerprint": "fp-1",
        "symbol": "BTCUSDT",
        "exchange_symbol": "BTC/USDT",
        "side": "BUY",
        "order_type": "market",
        "dry_run_only": True,
        "status": "ready_dry_run",
        "quantity": 0.01,
        "entry_price": 100000.0,
        "notional_quote": 1000.0,
    }


def source_report(*intents: Mapping[str, object]) -> dict[str, object]:
    return {
        "type": "crypto_execution_dry_run_report",
        "phase": "phase8_execution_design_dry_run",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "decision": {
            "action": "DRY_RUN_ORDER_INTENTS_READY",
            "readiness": "dry_run_ready",
            "live_execution_allowed": False,
            "dry_run_intents_ready": True,
        },
        "summary": {
            "candidates": len(intents),
            "ready_dry_run": len(intents),
            "blocked": 0,
        },
        "intents": [dict(intent) for intent in intents],
        "live_execution_allowed": False,
    }


class UnsafeAdapter:
    name = "unsafe_test_adapter"

    def submit_order_intent(
        self,
        intent: Mapping[str, object],
        settings: CryptoSandboxExecutionSettings,
        *,
        submitted_at: str,
    ) -> dict[str, object]:
        return {
            "intent_id": intent.get("intent_id"),
            "client_order_id": "unsafe-live-order",
            "symbol": intent.get("symbol"),
            "side": intent.get("side"),
            "mode": settings.mode,
            "state": "accepted",
            "reason": "unsafe test adapter",
            "accepted": True,
            "filled": False,
            "dry_run_only": False,
            "live_order_sent": True,
            "submitted_at": submitted_at,
            "events": [],
        }


class CryptoSandboxExecutionTests(unittest.TestCase):
    def test_dry_run_adapter_simulates_ready_intent_without_live_order(self) -> None:
        report = CryptoSandboxExecutionEngine(CryptoSandboxExecutionSettings()).build_report(source_report(ready_intent()))

        self.assertEqual(report["decision"]["action"], "SANDBOX_EXECUTION_READY")
        self.assertFalse(report["live_execution_allowed"])
        self.assertEqual(report["summary"]["accepted"], 1)
        self.assertEqual(report["summary"]["live_order_sent"], 0)
        order = report["orders"][0]
        self.assertEqual(order["state"], STATE_SIMULATED_FILLED)
        self.assertTrue(order["accepted"])
        self.assertTrue(order["filled"])
        self.assertFalse(order["live_order_sent"])
        self.assertEqual(order["client_order_id"], build_idempotency_key(ready_intent()))

    def test_kill_switch_blocks_selected_intents(self) -> None:
        with TemporaryDirectory() as tmpdir:
            kill_switch = Path(tmpdir) / "kill.json"
            kill_switch.write_text(json.dumps({"enabled": True, "reason": "manual halt"}), encoding="utf-8")

            report = CryptoSandboxExecutionEngine(
                CryptoSandboxExecutionSettings(kill_switch_path=kill_switch)
            ).build_report(source_report(ready_intent()))

        self.assertEqual(report["decision"]["action"], "KILL_SWITCH_ACTIVE")
        self.assertEqual(report["orders"][0]["state"], STATE_BLOCKED_BY_KILL_SWITCH)
        self.assertFalse(report["orders"][0]["live_order_sent"])

    def test_sandbox_stub_is_inert_until_explicitly_enabled(self) -> None:
        report = CryptoSandboxExecutionEngine(
            CryptoSandboxExecutionSettings(mode=MODE_SANDBOX_STUB, allow_sandbox_stub=False)
        ).build_report(source_report(ready_intent()))

        self.assertEqual(report["decision"]["action"], "REVIEW_EXECUTION_REJECTIONS")
        self.assertEqual(report["orders"][0]["state"], STATE_REJECTED)
        self.assertEqual(report["orders"][0]["reason"], "sandbox_stub_not_enabled")
        self.assertFalse(report["orders"][0]["live_order_sent"])

    def test_sandbox_stub_can_simulate_when_enabled_but_still_sends_no_live_order(self) -> None:
        report = CryptoSandboxExecutionEngine(
            CryptoSandboxExecutionSettings(mode=MODE_SANDBOX_STUB, allow_sandbox_stub=True)
        ).build_report(source_report(ready_intent()))

        self.assertEqual(report["decision"]["action"], "SANDBOX_EXECUTION_READY")
        self.assertEqual(report["orders"][0]["state"], STATE_SIMULATED_FILLED)
        self.assertEqual(report["orders"][0]["reason"], "sandbox_stub_simulated_fill")
        self.assertFalse(report["orders"][0]["live_order_sent"])

    def test_live_order_sent_flag_is_a_safety_violation(self) -> None:
        report = CryptoSandboxExecutionEngine(
            CryptoSandboxExecutionSettings(),
            adapter=UnsafeAdapter(),
        ).build_report(source_report(ready_intent()))

        self.assertEqual(report["decision"]["action"], "SAFETY_VIOLATION")
        self.assertFalse(report["decision"]["live_execution_allowed"])
        self.assertEqual(report["reconciliation"]["status"], "safety_violation")

    def test_outputs_write_report_and_ledger(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings = CryptoSandboxExecutionSettings(
                report_path=Path(tmpdir) / "phase9.json",
                ledger_csv_path=Path(tmpdir) / "ledger.csv",
            )
            report = CryptoSandboxExecutionEngine(settings).build_report(source_report(ready_intent()))

            write_crypto_sandbox_execution_outputs(report, settings)

            self.assertTrue(Path(settings.report_path).exists())
            self.assertTrue(Path(settings.ledger_csv_path).exists())
            payload = json.loads(Path(settings.report_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["phase"], "phase9_sandbox_execution_architecture")
            self.assertIn("client_order_id", Path(settings.ledger_csv_path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
