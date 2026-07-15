from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from research.crypto_paper_trading_report import build_parser
from services.crypto_paper_trading import (
    CryptoPaperTradingSimulator,
    PaperTradingSettings,
    render_paper_trading_dashboard,
    write_paper_trading_outputs,
)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows), encoding="utf-8")


def candidate(
    journal_id: str,
    *,
    symbol: str = "BTCUSDT",
    generated_at: str = "2026-01-01T00:00:00+00:00",
    score: int = 82,
    delivered: bool = True,
) -> list[dict[str, object]]:
    signal = {
        "symbol": symbol,
        "fingerprint": f"fp-{journal_id}",
        "side": "BUY",
        "generated_at": generated_at,
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "planned_rr": 2.0,
        "score": score,
        "entry_mode": "MARKET",
        "entry_source": "test",
        "time_stop_bars": 48,
        "regime_label": "RANGE",
        "trigger_event": "BOS",
        "zone": "DISCOUNT",
    }
    return [
        {
            "type": "forward_signal_candidate",
            "journal_id": journal_id,
            "cycle_id": "cycle-1",
            "signal": signal,
        },
        {
            "type": "forward_signal_delivery",
            "journal_id": journal_id,
            "fingerprint": signal["fingerprint"],
            "symbol": symbol,
            "side": "BUY",
            "delivered": delivered,
        },
    ]


def outcome(
    journal_id: str,
    *,
    entry_time: str = "2026-01-01T00:05:00+00:00",
    exit_time: str = "2026-01-01T01:00:00+00:00",
    r_multiple: float = 2.0,
    status: str = "closed",
) -> dict[str, object]:
    return {
        "type": "forward_signal_outcome",
        "journal_id": journal_id,
        "status": status,
        "entry_status": "filled",
        "exit_reason": "take_profit" if r_multiple >= 0 else "stop_loss",
        "entry_time": entry_time,
        "exit_time": exit_time,
        "r_multiple": r_multiple,
    }


class CryptoPaperTradingTests(unittest.TestCase):
    def test_fixed_risk_replay_builds_account_report(self) -> None:
        with TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "journal.jsonl"
            outcomes = Path(tmpdir) / "outcomes.jsonl"
            write_jsonl(journal, candidate("j1"))
            write_jsonl(outcomes, [outcome("j1", r_multiple=2.0)])

            report = CryptoPaperTradingSimulator(
                PaperTradingSettings(
                    journal_path=journal,
                    outcome_path=outcomes,
                    sent_only=True,
                    starting_balance=1000.0,
                    risk_mode="fixed",
                    risk_per_trade=25.0,
                )
            ).run()

            self.assertEqual(report["overall"]["executed_trades"], 1)
            self.assertEqual(report["overall"]["final_balance"], 1050.0)
            self.assertEqual(report["overall"]["net_pnl"], 50.0)
            self.assertEqual(report["overall"]["profit_factor"], "inf")
            self.assertEqual(report["by_symbol"]["BTCUSDT"]["trades"], 1)
            self.assertEqual(report["ledger"][0]["risk_usd"], 25.0)

    def test_open_position_limit_skips_overlapping_candidate(self) -> None:
        with TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "journal.jsonl"
            outcomes = Path(tmpdir) / "outcomes.jsonl"
            write_jsonl(
                journal,
                [
                    *candidate("j1", generated_at="2026-01-01T00:00:00+00:00"),
                    *candidate("j2", generated_at="2026-01-01T00:10:00+00:00", symbol="ETHUSDT"),
                ],
            )
            write_jsonl(
                outcomes,
                [
                    outcome("j1", entry_time="2026-01-01T00:05:00+00:00", exit_time="2026-01-01T02:00:00+00:00"),
                    outcome("j2", entry_time="2026-01-01T00:15:00+00:00", exit_time="2026-01-01T00:30:00+00:00"),
                ],
            )

            report = CryptoPaperTradingSimulator(
                PaperTradingSettings(
                    journal_path=journal,
                    outcome_path=outcomes,
                    sent_only=True,
                    starting_balance=1000.0,
                    risk_per_trade=10.0,
                    max_open_positions=1,
                )
            ).run()

            self.assertEqual(report["overall"]["executed_trades"], 1)
            self.assertEqual(report["overall"]["skipped"], 1)
            self.assertEqual(report["overall"]["skip_reasons"]["max_open_positions"], 1)
            skipped = next(row for row in report["ledger"] if row["journal_id"] == "j2")
            self.assertFalse(skipped["executed"])
            self.assertEqual(skipped["skip_reason"], "max_open_positions")

    def test_equity_pct_risk_uses_current_balance_after_closed_trade(self) -> None:
        with TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "journal.jsonl"
            outcomes = Path(tmpdir) / "outcomes.jsonl"
            write_jsonl(
                journal,
                [
                    *candidate("j1", generated_at="2026-01-01T00:00:00+00:00"),
                    *candidate("j2", generated_at="2026-01-01T02:00:00+00:00"),
                ],
            )
            write_jsonl(
                outcomes,
                [
                    outcome("j1", entry_time="2026-01-01T00:05:00+00:00", exit_time="2026-01-01T01:00:00+00:00", r_multiple=2.0),
                    outcome("j2", entry_time="2026-01-01T02:05:00+00:00", exit_time="2026-01-01T03:00:00+00:00", r_multiple=-1.0),
                ],
            )

            report = CryptoPaperTradingSimulator(
                PaperTradingSettings(
                    journal_path=journal,
                    outcome_path=outcomes,
                    starting_balance=1000.0,
                    risk_mode="equity_pct",
                    risk_pct=0.01,
                )
            ).run()
            risks = {row["journal_id"]: row["risk_usd"] for row in report["ledger"] if row["executed"]}

            self.assertEqual(risks["j1"], 10.0)
            self.assertEqual(risks["j2"], 10.2)
            self.assertEqual(report["overall"]["final_balance"], 1009.8)

    def test_final_balance_uses_close_order_for_overlapping_trades(self) -> None:
        with TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "journal.jsonl"
            outcomes = Path(tmpdir) / "outcomes.jsonl"
            write_jsonl(
                journal,
                [
                    *candidate("j1", generated_at="2026-01-01T00:00:00+00:00"),
                    *candidate("j2", generated_at="2026-01-01T00:10:00+00:00", symbol="ETHUSDT"),
                ],
            )
            write_jsonl(
                outcomes,
                [
                    outcome("j1", entry_time="2026-01-01T00:05:00+00:00", exit_time="2026-01-01T03:00:00+00:00", r_multiple=1.0),
                    outcome("j2", entry_time="2026-01-01T00:15:00+00:00", exit_time="2026-01-01T01:00:00+00:00", r_multiple=-1.0),
                ],
            )

            report = CryptoPaperTradingSimulator(
                PaperTradingSettings(
                    journal_path=journal,
                    outcome_path=outcomes,
                    starting_balance=1000.0,
                    risk_per_trade=10.0,
                    max_open_positions=2,
                )
            ).run()

        self.assertEqual(report["overall"]["executed_trades"], 2)
        self.assertEqual(report["overall"]["final_balance"], 1000.0)
        self.assertEqual(report["equity_curve"][-1]["journal_id"], "j1")

    def test_outputs_write_json_ledger_and_dashboard(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings = PaperTradingSettings(
                report_path=Path(tmpdir) / "paper.json",
                ledger_path=Path(tmpdir) / "paper.csv",
                dashboard_path=Path(tmpdir) / "paper.html",
            )
            report = {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "settings": {"risk_mode": "fixed", "account_currency": "USD"},
                "overall": {"final_balance": 1000, "starting_balance": 1000, "skip_reasons": {}},
                "by_symbol": {},
                "by_regime": {},
                "ledger": [{"journal_id": "j1", "symbol": "BTCUSDT", "executed": False}],
            }

            write_paper_trading_outputs(report, settings)

            self.assertTrue(Path(settings.report_path).exists())
            self.assertTrue(Path(settings.ledger_path).read_text(encoding="utf-8").startswith("journal_id"))
            self.assertIn("Crypto Paper Trading", Path(settings.dashboard_path).read_text(encoding="utf-8"))
            self.assertIn("Final Balance", render_paper_trading_dashboard(report))

    def test_parser_uses_paper_env_defaults(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "PAPER_REPORT_JSON": "tmp/paper.json",
                "PAPER_LEDGER_CSV": "tmp/paper.csv",
                "PAPER_DASHBOARD_HTML": "tmp/paper.html",
                "PAPER_STARTING_BALANCE": "2500",
                "PAPER_RISK_MODE": "equity_pct",
                "PAPER_RISK_PCT": "0.02",
                "PAPER_MAX_OPEN_POSITIONS": "3",
            },
            clear=True,
        ):
            args = build_parser().parse_args([])

        self.assertEqual(args.report_json, "tmp/paper.json")
        self.assertEqual(args.ledger_csv, "tmp/paper.csv")
        self.assertEqual(args.dashboard_html, "tmp/paper.html")
        self.assertEqual(args.starting_balance, 2500.0)
        self.assertEqual(args.risk_mode, "equity_pct")
        self.assertEqual(args.risk_pct, 0.02)
        self.assertEqual(args.max_open_positions, 3)


if __name__ == "__main__":
    unittest.main()
