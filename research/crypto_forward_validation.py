from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Mapping

from config import Settings, load_env_file
from main import run_engine


PHASE4_SYMBOL_SPECS: dict[str, dict[str, object]] = {
    "BTCUSDT": {
        "exchange_symbol": "BTC/USDT",
        "tick_size": 0.01,
        "min_order_size": 0.00001,
        "maker_fee_rate": 0.001,
        "taker_fee_rate": 0.001,
    },
    "ETHUSDT": {
        "exchange_symbol": "ETH/USDT",
        "tick_size": 0.01,
        "min_order_size": 0.0001,
        "maker_fee_rate": 0.001,
        "taker_fee_rate": 0.001,
    },
    "LTCUSDT": {
        "exchange_symbol": "LTC/USDT",
        "tick_size": 0.01,
        "min_order_size": 0.001,
        "maker_fee_rate": 0.001,
        "taker_fee_rate": 0.001,
    },
}

PHASE4_PAIR_PROFILES: dict[str, dict[str, object]] = {
    "BTCUSDT": {
        "min_score": 78,
        "evaluation_step": 20,
        "regime_blocklist": "EXPANSION,CONTRACTION,TREND",
        "description": "Phase 3 strict range-only forward-validation profile.",
    },
    "ETHUSDT": {
        "min_score": 90,
        "evaluation_step": 20,
        "regime_blocklist": "EXPANSION,CONTRACTION,TREND",
        "allow_market_fallback": False,
        "description": "Phase 13 tightened ETH profile: score 90 and no MARKET fallback.",
    },
    "LTCUSDT": {
        "min_score": 90,
        "evaluation_step": 20,
        "regime_blocklist": "EXPANSION,CONTRACTION,TREND",
        "allow_market_fallback": False,
        "description": "Phase 13 observation profile: score 90 and no MARKET fallback.",
    },
}


def _compact_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def phase4_default_env() -> dict[str, str]:
    return {
        "MARKET_TYPE": "crypto_spot",
        "DATA_SOURCE": "ccxt",
        "CCXT_EXCHANGE_ID": "binance",
        "CCXT_DEFAULT_TYPE": "spot",
        "CCXT_SANDBOX": "0",
        "CCXT_ENABLE_RATE_LIMIT": "1",
        "CCXT_OHLCV_REQUEST_LIMIT": "1000",
        "CCXT_HEALTH_CHECK_SYMBOL": "BTCUSDT",
        "PAIRS": "BTCUSDT,ETHUSDT,LTCUSDT",
        "SYMBOL_SPECS_JSON": _compact_json(PHASE4_SYMBOL_SPECS),
        "ENABLE_LIVE_MODE": "0",
        "ENABLE_PAIR_PROFILES": "1",
        "ALLOW_LIVE_PAIR_PROFILES": "1",
        "PAIR_PROFILES_BACKTEST_ONLY": "1",
        "PAIR_PROFILES_JSON": _compact_json(PHASE4_PAIR_PROFILES),
        "ENABLE_SESSION_GATE": "0",
        "ENABLE_REGIME_LABEL_GATE": "0",
        "SMT_REFERENCE_MAP": "",
        "LTF_TIMEFRAME": "M15",
        "HTF_TIMEFRAME": "H1",
        "TRIGGER_TIMEFRAME": "M5",
        "HISTORY_LIMIT": "1200",
        "SCAN_INTERVAL_MINUTES": "5",
        "MARKET_DATA_CACHE_ENABLED": "1",
        "MARKET_DATA_CACHE_MODE": "read_through",
        "MARKET_DATA_CACHE_TTL_HOURS": "0.05",
        "ENABLE_MARKET_DATA_FRESHNESS_GATE": "1",
        "MAX_LIVE_CANDLE_AGE_SECONDS": "1800",
        "ENABLE_MARKET_DATA_DIAGNOSTICS": "1",
        "MARKET_DATA_DIAGNOSTICS_LOG_PATH": "logs/crypto_forward_market_data.jsonl",
        "MARKET_DATA_DIAGNOSTICS_MAX_CANDLE_AGE_SECONDS": "1800",
        "ENABLE_FORWARD_JOURNAL": "1",
        "FORWARD_JOURNAL_LOG_PATH": "logs/crypto_forward_journal.jsonl",
        "FORWARD_JOURNAL_INCLUDE_SCORE_BREAKDOWN": "1",
        "ENABLE_LIVE_TELEMETRY": "1",
        "LIVE_TELEMETRY_LOG_PATH": "logs/crypto_forward_telemetry.jsonl",
        "ENABLE_LIVE_HEARTBEAT": "1",
        "LIVE_HEARTBEAT_PATH": "logs/crypto_forward_heartbeat.json",
        "HEALTH_MAX_SCAN_AGE_MINUTES": "15",
        "ENABLE_HEALTH_ALERTS": "0",
        "ENABLE_FEED_HEALTH_CHECKS": "1",
        "DAILY_FORWARD_REPORT_PATH": "reports/crypto_forward_daily_report.json",
        "FORWARD_OUTCOME_LOG_PATH": "logs/crypto_forward_outcomes.jsonl",
        "FORWARD_OUTCOME_SUMMARY_PATH": "reports/crypto_forward_outcomes_summary.json",
        "FORWARD_PERFORMANCE_REPORT_PATH": "reports/crypto_forward_performance_report.json",
    }


def apply_phase4_defaults(*, force: bool = False) -> dict[str, str]:
    applied: dict[str, str] = {}
    for key, value in phase4_default_env().items():
        existing = os.environ.get(key)
        if force or existing is None or not existing.strip():
            os.environ[key] = value
            applied[key] = value
    return applied


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase 4 crypto signal-only forward validation.")
    parser.add_argument("--loop", action="store_true", help="Run continuously instead of one validation cycle")
    parser.add_argument("--max-cycles", type=int, default=1, help="Number of scan cycles to run; ignored with --loop")
    parser.add_argument("--no-telegram", action="store_true", help="Evaluate and journal signals without Telegram delivery")
    parser.add_argument("--force-phase4-defaults", action="store_true", help="Override existing env values with Phase 4 defaults")
    parser.add_argument("--print-config", action="store_true", help="Print effective Phase 4 config before running")
    parser.add_argument("--config-only", action="store_true", help="Print effective config and exit")
    return parser


def _effective_max_cycles(args: argparse.Namespace) -> int | None:
    if args.loop:
        return None
    return max(1, int(args.max_cycles or 1))


def _print_effective_config(settings: Settings, applied: Mapping[str, str], *, telegram_enabled: bool) -> None:
    payload = {
        "phase": "phase4_crypto_forward_validation",
        "telegram_enabled": telegram_enabled,
        "data_source": settings.data_source,
        "market_type": settings.market_type,
        "pairs": settings.pairs,
        "timeframes": {
            "htf": settings.htf_timeframe,
            "ltf": settings.ltf_timeframe,
            "trigger": settings.trigger_timeframe,
        },
        "history_limit": settings.history_limit,
        "scan_interval_minutes": settings.scan_interval_minutes,
        "pair_profiles": settings.pair_profiles,
        "forward_journal": settings.forward_journal_log_path,
        "telemetry": settings.live_telemetry_log_path,
        "heartbeat": settings.live_heartbeat_path,
        "market_data_diagnostics": settings.market_data_diagnostics_log_path,
        "applied_defaults": sorted(applied.keys()),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def main() -> None:
    args = build_parser().parse_args()
    load_env_file()
    applied = apply_phase4_defaults(force=args.force_phase4_defaults)
    telegram_enabled = not args.no_telegram
    settings = Settings.from_env(require_telegram=telegram_enabled)
    if args.print_config or args.config_only:
        _print_effective_config(settings, applied, telegram_enabled=telegram_enabled)
    if args.config_only:
        return

    asyncio.run(
        run_engine(
            max_cycles=_effective_max_cycles(args),
            telegram_enabled=telegram_enabled,
            require_telegram=telegram_enabled,
        )
    )


if __name__ == "__main__":
    main()
