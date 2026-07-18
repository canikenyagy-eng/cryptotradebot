from __future__ import annotations

import argparse
import json
import os

from config import Settings, load_env_file
from execution.news import NewsFilter
from main import _build_live_pair_profiles, _build_market_data, _build_signal_engine, _effective_live_mode
from research.crypto_forward_validation import apply_phase4_defaults
from services.crypto_asof_replay import CryptoAsofReplayEngine, CryptoAsofReplaySettings


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    text = value.strip()
    return text or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env_str(name, "1" if default else "0").lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase 13 honest as-of replay validation.")
    parser.add_argument("--pairs", default=_env_str("PHASE13_PAIRS", "BTCUSDT,ETHUSDT,LTCUSDT"))
    parser.add_argument("--start", default=_env_str("PHASE13_START", ""))
    parser.add_argument("--end", default=_env_str("PHASE13_END", ""))
    parser.add_argument("--max-steps", type=int, default=_env_int("PHASE13_MAX_STEPS", 96))
    parser.add_argument("--history-limit", type=int, default=_env_int("PHASE13_HISTORY_LIMIT", 1200))
    parser.add_argument("--outcome-timeframe", default=_env_str("PHASE13_OUTCOME_TIMEFRAME", "M15"))
    parser.add_argument("--risk-per-trade-pct", type=float, default=_env_float("PHASE13_RISK_PER_TRADE_PCT", 1.0))
    parser.add_argument("--report-json", default=_env_str("PHASE13_REPORT_JSON", "reports/crypto_phase13_asof_replay_report.json"))
    parser.add_argument("--decisions-jsonl", default=_env_str("PHASE13_DECISIONS_JSONL", "logs/crypto_phase13_asof_replay_decisions.jsonl"))
    parser.add_argument("--journal-jsonl", default=_env_str("PHASE13_JOURNAL_JSONL", "logs/crypto_phase13_asof_replay_journal.jsonl"))
    parser.add_argument("--outcomes-jsonl", default=_env_str("PHASE13_OUTCOMES_JSONL", "logs/crypto_phase13_asof_replay_outcomes.jsonl"))
    parser.add_argument(
        "--outcome-summary-json",
        default=_env_str("PHASE13_OUTCOME_SUMMARY_JSON", "reports/crypto_phase13_asof_replay_outcome_summary.json"),
    )
    parser.add_argument(
        "--step-source",
        choices=("trigger", "live_journal", "trigger_plus_live_journal"),
        default=_env_str("PHASE13_STEP_SOURCE", "trigger"),
        help="Replay normal trigger candle steps, live journal scan steps, or both.",
    )
    parser.add_argument(
        "--live-journal",
        default=_env_str("PHASE13_LIVE_JOURNAL_JSONL", ""),
        help="Optional Phase 4 live journal for parity audit and live-journal scan steps.",
    )
    parser.add_argument(
        "--parity-report-json",
        default=_env_str("PHASE13_PARITY_REPORT_JSON", "reports/crypto_phase13_parity_report.json"),
        help="Output path for live-vs-replay parity report when --live-journal is set.",
    )
    parser.add_argument(
        "--market-diagnostics-jsonl",
        default=_env_str("PHASE13_MARKET_DIAGNOSTICS_JSONL", "logs/crypto_forward_market_data.jsonl"),
        help="Optional live market-data diagnostics JSONL attached to parity rows.",
    )
    parser.add_argument(
        "--allow-partial-warmup",
        action="store_true",
        default=not _env_bool("PHASE13_REQUIRE_FULL_WARMUP", True),
        help="Replay even before all pairs/timeframes have enough warmup candles.",
    )
    parser.add_argument(
        "--force-phase4-defaults",
        action="store_true",
        help="Override existing env values with the Phase 4 crypto profile before loading Settings.",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Print effective replay config and exit without fetching market data.",
    )
    return parser


def build_replay_settings(args: argparse.Namespace, app_settings: Settings) -> CryptoAsofReplaySettings:
    return CryptoAsofReplaySettings(
        pairs=args.pairs or ",".join(app_settings.pairs),
        start=args.start or None,
        end=args.end or None,
        max_steps=args.max_steps,
        history_limit=args.history_limit or app_settings.history_limit,
        report_path=args.report_json,
        decisions_path=args.decisions_jsonl,
        journal_path=args.journal_jsonl,
        outcomes_path=args.outcomes_jsonl,
        outcome_summary_path=args.outcome_summary_json,
        outcome_timeframe=args.outcome_timeframe or app_settings.forward_outcome_timeframe,
        risk_per_trade_pct=args.risk_per_trade_pct,
        require_full_warmup=not args.allow_partial_warmup,
        step_source=args.step_source,
        live_journal_path=args.live_journal or None,
        parity_report_path=args.parity_report_json or None,
        market_diagnostics_path=args.market_diagnostics_jsonl or None,
    ).normalized()


def print_config(replay_settings: CryptoAsofReplaySettings, app_settings: Settings, applied_defaults: dict[str, str]) -> None:
    payload = {
        "phase": "phase13_honest_asof_replay",
        "applied_defaults": sorted(applied_defaults),
        "data_source": app_settings.data_source,
        "market_type": app_settings.market_type,
        "pairs": replay_settings.pairs,
        "timeframes": {
            "htf": app_settings.htf_timeframe,
            "ltf": app_settings.ltf_timeframe,
            "trigger": app_settings.trigger_timeframe,
            "outcome": replay_settings.outcome_timeframe,
        },
        "history_limit": replay_settings.history_limit,
        "start": replay_settings.start,
        "end": replay_settings.end,
        "max_steps": replay_settings.max_steps,
        "require_full_warmup": replay_settings.require_full_warmup,
        "step_source": replay_settings.step_source,
        "live_journal": None if replay_settings.live_journal_path is None else str(replay_settings.live_journal_path),
        "paths": {
            "report": str(replay_settings.report_path),
            "decisions": str(replay_settings.decisions_path),
            "journal": str(replay_settings.journal_path),
            "outcomes": str(replay_settings.outcomes_path),
            "outcome_summary": str(replay_settings.outcome_summary_path),
            "parity_report": None
            if replay_settings.parity_report_path is None
            else str(replay_settings.parity_report_path),
            "market_diagnostics": None
            if replay_settings.market_diagnostics_path is None
            else str(replay_settings.market_diagnostics_path),
        },
        "no_future_rule": "Every engine market-data fetch is clipped to candles with timestamp <= replay step.",
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def print_summary(report: dict[str, object]) -> None:
    replay = report.get("replay") if isinstance(report.get("replay"), dict) else {}
    guard = report.get("no_future_guard") if isinstance(report.get("no_future_guard"), dict) else {}
    performance = report.get("performance") if isinstance(report.get("performance"), dict) else {}

    print()
    print("CRYPTO PHASE 13 HONEST AS-OF REPLAY")
    print(f"Steps: {replay.get('steps')} | decisions={replay.get('decisions')} | signals={replay.get('signals')}")
    print(f"No-future guard: passed={guard.get('passed')} blocked_future_rows={guard.get('future_rows_blocked')} leaks={guard.get('future_leaks')}")
    print(
        "Performance: closed={closed} avgR={avg_r} PF={pf} maxDD_R={dd} ROI%={roi}".format(
            closed=performance.get("closed"),
            avg_r=performance.get("avg_r"),
            pf=performance.get("profit_factor"),
            dd=performance.get("max_drawdown_r"),
            roi=performance.get("roi_pct"),
        )
    )
    parity = report.get("parity") if isinstance(report.get("parity"), dict) else None
    if parity:
        print(
            "Parity: status={status} exact={exact} live_only={live_only} replay_only={replay_only} report={path}".format(
                status=parity.get("status"),
                exact=parity.get("exact_matches"),
                live_only=parity.get("live_only"),
                replay_only=parity.get("replay_only"),
                path=parity.get("report_path"),
            )
        )
    print(f"Report: {report.get('paths', {}).get('report') if isinstance(report.get('paths'), dict) else ''}")


def main() -> None:
    load_env_file()
    args = build_parser().parse_args()
    applied = apply_phase4_defaults(force=args.force_phase4_defaults)
    app_settings = Settings.from_env(require_telegram=False)
    replay_settings = build_replay_settings(args, app_settings)
    print_config(replay_settings, app_settings, applied)
    if args.config_only:
        return

    source_market_data = _build_market_data(app_settings, ttl_hours=app_settings.market_data_cache_ttl_hours)
    live_mode = _effective_live_mode(app_settings)
    pair_profiles = _build_live_pair_profiles(app_settings, live_mode)
    signal_engine = _build_signal_engine(
        app_settings,
        source_market_data,
        NewsFilter(),
        live_mode=live_mode,
        pair_profiles=pair_profiles,
    )
    engine = CryptoAsofReplayEngine(
        replay_settings,
        signal_engine=signal_engine,
        source_provider=source_market_data,
        htf_timeframe=app_settings.htf_timeframe,
        ltf_timeframe=app_settings.ltf_timeframe,
        trigger_timeframe=app_settings.trigger_timeframe,
    )
    try:
        report = engine.run()
    finally:
        source_market_data.close()
    print_summary(report)


if __name__ == "__main__":
    main()
