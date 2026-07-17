from __future__ import annotations

import argparse
import os

from config import load_env_file
from services.crypto_sandbox_execution import (
    MODE_DRY_RUN,
    MODE_SANDBOX_STUB,
    CryptoSandboxExecutionEngine,
    CryptoSandboxExecutionSettings,
    write_crypto_sandbox_execution_outputs,
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


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = _env_str(name, default).lower()
    return value if value in choices else default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase 9 crypto sandbox execution architecture checks.")
    parser.add_argument("--dry-run-report", default=_env_str("PHASE9_DRY_RUN_JSON", "reports/crypto_phase8_execution_dry_run.json"))
    parser.add_argument("--report-json", default=_env_str("PHASE9_SANDBOX_REPORT_JSON", "reports/crypto_phase9_sandbox_execution_report.json"))
    parser.add_argument("--ledger-csv", default=_env_str("PHASE9_SANDBOX_LEDGER_CSV", "reports/crypto_phase9_sandbox_order_ledger.csv"))
    parser.add_argument("--kill-switch", default=_env_str("PHASE9_KILL_SWITCH_PATH", "logs/crypto_execution_kill_switch.json"))
    parser.add_argument(
        "--mode",
        choices=(MODE_DRY_RUN, MODE_SANDBOX_STUB),
        default=_env_choice("PHASE9_EXECUTION_MODE", MODE_DRY_RUN, {MODE_DRY_RUN, MODE_SANDBOX_STUB}),
    )
    parser.add_argument("--max-orders", type=int, default=_env_int("PHASE9_MAX_ORDERS_PER_RUN", 5))
    parser.add_argument(
        "--allow-sandbox-stub",
        action="store_true",
        default=_env_bool("PHASE9_ALLOW_SANDBOX_STUB", False),
        help="Allow the sandbox/testnet stub to simulate accepted orders. It still sends no exchange requests.",
    )
    parser.add_argument(
        "--allow-non-dry-run-intents",
        action="store_true",
        default=not _env_bool("PHASE9_REQUIRE_DRY_RUN_ONLY", True),
        help="Do not reject intents missing dry_run_only=true. For diagnostics only.",
    )
    return parser


def build_settings(args: argparse.Namespace) -> CryptoSandboxExecutionSettings:
    return CryptoSandboxExecutionSettings(
        dry_run_report_path=args.dry_run_report,
        report_path=args.report_json,
        ledger_csv_path=args.ledger_csv,
        kill_switch_path=args.kill_switch,
        mode=args.mode,
        max_orders_per_run=args.max_orders,
        require_dry_run_only=not args.allow_non_dry_run_intents,
        allow_sandbox_stub=args.allow_sandbox_stub,
        allow_live_orders=False,
    ).normalized()


def print_summary(report: dict[str, object], settings: CryptoSandboxExecutionSettings) -> None:
    decision = report.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    summary = report.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    reconciliation = report.get("reconciliation")
    if not isinstance(reconciliation, dict):
        reconciliation = {}

    print()
    print("CRYPTO PHASE 9 SANDBOX EXECUTION")
    print(f"Action: {decision.get('action')} | readiness={decision.get('readiness')}")
    print(f"Reason: {decision.get('reason')}")
    print(
        "Orders: selected={selected} accepted={accepted} filled={filled} rejected={rejected} live_sent={live_sent}".format(
            selected=int(summary.get("selected_for_execution", 0) or 0),
            accepted=int(summary.get("accepted", 0) or 0),
            filled=int(summary.get("filled", 0) or 0),
            rejected=int(summary.get("rejected", 0) or 0),
            live_sent=int(summary.get("live_order_sent", 0) or 0),
        )
    )
    print(f"Reconciliation: {reconciliation.get('status')}")
    print(f"Live execution allowed: {report.get('live_execution_allowed')}")
    print(f"Report: {settings.report_path}")
    print(f"Ledger: {settings.ledger_csv_path}")


def main() -> None:
    load_env_file()
    args = build_parser().parse_args()
    settings = build_settings(args)
    report = CryptoSandboxExecutionEngine(settings).build_report()
    write_crypto_sandbox_execution_outputs(report, settings)
    print_summary(report, settings)


if __name__ == "__main__":
    main()
