from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.crypto_execution_readiness import (
    CryptoExecutionReadinessThresholds,
    build_crypto_execution_readiness_report,
    render_crypto_execution_readiness_dashboard,
    write_crypto_execution_readiness_outputs,
)


def paper_report(
    *,
    executed: int,
    avg_r: float,
    profit_factor: float | str,
    drawdown_pct: float,
    roi_pct: float,
    btc_trades: int = 0,
    eth_trades: int = 0,
) -> dict[str, object]:
    return {
        "overall": {
            "candidates": max(executed, 1),
            "executed_trades": executed,
            "skipped": 0,
            "win_rate": 0.6,
            "avg_r": avg_r,
            "total_r": round(avg_r * executed, 6),
            "profit_factor": profit_factor,
            "final_balance": 1100.0,
            "net_pnl": 100.0,
            "roi_pct": roi_pct,
            "max_drawdown": 25.0,
            "max_drawdown_pct": drawdown_pct,
        },
        "by_symbol": {
            "BTCUSDT": {"trades": btc_trades, "win_rate": 0.6, "avg_r": avg_r, "profit_factor": profit_factor, "net_pnl": 50.0},
            "ETHUSDT": {"trades": eth_trades, "win_rate": 0.6, "avg_r": avg_r, "profit_factor": profit_factor, "net_pnl": 50.0},
        },
    }


def settings_snapshot(*, enable_live_mode: bool = False) -> dict[str, object]:
    return {
        "market_type": "crypto_spot",
        "data_source": "ccxt",
        "pairs": ["BTCUSDT", "ETHUSDT"],
        "enable_live_mode": enable_live_mode,
        "live_mode": "legacy",
    }


class CryptoExecutionReadinessTests(unittest.TestCase):
    def test_collects_more_data_before_minimum_paper_sample(self) -> None:
        report = build_crypto_execution_readiness_report(
            paper_report=paper_report(
                executed=3,
                avg_r=0.3,
                profit_factor=2.0,
                drawdown_pct=1.0,
                roi_pct=3.0,
                btc_trades=2,
                eth_trades=1,
            ),
            health={"ok": True, "reason": "healthy"},
            settings_snapshot=settings_snapshot(),
            thresholds=CryptoExecutionReadinessThresholds(min_paper_trades=30),
        )

        self.assertEqual(report["decision"]["action"], "KEEP_PAPER_MONITORING")
        self.assertFalse(report["decision"]["live_execution_allowed"])

    def test_ready_for_execution_design_review_keeps_live_execution_disabled(self) -> None:
        report = build_crypto_execution_readiness_report(
            paper_report=paper_report(
                executed=40,
                avg_r=0.22,
                profit_factor=1.8,
                drawdown_pct=4.0,
                roi_pct=8.0,
                btc_trades=20,
                eth_trades=20,
            ),
            health={"ok": True, "reason": "healthy"},
            settings_snapshot=settings_snapshot(),
            thresholds=CryptoExecutionReadinessThresholds(min_paper_trades=30, min_symbol_trades=5),
        )

        self.assertEqual(report["decision"]["action"], "READY_FOR_EXECUTION_DESIGN_REVIEW")
        self.assertTrue(report["decision"]["next_phase_allowed"])
        self.assertFalse(report["decision"]["live_execution_allowed"])

    def test_health_or_signal_live_mode_blocks_readiness(self) -> None:
        unhealthy = build_crypto_execution_readiness_report(
            paper_report=paper_report(
                executed=40,
                avg_r=0.22,
                profit_factor=1.8,
                drawdown_pct=4.0,
                roi_pct=8.0,
                btc_trades=20,
                eth_trades=20,
            ),
            health={"ok": False, "reason": "heartbeat stale"},
            settings_snapshot=settings_snapshot(),
        )
        live_mode = build_crypto_execution_readiness_report(
            paper_report=paper_report(
                executed=40,
                avg_r=0.22,
                profit_factor=1.8,
                drawdown_pct=4.0,
                roi_pct=8.0,
                btc_trades=20,
                eth_trades=20,
            ),
            health={"ok": True, "reason": "healthy"},
            settings_snapshot=settings_snapshot(enable_live_mode=True),
        )

        self.assertEqual(unhealthy["decision"]["action"], "BLOCK_LIVE_EXECUTION_DESIGN")
        self.assertEqual(live_mode["decision"]["action"], "BLOCK_LIVE_EXECUTION_DESIGN")

    def test_dashboard_and_outputs_are_written(self) -> None:
        report = build_crypto_execution_readiness_report(
            paper_report=paper_report(
                executed=40,
                avg_r=0.22,
                profit_factor=1.8,
                drawdown_pct=4.0,
                roi_pct=8.0,
                btc_trades=20,
                eth_trades=20,
            ),
            health={"ok": True, "reason": "healthy"},
            settings_snapshot=settings_snapshot(),
        )
        html = render_crypto_execution_readiness_dashboard(report)
        self.assertIn("Crypto Execution Readiness", html)
        self.assertIn("READY_FOR_EXECUTION_DESIGN_REVIEW", html)

        with TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "readiness.json"
            dashboard_path = Path(tmpdir) / "readiness.html"
            write_crypto_execution_readiness_outputs(
                report,
                report_path=report_path,
                dashboard_path=dashboard_path,
            )
            self.assertTrue(report_path.exists())
            self.assertIn("Guardrails", dashboard_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
