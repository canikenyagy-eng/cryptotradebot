from __future__ import annotations

import argparse
import asyncio
import os

from config import load_env_file
from services.crypto_realtime_guard import (
    BinanceBookTickerWebSocketClient,
    CryptoRealtimeGuardEngine,
    CryptoRealtimeGuardSettings,
    RealtimeSnapshotStore,
    write_crypto_realtime_guard_outputs,
)


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
    parser = argparse.ArgumentParser(description="Run Phase 12 realtime final pre-order guard checks.")
    parser.add_argument("--phase11-report", default=_env_str("PHASE12_PHASE11_REPORT_JSON", "reports/crypto_phase11_market_safety_report.json"))
    parser.add_argument("--report-json", default=_env_str("PHASE12_REALTIME_GUARD_JSON", "reports/crypto_phase12_realtime_guard_report.json"))
    parser.add_argument("--checks-csv", default=_env_str("PHASE12_REALTIME_GUARD_CSV", "reports/crypto_phase12_realtime_guard_checks.csv"))
    parser.add_argument("--snapshot-json", default=_env_str("PHASE12_SNAPSHOT_JSON", "logs/crypto_realtime_snapshots.json"))
    parser.add_argument("--market-data-diagnostics", default=_env_str("PHASE12_MARKET_DATA_DIAGNOSTICS_JSONL", "logs/crypto_forward_market_data.jsonl"))
    parser.add_argument("--symbols", default=_env_str("PHASE12_SYMBOLS", "BTCUSDT,ETHUSDT"))
    parser.add_argument("--exchange-id", default=_env_str("PHASE12_EXCHANGE_ID", "binance"))
    parser.add_argument("--default-type", default=_env_str("PHASE12_DEFAULT_TYPE", "spot"))
    parser.add_argument("--timeout-ms", type=int, default=_env_int("PHASE12_TIMEOUT_MS", 10000))
    parser.add_argument("--websocket-url", default=_env_str("PHASE12_BINANCE_WEBSOCKET_URL", "wss://stream.binance.com:9443"))
    parser.add_argument(
        "--collect-websocket-seconds",
        type=float,
        default=_env_float("PHASE12_COLLECT_WEBSOCKET_SECONDS", 0.0),
        help="Collect Binance bookTicker/miniTicker snapshots before running the guard.",
    )
    parser.add_argument(
        "--keep-collecting-until-timeout",
        action="store_true",
        default=_env_bool("PHASE12_KEEP_COLLECTING_UNTIL_TIMEOUT", False),
        help="Do not stop websocket collection early when all symbols have snapshots.",
    )
    parser.add_argument("--order-book-limit", type=int, default=_env_int("PHASE12_ORDER_BOOK_LIMIT", 5))
    parser.add_argument("--max-snapshot-age", type=float, default=_env_float("PHASE12_MAX_SNAPSHOT_AGE_SECONDS", 10.0))
    parser.add_argument("--max-spread-bps", type=float, default=_env_float("PHASE12_MAX_SPREAD_BPS", 10.0))
    parser.add_argument("--max-entry-deviation-bps", type=float, default=_env_float("PHASE12_MAX_ENTRY_PRICE_DEVIATION_BPS", 50.0))
    parser.add_argument("--max-time-drift-ms", type=float, default=_env_float("PHASE12_MAX_EXCHANGE_TIME_DRIFT_MS", 2000.0))
    parser.add_argument("--diagnostics-timeframe", default=_env_str("PHASE12_DIAGNOSTICS_TIMEFRAME", "M5"))
    parser.add_argument(
        "--max-diagnostics-age",
        type=float,
        default=_env_float("PHASE12_MAX_MARKET_DIAGNOSTICS_AGE_SECONDS", 900.0),
    )
    parser.add_argument(
        "--disable-rest-fallback",
        action="store_true",
        default=not _env_bool("PHASE12_ALLOW_REST_FALLBACK", True),
        help="Block when websocket snapshots are stale/missing instead of using REST ticker fallback.",
    )
    parser.add_argument(
        "--disable-order-book-refresh",
        action="store_true",
        default=not _env_bool("PHASE12_ENABLE_ORDER_BOOK_REFRESH", True),
        help="Skip the final REST order-book refresh confirmation.",
    )
    parser.add_argument(
        "--allow-phase11-not-ready",
        action="store_true",
        default=not _env_bool("PHASE12_REQUIRE_PHASE11_READY", True),
        help="Run final guard checks even if Phase 11 did not mark market data safe. For diagnostics only.",
    )
    parser.add_argument(
        "--allow-missing-diagnostics",
        action="store_true",
        default=not _env_bool("PHASE12_REQUIRE_CLEAN_MARKET_DIAGNOSTICS", True),
        help="Do not block on missing/stale Phase 4 trigger diagnostics. For diagnostics only.",
    )
    return parser


def build_settings(args: argparse.Namespace) -> CryptoRealtimeGuardSettings:
    return CryptoRealtimeGuardSettings(
        phase11_report_path=args.phase11_report,
        report_path=args.report_json,
        checks_csv_path=args.checks_csv,
        snapshot_path=args.snapshot_json,
        market_data_diagnostics_path=args.market_data_diagnostics,
        symbols=args.symbols,
        exchange_id=args.exchange_id,
        default_type=args.default_type,
        timeout_ms=args.timeout_ms,
        allow_rest_fallback=not args.disable_rest_fallback,
        enable_order_book_refresh=not args.disable_order_book_refresh,
        order_book_limit=args.order_book_limit,
        max_snapshot_age_seconds=args.max_snapshot_age,
        max_spread_bps=args.max_spread_bps,
        max_entry_price_deviation_bps=args.max_entry_deviation_bps,
        max_exchange_time_drift_ms=args.max_time_drift_ms,
        require_phase11_ready=not args.allow_phase11_not_ready,
        require_clean_market_diagnostics=not args.allow_missing_diagnostics,
        diagnostics_timeframe=args.diagnostics_timeframe,
        max_market_diagnostics_age_seconds=args.max_diagnostics_age,
        allow_live_orders=False,
    ).normalized()


def print_summary(report: dict[str, object], settings: CryptoRealtimeGuardSettings) -> None:
    decision = report.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    summary = report.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    print()
    print("CRYPTO PHASE 12 REALTIME FINAL PRE-ORDER GUARD")
    print(f"Action: {decision.get('action')} | readiness={decision.get('readiness')}")
    print(f"Reason: {decision.get('reason')}")
    print(
        "Checks: checked={checked} ready={ready} blocked={blocked}".format(
            checked=int(summary.get("requests_checked", 0) or 0),
            ready=int(summary.get("final_guard_ready", 0) or 0),
            blocked=int(summary.get("blocked", 0) or 0),
        )
    )
    print(f"Paper/testnet readiness allowed: {report.get('paper_testnet_readiness_allowed')}")
    print(f"Order submission allowed: {report.get('order_submission_allowed')}")
    print(f"Live execution allowed: {report.get('live_execution_allowed')}")
    print(f"Report: {settings.report_path}")
    print(f"Checks: {settings.checks_csv_path}")
    print(f"Snapshots: {settings.snapshot_path}")


async def maybe_collect_websocket(args: argparse.Namespace, settings: CryptoRealtimeGuardSettings, store: RealtimeSnapshotStore) -> None:
    if args.collect_websocket_seconds <= 0:
        return
    collector = BinanceBookTickerWebSocketClient(
        symbols=settings.symbols,
        store=store,
        base_url=args.websocket_url,
        open_timeout_seconds=max(1.0, args.timeout_ms / 1000.0),
    )
    result = await collector.collect_for(
        duration_seconds=args.collect_websocket_seconds,
        stop_when_all_ready=not args.keep_collecting_until_timeout,
    )
    print(
        "Collected websocket snapshots: messages={messages} updates={updates} url={url}".format(
            messages=result.get("messages"),
            updates=result.get("updates"),
            url=result.get("url"),
        )
    )


def main() -> None:
    load_env_file()
    args = build_parser().parse_args()
    settings = build_settings(args)
    store = RealtimeSnapshotStore(settings.snapshot_path)
    asyncio.run(maybe_collect_websocket(args, settings, store))
    engine = CryptoRealtimeGuardEngine(settings, store=store)
    try:
        report = engine.build_report()
    finally:
        engine.close()
    write_crypto_realtime_guard_outputs(report, settings)
    print_summary(report, settings)


if __name__ == "__main__":
    main()
