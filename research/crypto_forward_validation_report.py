from __future__ import annotations

import argparse
import json
from pathlib import Path

from config import Settings, load_env_file
from data.market_data import MarketDataCacheConfig, MarketDataClient
from main import (
    _ccxt_config_from_settings,
    _itick_config_from_settings,
    _live_bar_config_from_settings,
    _redundant_config_from_settings,
)
from research.crypto_forward_validation import apply_phase4_defaults
from services.crypto_validation_report import (
    CryptoValidationThresholds,
    build_crypto_validation_report,
    write_crypto_validation_outputs,
)
from services.feed_health import build_feed_health_components
from services.forward_outcomes import ForwardOutcomeSettings, ForwardOutcomeTracker
from services.forward_performance import ForwardPerformanceReporter, ForwardPerformanceSettings
from services.live_health import HealthCheckSettings, LiveHealthChecker, combine_health_components


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the Phase 5 crypto forward validation report and dashboard.")
    parser.add_argument("--journal", default=None, help="Forward journal JSONL path")
    parser.add_argument("--outcomes", default=None, help="Forward outcomes JSONL path")
    parser.add_argument("--outcome-summary", default=None, help="Outcome summary JSON path")
    parser.add_argument("--performance-report", default=None, help="Forward performance JSON path")
    parser.add_argument("--report-json", default="reports/crypto_forward_validation_report.json")
    parser.add_argument("--dashboard-html", default="reports/crypto_forward_validation_dashboard.html")
    parser.add_argument("--data-source", default=None, help="Market data source for outcome refresh")
    parser.add_argument("--timeframe", default=None, help="Outcome tracking timeframe")
    parser.add_argument("--history-limit", type=int, default=None, help="Outcome market data history limit")
    parser.add_argument("--cache-dir", default=None, help="OHLCV cache directory")
    parser.add_argument("--cache-only", action="store_true", help="Use cache only for outcome refresh")
    parser.add_argument("--refresh-cache", action="store_true", help="Refresh provider cache during outcome update")
    parser.add_argument("--skip-outcome-update", action="store_true", help="Do not refresh outcomes before reporting")
    parser.add_argument("--no-write-outcomes", action="store_true", help="Evaluate outcomes but do not append rows")
    parser.add_argument("--write-all", action="store_true", help="Do not skip candidates with terminal outcomes")
    parser.add_argument("--sent-only", action="store_true", help="Report only Telegram-delivered candidates")
    parser.add_argument("--include-unsent", action="store_true", help="Include unsent candidates even if env sent-only is enabled")
    parser.add_argument("--recent-minutes", type=int, default=None, help="Only include recent candidates in performance report")
    parser.add_argument("--min-closed-trades", type=int, default=30)
    parser.add_argument("--min-avg-r", type=float, default=0.10)
    parser.add_argument("--min-profit-factor", type=float, default=1.20)
    parser.add_argument("--max-drawdown-r", type=float, default=3.0)
    parser.add_argument("--force-phase4-defaults", action="store_true", help="Override env with Phase 4 crypto defaults first")
    return parser


def cache_mode(args: argparse.Namespace) -> str:
    if args.cache_only:
        return "cache_only"
    if args.refresh_cache:
        return "refresh"
    return "read_through"


def sent_only(settings_value: bool, args: argparse.Namespace) -> bool:
    value = settings_value
    if args.sent_only:
        value = True
    if args.include_unsent:
        value = False
    return value


def build_market_data(settings: Settings, args: argparse.Namespace, *, history_limit: int) -> MarketDataClient:
    source = (args.data_source or settings.data_source).strip().lower()
    selected_cache_mode = cache_mode(args)
    cache_enabled = settings.market_data_cache_enabled
    if source in {"live_bars", "redundant"}:
        cache_enabled = False
        selected_cache_mode = "disabled"

    return MarketDataClient(
        history_limit=max(settings.history_limit, history_limit),
        data_source=source,
        market_type=settings.market_type,
        symbol_specs=settings.symbol_specs,
        mt5_login=settings.mt5_login,
        mt5_password=settings.mt5_password,
        mt5_server=settings.mt5_server,
        mt5_path=settings.mt5_path,
        itick_config=_itick_config_from_settings(settings),
        ccxt_config=_ccxt_config_from_settings(settings),
        live_bar_config=_live_bar_config_from_settings(settings),
        redundant_config=_redundant_config_from_settings(settings),
        cache_config=MarketDataCacheConfig(
            enabled=cache_enabled,
            cache_dir=args.cache_dir or settings.market_data_cache_dir,
            ttl_hours=settings.market_data_cache_ttl_hours,
            mode=selected_cache_mode,
        ),
    )


def build_outcome_settings(settings: Settings, args: argparse.Namespace) -> ForwardOutcomeSettings:
    return ForwardOutcomeSettings(
        journal_path=args.journal or settings.forward_journal_log_path,
        output_path=args.outcomes or settings.forward_outcome_log_path,
        timeframe=args.timeframe or settings.forward_outcome_timeframe,
        history_limit=args.history_limit or settings.forward_outcome_history_limit,
        sent_only=sent_only(settings.forward_outcome_sent_only, args),
        max_hold_bars=settings.forward_outcome_max_hold_bars,
        entry_expiry_bars=settings.forward_outcome_entry_expiry_bars,
        ambiguous_policy=settings.forward_outcome_ambiguous_policy,
        skip_terminal_existing=not args.write_all,
    )


def update_outcomes(settings: Settings, args: argparse.Namespace) -> dict[str, object]:
    tracker = ForwardOutcomeTracker(build_outcome_settings(settings, args))
    market_data = build_market_data(settings, args, history_limit=tracker.settings.history_limit)
    try:
        outcomes = tracker.run(market_data)
        written = 0 if args.no_write_outcomes else tracker.append_outcomes(outcomes)
        summary = tracker.summarize(outcomes)
    finally:
        market_data.close()

    summary_path = Path(args.outcome_summary or settings.forward_outcome_summary_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return {
        "enabled": True,
        "written": written,
        "evaluated": len(outcomes),
        "summary_path": str(summary_path),
        "outcome_path": str(tracker.settings.output_path),
        "summary": summary,
    }


def build_performance_report(settings: Settings, args: argparse.Namespace) -> dict[str, object]:
    reporter = ForwardPerformanceReporter(
        ForwardPerformanceSettings(
            journal_path=args.journal or settings.forward_journal_log_path,
            outcome_path=args.outcomes or settings.forward_outcome_log_path,
            report_path=args.performance_report or settings.forward_performance_report_path,
            sent_only=sent_only(settings.forward_performance_sent_only, args),
            score_bucket_size=settings.forward_performance_score_bucket_size,
            min_closed_trades=args.min_closed_trades,
            recent_minutes=args.recent_minutes,
        )
    )
    report = reporter.build_report()
    reporter.write_report(report)
    return report


def build_health(settings: Settings) -> dict[str, object]:
    result = LiveHealthChecker(
        HealthCheckSettings(
            heartbeat_path=settings.live_heartbeat_path,
            max_scan_age_minutes=settings.health_max_scan_age_minutes,
        )
    ).check()
    combined = combine_health_components(result, build_feed_health_components(settings))
    return combined.to_dict()


def print_summary(report: dict[str, object]) -> None:
    overall = report.get("overall", {})
    if not isinstance(overall, dict):
        overall = {}
    recommendation = report.get("recommendation", {})
    if not isinstance(recommendation, dict):
        recommendation = {}

    print()
    print("CRYPTO FORWARD VALIDATION")
    print(
        "Candidates: {candidates} | Delivered: {delivered} | Closed-with-R: {closed}".format(
            candidates=int(overall.get("candidates", 0) or 0),
            delivered=int(overall.get("delivered", 0) or 0),
            closed=int(overall.get("closed_with_r", 0) or 0),
        )
    )
    print(
        "Win rate: {wr:.1%} | AvgR: {avg:.3f} | PF: {pf} | MaxDD: {dd:.3f}R".format(
            wr=float(overall.get("win_rate", 0.0) or 0.0),
            avg=float(overall.get("avg_r", 0.0) or 0.0),
            pf=overall.get("profit_factor", 0.0),
            dd=float(overall.get("max_drawdown_r", 0.0) or 0.0),
        )
    )
    print(f"Action: {recommendation.get('action')} | {recommendation.get('reason')}")
    print(f"JSON: {report.get('paths', {}).get('validation_report')}")
    print(f"Dashboard: {report.get('paths', {}).get('dashboard')}")


def main() -> None:
    args = build_parser().parse_args()
    load_env_file()
    apply_phase4_defaults(force=args.force_phase4_defaults)
    settings = Settings.from_env(require_telegram=False)

    if args.skip_outcome_update:
        outcome_update = {"enabled": False, "reason": "skipped"}
    else:
        outcome_update = update_outcomes(settings, args)

    performance = build_performance_report(settings, args)
    paths = {
        "validation_report": args.report_json,
        "dashboard": args.dashboard_html,
        "performance_report": args.performance_report or settings.forward_performance_report_path,
        "outcomes": args.outcomes or settings.forward_outcome_log_path,
        "journal": args.journal or settings.forward_journal_log_path,
    }
    report = build_crypto_validation_report(
        performance_report=performance,
        outcome_update=outcome_update,
        health=build_health(settings),
        thresholds=CryptoValidationThresholds(
            min_closed_trades=args.min_closed_trades,
            min_avg_r=args.min_avg_r,
            min_profit_factor=args.min_profit_factor,
            max_drawdown_r=args.max_drawdown_r,
        ),
        generated_paths=paths,
    )
    write_crypto_validation_outputs(report, report_path=args.report_json, dashboard_path=args.dashboard_html)
    print_summary(report)


if __name__ == "__main__":
    main()
