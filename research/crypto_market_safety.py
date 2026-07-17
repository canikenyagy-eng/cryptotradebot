from __future__ import annotations

import argparse
import os

from config import load_env_file
from services.crypto_market_safety import (
    CryptoMarketSafetyEngine,
    CryptoMarketSafetySettings,
    write_crypto_market_safety_outputs,
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
    parser = argparse.ArgumentParser(description="Run Phase 11 realtime market safety checks.")
    parser.add_argument("--phase10-report", default=_env_str("PHASE11_PHASE10_REPORT_JSON", "reports/crypto_phase10_testnet_order_preview.json"))
    parser.add_argument("--report-json", default=_env_str("PHASE11_MARKET_SAFETY_JSON", "reports/crypto_phase11_market_safety_report.json"))
    parser.add_argument("--checks-csv", default=_env_str("PHASE11_MARKET_SAFETY_CSV", "reports/crypto_phase11_market_safety_checks.csv"))
    parser.add_argument("--market-data-diagnostics", default=_env_str("PHASE11_MARKET_DATA_DIAGNOSTICS_JSONL", "logs/crypto_forward_market_data.jsonl"))
    parser.add_argument("--exchange-id", default=_env_str("PHASE11_EXCHANGE_ID", "binance"))
    parser.add_argument("--default-type", default=_env_str("PHASE11_DEFAULT_TYPE", "spot"))
    parser.add_argument("--timeout-ms", type=int, default=_env_int("PHASE11_TIMEOUT_MS", 10000))
    parser.add_argument("--order-book-limit", type=int, default=_env_int("PHASE11_ORDER_BOOK_LIMIT", 5))
    parser.add_argument("--max-ticker-age", type=float, default=_env_float("PHASE11_MAX_TICKER_AGE_SECONDS", 30.0))
    parser.add_argument("--max-spread-bps", type=float, default=_env_float("PHASE11_MAX_SPREAD_BPS", 10.0))
    parser.add_argument("--max-time-drift-ms", type=float, default=_env_float("PHASE11_MAX_EXCHANGE_TIME_DRIFT_MS", 2000.0))
    parser.add_argument("--max-entry-deviation-bps", type=float, default=_env_float("PHASE11_MAX_ENTRY_PRICE_DEVIATION_BPS", 100.0))
    parser.add_argument("--diagnostics-timeframe", default=_env_str("PHASE11_DIAGNOSTICS_TIMEFRAME", "M5"))
    parser.add_argument(
        "--max-diagnostics-age",
        type=float,
        default=_env_float("PHASE11_MAX_MARKET_DIAGNOSTICS_AGE_SECONDS", 900.0),
    )
    parser.add_argument(
        "--disable-order-book-check",
        action="store_true",
        default=not _env_bool("PHASE11_ENABLE_ORDER_BOOK_CHECK", True),
        help="Use ticker bid/ask only and skip order-book snapshot checks.",
    )
    parser.add_argument(
        "--allow-phase10-not-ready",
        action="store_true",
        default=not _env_bool("PHASE11_REQUIRE_PHASE10_READY", True),
        help="Run market checks even if Phase 10 did not report preview-ready. For diagnostics only.",
    )
    parser.add_argument(
        "--allow-missing-diagnostics",
        action="store_true",
        default=not _env_bool("PHASE11_REQUIRE_CLEAN_MARKET_DIAGNOSTICS", True),
        help="Do not block when recent Phase 4 market diagnostics are missing/stale. For diagnostics only.",
    )
    return parser


def build_settings(args: argparse.Namespace) -> CryptoMarketSafetySettings:
    return CryptoMarketSafetySettings(
        phase10_report_path=args.phase10_report,
        report_path=args.report_json,
        checks_csv_path=args.checks_csv,
        market_data_diagnostics_path=args.market_data_diagnostics,
        exchange_id=args.exchange_id,
        default_type=args.default_type,
        timeout_ms=args.timeout_ms,
        enable_order_book_check=not args.disable_order_book_check,
        order_book_limit=args.order_book_limit,
        max_ticker_age_seconds=args.max_ticker_age,
        max_spread_bps=args.max_spread_bps,
        max_exchange_time_drift_ms=args.max_time_drift_ms,
        max_entry_price_deviation_bps=args.max_entry_deviation_bps,
        require_phase10_ready=not args.allow_phase10_not_ready,
        require_clean_market_diagnostics=not args.allow_missing_diagnostics,
        diagnostics_timeframe=args.diagnostics_timeframe,
        max_market_diagnostics_age_seconds=args.max_diagnostics_age,
        allow_live_orders=False,
    ).normalized()


def print_summary(report: dict[str, object], settings: CryptoMarketSafetySettings) -> None:
    decision = report.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    summary = report.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    print()
    print("CRYPTO PHASE 11 REALTIME MARKET SAFETY")
    print(f"Action: {decision.get('action')} | readiness={decision.get('readiness')}")
    print(f"Reason: {decision.get('reason')}")
    print(
        "Checks: checked={checked} safe={safe} blocked={blocked}".format(
            checked=int(summary.get("requests_checked", 0) or 0),
            safe=int(summary.get("market_safe", 0) or 0),
            blocked=int(summary.get("blocked", 0) or 0),
        )
    )
    print(f"Market data safe: {report.get('execution_market_data_safe')}")
    print(f"Order submission allowed: {report.get('order_submission_allowed')}")
    print(f"Live execution allowed: {report.get('live_execution_allowed')}")
    print(f"Report: {settings.report_path}")
    print(f"Checks: {settings.checks_csv_path}")


def main() -> None:
    load_env_file()
    args = build_parser().parse_args()
    settings = build_settings(args)
    engine = CryptoMarketSafetyEngine(settings)
    try:
        report = engine.build_report()
    finally:
        engine.close()
    write_crypto_market_safety_outputs(report, settings)
    print_summary(report, settings)


if __name__ == "__main__":
    main()
