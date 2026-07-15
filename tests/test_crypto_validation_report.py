from __future__ import annotations

import unittest

from services.crypto_validation_report import (
    CryptoValidationThresholds,
    build_crypto_validation_report,
    render_crypto_validation_dashboard,
)


def sample_performance(*, closed: int, avg_r: float, profit_factor: float, max_dd: float) -> dict[str, object]:
    return {
        "overall": {
            "candidates": max(closed, 1),
            "delivered": max(closed, 1),
            "closed_with_r": closed,
            "win_rate": 0.6,
            "avg_r": avg_r,
            "total_r": round(avg_r * closed, 6),
            "profit_factor": profit_factor,
            "max_drawdown_r": max_dd,
        },
        "by_pair": {
            "BTCUSDT": {
                "candidates": closed,
                "closed_with_r": closed,
                "win_rate": 0.6,
                "avg_r": avg_r,
                "profit_factor": profit_factor,
                "total_r": round(avg_r * closed, 6),
                "max_drawdown_r": max_dd,
            }
        },
        "by_regime": {},
        "by_session": {},
        "by_score_bucket": {},
        "rows": [],
    }


class CryptoValidationReportTests(unittest.TestCase):
    def test_recommendation_collects_more_data_before_minimum_sample(self) -> None:
        report = build_crypto_validation_report(
            performance_report=sample_performance(closed=3, avg_r=0.4, profit_factor=2.0, max_dd=0.5),
            health={"ok": True, "reason": "healthy"},
            thresholds=CryptoValidationThresholds(min_closed_trades=30),
        )

        self.assertEqual(report["recommendation"]["action"], "COLLECT_MORE_FORWARD_DATA")
        self.assertEqual(report["recommendation"]["readiness"], "collecting")

    def test_recommendation_flags_negative_forward_expectancy(self) -> None:
        report = build_crypto_validation_report(
            performance_report=sample_performance(closed=35, avg_r=-0.1, profit_factor=0.8, max_dd=2.0),
            health={"ok": True, "reason": "healthy"},
            thresholds=CryptoValidationThresholds(min_closed_trades=30),
        )

        self.assertEqual(report["recommendation"]["action"], "PAUSE_OR_TIGHTEN_PROFILE")
        self.assertEqual(report["watchlist"]["pairs"][0]["name"], "BTCUSDT")

    def test_dashboard_html_contains_decision_and_metrics(self) -> None:
        report = build_crypto_validation_report(
            performance_report=sample_performance(closed=35, avg_r=0.2, profit_factor=1.5, max_dd=1.0),
            health={"ok": True, "reason": "healthy"},
            thresholds=CryptoValidationThresholds(min_closed_trades=30),
        )

        html = render_crypto_validation_dashboard(report)

        self.assertIn("Crypto Forward Validation", html)
        self.assertIn("KEEP_PROFILE_SIGNAL_ONLY", html)
        self.assertIn("BTCUSDT", html)


if __name__ == "__main__":
    unittest.main()
