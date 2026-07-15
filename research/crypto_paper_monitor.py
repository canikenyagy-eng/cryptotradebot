from __future__ import annotations

import argparse
import os
import sys

from config import Settings, load_env_file
from research.crypto_forward_validation import apply_phase4_defaults
from research.crypto_forward_validation_report import build_health, update_outcomes
from research.crypto_paper_trading_report import build_settings as build_paper_settings
from services.crypto_execution_readiness import (
    CryptoExecutionReadinessThresholds,
    build_crypto_execution_readiness_report,
    write_crypto_execution_readiness_outputs,
)
from services.crypto_paper_trading import CryptoPaperTradingSimulator, write_paper_trading_outputs


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    text = value.strip()
    return text or default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env_str(name, "1" if default else "0").lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = _env_str(name, default).lower()
    return value if value in choices else default


def _env_symbols(name: str, default: str) -> tuple[str, ...]:
    return tuple(symbol.strip().upper() for symbol in _env_str(name, default).split(",") if symbol.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase 7 crypto paper monitor and execution-readiness guardrails.")
    parser.add_argument("--journal", default=None, help="Forward journal JSONL path")
    parser.add_argument("--outcomes", default=None, help="Forward outcomes JSONL path")
    parser.add_argument("--skip-outcome-update", action="store_true", help="Do not refresh theoretical outcomes first")
    parser.add_argument("--no-write-outcomes", action="store_true", help="Evaluate outcomes but do not append JSONL rows")
    parser.add_argument("--write-all", action="store_true", help="Do not skip candidates with terminal outcomes")
    parser.add_argument("--data-source", default=None, help="Market data source for outcome refresh")
    parser.add_argument("--timeframe", default=None, help="Outcome tracking timeframe")
    parser.add_argument("--history-limit", type=int, default=None, help="Outcome market data history limit")
    parser.add_argument("--cache-dir", default=None, help="OHLCV cache directory")
    parser.add_argument("--cache-only", action="store_true", help="Use cache only for outcome refresh")
    parser.add_argument("--refresh-cache", action="store_true", help="Refresh provider cache during outcome update")
    parser.add_argument("--sent-only", action="store_true", help="Use only Telegram-delivered candidates")
    parser.add_argument("--include-unsent", action="store_true", help="Include dry-run/unsent candidates")

    parser.add_argument("--paper-report-json", dest="report_json", default=_env_str("PAPER_REPORT_JSON", "reports/crypto_paper_trading_report.json"))
    parser.add_argument("--paper-ledger-csv", dest="ledger_csv", default=_env_str("PAPER_LEDGER_CSV", "reports/crypto_paper_trading_ledger.csv"))
    parser.add_argument(
        "--paper-dashboard-html",
        dest="dashboard_html",
        default=_env_str("PAPER_DASHBOARD_HTML", "reports/crypto_paper_trading_dashboard.html"),
    )
    parser.add_argument("--starting-balance", type=float, default=_env_float("PAPER_STARTING_BALANCE", 1000.0))
    parser.add_argument(
        "--risk-mode",
        choices=("fixed", "equity_pct"),
        default=_env_choice("PAPER_RISK_MODE", "fixed", {"fixed", "equity_pct"}),
    )
    parser.add_argument("--risk-per-trade", type=float, default=_env_float("PAPER_RISK_PER_TRADE", 25.0))
    parser.add_argument("--risk-pct", type=float, default=_env_float("PAPER_RISK_PCT", 0.01))
    parser.add_argument("--account-currency", default=_env_str("PAPER_ACCOUNT_CURRENCY", "USD"))
    parser.add_argument("--max-open-positions", type=int, default=_env_int("PAPER_MAX_OPEN_POSITIONS", 1))
    parser.add_argument("--allow-same-symbol-overlap", action="store_true")
    parser.add_argument("--min-score", type=int, default=_env_int("PAPER_MIN_SCORE", 0))

    parser.add_argument("--monitor-json", default=_env_str("PHASE7_MONITOR_JSON", "reports/crypto_phase7_paper_monitor.json"))
    parser.add_argument(
        "--monitor-dashboard-html",
        default=_env_str("PHASE7_MONITOR_DASHBOARD_HTML", "reports/crypto_phase7_paper_monitor_dashboard.html"),
    )
    parser.add_argument("--min-paper-trades", type=int, default=_env_int("PHASE7_MIN_PAPER_TRADES", 30))
    parser.add_argument("--min-symbol-trades", type=int, default=_env_int("PHASE7_MIN_SYMBOL_TRADES", 5))
    parser.add_argument("--min-avg-r", type=float, default=_env_float("PHASE7_MIN_AVG_R", 0.10))
    parser.add_argument("--min-profit-factor", type=float, default=_env_float("PHASE7_MIN_PROFIT_FACTOR", 1.20))
    parser.add_argument("--max-drawdown-pct", type=float, default=_env_float("PHASE7_MAX_DRAWDOWN_PCT", 10.0))
    parser.add_argument("--min-roi-pct", type=float, default=_env_float("PHASE7_MIN_ROI_PCT", 0.0))
    parser.add_argument(
        "--required-symbols",
        default=",".join(_env_symbols("PHASE7_REQUIRED_SYMBOLS", "BTCUSDT,ETHUSDT")),
        help="Comma-separated symbols that must have enough paper evidence",
    )
    parser.add_argument(
        "--no-require-health-ok",
        action="store_true",
        default=not _env_bool("PHASE7_REQUIRE_HEALTH_OK", True),
        help="Do not block readiness when heartbeat/feed health is unhealthy or missing",
    )
    parser.add_argument(
        "--allow-signal-live-mode",
        action="store_true",
        default=not _env_bool("PHASE7_REQUIRE_SIGNAL_LIVE_MODE_DISABLED", True),
        help="Do not block readiness when ENABLE_LIVE_MODE is enabled",
    )
    parser.add_argument("--fail-on-not-ready", action="store_true", help="Exit with code 2 unless Phase 7 guardrails pass")
    parser.add_argument("--force-phase4-defaults", action="store_true", help="Override env with Phase 4 crypto defaults first")
    return parser


def build_thresholds(args: argparse.Namespace) -> CryptoExecutionReadinessThresholds:
    return CryptoExecutionReadinessThresholds(
        min_paper_trades=args.min_paper_trades,
        min_symbol_trades=args.min_symbol_trades,
        min_avg_r=args.min_avg_r,
        min_profit_factor=args.min_profit_factor,
        max_drawdown_pct=args.max_drawdown_pct,
        min_roi_pct=args.min_roi_pct,
        required_symbols=tuple(symbol.strip().upper() for symbol in args.required_symbols.split(",") if symbol.strip()),
        require_health_ok=not args.no_require_health_ok,
        require_signal_live_mode_disabled=not args.allow_signal_live_mode,
    )


def settings_snapshot(settings: Settings) -> dict[str, object]:
    return {
        "market_type": settings.market_type,
        "data_source": settings.data_source,
        "pairs": settings.pairs,
        "enable_live_mode": settings.enable_live_mode,
        "live_mode": settings.live_mode,
        "forward_journal_log_path": settings.forward_journal_log_path,
        "forward_outcome_log_path": settings.forward_outcome_log_path,
        "live_heartbeat_path": settings.live_heartbeat_path,
        "market_data_diagnostics_log_path": settings.market_data_diagnostics_log_path,
    }


def build_paths(args: argparse.Namespace, paper_settings) -> dict[str, object]:
    return {
        "monitor_report": args.monitor_json,
        "monitor_dashboard": args.monitor_dashboard_html,
        "paper_report": str(paper_settings.report_path),
        "paper_ledger": str(paper_settings.ledger_path),
        "paper_dashboard": str(paper_settings.dashboard_path),
        "journal": str(paper_settings.journal_path),
        "outcomes": str(paper_settings.outcome_path),
    }


def run_monitor_once(args: argparse.Namespace) -> dict[str, object]:
    applied = apply_phase4_defaults(force=args.force_phase4_defaults)
    settings = Settings.from_env(require_telegram=False)

    outcome_update = {"enabled": False, "reason": "skipped"}
    if not args.skip_outcome_update:
        outcome_update = update_outcomes(settings, args)

    paper_settings = build_paper_settings(settings, args).normalized()
    paper_report = CryptoPaperTradingSimulator(paper_settings).run()
    write_paper_trading_outputs(paper_report, paper_settings)

    health = build_health(settings)
    snapshot = settings_snapshot(settings)
    snapshot["applied_phase4_defaults"] = sorted(applied.keys())
    report = build_crypto_execution_readiness_report(
        paper_report=paper_report,
        health=health,
        thresholds=build_thresholds(args),
        settings_snapshot=snapshot,
        outcome_update=outcome_update,
        generated_paths=build_paths(args, paper_settings),
    )
    write_crypto_execution_readiness_outputs(
        report,
        report_path=args.monitor_json,
        dashboard_path=args.monitor_dashboard_html,
    )
    return report


def print_summary(report: dict[str, object]) -> None:
    decision = report.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    paper = report.get("paper_summary")
    if not isinstance(paper, dict):
        paper = {}
    paths = report.get("paths")
    if not isinstance(paths, dict):
        paths = {}

    print()
    print("CRYPTO PHASE 7 PAPER MONITOR")
    print(f"Action: {decision.get('action')} | readiness={decision.get('readiness')}")
    print(f"Reason: {decision.get('reason')}")
    print(
        "Paper: candidates={candidates} executed={executed} final={final:.2f} ROI={roi:.2f}% DD={dd:.2f}%".format(
            candidates=int(paper.get("candidates", 0) or 0),
            executed=int(paper.get("executed_trades", 0) or 0),
            final=float(paper.get("final_balance", 0.0) or 0.0),
            roi=float(paper.get("roi_pct", 0.0) or 0.0),
            dd=float(paper.get("max_drawdown_pct", 0.0) or 0.0),
        )
    )
    print(f"Live execution allowed: {decision.get('live_execution_allowed')}")
    print(f"Monitor: {paths.get('monitor_report')}")
    print(f"Dashboard: {paths.get('monitor_dashboard')}")
    print(f"Paper report: {paths.get('paper_report')}")


def main() -> None:
    load_env_file()
    args = build_parser().parse_args()
    report = run_monitor_once(args)
    print_summary(report)
    decision = report.get("decision")
    ready = isinstance(decision, dict) and decision.get("next_phase_allowed") is True
    if args.fail_on_not_ready and not ready:
        sys.exit(2)


if __name__ == "__main__":
    main()
