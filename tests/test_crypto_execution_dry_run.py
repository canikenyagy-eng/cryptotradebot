from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.crypto_execution_dry_run import (
    CryptoExecutionDryRunPlanner,
    CryptoExecutionDryRunSettings,
    render_crypto_execution_dry_run_dashboard,
    write_crypto_execution_dry_run_outputs,
)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows), encoding="utf-8")


def write_readiness(path: Path, *, ready: bool = True) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "crypto_execution_readiness_report",
                "generated_at": "2026-01-01T00:00:00+00:00",
                "decision": {
                    "next_phase_allowed": ready,
                    "readiness": "paper_ready_for_execution_design" if ready else "collecting",
                    "action": "READY_FOR_EXECUTION_DESIGN_REVIEW" if ready else "KEEP_PAPER_MONITORING",
                    "reason": "ready" if ready else "more data needed",
                },
            }
        ),
        encoding="utf-8",
    )


def candidate(journal_id: str, *, side: str = "BUY", delivered: bool = True) -> list[dict[str, object]]:
    signal = {
        "symbol": "BTCUSDT",
        "fingerprint": f"fp-{journal_id}",
        "side": side,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "planned_rr": 2.0,
        "score": 82,
        "entry_mode": "MARKET",
        "entry_source": "test",
        "time_stop_bars": 48,
        "regime_label": "RANGE",
        "trigger_event": "BOS",
        "zone": "DISCOUNT",
    }
    return [
        {"type": "forward_signal_candidate", "journal_id": journal_id, "cycle_id": "cycle-1", "signal": signal},
        {
            "type": "forward_signal_delivery",
            "journal_id": journal_id,
            "fingerprint": signal["fingerprint"],
            "symbol": "BTCUSDT",
            "side": side,
            "delivered": delivered,
        },
    ]


SYMBOL_SPECS = {
    "BTCUSDT": {
        "exchange_symbol": "BTC/USDT",
        "tick_size": 0.01,
        "min_order_size": 0.00001,
        "quantity_step": 0.00001,
        "taker_fee_rate": 0.001,
    }
}


class CryptoExecutionDryRunTests(unittest.TestCase):
    def test_builds_ready_dry_run_order_intent_when_phase7_passed(self) -> None:
        with TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "journal.jsonl"
            readiness = Path(tmpdir) / "readiness.json"
            write_jsonl(journal, candidate("j1"))
            write_readiness(readiness, ready=True)

            report = CryptoExecutionDryRunPlanner(
                CryptoExecutionDryRunSettings(
                    journal_path=journal,
                    readiness_path=readiness,
                    risk_per_intent=25.0,
                    min_notional_quote=10.0,
                ),
                symbol_specs=SYMBOL_SPECS,
                market_type="crypto_spot",
                pairs=("BTCUSDT",),
            ).build_report()

        intent = report["intents"][0]
        self.assertEqual(report["decision"]["action"], "DRY_RUN_ORDER_INTENTS_READY")
        self.assertFalse(report["live_execution_allowed"])
        self.assertEqual(intent["status"], "ready_dry_run")
        self.assertEqual(intent["exchange_symbol"], "BTC/USDT")
        self.assertAlmostEqual(intent["quantity"], 5.0)
        self.assertEqual(intent["notional_quote"], 500.0)

    def test_phase7_not_ready_blocks_intents_by_default(self) -> None:
        with TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "journal.jsonl"
            readiness = Path(tmpdir) / "readiness.json"
            write_jsonl(journal, candidate("j1"))
            write_readiness(readiness, ready=False)

            report = CryptoExecutionDryRunPlanner(
                CryptoExecutionDryRunSettings(journal_path=journal, readiness_path=readiness),
                symbol_specs=SYMBOL_SPECS,
                market_type="crypto_spot",
                pairs=("BTCUSDT",),
            ).build_report()

        self.assertEqual(report["decision"]["action"], "WAIT_FOR_PHASE7_READINESS")
        self.assertIn("phase7_not_ready", report["intents"][0]["block_reasons"])

    def test_spot_sell_is_blocked_without_override(self) -> None:
        with TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "journal.jsonl"
            readiness = Path(tmpdir) / "readiness.json"
            write_jsonl(journal, candidate("j1", side="SELL"))
            write_readiness(readiness, ready=True)

            report = CryptoExecutionDryRunPlanner(
                CryptoExecutionDryRunSettings(journal_path=journal, readiness_path=readiness),
                symbol_specs=SYMBOL_SPECS,
                market_type="crypto_spot",
                pairs=("BTCUSDT",),
            ).build_report()

        self.assertEqual(report["decision"]["action"], "REVIEW_BLOCKED_ORDER_INTENTS")
        self.assertIn("spot_sell_intent_blocked", report["intents"][0]["block_reasons"])

    def test_outputs_write_json_csv_and_dashboard(self) -> None:
        with TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "journal.jsonl"
            readiness = Path(tmpdir) / "readiness.json"
            write_jsonl(journal, candidate("j1"))
            write_readiness(readiness, ready=True)
            settings = CryptoExecutionDryRunSettings(
                journal_path=journal,
                readiness_path=readiness,
                report_path=Path(tmpdir) / "dry_run.json",
                tickets_csv_path=Path(tmpdir) / "tickets.csv",
                dashboard_path=Path(tmpdir) / "dry_run.html",
            )
            report = CryptoExecutionDryRunPlanner(
                settings,
                symbol_specs=SYMBOL_SPECS,
                market_type="crypto_spot",
                pairs=("BTCUSDT",),
            ).build_report()

            write_crypto_execution_dry_run_outputs(report, settings)

            self.assertTrue(Path(settings.report_path).exists())
            self.assertIn("intent_id", Path(settings.tickets_csv_path).read_text(encoding="utf-8"))
            self.assertIn("Crypto Execution Dry Run", Path(settings.dashboard_path).read_text(encoding="utf-8"))
            self.assertIn("Order Intents", render_crypto_execution_dry_run_dashboard(report))


if __name__ == "__main__":
    unittest.main()
