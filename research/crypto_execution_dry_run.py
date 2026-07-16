from __future__ import annotations

import argparse
import os

from config import Settings, load_env_file
from research.crypto_forward_validation import apply_phase4_defaults
from services.crypto_execution_dry_run import (
    CryptoExecutionDryRunPlanner,
    CryptoExecutionDryRunSettings,
    write_crypto_execution_dry_run_outputs,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Phase 8 dry-run crypto order intents and preflight report.")
    parser.add_argument("--journal", default=None, help="Forward journal JSONL path")
    parser.add_argument("--readiness-json", default=_env_str("PHASE8_READINESS_JSON", "reports/crypto_phase7_paper_monitor.json"))
    parser.add_argument("--report-json", default=_env_str("PHASE8_DRY_RUN_JSON", "reports/crypto_phase8_execution_dry_run.json"))
    parser.add_argument("--tickets-csv", default=_env_str("PHASE8_ORDER_INTENTS_CSV", "reports/crypto_phase8_order_intents.csv"))
    parser.add_argument("--dashboard-html", default=_env_str("PHASE8_DRY_RUN_DASHBOARD_HTML", "reports/crypto_phase8_execution_dry_run.html"))
    parser.add_argument("--sent-only", action="store_true", help="Use only Telegram-delivered candidates")
    parser.add_argument("--include-unsent", action="store_true", help="Include dry-run/unsent candidates")
    parser.add_argument("--max-candidates", type=int, default=_env_int("PHASE8_MAX_CANDIDATES", 10))
    parser.add_argument("--min-score", type=int, default=_env_int("PHASE8_MIN_SCORE", 0))
    parser.add_argument("--account-equity", type=float, default=_env_float("PHASE8_ACCOUNT_EQUITY", 1000.0))
    parser.add_argument(
        "--risk-mode",
        choices=("fixed", "equity_pct"),
        default=_env_choice("PHASE8_RISK_MODE", "fixed", {"fixed", "equity_pct"}),
    )
    parser.add_argument("--risk-per-intent", type=float, default=_env_float("PHASE8_RISK_PER_INTENT", 25.0))
    parser.add_argument("--risk-pct", type=float, default=_env_float("PHASE8_RISK_PCT", 0.01))
    parser.add_argument("--min-notional", type=float, default=_env_float("PHASE8_MIN_NOTIONAL_QUOTE", 10.0))
    parser.add_argument("--max-notional", type=float, default=_env_float("PHASE8_MAX_NOTIONAL_QUOTE", 5000.0))
    parser.add_argument("--max-risk", type=float, default=_env_float("PHASE8_MAX_RISK_QUOTE", 50.0))
    parser.add_argument(
        "--allow-spot-sell",
        action="store_true",
        default=_env_bool("PHASE8_ALLOW_SPOT_SELL", False),
        help="Allow sell-side spot intents in dry-run output",
    )
    parser.add_argument(
        "--ignore-phase7-not-ready",
        action="store_true",
        default=not _env_bool("PHASE8_REQUIRE_PHASE7_READY", True),
        help="Do not block dry-run intents when Phase 7 readiness has not passed",
    )
    parser.add_argument("--force-phase4-defaults", action="store_true", help="Override env with Phase 4 crypto defaults first")
    return parser


def sent_only_value(settings: Settings, args: argparse.Namespace) -> bool:
    value = settings.forward_performance_sent_only
    if args.sent_only:
        value = True
    if args.include_unsent:
        value = False
    return value


def build_settings(app_settings: Settings, args: argparse.Namespace) -> CryptoExecutionDryRunSettings:
    return CryptoExecutionDryRunSettings(
        journal_path=args.journal or app_settings.forward_journal_log_path,
        readiness_path=args.readiness_json,
        report_path=args.report_json,
        tickets_csv_path=args.tickets_csv,
        dashboard_path=args.dashboard_html,
        sent_only=sent_only_value(app_settings, args),
        max_candidates=args.max_candidates,
        min_score=args.min_score,
        account_equity=args.account_equity,
        risk_mode=args.risk_mode,
        risk_per_intent=args.risk_per_intent,
        risk_pct=args.risk_pct,
        min_notional_quote=args.min_notional,
        max_notional_quote=args.max_notional,
        max_risk_quote=args.max_risk,
        allow_spot_sell=args.allow_spot_sell,
        require_phase7_ready=not args.ignore_phase7_not_ready,
    )


def print_summary(report: dict[str, object], settings: CryptoExecutionDryRunSettings) -> None:
    decision = report.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    summary = report.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    print()
    print("CRYPTO PHASE 8 EXECUTION DRY RUN")
    print(f"Action: {decision.get('action')} | readiness={decision.get('readiness')}")
    print(f"Reason: {decision.get('reason')}")
    print(
        "Intents: candidates={candidates} ready={ready} blocked={blocked} risk={risk:.2f} notional={notional:.2f}".format(
            candidates=int(summary.get("candidates", 0) or 0),
            ready=int(summary.get("ready_dry_run", 0) or 0),
            blocked=int(summary.get("blocked", 0) or 0),
            risk=float(summary.get("total_risk_quote", 0.0) or 0.0),
            notional=float(summary.get("total_notional_quote", 0.0) or 0.0),
        )
    )
    print(f"Live execution allowed: {report.get('live_execution_allowed')}")
    print(f"Report: {settings.report_path}")
    print(f"Tickets: {settings.tickets_csv_path}")
    print(f"Dashboard: {settings.dashboard_path}")


def main() -> None:
    load_env_file()
    args = build_parser().parse_args()
    apply_phase4_defaults(force=args.force_phase4_defaults)
    app_settings = Settings.from_env(require_telegram=False)
    settings = build_settings(app_settings, args).normalized()
    planner = CryptoExecutionDryRunPlanner(
        settings,
        symbol_specs=app_settings.symbol_specs,
        market_type=app_settings.market_type,
        pairs=app_settings.pairs,
    )
    report = planner.build_report()
    write_crypto_execution_dry_run_outputs(report, settings)
    print_summary(report, settings)


if __name__ == "__main__":
    main()
