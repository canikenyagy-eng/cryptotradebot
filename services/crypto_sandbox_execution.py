from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, Sequence


MODE_DRY_RUN = "dry_run"
MODE_SANDBOX_STUB = "sandbox_stub"

STATE_RECEIVED = "received"
STATE_VALIDATED = "validated"
STATE_ACCEPTED = "accepted"
STATE_SIMULATED_FILLED = "simulated_filled"
STATE_REJECTED = "rejected"
STATE_CANCELED = "canceled"
STATE_BLOCKED_BY_KILL_SWITCH = "blocked_by_kill_switch"

ORDER_LIFECYCLE_STATES = (
    STATE_RECEIVED,
    STATE_VALIDATED,
    STATE_ACCEPTED,
    STATE_SIMULATED_FILLED,
    STATE_REJECTED,
    STATE_CANCELED,
    STATE_BLOCKED_BY_KILL_SWITCH,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _money(value: object) -> float:
    return round(_as_float(value), 2)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled", "active", "halt", "stop"}
    return False


def _event(state: str, observed_at: str, reason: str = "") -> dict[str, object]:
    row: dict[str, object] = {"state": state, "at": observed_at}
    if reason:
        row["reason"] = reason
    return row


@dataclass(frozen=True)
class CryptoSandboxExecutionSettings:
    dry_run_report_path: Path | str = Path("reports/crypto_phase8_execution_dry_run.json")
    report_path: Path | str = Path("reports/crypto_phase9_sandbox_execution_report.json")
    ledger_csv_path: Path | str = Path("reports/crypto_phase9_sandbox_order_ledger.csv")
    kill_switch_path: Path | str = Path("logs/crypto_execution_kill_switch.json")
    mode: str = MODE_DRY_RUN
    max_orders_per_run: int = 5
    require_dry_run_only: bool = True
    allow_sandbox_stub: bool = False
    allow_live_orders: bool = False

    def normalized(self) -> "CryptoSandboxExecutionSettings":
        mode = str(self.mode or MODE_DRY_RUN).strip().lower()
        if mode not in {MODE_DRY_RUN, MODE_SANDBOX_STUB}:
            mode = MODE_DRY_RUN
        return CryptoSandboxExecutionSettings(
            dry_run_report_path=Path(self.dry_run_report_path),
            report_path=Path(self.report_path),
            ledger_csv_path=Path(self.ledger_csv_path),
            kill_switch_path=Path(self.kill_switch_path),
            mode=mode,
            max_orders_per_run=max(0, int(self.max_orders_per_run)),
            require_dry_run_only=bool(self.require_dry_run_only),
            allow_sandbox_stub=bool(self.allow_sandbox_stub),
            allow_live_orders=False,
        )


class ExchangeExecutionAdapter(Protocol):
    name: str

    def submit_order_intent(
        self,
        intent: Mapping[str, object],
        settings: CryptoSandboxExecutionSettings,
        *,
        submitted_at: str,
    ) -> dict[str, object]:
        ...


def build_idempotency_key(intent: Mapping[str, object], *, prefix: str = "phase9") -> str:
    symbol = str(intent.get("symbol") or "unknown").replace("/", "").replace(" ", "").upper()
    side = str(intent.get("side") or "na").lower()
    raw = "|".join(
        str(intent.get(name, ""))
        for name in (
            "intent_id",
            "journal_id",
            "fingerprint",
            "symbol",
            "side",
            "order_type",
            "quantity",
            "entry_price",
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:18]
    return f"{prefix}-{symbol}-{side}-{digest}"[:64]


def validate_order_intent(intent: Mapping[str, object], settings: CryptoSandboxExecutionSettings) -> list[str]:
    reasons: list[str] = []
    if str(intent.get("status", "")).strip().lower() != "ready_dry_run":
        reasons.append("intent_not_ready_dry_run")
    if settings.require_dry_run_only and intent.get("dry_run_only") is not True:
        reasons.append("intent_not_marked_dry_run_only")
    if not str(intent.get("intent_id") or "").strip():
        reasons.append("missing_intent_id")
    if not str(intent.get("symbol") or "").strip():
        reasons.append("missing_symbol")
    side = str(intent.get("side") or "").strip().upper()
    if side not in {"BUY", "SELL"}:
        reasons.append("unsupported_side")
    order_type = str(intent.get("order_type") or "").strip().lower()
    if order_type not in {"market", "limit"}:
        reasons.append("unsupported_order_type")
    if _as_float(intent.get("quantity")) <= 0:
        reasons.append("invalid_quantity")
    if _as_float(intent.get("entry_price")) <= 0:
        reasons.append("invalid_entry_price")
    return reasons


def _execution_result(
    intent: Mapping[str, object],
    *,
    settings: CryptoSandboxExecutionSettings,
    submitted_at: str,
    state: str,
    reason: str,
    accepted: bool = False,
    filled: bool = False,
    live_order_sent: bool = False,
    events: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    return {
        "intent_id": intent.get("intent_id"),
        "journal_id": intent.get("journal_id"),
        "fingerprint": intent.get("fingerprint"),
        "client_order_id": build_idempotency_key(intent),
        "symbol": intent.get("symbol"),
        "exchange_symbol": intent.get("exchange_symbol"),
        "side": intent.get("side"),
        "order_type": intent.get("order_type"),
        "mode": settings.mode,
        "state": state,
        "reason": reason,
        "accepted": bool(accepted),
        "filled": bool(filled),
        "dry_run_only": intent.get("dry_run_only") is True,
        "live_order_sent": bool(live_order_sent),
        "quantity": intent.get("quantity"),
        "entry_price": intent.get("entry_price"),
        "notional_quote": _money(intent.get("notional_quote")),
        "submitted_at": submitted_at,
        "events": [dict(event) for event in events],
    }


class DryRunExecutionAdapter:
    name = MODE_DRY_RUN

    def submit_order_intent(
        self,
        intent: Mapping[str, object],
        settings: CryptoSandboxExecutionSettings,
        *,
        submitted_at: str,
    ) -> dict[str, object]:
        reasons = validate_order_intent(intent, settings)
        if reasons:
            return _execution_result(
                intent,
                settings=settings,
                submitted_at=submitted_at,
                state=STATE_REJECTED,
                reason=",".join(reasons),
                events=(
                    _event(STATE_RECEIVED, submitted_at),
                    _event(STATE_REJECTED, submitted_at, ",".join(reasons)),
                ),
            )
        return _execution_result(
            intent,
            settings=settings,
            submitted_at=submitted_at,
            state=STATE_SIMULATED_FILLED,
            reason="dry_run_simulated_fill",
            accepted=True,
            filled=True,
            events=(
                _event(STATE_RECEIVED, submitted_at),
                _event(STATE_VALIDATED, submitted_at),
                _event(STATE_ACCEPTED, submitted_at),
                _event(STATE_SIMULATED_FILLED, submitted_at),
            ),
        )


class SandboxTestnetExecutionAdapterStub:
    name = MODE_SANDBOX_STUB

    def submit_order_intent(
        self,
        intent: Mapping[str, object],
        settings: CryptoSandboxExecutionSettings,
        *,
        submitted_at: str,
    ) -> dict[str, object]:
        if not settings.allow_sandbox_stub:
            return _execution_result(
                intent,
                settings=settings,
                submitted_at=submitted_at,
                state=STATE_REJECTED,
                reason="sandbox_stub_not_enabled",
                events=(
                    _event(STATE_RECEIVED, submitted_at),
                    _event(STATE_REJECTED, submitted_at, "sandbox_stub_not_enabled"),
                ),
            )
        reasons = validate_order_intent(intent, settings)
        if reasons:
            return _execution_result(
                intent,
                settings=settings,
                submitted_at=submitted_at,
                state=STATE_REJECTED,
                reason=",".join(reasons),
                events=(
                    _event(STATE_RECEIVED, submitted_at),
                    _event(STATE_REJECTED, submitted_at, ",".join(reasons)),
                ),
            )
        return _execution_result(
            intent,
            settings=settings,
            submitted_at=submitted_at,
            state=STATE_SIMULATED_FILLED,
            reason="sandbox_stub_simulated_fill",
            accepted=True,
            filled=True,
            events=(
                _event(STATE_RECEIVED, submitted_at),
                _event(STATE_VALIDATED, submitted_at),
                _event(STATE_ACCEPTED, submitted_at),
                _event(STATE_SIMULATED_FILLED, submitted_at, "no exchange request was made"),
            ),
        )


def build_execution_adapter(settings: CryptoSandboxExecutionSettings) -> ExchangeExecutionAdapter:
    cfg = settings.normalized()
    if cfg.mode == MODE_SANDBOX_STUB:
        return SandboxTestnetExecutionAdapterStub()
    return DryRunExecutionAdapter()


def read_kill_switch(path: Path | str) -> dict[str, object]:
    kill_path = Path(path)
    if not kill_path.exists():
        return {
            "active": False,
            "path": str(kill_path),
            "reason": "kill switch file missing",
        }

    try:
        text = kill_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return {
            "active": True,
            "path": str(kill_path),
            "reason": f"kill switch unreadable: {exc}",
        }

    if not text:
        return {
            "active": False,
            "path": str(kill_path),
            "reason": "kill switch file empty",
        }

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        active = text.lower() in {"1", "true", "yes", "on", "enabled", "active", "halt", "stop", "kill"}
        return {
            "active": active,
            "path": str(kill_path),
            "reason": "plain-text kill switch active" if active else "plain-text kill switch inactive",
        }

    if not isinstance(payload, Mapping):
        return {
            "active": False,
            "path": str(kill_path),
            "reason": "kill switch JSON is not an object",
        }

    active = any(_truthy(payload.get(key)) for key in ("enabled", "active", "kill_switch", "halt", "stop"))
    return {
        "active": active,
        "path": str(kill_path),
        "reason": str(payload.get("reason") or ("kill switch enabled" if active else "kill switch disabled")),
        "payload": dict(payload),
    }


def load_crypto_execution_dry_run_report(path: Path | str) -> dict[str, object]:
    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "type": "missing_crypto_execution_dry_run_report",
            "path": str(report_path),
            "intents": [],
            "live_execution_allowed": False,
            "decision": {
                "action": "MISSING_PHASE8_DRY_RUN_REPORT",
                "readiness": "missing",
                "reason": f"Phase 8 dry-run report missing: {report_path}",
                "live_execution_allowed": False,
            },
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "type": "invalid_crypto_execution_dry_run_report",
            "path": str(report_path),
            "intents": [],
            "live_execution_allowed": False,
            "decision": {
                "action": "INVALID_PHASE8_DRY_RUN_REPORT",
                "readiness": "invalid",
                "reason": f"Phase 8 dry-run report unreadable: {exc}",
                "live_execution_allowed": False,
            },
        }
    return payload if isinstance(payload, dict) else {}


def _ready_intents(source_report: Mapping[str, object], max_orders: int) -> list[dict[str, object]]:
    raw_intents = source_report.get("intents") if isinstance(source_report.get("intents"), list) else []
    ready = [
        dict(intent)
        for intent in raw_intents
        if isinstance(intent, Mapping) and str(intent.get("status", "")).strip().lower() == "ready_dry_run"
    ]
    return ready[:max_orders] if max_orders >= 0 else ready


def _blocked_by_kill_switch(
    intent: Mapping[str, object],
    settings: CryptoSandboxExecutionSettings,
    submitted_at: str,
    reason: str,
) -> dict[str, object]:
    return _execution_result(
        intent,
        settings=settings,
        submitted_at=submitted_at,
        state=STATE_BLOCKED_BY_KILL_SWITCH,
        reason=reason,
        events=(
            _event(STATE_RECEIVED, submitted_at),
            _event(STATE_BLOCKED_BY_KILL_SWITCH, submitted_at, reason),
        ),
    )


def build_reconciliation_report(orders: Sequence[Mapping[str, object]], *, expected_orders: int) -> dict[str, object]:
    ids: dict[str, int] = {}
    for order in orders:
        key = str(order.get("client_order_id") or "")
        if key:
            ids[key] = ids.get(key, 0) + 1
    duplicate_ids = sorted(key for key, count in ids.items() if count > 1)
    live_order_sent = sum(1 for order in orders if order.get("live_order_sent") is True)
    accepted = sum(1 for order in orders if order.get("accepted") is True)
    filled = sum(1 for order in orders if order.get("filled") is True)
    rejected = sum(1 for order in orders if order.get("state") in {STATE_REJECTED, STATE_BLOCKED_BY_KILL_SWITCH})
    missing_results = max(0, expected_orders - len(orders))
    status = "reconciled"
    if live_order_sent:
        status = "safety_violation"
    elif duplicate_ids:
        status = "duplicate_idempotency_keys"
    elif missing_results:
        status = "missing_execution_results"
    return {
        "status": status,
        "expected_orders": expected_orders,
        "recorded_orders": len(orders),
        "accepted": accepted,
        "filled": filled,
        "rejected": rejected,
        "missing_results": missing_results,
        "duplicate_client_order_ids": duplicate_ids,
        "live_order_sent": live_order_sent,
    }


class CryptoSandboxExecutionEngine:
    def __init__(
        self,
        settings: CryptoSandboxExecutionSettings,
        *,
        adapter: ExchangeExecutionAdapter | None = None,
    ) -> None:
        self.settings = settings.normalized()
        self.adapter = adapter or build_execution_adapter(self.settings)

    def build_report(self, source_report: Mapping[str, object] | None = None) -> dict[str, object]:
        if source_report is None:
            report = load_crypto_execution_dry_run_report(self.settings.dry_run_report_path)
        else:
            report = dict(source_report)
        generated_at = utc_now()
        kill_switch = read_kill_switch(self.settings.kill_switch_path)
        source_decision = _as_dict(report.get("decision"))
        source_summary = _as_dict(report.get("summary"))
        source_live_flag = report.get("live_execution_allowed") is True or source_decision.get("live_execution_allowed") is True
        selected = _ready_intents(report, self.settings.max_orders_per_run)

        orders: list[dict[str, object]] = []
        if source_live_flag:
            for intent in selected:
                orders.append(
                    _blocked_by_kill_switch(
                        intent,
                        self.settings,
                        generated_at,
                        "source_report_live_execution_allowed_true",
                    )
                )
        elif kill_switch.get("active") is True:
            for intent in selected:
                orders.append(
                    _blocked_by_kill_switch(
                        intent,
                        self.settings,
                        generated_at,
                        str(kill_switch.get("reason") or "kill switch active"),
                    )
                )
        else:
            for intent in selected:
                orders.append(self.adapter.submit_order_intent(intent, self.settings, submitted_at=generated_at))

        reconciliation = build_reconciliation_report(orders, expected_orders=len(selected))
        summary = self._summary(report, selected, orders)
        decision = self._decision(summary, reconciliation, kill_switch, source_live_flag)

        return {
            "type": "crypto_sandbox_execution_report",
            "version": 1,
            "phase": "phase9_sandbox_execution_architecture",
            "generated_at": generated_at,
            "decision": decision,
            "settings": {
                "dry_run_report_path": str(self.settings.dry_run_report_path),
                "mode": self.settings.mode,
                "adapter": getattr(self.adapter, "name", self.settings.mode),
                "kill_switch_path": str(self.settings.kill_switch_path),
                "max_orders_per_run": self.settings.max_orders_per_run,
                "require_dry_run_only": self.settings.require_dry_run_only,
                "allow_sandbox_stub": self.settings.allow_sandbox_stub,
                "allow_live_orders": False,
            },
            "source_report": {
                "type": report.get("type"),
                "phase": report.get("phase"),
                "generated_at": report.get("generated_at"),
                "decision": source_decision,
                "summary": source_summary,
                "live_execution_allowed": report.get("live_execution_allowed") is True,
            },
            "summary": summary,
            "kill_switch": kill_switch,
            "reconciliation": reconciliation,
            "order_lifecycle_states": list(ORDER_LIFECYCLE_STATES),
            "orders": orders,
            "live_execution_allowed": False,
            "safety_note": "Phase 9 supports dry-run and sandbox-stub execution only. It does not submit exchange orders.",
        }

    def _summary(
        self,
        source_report: Mapping[str, object],
        selected: Sequence[Mapping[str, object]],
        orders: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        raw_intents = source_report.get("intents") if isinstance(source_report.get("intents"), list) else []
        ready = [
            intent
            for intent in raw_intents
            if isinstance(intent, Mapping) and str(intent.get("status", "")).strip().lower() == "ready_dry_run"
        ]
        rejected = [order for order in orders if order.get("state") in {STATE_REJECTED, STATE_BLOCKED_BY_KILL_SWITCH}]
        return {
            "source_intents": len(raw_intents),
            "source_ready_dry_run": len(ready),
            "selected_for_execution": len(selected),
            "accepted": sum(1 for order in orders if order.get("accepted") is True),
            "filled": sum(1 for order in orders if order.get("filled") is True),
            "rejected": len(rejected),
            "live_order_sent": sum(1 for order in orders if order.get("live_order_sent") is True),
            "total_notional_quote": _money(sum(_as_float(order.get("notional_quote")) for order in orders if order.get("accepted") is True)),
        }

    def _decision(
        self,
        summary: Mapping[str, object],
        reconciliation: Mapping[str, object],
        kill_switch: Mapping[str, object],
        source_live_flag: bool,
    ) -> dict[str, object]:
        if _as_int(summary.get("live_order_sent")) > 0 or _as_int(reconciliation.get("live_order_sent")) > 0:
            return {
                "action": "SAFETY_VIOLATION",
                "readiness": "blocked",
                "reason": "an adapter reported live_order_sent=true",
                "live_execution_allowed": False,
                "sandbox_execution_ready": False,
            }
        if source_live_flag:
            return {
                "action": "BLOCK_SOURCE_LIVE_FLAG",
                "readiness": "blocked",
                "reason": "source dry-run report unexpectedly allowed live execution",
                "live_execution_allowed": False,
                "sandbox_execution_ready": False,
            }
        if kill_switch.get("active") is True:
            return {
                "action": "KILL_SWITCH_ACTIVE",
                "readiness": "blocked",
                "reason": str(kill_switch.get("reason") or "kill switch active"),
                "live_execution_allowed": False,
                "sandbox_execution_ready": False,
            }
        if _as_int(summary.get("selected_for_execution")) <= 0:
            return {
                "action": "COLLECT_DRY_RUN_INTENTS",
                "readiness": "collecting",
                "reason": "no ready Phase 8 dry-run intents available for Phase 9",
                "live_execution_allowed": False,
                "sandbox_execution_ready": False,
            }
        if reconciliation.get("status") != "reconciled":
            return {
                "action": "REVIEW_RECONCILIATION",
                "readiness": "needs_review",
                "reason": str(reconciliation.get("status")),
                "live_execution_allowed": False,
                "sandbox_execution_ready": False,
            }
        if _as_int(summary.get("rejected")) > 0:
            return {
                "action": "REVIEW_EXECUTION_REJECTIONS",
                "readiness": "needs_review",
                "reason": "one or more selected intents was rejected by Phase 9 execution validation",
                "live_execution_allowed": False,
                "sandbox_execution_ready": False,
            }
        return {
            "action": "SANDBOX_EXECUTION_READY",
            "readiness": "sandbox_ready",
            "reason": "selected dry-run intents passed Phase 9 adapter and reconciliation checks",
            "live_execution_allowed": False,
            "sandbox_execution_ready": True,
        }


def write_crypto_sandbox_execution_outputs(report: Mapping[str, object], settings: CryptoSandboxExecutionSettings) -> None:
    cfg = settings.normalized()
    report_path = Path(cfg.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    orders = report.get("orders") if isinstance(report.get("orders"), list) else []
    ledger_path = Path(cfg.ledger_csv_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "client_order_id",
        "intent_id",
        "journal_id",
        "symbol",
        "exchange_symbol",
        "side",
        "order_type",
        "mode",
        "state",
        "reason",
        "accepted",
        "filled",
        "dry_run_only",
        "live_order_sent",
        "quantity",
        "entry_price",
        "notional_quote",
        "submitted_at",
    ]
    with ledger_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for order in orders:
            writer.writerow(dict(order) if isinstance(order, Mapping) else {})
