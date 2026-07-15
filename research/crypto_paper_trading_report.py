from __future__ import annotations

import argparse
import os

from config import Settings, load_env_file
from research.crypto_forward_validation import apply_phase4_defaults
from services.crypto_paper_trading import (
    CryptoPaperTradingSimulator,
    PaperTradingSettings,
    write_paper_trading_outputs,
)


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


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = _env_str(name, default).lower()
    return value if value in choices else default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay forward outcomes into a crypto paper-trading account.")
    parser.add_argument("--journal", default=None, help="Forward journal JSONL path")
    parser.add_argument("--outcomes", default=None, help="Forward outcomes JSONL path")
    parser.add_argument("--report-json", default=_env_str("PAPER_REPORT_JSON", "reports/crypto_paper_trading_report.json"))
    parser.add_argument("--ledger-csv", default=_env_str("PAPER_LEDGER_CSV", "reports/crypto_paper_trading_ledger.csv"))
    parser.add_argument(
        "--dashboard-html",
        default=_env_str("PAPER_DASHBOARD_HTML", "reports/crypto_paper_trading_dashboard.html"),
    )
    parser.add_argument("--sent-only", action="store_true", help="Use only Telegram-delivered candidates")
    parser.add_argument("--include-unsent", action="store_true", help="Include dry-run/unsent candidates")
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
    parser.add_argument("--force-phase4-defaults", action="store_true", help="Apply Phase 4 crypto defaults before loading paths")
    return parser


def build_settings(app_settings: Settings, args: argparse.Namespace) -> PaperTradingSettings:
    sent_only = app_settings.forward_performance_sent_only
    if args.sent_only:
        sent_only = True
    if args.include_unsent:
        sent_only = False

    return PaperTradingSettings(
        journal_path=args.journal or app_settings.forward_journal_log_path,
        outcome_path=args.outcomes or app_settings.forward_outcome_log_path,
        report_path=args.report_json,
        ledger_path=args.ledger_csv,
        dashboard_path=args.dashboard_html,
        sent_only=sent_only,
        starting_balance=args.starting_balance,
        risk_mode=args.risk_mode,
        risk_per_trade=args.risk_per_trade,
        risk_pct=args.risk_pct,
        account_currency=args.account_currency,
        max_open_positions=args.max_open_positions,
        one_position_per_symbol=not args.allow_same_symbol_overlap,
        min_score=args.min_score,
    )


def print_summary(report: dict[str, object], settings: PaperTradingSettings) -> None:
    overall = report.get("overall", {})
    if not isinstance(overall, dict):
        overall = {}

    print()
    print("CRYPTO PAPER TRADING")
    print(
        "Candidates: {candidates} | Executed: {executed} | Skipped: {skipped}".format(
            candidates=int(overall.get("candidates", 0) or 0),
            executed=int(overall.get("executed_trades", 0) or 0),
            skipped=int(overall.get("skipped", 0) or 0),
        )
    )
    print(
        "Balance: {start:.2f} -> {final:.2f} {currency} | Net: {net:.2f} | ROI: {roi:.2f}% | MaxDD: {dd:.2f}".format(
            start=float(overall.get("starting_balance", 0.0) or 0.0),
            final=float(overall.get("final_balance", 0.0) or 0.0),
            currency=settings.account_currency,
            net=float(overall.get("net_pnl", 0.0) or 0.0),
            roi=float(overall.get("roi_pct", 0.0) or 0.0),
            dd=float(overall.get("max_drawdown", 0.0) or 0.0),
        )
    )
    print(
        "Win rate: {wr:.1%} | AvgR: {avg:.3f} | PF: {pf}".format(
            wr=float(overall.get("win_rate", 0.0) or 0.0),
            avg=float(overall.get("avg_r", 0.0) or 0.0),
            pf=overall.get("profit_factor", 0.0),
        )
    )
    print(f"Report: {settings.report_path}")
    print(f"Ledger: {settings.ledger_path}")
    print(f"Dashboard: {settings.dashboard_path}")


def main() -> None:
    load_env_file()
    args = build_parser().parse_args()
    if args.force_phase4_defaults:
        apply_phase4_defaults(force=True)
    else:
        apply_phase4_defaults(force=False)

    app_settings = Settings.from_env(require_telegram=False)
    paper_settings = build_settings(app_settings, args).normalized()
    simulator = CryptoPaperTradingSimulator(paper_settings)
    report = simulator.run()
    write_paper_trading_outputs(report, paper_settings)
    print_summary(report, paper_settings)


if __name__ == "__main__":
    main()
