from __future__ import annotations

import argparse
import os

from config import load_env_file
from services.crypto_testnet_order_preview import (
    CryptoTestnetOrderPreviewEngine,
    CryptoTestnetOrderPreviewSettings,
    write_crypto_testnet_order_preview_outputs,
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


def _env_bool(name: str, default: bool) -> bool:
    value = _env_str(name, "1" if default else "0").lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Phase 10 crypto testnet order request previews.")
    parser.add_argument("--phase9-report", default=_env_str("PHASE10_PHASE9_REPORT_JSON", "reports/crypto_phase9_sandbox_execution_report.json"))
    parser.add_argument("--report-json", default=_env_str("PHASE10_PREVIEW_REPORT_JSON", "reports/crypto_phase10_testnet_order_preview.json"))
    parser.add_argument("--requests-csv", default=_env_str("PHASE10_REQUESTS_CSV", "reports/crypto_phase10_testnet_order_requests.csv"))
    parser.add_argument("--exchange-id", default=_env_str("PHASE10_EXCHANGE_ID", "binance"))
    parser.add_argument("--default-type", default=_env_str("PHASE10_DEFAULT_TYPE", "spot"))
    parser.add_argument("--max-orders", type=int, default=_env_int("PHASE10_MAX_ORDERS_PER_RUN", 5))
    parser.add_argument("--allowed-symbols", default=_env_str("PHASE10_ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT"))
    parser.add_argument(
        "--allow-phase9-not-ready",
        action="store_true",
        default=not _env_bool("PHASE10_REQUIRE_PHASE9_READY", True),
        help="Preview requests even when Phase 9 did not report sandbox_execution_ready. For diagnostics only.",
    )
    parser.add_argument(
        "--allow-non-buy-spot",
        action="store_true",
        default=not _env_bool("PHASE10_REQUIRE_SPOT_LONG_ONLY", True),
        help="Allow non-BUY spot request previews. For diagnostics only.",
    )
    return parser


def build_settings(args: argparse.Namespace) -> CryptoTestnetOrderPreviewSettings:
    return CryptoTestnetOrderPreviewSettings(
        phase9_report_path=args.phase9_report,
        report_path=args.report_json,
        requests_csv_path=args.requests_csv,
        exchange_id=args.exchange_id,
        default_type=args.default_type,
        max_orders_per_run=args.max_orders,
        require_phase9_ready=not args.allow_phase9_not_ready,
        allowed_symbols=args.allowed_symbols,
        require_spot_long_only=not args.allow_non_buy_spot,
        testnet_submission_enabled=False,
        allow_live_orders=False,
    ).normalized()


def print_summary(report: dict[str, object], settings: CryptoTestnetOrderPreviewSettings) -> None:
    decision = report.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    summary = report.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    print()
    print("CRYPTO PHASE 10 TESTNET ORDER PREVIEW")
    print(f"Action: {decision.get('action')} | readiness={decision.get('readiness')}")
    print(f"Reason: {decision.get('reason')}")
    print(
        "Requests: selected={selected} previews={previews} blocked={blocked} submitted={submitted} live_sent={live_sent}".format(
            selected=int(summary.get("source_orders_selected", 0) or 0),
            previews=int(summary.get("request_previews", 0) or 0),
            blocked=int(summary.get("blocked", 0) or 0),
            submitted=int(summary.get("order_submission_attempted", 0) or 0),
            live_sent=int(summary.get("live_order_sent", 0) or 0),
        )
    )
    print(f"Live execution allowed: {report.get('live_execution_allowed')}")
    print(f"Order submission attempted: {report.get('order_submission_attempted')}")
    print(f"Report: {settings.report_path}")
    print(f"Requests: {settings.requests_csv_path}")


def main() -> None:
    load_env_file()
    args = build_parser().parse_args()
    settings = build_settings(args)
    report = CryptoTestnetOrderPreviewEngine(settings).build_report()
    write_crypto_testnet_order_preview_outputs(report, settings)
    print_summary(report, settings)


if __name__ == "__main__":
    main()
